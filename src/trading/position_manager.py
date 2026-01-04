"""Position monitoring and management"""

import sqlite3
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from src.config.settings import (
    DB_FILE,
    ENABLE_STOP_LOSS,
    STOP_LOSS_PERCENT,
    ENABLE_TAKE_PROFIT,
    TAKE_PROFIT_PERCENT,
    ENABLE_REVERSAL,
    ENABLE_SCALE_IN,
    SCALE_IN_MIN_PRICE,
    SCALE_IN_MAX_PRICE,
    SCALE_IN_TIME_LEFT,
    SCALE_IN_MULTIPLIER,
    CANCEL_UNFILLED_ORDERS,
    UNFILLED_CANCEL_THRESHOLD,
)
from src.utils.logger import log, send_discord
from src.trading.orders import (
    get_clob_client,
    sell_position,
    place_order,
    place_limit_order,
    cancel_order,
    get_order_status,
    SELL,
)
from src.data.market_data import (
    get_token_ids,
    get_current_slug,
    get_window_times,
    get_funding_bias,
    get_current_spot_price,
)
from src.data.database import save_trade


def recover_open_positions():
    """
    Recover and log open positions from database on bot startup
    This ensures positions are monitored after a restart
    """
    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    c = conn.cursor()
    now = datetime.now(tz=ZoneInfo("UTC"))

    # Get all unsettled trades that are still in their window
    c.execute(
        """SELECT id, symbol, side, entry_price, size, bet_usd, window_end, order_status
           FROM trades 
           WHERE settled = 0 
           AND exited_early = 0
           AND datetime(window_end) > datetime(?)""",
        (now.isoformat(),),
    )
    open_positions = c.fetchall()
    conn.close()

    if not open_positions:
        log("✓ No open positions to recover")
        return

    log("=" * 90)
    log(f"🔄 RECOVERING {len(open_positions)} OPEN POSITIONS FROM DATABASE")
    log("=" * 90)

    for (
        trade_id,
        symbol,
        side,
        entry_price,
        size,
        bet_usd,
        window_end,
        order_status,
    ) in open_positions:
        window_end_dt = datetime.fromisoformat(window_end)
        time_left = (window_end_dt - now).total_seconds() / 60.0  # minutes

        log(
            f"  [{symbol}] Trade #{trade_id} {side}: ${bet_usd:.2f} @ ${entry_price:.4f} | Status: {order_status} | {time_left:.0f}m left"
        )

    log("=" * 90)
    log(f"✓ Position monitoring ACTIVE for {len(open_positions)} positions")
    log("=" * 90)


