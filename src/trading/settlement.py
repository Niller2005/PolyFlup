"""Trade settlement logic"""

import json
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from src.config.settings import GAMMA_API_BASE, PROXY_PK
from src.utils.logger import log, log_error, send_discord
from src.trading.orders import cancel_order, get_closed_positions
from src.data.db_connection import db_connection
from eth_account import Account


def get_market_resolution(slug: str):
    """
    Fetch market resolution from Gamma API.
    Returns:
        (resolved, outcome_prices)
        resolved: bool - True if market is fully resolved (prices are 0 or 1)
        outcome_prices: list[float] - [price_up, price_down] e.g. [1.0, 0.0]
    """
    try:
        r = requests.get(f"{GAMMA_API_BASE}/markets/slug/{slug}", timeout=5)
        if r.status_code == 200:
            data = r.json()

            # Check outcomePrices
            outcome_prices = data.get("outcomePrices")
            if isinstance(outcome_prices, str):
                outcome_prices = json.loads(outcome_prices)

            if not outcome_prices or len(outcome_prices) < 2:
                return False, None

            # Parse prices
            p0 = float(outcome_prices[0])
            p1 = float(outcome_prices[1])

            # Check if resolved (one is 1, one is 0)
            # We use a loose check (>= 0.99 or <= 0.01) just in case
            if (p0 >= 0.99 and p1 <= 0.01) or (p0 <= 0.01 and p1 >= 0.99):
                return True, [p0, p1]

    except Exception as e:
        log_error(f"Error fetching resolution for {slug}: {e}")

    return False, None


def _audit_settlements():
    """Audit settled trades against actual closed positions on Polymarket"""
    try:
        addr = Account.from_key(PROXY_PK).address
        closed_positions = get_closed_positions(addr, limit=20)

        if not closed_positions:
            return

        with db_connection() as conn:
            c = conn.cursor()
            for pos in closed_positions:
                token_id = pos.get("assetId")
                if not token_id or not isinstance(token_id, str):
                    continue

                pnl = float(pos.get("pnl", 0))

                # Look for matching position in DB that was recently settled
                from src.data.normalized_db import get_last_settled_position_for_token

                position = get_last_settled_position_for_token(c, token_id)
                if position:
                    position_id = position["id"]
                    symbol = position["symbol"]
                    db_pnl = position["pnl_usd"]
                    diff = abs(db_pnl - pnl)
                    if diff > 0.1:  # More than 10 cents difference
                        log(
                            f"🔍 Audit [{symbol}] #{position_id}: DB PnL ${db_pnl:.2f} vs API PnL ${pnl:.2f} (Diff: ${diff:.2f})"
                        )
    except Exception as e:
        log_error(f"Settlement audit error: {e}")


