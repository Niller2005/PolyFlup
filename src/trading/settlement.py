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
                pnl = float(pos.get("pnl", 0))

                # Look for matching trade in DB that was recently settled
                c.execute(
                    "SELECT id, symbol, pnl_usd FROM trades WHERE token_id = ? AND settled = 1 ORDER BY settled_at DESC LIMIT 1",
                    (token_id,),
                )
                row = c.fetchone()
                if row:
                    trade_id, symbol, db_pnl = row
                    diff = abs(db_pnl - pnl)
                    if diff > 0.1:  # More than 10 cents difference
                        log(
                            f"🔍 Audit [{symbol}] #{trade_id}: DB PnL ${db_pnl:.2f} vs API PnL ${pnl:.2f} (Diff: ${diff:.2f})"
                        )
    except Exception as e:
        log_error(f"Settlement audit error: {e}")


def force_settle_trade(trade_id: int):
    """Force settle a specific trade regardless of window_end"""
    with db_connection() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT id, symbol, slug, token_id, side, entry_price, size, bet_usd, limit_sell_order_id, scale_in_order_id FROM trades WHERE id = ?",
            (trade_id,),
        )
        trade = c.fetchone()
        if not trade:
            return

        now = datetime.now(tz=ZoneInfo("UTC"))
        (
            trade_id,
            symbol,
            slug,
            token_id,
            side,
            entry_price,
            size,
            bet_usd,
            limit_sell_order_id,
            scale_in_order_id,
        ) = trade

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

            if limit_sell_order_id:
                cancel_order(limit_sell_order_id)

            if scale_in_order_id:
                log(
                    f"   🧹 [{symbol}] #{trade_id} Force Settling: Cancelling orphan scale-in order {scale_in_order_id[:10]}..."
                )
                cancel_order(scale_in_order_id)

            pnl_usd = (final_price * size) - bet_usd
            roi_pct = (pnl_usd / bet_usd) * 100 if bet_usd > 0 else 0

            # Attempt redemption for force settle too
            c.execute(
                "SELECT merge_tx_hash, exited_early, condition_id FROM trades WHERE id = ?",
                (trade_id,),
            )
            redemption_check = c.fetchone()

            redeem_tx_hash = None
            if redemption_check:
                merge_tx, exited_early, condition_id = redemption_check
                should_redeem = not merge_tx and not exited_early and condition_id

                if should_redeem:
                    try:
                        from src.trading.ctf_operations import redeem_winning_tokens

                        log(f"   🎫 [{symbol}] #{trade_id} Redeeming tokens...")
                        redeem_tx_hash = redeem_winning_tokens(
                            trade_id, symbol, condition_id
                        )
                    except Exception as e:
                        log_error(f"[{symbol}] #{trade_id} Error redeeming tokens: {e}")

            c.execute(
                "UPDATE trades SET final_outcome=?, exit_price=?, pnl_usd=?, roi_pct=?, settled=1, settled_at=?, scale_in_order_id=NULL, redeem_tx_hash=? WHERE id=?",
                (
                    "FORCE_SETTLED",
                    final_price,
                    pnl_usd,
                    roi_pct,
                    now.isoformat(),
                    redeem_tx_hash,
                    trade_id,
                ),
            )
            log(
                f"✅ Force settled zombie trade [{symbol}] #{trade_id}: {pnl_usd:+.2f}$"
            )
        except Exception as e:
            log_error(f"Error force settling trade #{trade_id}: {e}")


