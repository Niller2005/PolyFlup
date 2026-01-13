"""Stop loss logic"""

import time
from datetime import datetime
from zoneinfo import ZoneInfo
from src.config.settings import (
    ENABLE_STOP_LOSS,
    STOP_LOSS_PERCENT,
    STOP_LOSS_PRICE,
    ENABLE_HEDGED_REVERSAL,
)
from src.utils.logger import log, log_error, send_discord
from src.trading.orders import (
    get_enhanced_balance_allowance,
    sell_position,
    cancel_order,
    cancel_market_orders,
    get_order_status,
    get_clob_client,
)
from src.data.market_data import (
    get_current_spot_price,
    get_token_ids,
)
from src.trading import calculate_confidence
from .reversal import check_and_trigger_reversal


def _check_stop_loss(
    user_address,
    symbol,
    trade_id,
    token_id,
    side,
    entry_price,
    size,
    pnl_pct,
    pnl_usd,
    current_price,
    target_price,
    limit_sell_order_id,
    is_reversal,
    c,
    conn,
    now,
    buy_order_status,
    scale_in_order_id=None,
    reversal_triggered=False,
    reversal_triggered_at=None,
    window_end_str=None,
):
    """
    Check and execute stop loss.
    Reversal is handled by position_manager.check_and_trigger_reversal.
    """

    c.execute("SELECT settled FROM trades WHERE id = ?", (trade_id,))
    if (row := c.fetchone()) and row[0] == 1:
        return True

    # Early exit: Do not check stop loss for unfilled orders
    if buy_order_status not in ["FILLED", "MATCHED"]:
        return False

    # STOP LOSS / REVERSAL TRIGGER: Dynamic Headroom
    # We use a $0.30 floor, but ensure at least $0.10 headspace for low-priced entries
    dynamic_trigger = min(STOP_LOSS_PRICE, entry_price - 0.10)

    # If price is above trigger, no action needed
    if current_price > dynamic_trigger or size == 0:
        return False

    # TRIGGER REVERSAL FIRST (via position_manager)
    if not reversal_triggered:
        reversed_triggered = check_and_trigger_reversal(
            symbol,
            trade_id,
            side,
            current_price,
            entry_price,
            c,
            conn,
            now,
            reversal_triggered,
        )
        if reversed_triggered:
            return False  # Don't stop loss in the same cycle as reversal trigger

    # HEDGED STOP LOSS LOGIC (Enhanced Multi-Check)
    # First, calculate time since reversal for all hedge checks
    try:
        if not reversal_triggered_at:
            seconds_since_rev = 0
        else:
            rev_time = datetime.fromisoformat(reversal_triggered_at)
            seconds_since_rev = (now - rev_time).total_seconds()
    except:
        seconds_since_rev = 0

    # Calculate window end and time left
    window_end = datetime.fromisoformat(window_end_str) if window_end_str else None
    time_left = (window_end - now).total_seconds() if window_end else 900

    # 0. Hedge Size Balance Check (if hedge is much larger than original)
    if reversal_triggered and seconds_since_rev > 10:
        try:
            # Query opposite side position for this symbol and window
            c.execute(
                "SELECT size, side FROM trades WHERE symbol = ? AND id != ? AND settled = 0 AND window_end = ?",
                (symbol, trade_id, window_end_str),
            )
            hedge_positions = c.fetchall()

            for hedge_size, hedge_side in hedge_positions:
                # Check if this is an opposite side hedge
                opposite_side = "DOWN" if side == "UP" else "UP"
                if hedge_side == opposite_side:
                    hedge_ratio = hedge_size / size if size > 0 else 1.0
                    if hedge_ratio > 1.2:
                        log(
                            f"⚠️  [{symbol}] #{trade_id} HEDGE OVERSIZED: Hedge {hedge_size:.1f} ({hedge_ratio:.1f}x) vs original {size:.1f}. Clearing original to reduce waste."
                        )
        except Exception as e:
            pass  # Non-critical check, don't fail if it errors

    # 1. Immediate Price Floor
    if current_price <= 0.15:
        log(
            f"🛑 [{symbol}] #{trade_id} CRITICAL FLOOR hit (${current_price:.2f}). Executing immediate stop loss."
        )
    else:
        # 2. PnL-Based Early Exit (if already deep in loss)
        if pnl_pct < -40:
            log(
                f"🛑 [{symbol}] #{trade_id} HEDGE PNL EXIT: Original position down {pnl_pct:.1f}%. Cutting losses."
            )
        else:
            # 3. Time Check (Dynamic Cooldown)
            # Dynamic cooldown: max 10% of remaining window time, capped at 120s
            dynamic_cooldown = min(120, time_left * 0.10)

            if seconds_since_rev < dynamic_cooldown:
                if int(seconds_since_rev) % 60 == 0:
                    log(
                        f"⏳ [{symbol}] #{trade_id} HEDGE COOLDOWN: {seconds_since_rev:.0f}s/{dynamic_cooldown:.0f}s passed. Holding..."
                    )
                return False

            # 4. Time Pressure Exit (less than 2 minutes left)
            if time_left < 120:
                log(
                    f"🛑 [{symbol}] #{trade_id} HEDGE TIMEOUT: Only {time_left:.0f}s left. Clearing both sides."
                )
            else:
                # 5. Strategy Confirmation with Relative Confidence
                try:
                    client = get_clob_client()
                    up_id, _ = get_token_ids(symbol)
                    if not up_id:
                        return False

                    conf, bias, _, _, _, _, _ = calculate_confidence(
                        symbol, up_id, client
                    )

                    target_bias = "DOWN" if side == "UP" else "UP"

                    # Fetch original confidence from database for relative comparison
                    c.execute("SELECT confidence FROM trades WHERE id = ?", (trade_id,))
                    orig_conf_row = c.fetchone()
                    original_confidence = orig_conf_row[0] if orig_conf_row else 0.50

                    # Hedge confidence must be significantly better than original adjusted threshold
                    confidence_threshold = max(0.30, (1.0 - original_confidence * 0.50))

                    if bias == target_bias and conf > confidence_threshold:
                        log(
                            f"🛡️  [{symbol}] #{trade_id} HEDGE CONFIRMED: Strategy favors {bias} @ {conf:.1%} (threshold: {confidence_threshold:.1%}). Clearing losing side."
                        )
                    elif seconds_since_rev > 180 and conf < 0.35:
                        # Confidence improvement timeout: after 3 minutes with weak signal, cut losses
                        log(
                            f"🛑 [{symbol}] #{trade_id} HEDGE FAILED: Confidence {conf:.1%} below 35% after 180s. Clearing losing side."
                        )
                    else:
                        # Still holding hedge
                        if int(seconds_since_rev) % 60 == 0:
                            log(
                                f"🛡️  [{symbol}] #{trade_id} HEDGE ACTIVE: Waiting for strategy flip (Current: {bias} @ {conf:.1%}, threshold: {confidence_threshold:.1%})"
                            )
                        return False
                except Exception as e:
                    log_error(f"Error checking hedge confirmation for {symbol}: {e}")
                    return False

    # STOP LOSS: Only if SL is enabled
    if not ENABLE_STOP_LOSS:
        return False

    c.execute("SELECT timestamp FROM trades WHERE id = ?", (trade_id,))
    if row := c.fetchone():
        try:
            # Positions must be at least 30s old to avoid noise
            if (now - datetime.fromisoformat(row[0])).total_seconds() < 30:
                return False
        except:
            pass

    # Secondary Safety: Check if we are winning side using Polymarket outcome prices
    is_on_losing_side = True

    # Use Polymarket outcome prices for accurate winning side detection
    from src.data.market_data import get_outcome_prices

    outcome_data = get_outcome_prices(symbol)

    if outcome_data:
        # Use specific winning status for this side
        if side == "UP":
            is_on_losing_side = not outcome_data.get("up_wins", False)
        elif side == "DOWN":
            is_on_losing_side = not outcome_data.get("down_wins", False)
        else:
            # Unknown side, assume losing for safety
            is_on_losing_side = True

    if not is_on_losing_side:
        return False

    outcome = "STOP_LOSS"
    if is_reversal:
        outcome = "REVERSAL_STOP_LOSS"

    # Redundant check - already done at top of function

    # Robust size check: fetch actual balance to ensure we sell everything
    try:
        # Calculate trade age for enhanced balance validation
        c.execute("SELECT timestamp FROM trades WHERE id = ?", (trade_id,))
        trade_timestamp = None
        if row := c.fetchone():
            try:
                trade_timestamp = datetime.fromisoformat(row[0])
            except:
                pass

        trade_age_seconds = (
            (now - trade_timestamp).total_seconds() if trade_timestamp else 0
        )

        # Use enhanced balance validation for better reliability
        enhanced_balance_info = get_enhanced_balance_allowance(
            token_id, symbol, user_address, trade_age_seconds
        )
        actual_balance = enhanced_balance_info.get("balance", 0)
        if actual_balance < 0.1:
            log(
                f"   ⚠️  [{symbol}] #{trade_id} Stop Loss: Balance is 0 or near 0. Settling as ghost trade."
            )
            c.execute(
                "UPDATE trades SET settled=1, final_outcome='STOP_LOSS_GHOST_FILL', scale_in_order_id=NULL, pnl_usd=0.0, roi_pct=0.0 WHERE id=?",
                (trade_id,),
            )
            return True

        # Use a tighter threshold (0.0001) for 6-decimal precision tokens
        if abs(actual_balance - size) > 0.0001:
            log(
                f"   📊 [{symbol}] #{trade_id} Sync: Database size {size:.4f} != actual balance {actual_balance:.4f} - Updating sell size."
            )
            size = actual_balance
            c.execute(
                "UPDATE trades SET size = ? WHERE id = ?",
                (size, trade_id),
            )
    except Exception as e:
        log(f"   ⚠️  [{symbol}] Could not verify balance before sell: {e}")

    log(
        f"🛑 [{symbol}] #{trade_id} {outcome}: Midpoint ${current_price:.2f} <= ${dynamic_trigger:.2f} trigger"
    )

    # CANCEL ANY PENDING ORDERS
    if limit_sell_order_id:
        l_status = get_order_status(limit_sell_order_id)
        if l_status in ["FILLED", "MATCHED"]:
            log(
                f"   ℹ️  [{symbol}] #{trade_id} Stop loss skipped: Exit plan already filled."
            )
            c.execute(
                "UPDATE trades SET order_status = 'EXIT_PLAN_FILLED', settled=1, exited_early=1, pnl_usd=?, roi_pct=? WHERE id=?",
                (pnl_usd, pnl_pct, trade_id),
            )
            return True

        if cancel_order(limit_sell_order_id):
            log(
                f"   🔓 [{symbol}] #{trade_id} Cancelled existing exit plan to execute stop loss."
            )
            # Clear of exit order ID to prevent re-using it
            c.execute(
                "UPDATE trades SET limit_sell_order_id = NULL WHERE id = ?",
                (trade_id,),
            )
            # Use enhanced balance validation for better reliability
            c.execute("SELECT timestamp FROM trades WHERE id = ?", (trade_id,))
            trade_timestamp = None
            if row := c.fetchone():
                try:
                    trade_timestamp = datetime.fromisoformat(row[0])
                except:
                    pass

            trade_age_seconds = (
                (now - trade_timestamp).total_seconds() if trade_timestamp else 0
            )
            enhanced_balance_info = get_enhanced_balance_allowance(
                token_id, symbol, user_address, trade_age_seconds
            )
            actual_balance = enhanced_balance_info.get("balance", 0)
            # Update size with actual balance after cancellation
            size = actual_balance
    if scale_in_order_id:
        log(
            f"   Sweep [{symbol}] #{trade_id} Stop Loss: Cancelling pending scale-in order {scale_in_order_id[:10]}..."
        )
        cancel_order(scale_in_order_id)

    log(
        f"   🔓 [{symbol}] #{trade_id} Canceling ALL orders for token to ensure clean exit..."
    )
    cancel_market_orders(asset_id=token_id)

    log(
        f"   💰 [{symbol}] #{trade_id} Selling {size:.2f} shares at ${current_price:.2f}..."
    )
    sell_result = sell_position(token_id, size, current_price)
    if not sell_result["success"]:
        err = sell_result.get("error", "")
        log(f"   ❌ [{symbol}] #{trade_id} Sell failed: {err}")
        if "balance" in err.lower() or "allowance" in err.lower():
            # Use enhanced balance validation for better reliability
            c.execute("SELECT timestamp FROM trades WHERE id = ?", (trade_id,))
            trade_timestamp = None
            if row := c.fetchone():
                try:
                    trade_timestamp = datetime.fromisoformat(row[0])
                except:
                    pass

            trade_age_seconds = (
                (now - trade_timestamp).total_seconds() if trade_timestamp else 0
            )
            enhanced_balance_info = get_enhanced_balance_allowance(
                token_id, symbol, user_address, trade_age_seconds
            )
            actual_balance = enhanced_balance_info.get("balance", 0)
            if actual_balance >= 1.0:
                c.execute(
                    "UPDATE trades SET size = ? WHERE id = ?",
                    (actual_balance, trade_id),
                )
                return False
            c.execute(
                "UPDATE trades SET settled=1, final_outcome='UNFILLED_NO_BALANCE', scale_in_order_id=NULL, pnl_usd=0.0, roi_pct=0.0 WHERE id=?",
                (trade_id,),
            )
            return True
        return False

    c.execute(
        "UPDATE trades SET exited_early=1, exit_price=?, pnl_usd=?, roi_pct=?, final_outcome=?, settled=1, settled_at=?, scale_in_order_id=NULL WHERE id=?",
        (current_price, pnl_usd, pnl_pct, outcome, now.isoformat(), trade_id),
    )
    send_discord(
        f"🛑 {outcome} [{symbol}] {side} closed at midpoint ${current_price:.2f} ({pnl_pct:+.1f}%)"
    )

    return True