def force_settle_trade(trade_id: int):
    """Force settle a specific position regardless of window_end"""
    from src.data.normalized_db import (
        get_position_with_window,
        settle_position,
        update_position_redeem_tx,
        update_window_outcome,
    )

    with db_connection() as conn:
        c = conn.cursor()

        # Get position with window data
        position = get_position_with_window(c, trade_id)
        if not position:
            return

        now = datetime.now(tz=ZoneInfo("UTC"))
        position_id = position["id"]
        symbol = position["symbol"]
        slug = position["slug"]
        token_id = position["token_id"]
        side = position["side"]
        entry_price = position["entry_price"]
        size = position["size"]
        bet_usd = position["bet_usd"]

        try:
            # 1. Get resolution from API
            is_resolved, prices = get_market_resolution(slug)
            if not is_resolved:
                return

            # 2. Identify which token we hold
            r = requests.get(f"{GAMMA_API_BASE}/markets/slug/{slug}", timeout=5)
            data = r.json()
            clob_ids = data.get("clobTokenIds") or data.get("clob_token_ids")
            if isinstance(clob_ids, str):
                try:
                    clob_ids = json.loads(clob_ids)
                except:
                    pass

            final_price = 0.0
            if prices and clob_ids and len(clob_ids) >= 2:
                final_price = (
                    float(prices[0])
                    if str(token_id) == str(clob_ids[0])
                    else float(prices[1])
                )
            else:
                return

            # Cancel any open orders
            c.execute(
                "SELECT order_id FROM orders WHERE position_id = ? AND order_status IN ('OPEN', 'PENDING')",
                (position_id,),
            )
            open_orders = c.fetchall()
            for order_row in open_orders:
                order_id = order_row[0]
                if order_id:
                    cancel_order(order_id)
                    c.execute(
                        "UPDATE orders SET order_status = 'CANCELLED', cancelled_at = ? WHERE order_id = ?",
                        (now.isoformat(), order_id),
                    )

            pnl_usd = (final_price * size) - bet_usd
            roi_pct = (pnl_usd / bet_usd) * 100 if bet_usd > 0 else 0

            # Attempt redemption for force settle too
            condition_id = position.get("condition_id")
            merge_tx_hash = position.get("merge_tx_hash")
            exited_early = position.get("exited_early")

            redeem_tx_hash = None
            should_redeem = (
                not merge_tx_hash
                and not exited_early
                and condition_id
                and isinstance(condition_id, str)
            )

            if should_redeem:
                try:
                    from src.trading.ctf_operations import redeem_winning_tokens

                    log(f"   🎫 [{symbol}] #{position_id} Redeeming tokens...")
                    # Type safety: condition_id is checked in should_redeem
                    redeem_tx_hash = redeem_winning_tokens(
                        position_id, symbol, str(condition_id)
                    )
                except Exception as e:
                    log_error(f"[{symbol}] #{position_id} Error redeeming tokens: {e}")

            # Settle the position
            settle_position(
                c,
                position_id,
                exit_price=final_price,
                pnl_usd=pnl_usd,
                roi_pct=roi_pct,
                exited_early=False,
                redeem_tx_hash=redeem_tx_hash,
            )

            # Update window outcome
            update_window_outcome(c, position["window_id"], "FORCE_SETTLED")

            log(
                f"✅ Force settled zombie position [{symbol}] #{position_id}: {pnl_usd:+.2f}$"
            )
        except Exception as e:
            log_error(f"Error force settling trade #{trade_id}: {e}")


