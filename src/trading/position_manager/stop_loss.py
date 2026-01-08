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
    sell_position,
    cancel_order,
    get_balance_allowance,
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
    # We use the $0.30 floor, but ensure at least $0.10 headspace for low-priced entries
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

    # HEDGED STOP LOSS LOGIC (Triple Check)
    # 1. Immediate Price Floor
    if current_price <= 0.15:
        log(
            f"🛑 [{symbol}] #{trade_id} CRITICAL FLOOR hit (${current_price:.2f}). Executing immediate stop loss."
        )
    else:
        # 2. Time Check (Cooldown)
        try:
            if not reversal_triggered_at:
                # Should not happen if triggered is True, but for safety
                seconds_since_rev = 0
            else:
                rev_time = datetime.fromisoformat(reversal_triggered_at)
                seconds_since_rev = (now - rev_time).total_seconds()
        except:
            seconds_since_rev = 0

        if seconds_since_rev < 120:
            # Only log every 60s to avoid spam
            if int(seconds_since_rev) % 60 == 0:
                log(
                    f"⏳ [{symbol}] #{trade_id} HEDGE COOLDOWN: {seconds_since_rev:.0f}s/120s passed. Holding..."
                )
            return False

        # 3. Strategy Confirmation
        # Check if the strategy now favors the opposite side with decent confidence
        try:
            client = get_clob_client()
            up_id, _ = get_token_ids(symbol)
            if not up_id:
                return False

            conf, bias, _, _, _, _ = calculate_confidence(symbol, up_id, client)

            target_bias = "DOWN" if side == "UP" else "UP"
            if bias == target_bias and conf > 0.30:
                log(
                    f"🛡️  [{symbol}] #{trade_id} HEDGE CONFIRMED: Strategy favors {bias} @ {conf:.1%}. Clearing losing side."
                )
            else:
                # Still holding hedge
                if int(seconds_since_rev) % 60 == 0:
                    log(
                        f"🛡️  [{symbol}] #{trade_id} HEDGE ACTIVE: Waiting for strategy flip (Current: {bias} @ {conf:.1%})"
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
        balance_info = get_balance_allowance(token_id)
        actual_balance = balance_info.get("balance", 0) if balance_info else 0
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
            time.sleep(1)  # Short wait for exchange to unlock tokens

    if scale_in_order_id:
        log(
            f"   Sweep [{symbol}] #{trade_id} Stop Loss: Cancelling pending scale-in order {scale_in_order_id[:10]}..."
        )
        cancel_order(scale_in_order_id)

    sell_result = sell_position(token_id, size, current_price)
    if not sell_result["success"]:
        err = sell_result.get("error", "").lower()
        if "balance" in err or "allowance" in err:
            balance_info = get_balance_allowance(token_id)
            actual_balance = balance_info.get("balance", 0) if balance_info else 0
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