def check_and_settle_trades():
    """Check and settle completed trades using definitive API resolution"""
    with db_connection() as conn:
        c = conn.cursor()
        now = datetime.now(tz=ZoneInfo("UTC"))

        # Only check trades where window has ended
        c.execute(
            "SELECT id, symbol, slug, token_id, side, entry_price, size, bet_usd, limit_sell_order_id, scale_in_order_id, window_start, window_end FROM trades WHERE settled = 0 AND datetime(window_end) < datetime(?)",
            (now.isoformat(),),
        )
        unsettled = c.fetchall()

        if not unsettled:
            return

        total_pnl = 0
        settled_count = 0
        logged_spacing = False
        involved_windows = set()

        for (
            trade_id,
            symbol,
            slug,
            token_id,
            side,
            entry_price,
            size,
            bet_usd,
            limit_sell_order_id,
            scale_in_order_id,
            window_start,
            window_end,
        ) in unsettled:
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

                # NEW: Cancel limit sell order if it exists (market is resolved now)
                if limit_sell_order_id:
                    cancel_order(limit_sell_order_id)

                # NEW: Cancel orphan scale-in order
                if scale_in_order_id:
                    log(
                        f"   🧹 [{symbol}] #{trade_id} Resolved: Cancelling orphan scale-in order {scale_in_order_id[:10]}..."
                    )
                    cancel_order(scale_in_order_id)

                # Determine exit value for P&L calculation
                # If position was exited early (pre-settlement or emergency sell),
                # use the recorded exit price instead of the resolution price
                exit_value = final_price  # Default to resolution price (0.00 or 1.00)

                # Check if position was exited early with recorded exit price
                c.execute(
                    "SELECT exit_price, exited_early FROM trades WHERE id = ?",
                    (trade_id,),
                )
                exit_check = c.fetchone()

                if exit_check:
                    recorded_exit_price, exited_early_flag = exit_check

                    # Use recorded exit price if available and position was exited early
                    if exited_early_flag and recorded_exit_price is not None:
                        exit_value = recorded_exit_price
                        log(
                            f"   💰 [{symbol}] #{trade_id} Using early exit price ${exit_value:.2f} (resolution: ${final_price:.2f})"
                        )

                pnl_usd = (exit_value * size) - bet_usd
                roi_pct = (pnl_usd / bet_usd) * 100 if bet_usd > 0 else 0

                # NEW: Redeem winning tokens for unmerged/unexited positions
                # Check if position needs redemption (not merged, not exited early)
                c.execute(
                    "SELECT merge_tx_hash, exited_early, condition_id FROM trades WHERE id = ?",
                    (trade_id,),
                )
                redemption_check = c.fetchone()

                redeem_tx_hash = None
                if redemption_check:
                    merge_tx, exited_early, condition_id = redemption_check

                    # Only redeem if:
                    # 1. Position wasn't merged (no merge_tx_hash)
                    # 2. Position wasn't exited early (exited_early = 0)
                    # 3. We have condition_id stored
                    should_redeem = not merge_tx and not exited_early and condition_id

                    if should_redeem:
                        try:
                            from src.trading.ctf_operations import (
                                redeem_winning_tokens,
                            )

                            log(
                                f"   🎫 [{symbol}] #{trade_id} Redeeming winning tokens..."
                            )
                            redeem_tx_hash = redeem_winning_tokens(
                                trade_id, symbol, condition_id
                            )

                            if redeem_tx_hash:
                                log(
                                    f"   ✅ [{symbol}] #{trade_id} Redemption successful! Tx: {redeem_tx_hash[:16]}..."
                                )
                            else:
                                log(
                                    f"   ⚠️  [{symbol}] #{trade_id} Redemption failed, tokens remain in wallet"
                                )
                        except Exception as e:
                            log_error(
                                f"[{symbol}] #{trade_id} Error redeeming tokens: {e}"
                            )

                c.execute(
                    "UPDATE trades SET final_outcome=?, exit_price=?, pnl_usd=?, roi_pct=?, settled=1, settled_at=?, scale_in_order_id=NULL, redeem_tx_hash=? WHERE id=?",
                    (
                        "RESOLVED",
                        final_price,
                        pnl_usd,
                        roi_pct,
                        now.isoformat(),
                        redeem_tx_hash,
                        trade_id,
                    ),
                )

                if not logged_spacing:
                    log("")
                    logged_spacing = True

                emoji = "💰" if pnl_usd > 0 else "💀"
                log(
                    f"{emoji} [{symbol}] #{trade_id} {side}: {pnl_usd:+.2f}$ ({roi_pct:+.1f}%)"
                )
                log(
                    f"   💸 Released ${(exit_value * size):.2f} USDC to wallet (final_price: {final_price:.4f} × {size:.1f} shares)"
                )
                total_pnl += pnl_usd
                settled_count += 1

            except Exception as e:
                log_error(f"[{symbol}] #{trade_id} Error settling trade: {e}")

        if settled_count > 0:
            send_discord(
                f"📊 Settled {settled_count} trades | Total PnL: ${total_pnl:+.2f}"
            )

            # Log Window Summaries for completed windows
            for window_start, window_end in involved_windows:
                try:
                    c.execute(
                        "SELECT COUNT(*) FROM trades WHERE window_start = ? AND settled = 0",
                        (window_start,),
                    )
                    remaining = c.fetchone()[0]
                    if remaining == 0:
                        # All trades for this window are now settled. Log summary.
                        c.execute(
                            "SELECT symbol, side, pnl_usd, roi_pct, final_outcome, bet_usd FROM trades WHERE window_start = ?",
                            (window_start,),
                        )
                        window_trades = c.fetchall()
                        if not window_trades:
                            continue

                        win_pnl = sum((t[2] or 0.0) for t in window_trades)
                        win_bet = sum((t[5] or 0.0) for t in window_trades)
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
                        log(f"   Trades:    {len(window_trades)}")
                        for sym, side, pnl, roi, outcome, bet in window_trades:
                            pnl = pnl or 0.0
                            roi = roi or 0.0
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
                            # Update database with condition_id
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
                redeemed = 0
                for trade_id in trade_info.keys():
                    symbol, value = trade_info[trade_id]
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