def check_and_settle_trades():
    """Check and settle completed positions using definitive API resolution"""
    from src.data.normalized_db import (
        get_expired_positions,
        get_position_with_window,
        settle_position,
        update_window_outcome,
        update_position_redeem_tx,
        count_unsettled_positions_for_window,
        get_positions_for_window_settlement,
    )

    with db_connection() as conn:
        c = conn.cursor()
        now = datetime.now(tz=ZoneInfo("UTC"))

        # Only check positions where window has ended
        unsettled = get_expired_positions(c, now.isoformat())

        if not unsettled:
            return

        total_pnl = 0
        settled_count = 0
        logged_spacing = False
        involved_windows = set()

        for position in unsettled:
            position_id = position["id"]
            symbol = position["symbol"]
            slug = position["slug"]
            token_id = position["token_id"]
            side = position["side"]
            entry_price = position["entry_price"]
            size = position["size"]
            bet_usd = position["bet_usd"]
            window_start = position["window_start"]
            window_end = position["window_end"]

            try:
                # 1. Get resolution from API
                is_resolved, prices = get_market_resolution(slug)

                if not is_resolved:
                    # Market not resolved yet, skip and check next cycle
                    continue

                involved_windows.add((window_start, window_end))

                # 2. Identify which token we hold (UP or DOWN)
                # Fetch specific market data to match IDs safely
                r = requests.get(f"{GAMMA_API_BASE}/markets/slug/{slug}", timeout=5)
                data = r.json()
                clob_ids = data.get("clobTokenIds") or data.get("clob_token_ids")
                if isinstance(clob_ids, str):
                    try:
                        clob_ids = json.loads(clob_ids)
                    except:
                        # fallback parsing if simple string
                        clob_ids = [
                            x.strip().strip('"')
                            for x in clob_ids.strip("[]").split(",")
                        ]

                # Determine outcome value
                final_price = 0.0
                if prices and clob_ids and len(clob_ids) >= 2:
                    final_price = (
                        float(prices[0])
                        if str(token_id) == str(clob_ids[0])
                        else float(prices[1])
                    )
                else:
                    continue

                # NEW: Cancel any open orders for this position (market is resolved now)
                c.execute(
                    "SELECT order_id, order_type FROM orders WHERE position_id = ? AND order_status IN ('OPEN', 'PENDING')",
                    (position_id,),
                )
                open_orders = c.fetchall()
                for order_id, order_type in open_orders:
                    log(
                        f"   🧹 [{symbol}] #{position_id} Resolved: Cancelling {order_type} order {order_id[:10]}..."
                    )
                    cancel_order(order_id)
                    # Update order status in database
                    c.execute(
                        "UPDATE orders SET order_status = 'CANCELLED' WHERE order_id = ?",
                        (order_id,),
                    )

                # Determine exit value for P&L calculation
                # If position was exited early (pre-settlement or emergency sell),
                # use the recorded exit price instead of the resolution price
                exit_value = final_price  # Default to resolution price (0.00 or 1.00)

                # Get full position data with exit information
                full_position = get_position_with_window(c, position_id)

                if not full_position:
                    log_error(
                        f"[{symbol}] #{position_id} Could not retrieve full position data"
                    )
                    continue

                recorded_exit_price = full_position.get("exit_price")
                exited_early_flag = full_position.get("exited_early")
                is_hedged = full_position.get("is_hedged")
                hedge_exit_price = full_position.get("hedge_exit_price")
                hedge_exited_early = full_position.get("hedge_exited_early")

                # Get hedge price from orders table
                hedge_price = None
                c.execute(
                    "SELECT price FROM orders WHERE position_id = ? AND order_type = 'HEDGE' LIMIT 1",
                    (position_id,),
                )
                hedge_order = c.fetchone()
                if hedge_order:
                    hedge_price = hedge_order[0]

                # Use recorded exit price if available and position was exited early
                if exited_early_flag and recorded_exit_price is not None:
                    exit_value = recorded_exit_price
                    log(
                        f"   💰 [{symbol}] #{position_id} Using early exit price ${exit_value:.2f} (resolution: ${final_price:.2f})"
                    )

                # For hedged positions, calculate P&L differently
                if is_hedged and hedge_price:
                    # Hedged position P&L calculation
                    # Entry side value
                    if exited_early_flag and recorded_exit_price is not None:
                        entry_return = size * recorded_exit_price
                    else:
                        entry_return = size * final_price

                    # Hedge side value
                    if hedge_exited_early and hedge_exit_price is not None:
                        # Hedge was sold early (pre-settlement exit)
                        hedge_return = size * hedge_exit_price
                        hedge_resolution = size * (
                            1.0 - final_price
                        )  # What hedge would have been worth
                        log(
                            f"   💰 [{symbol}] #{position_id} Hedge exited early @ ${hedge_exit_price:.2f} (resolution would be: ${1.0 - final_price:.2f})"
                        )
                    else:
                        # Hedge held to resolution
                        hedge_return = size * (1.0 - final_price)

                    # Total return
                    total_return = entry_return + hedge_return

                    # Total cost
                    entry_cost = size * entry_price
                    hedge_cost = size * hedge_price
                    total_cost = entry_cost + hedge_cost

                    pnl_usd = total_return - total_cost
                    roi_pct = (pnl_usd / total_cost) * 100 if total_cost > 0 else 0

                    log(
                        f"   📊 [{symbol}] #{position_id} HEDGED P&L: Return=${total_return:.2f} (entry=${entry_return:.2f} + hedge=${hedge_return:.2f}) - Cost=${total_cost:.2f} (entry=${entry_cost:.2f} + hedge=${hedge_cost:.2f}) = ${pnl_usd:+.2f}"
                    )
                else:
                    # Unhedged position - original calculation
                    pnl_usd = (exit_value * size) - bet_usd
                    roi_pct = (pnl_usd / bet_usd) * 100 if bet_usd > 0 else 0

                # NEW: Redeem winning tokens for unmerged/unexited positions
                # Use data from full_position dict (already fetched above)
                redeem_tx_hash = None
                merge_tx = full_position.get("merge_tx_hash")
                exited_early = full_position.get("exited_early")
                condition_id = full_position.get("condition_id")

                # Only redeem if:
                # 1. Position wasn't merged (no merge_tx_hash)
                # 2. Position wasn't exited early (exited_early = 0)
                # 3. We have condition_id stored
                should_redeem = not merge_tx and not exited_early and condition_id

                if should_redeem and condition_id:  # Extra check for type safety
                    try:
                        from src.trading.ctf_operations import (
                            redeem_winning_tokens,
                        )

                        log(
                            f"   🎫 [{symbol}] #{position_id} Redeeming winning tokens..."
                        )
                        # Type safety: condition_id is checked above
                        redeem_tx_hash = redeem_winning_tokens(
                            position_id, symbol, str(condition_id)
                        )

                        if redeem_tx_hash:
                            log(
                                f"   ✅ [{symbol}] #{position_id} Redemption successful! Tx: {redeem_tx_hash[:16]}..."
                            )
                        else:
                            log(
                                f"   ⚠️  [{symbol}] #{position_id} Redemption failed, tokens remain in wallet"
                            )
                    except Exception as e:
                        log_error(
                            f"[{symbol}] #{position_id} Error redeeming tokens: {e}"
                        )

                # Update position and window with settlement data
                settle_position(
                    c,
                    position_id,
                    exit_price=final_price,
                    pnl_usd=pnl_usd,
                    roi_pct=roi_pct,
                    redeem_tx_hash=redeem_tx_hash,
                )

                # Update window outcome to RESOLVED
                window_id = full_position.get("window_id")
                if window_id:
                    update_window_outcome(c, window_id, "RESOLVED")

                if not logged_spacing:
                    log("")
                    logged_spacing = True

                emoji = "💰" if pnl_usd > 0 else "💀"
                log(
                    f"{emoji} [{symbol}] #{position_id} {side}: {pnl_usd:+.2f}$ ({roi_pct:+.1f}%)"
                )
                log(
                    f"   💸 Released ${(exit_value * size):.2f} USDC to wallet (final_price: {final_price:.4f} × {size:.1f} shares)"
                )
                total_pnl += pnl_usd
                settled_count += 1

            except Exception as e:
                log_error(f"[{symbol}] #{position_id} Error settling trade: {e}")

        if settled_count > 0:
            send_discord(
                f"📊 Settled {settled_count} trades | Total PnL: ${total_pnl:+.2f}"
            )

            # Log Window Summaries for completed windows
            for window_start, window_end in involved_windows:
                try:
                    # Check if all positions for this window are settled
                    remaining = count_unsettled_positions_for_window(c, window_start)

                    if remaining == 0:
                        # All trades for this window are now settled. Log summary.
                        window_positions = get_positions_for_window_settlement(
                            c, window_start
                        )
                        if not window_positions:
                            continue

                        # Calculate window-level stats
                        win_pnl = sum((p["pnl_usd"] or 0.0) for p in window_positions)
                        win_bet = sum((p["bet_usd"] or 0.0) for p in window_positions)
                        win_roi = (win_pnl / win_bet * 100) if win_bet > 0 else 0

                        # Format window range
                        try:
                            ws_dt = datetime.fromisoformat(window_start).astimezone(
                                ZoneInfo("America/New_York")
                            )
                            we_dt = datetime.fromisoformat(window_end).astimezone(
                                ZoneInfo("America/New_York")
                            )
                            range_str = (
                                f"{ws_dt.strftime('%H:%M')} - {we_dt.strftime('%H:%M')}"
                            )
                        except:
                            range_str = f"{window_start} - {window_end}"

                        log("=" * 60)
                        log(f"🏁 WINDOW SUMMARY: {range_str}")
                        log(f"   Total PnL: {win_pnl:+.2f}$ ({win_roi:+.1f}%)")
                        log(f"   Trades:    {len(window_positions)}")
                        for pos in window_positions:
                            sym = pos["symbol"]
                            side = pos["side"]
                            pnl = pos["pnl_usd"] or 0.0
                            roi = pos["roi_pct"] or 0.0
                            outcome = pos["final_outcome"]
                            log(
                                f"     - [{sym}] {side}: {pnl:+.2f}$ ({roi:+.1f}%) | {outcome}"
                            )
                        log("=" * 60)
                except Exception as e:
                    log_error(
                        f"Error generating window summary for {window_start}: {e}"
                    )

    # Audit after settlement
    try:
        _audit_settlements()
    except:
        pass