def check_open_positions(verbose: bool = True, check_orders: bool = False):
    """
    Check open positions and manage them

    Args:
        verbose: If True, log position checks. If False, only log actions (stop loss, etc.)
        check_orders: If True, check status of limit sell orders
    """
    if not any(
        [ENABLE_STOP_LOSS, ENABLE_TAKE_PROFIT, ENABLE_SCALE_IN, ENABLE_REVERSAL]
    ):
        if not check_orders:
            return

    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    c = conn.cursor()
    now = datetime.now(tz=ZoneInfo("UTC"))

    # Get unsettled trades that are still in their window
    c.execute(
        """SELECT id, symbol, slug, token_id, side, entry_price, size, bet_usd, window_end, scaled_in, is_reversal, target_price, limit_sell_order_id, order_id, order_status
           FROM trades 
           WHERE settled = 0 
           AND exited_early = 0
           AND datetime(window_end) > datetime(?)""",
        (now.isoformat(),),
    )
    open_positions = c.fetchall()

    if not open_positions:
        conn.close()
        return

    if verbose:
        log(f"🔍 Checking {len(open_positions)} open positions...")

    client = get_clob_client()

    for (
        trade_id,
        symbol,
        slug,
        token_id,
        side,
        entry_price,
        size,
        bet_usd,
        window_end,
        scaled_in,
        is_reversal,
        target_price,
        limit_sell_order_id,
        buy_order_id,
        buy_order_status,
    ) in open_positions:
        try:
            # 0. Check buy order status if not filled
            current_buy_status = buy_order_status
            if buy_order_status != "FILLED" and buy_order_id:
                current_buy_status = get_order_status(buy_order_id)
                if current_buy_status == "FILLED":
                    log(f"✅ BUY order for trade #{trade_id} has been FILLED")
                    c.execute(
                        "UPDATE trades SET order_status = 'FILLED' WHERE id = ?",
                        (trade_id,),
                    )
                    conn.commit()
                elif current_buy_status in ["CANCELED", "EXPIRED"]:
                    log(
                        f"⚠️ BUY order for trade #{trade_id} was {current_buy_status}. Settling trade."
                    )
                    c.execute(
                        "UPDATE trades SET settled = 1, final_outcome = ? WHERE id = ?",
                        (current_buy_status, trade_id),
                    )
                    conn.commit()
                    continue

            # Note: Limit sell order placement moved to 60-second mark (after price checks below)

            # 2. Check if limit sell order was filled
            if check_orders and limit_sell_order_id:
                try:
                    order_data = client.get_order(limit_sell_order_id)
                    # Handle both dict and object response
                    if isinstance(order_data, dict):
                        status = order_data.get("status")
                    else:
                        status = getattr(order_data, "status", None)

                    if status == "FILLED":
                        log(
                            f"🎯 Limit sell order filled for trade #{trade_id} at 0.99!"
                        )
                        exit_price = 0.99
                        pnl_usd = (exit_price * size) - bet_usd
                        roi_pct = (pnl_usd / bet_usd) * 100 if bet_usd > 0 else 0

                        c.execute(
                            """UPDATE trades 
                               SET settled=1, exited_early=1, exit_price=?, 
                                   pnl_usd=?, roi_pct=?, 
                                   final_outcome='LIMIT_SELL_099', settled_at=? 
                               WHERE id=?""",
                            (exit_price, pnl_usd, roi_pct, now.isoformat(), trade_id),
                        )
                        conn.commit()
                        send_discord(
                            f"🎯 **LIMIT SELL FILLED** [{symbol}] {side} closed at 0.99"
                        )
                        continue
                except Exception as e:
                    # Order might be too old or other error, log if significant
                    if "404" not in str(e):
                        log(f"⚠️ Error checking order {limit_sell_order_id}: {e}")

            # Get current market price
            book = client.get_order_book(token_id)
            if isinstance(book, dict):
                bids = book.get("bids", []) or []
                asks = book.get("asks", []) or []
            else:
                bids = getattr(book, "bids", []) or []
                asks = getattr(book, "asks", []) or []

            if not bids or not asks:
                continue

            best_bid = float(
                bids[-1].price
                if hasattr(bids[-1], "price")
                else bids[-1].get("price", 0)
            )
            best_ask = float(
                asks[-1].price
                if hasattr(asks[-1], "price")
                else asks[-1].get("price", 0)
            )
            current_price = (best_bid + best_ask) / 2.0

            # Calculate current P&L
            # Both UP and DOWN tokens increase in value when the prediction is moving in our favor
            price_change_pct = ((current_price - entry_price) / entry_price) * 100

            current_value = current_price * size
            pnl_usd = current_value - bet_usd
            pnl_pct = (pnl_usd / bet_usd) * 100 if bet_usd > 0 else 0

            if verbose:
                log(
                    f"  [{symbol}] Trade #{trade_id} {side}: Entry=${entry_price:.4f} Current=${current_price:.4f} PnL={price_change_pct:+.1f}%"
                )

            # ============================================================
            # PRIORITY #1: STOP LOSS (checked FIRST, before everything else)
            # ============================================================
            # Stop loss is highest priority - check it regardless of order status
            # If we can calculate PnL, we have a position that needs protection
            stop_threshold = -STOP_LOSS_PERCENT
            sl_label = "STOP LOSS"

            # Smart Breakeven Protection (only if position was profitable)
            if ENABLE_STOP_LOSS and pnl_pct >= 20.0:
                # Only activate breakeven if market is moving AGAINST our position
                is_moving_against = False
                if side == "UP" and current_price < entry_price:
                    is_moving_against = True
                elif side == "DOWN" and current_price > entry_price:
                    is_moving_against = True

                if is_moving_against:
                    stop_threshold = (
                        -5.0
                    )  # Allow 5% drawback from peak before breakeven exit
                    sl_label = "BREAKEVEN PROTECTION"

            # CRITICAL: Stop loss works regardless of order_status
            # If size > 0, we have a position that needs stop loss protection
            if ENABLE_STOP_LOSS and pnl_pct <= stop_threshold and size > 0:
                # NEW REQUIREMENT: Stop loss ONLY triggers if we are on the 'opposite' (losing) side of target
                # Get current spot price to confirm we are losing
                current_spot = get_current_spot_price(symbol)
                is_on_losing_side = False

                if current_spot > 0 and target_price is not None:
                    if side == "UP" and current_spot < target_price:
                        is_on_losing_side = True
                    elif side == "DOWN" and current_spot > target_price:
                        is_on_losing_side = True
                else:
                    # Fallback if target price or spot is missing: assume losing if pnl is bad
                    is_on_losing_side = True

                if is_on_losing_side:
                    log(
                        f"🛑 {sl_label} trade #{trade_id}: {pnl_pct:.1f}% PnL (Threshold: {stop_threshold}%) | Spot ${current_spot:,.2f} vs Target ${target_price:,.2f}"
                    )

                    # Cancel existing limit sell order if it exists
                    if limit_sell_order_id:
                        if cancel_order(limit_sell_order_id):
                            log(
                                f"[{symbol}] ⏳ Limit sell order cancelled, waiting for tokens to be freed..."
                            )
                            time.sleep(2)

                    # Sell current position
                    sell_result = sell_position(token_id, size, current_price)

                    if sell_result["success"]:
                        # Mark as exited early
                        c.execute(
                            """UPDATE trades 
                               SET exited_early=1, exit_price=?, pnl_usd=?, roi_pct=?, 
                                   final_outcome=?, settled=1, settled_at=? 
                               WHERE id=?""",
                            (
                                current_price,
                                pnl_usd,
                                pnl_pct,
                                sl_label,
                                now.isoformat(),
                                trade_id,
                            ),
                        )
                        conn.commit()

                        send_discord(
                            f"🛑 **STOP LOSS** [{symbol}] {side} closed at {pnl_pct:+.1f}%"
                        )

                        # ALWAYS trigger reversal to the 'winning' side
                        if not is_reversal:  # Don't reverse a reversal
                            opposite_side = "DOWN" if side == "UP" else "UP"
                            log(
                                f"🔄 Reversing [{symbol}] {side} → {opposite_side} to get on winning side"
                            )

                            up_id, down_id = get_token_ids(symbol)
                            if up_id and down_id:
                                opposite_token = down_id if side == "UP" else up_id
                                opposite_price = 1.0 - current_price

                                # Place reverse order with same size
                                reverse_result = place_order(
                                    opposite_token, opposite_price, size
                                )
                                if reverse_result["success"]:
                                    send_discord(
                                        f"🔄 **REVERSED** [{symbol}] {side} → {opposite_side} (Target: ${target_price:,.2f}, Spot: ${current_spot:,.2f})"
                                    )

                                    # Save reversed trade to database
                                    try:
                                        window_start, window_end = get_window_times(
                                            symbol
                                        )
                                        bet_usd_effective = size * opposite_price
                                        save_trade(
                                            symbol=symbol,
                                            window_start=window_start.isoformat(),
                                            window_end=window_end.isoformat(),
                                            slug=get_current_slug(symbol),
                                            token_id=opposite_token,
                                            side=opposite_side,
                                            edge=0.0,
                                            price=opposite_price,
                                            size=size,
                                            bet_usd=bet_usd_effective,
                                            p_yes=opposite_price
                                            if opposite_side == "UP"
                                            else 1.0 - opposite_price,
                                            order_status=reverse_result["status"],
                                            order_id=reverse_result["order_id"],
                                            is_reversal=True,
                                            target_price=target_price,
                                        )
                                    except Exception as e:
                                        log(f"⚠️ DB Error (reversal): {e}")
                else:
                    log(
                        f"ℹ️ [{symbol}] PnL is bad ({pnl_pct:.1f}%) but price is on WINNING side of target - HOLDING (Spot ${current_spot:,.2f} vs Target ${target_price:,.2f})"
                    )

            # ============================================================
            # PRIORITY #2: Other Risk Management (after stop loss)
            # ============================================================

            # Calculate time left until window ends
            if isinstance(window_end, str):
                window_end_dt = datetime.fromisoformat(window_end)
            else:
                window_end_dt = window_end  # Fallback if already a datetime object

            time_left_seconds = (window_end_dt - now).total_seconds()

            # Place 0.99 limit sell order when 1 minute left (after scale-in/reversals complete)
            if (
                current_buy_status == "FILLED"
                and not limit_sell_order_id
                and time_left_seconds <= 60
                and time_left_seconds > 0
            ):
                log(
                    f"[{symbol}] 📉 Placing limit sell order at 0.99 for {size} units ({time_left_seconds:.0f}s left)"
                )
                sell_limit_result = place_limit_order(
                    token_id, 0.99, size, SELL, silent_on_balance_error=True
                )
                if sell_limit_result["success"]:
                    limit_sell_order_id = sell_limit_result["order_id"]
                    log(
                        f"[{symbol}] ✅ Limit sell order placed at 0.99: {limit_sell_order_id}"
                    )
                    c.execute(
                        "UPDATE trades SET limit_sell_order_id = ? WHERE id = ?",
                        (limit_sell_order_id, trade_id),
                    )
                    conn.commit()
                else:
                    log(
                        f"[{symbol}] ⚠️ Failed to place limit sell at 0.99 (will retry next cycle)"
                    )

            # Check if we should scale into position
            if (
                ENABLE_SCALE_IN
                and not scaled_in
                and time_left_seconds <= SCALE_IN_TIME_LEFT
                and time_left_seconds > 0
                and SCALE_IN_MIN_PRICE <= current_price <= SCALE_IN_MAX_PRICE
            ):
                log(
                    f"📈 SCALE IN triggered for trade #{trade_id}: price=${current_price:.2f}, {time_left_seconds:.0f}s left"
                )

                # Calculate additional size
                additional_size = size * SCALE_IN_MULTIPLIER
                additional_bet = additional_size * current_price

                # Place additional order
                scale_result = place_order(token_id, current_price, additional_size)

                if scale_result["success"]:
                    # Update trade with new size and bet
                    new_total_size = size + additional_size
                    new_total_bet = bet_usd + additional_bet
                    new_avg_price = new_total_bet / new_total_size

                    # Update database with scaled-in position (don't place limit sell yet - will happen at 60s)
                    c.execute(
                        """UPDATE trades 
                           SET size=?, bet_usd=?, entry_price=?, scaled_in=1 
                           WHERE id=?""",
                        (
                            new_total_size,
                            new_total_bet,
                            new_avg_price,
                            trade_id,
                        ),
                    )

                    log(
                        f"✅ Scaled in! Size: {size:.2f} → {new_total_size:.2f} (+{additional_size:.2f})"
                    )
                    log(f"   Avg price: ${entry_price:.4f} → ${new_avg_price:.4f}")
                    send_discord(
                        f"📈 **SCALED IN** [{symbol}] {side} +${additional_bet:.2f} @ ${current_price:.2f} ({time_left_seconds:.0f}s left)"
                    )

            # Handle unfilled order cancellation (separate from stop loss)
            if (
                CANCEL_UNFILLED_ORDERS
                and current_buy_status != "FILLED"
                and price_change_pct <= -UNFILLED_CANCEL_THRESHOLD
            ):
                log(
                    f"🛑 Price moved away from unfilled bid for trade #{trade_id} ({price_change_pct:.1f}% < -{UNFILLED_CANCEL_THRESHOLD}%). CANCELLING BUY ORDER."
                )
                cancel_result = cancel_order(buy_order_id)

                # Always settle the trade, regardless of cancellation success
                # The order may already be cancelled, expired, or not found
                c.execute(
                    "UPDATE trades SET settled = 1, final_outcome = 'CANCELLED_UNFILLED' WHERE id = ?",
                    (trade_id,),
                )
                conn.commit()

                if cancel_result:
                    log(f"✅ Buy order for trade #{trade_id} cancelled successfully")
                else:
                    log(
                        f"⚠️ Buy order for trade #{trade_id} may already be cancelled or not found"
                    )
                continue

            # Check take profit
            if ENABLE_TAKE_PROFIT and pnl_pct >= TAKE_PROFIT_PERCENT:
                log(
                    f"🎯 TAKE PROFIT triggered for trade #{trade_id}: {pnl_pct:.1f}% gain"
                )

                # Cancel existing limit sell order if it exists
                if limit_sell_order_id:
                    if cancel_order(limit_sell_order_id):
                        log(
                            f"[{symbol}] ⏳ Limit sell order cancelled, waiting for tokens to be freed..."
                        )
                        time.sleep(2)  # Wait for exchange to free up tokens

                # Sell current position
                sell_result = sell_position(token_id, size, current_price)

                if sell_result["success"]:
                    c.execute(
                        """UPDATE trades 
                           SET exited_early=1, exit_price=?, pnl_usd=?, roi_pct=?, 
                           final_outcome='TAKE_PROFIT', settled=1, settled_at=? 
                           WHERE id=?""",
                        (current_price, pnl_usd, pnl_pct, now.isoformat(), trade_id),
                    )
                    # Commit immediately after take profit update
                    conn.commit()

                    send_discord(
                        f"🎯 **TAKE PROFIT** [{symbol}] {side} closed at {pnl_pct:+.1f}%"
                    )

        except Exception as e:
            log(f"⚠️ Error checking position #{trade_id}: {e}")

    conn.commit()
    conn.close()