def redeem_recent_settled_trades():
    """
    Redeem winning tokens from recently settled trades (last 30 minutes).

    Called after check_and_settle_trades() to automatically redeem winning positions.
    Only processes trades that:
    - Are settled (settled=1)
    - Resolved favorably (final_outcome='RESOLVED')
    - Not exited early (exited_early=0)
    - Haven't been merged or redeemed yet
    - Settled in last 30 minutes

    Transaction Budget:
    - Uses BATCH redemption: 1 transaction for ALL trades (vs N individual transactions)
    - Unverified tier: 100 transactions/day
    - With 96 trades/day max (4 symbols × 4 windows/hour × 24 hours)
    - Batch redemption saves significant transaction quota
    """
    from src.trading.ctf_operations import batch_redeem_winning_tokens

    with db_connection() as conn:
        c = conn.cursor()

        # Find recently settled trades that need redemption (last 30 minutes)
        c.execute("""
            SELECT id, symbol, slug, condition_id, pnl_usd
            FROM trades 
            WHERE settled = 1 
                AND final_outcome = 'RESOLVED'
                AND exited_early = 0
                AND merge_tx_hash IS NULL
                AND redeem_tx_hash IS NULL
                AND datetime(settled_at) > datetime('now', '-30 minutes')
            ORDER BY id DESC
        """)

        trades = c.fetchall()

        if not trades:
            return

        log("")
        log(f"💰 Auto-redeeming {len(trades)} recently settled trade(s)")

        # Collect all redemptions for batch processing
        redemption_batch = []
        trade_info = {}  # Map trade_id to (symbol, pnl)
        skipped = 0
        total_value = 0.0

        # First pass: collect all valid redemptions
        for trade_id, symbol, slug, condition_id, pnl_usd in trades:
            try:
                value = pnl_usd or 0.0
                total_value += value

                # Fetch condition_id from API if not in database
                if not condition_id:
                    log(
                        f"   [{symbol}] #{trade_id} Fetching condition_id from {slug}..."
                    )
                    r = requests.get(f"{GAMMA_API_BASE}/markets/slug/{slug}", timeout=5)
                    if r.status_code == 200:
                        data = r.json()
                        condition_id = (
                            data.get("conditionId") or data.get("condition_id") or ""
                        )
                        if condition_id and condition_id != "0x" + ("0" * 64):
                            # Update database with condition_id (normalized schema)
                            from src.data.normalized_db import (
                                update_position_condition_id,
                            )

                            update_position_condition_id(c, trade_id, condition_id)

                            # Also update legacy trades table for backward compat
                            c.execute(
                                "UPDATE trades SET condition_id = ? WHERE id = ?",
                                (condition_id, trade_id),
                            )

                if not condition_id or condition_id == "0x" + ("0" * 64):
                    log(
                        f"   ⏭️  [{symbol}] #{trade_id} Skipped (no condition_id, market may be expired)"
                    )
                    skipped += 1
                    continue

                # Add to batch
                redemption_batch.append((trade_id, symbol, condition_id))
                trade_info[trade_id] = (symbol, value)

            except Exception as e:
                log_error(f"[{symbol}] #{trade_id} Error preparing redemption: {e}")
                skipped += 1
                continue

        # Execute batch redemption if we have any
        if redemption_batch:
            log(
                f"   📦 Executing batch redemption for {len(redemption_batch)} trade(s)"
            )

            redeem_tx_hash = batch_redeem_winning_tokens(redemption_batch)

            if redeem_tx_hash:
                # Update all trades in batch with the same tx hash
                from src.data.normalized_db import update_position_redeem_tx

                redeemed = 0
                for trade_id in trade_info.keys():
                    symbol, value = trade_info[trade_id]

                    # Update normalized schema
                    update_position_redeem_tx(c, trade_id, redeem_tx_hash)

                    # Also update legacy trades table for backward compat
                    c.execute(
                        "UPDATE trades SET redeem_tx_hash = ? WHERE id = ?",
                        (redeem_tx_hash, trade_id),
                    )

                    log(f"   ✅ [{symbol}] #{trade_id} Redeemed! (${value:+.2f})")
                    redeemed += 1

                log(
                    f"💰 Batch redemption complete: {redeemed} trade(s), Total: ${total_value:+.2f}"
                )
            else:
                log_error(
                    f"⚠️  Batch redemption failed for {len(redemption_batch)} trade(s)"
                )
        elif skipped > 0:
            log(f"   ⏭️  All {skipped} trade(s) skipped (no condition_id)")
