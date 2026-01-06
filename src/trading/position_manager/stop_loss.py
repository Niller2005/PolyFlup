"""Stop loss and reversal logic"""

import time
from datetime import datetime
from src.config.settings import (
    ENABLE_STOP_LOSS,
    STOP_LOSS_PERCENT,
    STOP_LOSS_PRICE,
    ENABLE_REVERSAL,
)
from src.utils.logger import log, send_discord
from src.trading.orders import (
    sell_position,
    cancel_order,
    get_balance_allowance,
    place_order,
    get_order_status,
)
from src.data.market_data import (
    get_current_spot_price,
    get_window_times,
    get_current_slug,
    get_token_ids,
)
from src.data.database import save_trade


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
):
    """
    Check and execute stop loss based on midpoint price.
    Hedged Reversal Note: opening of opposite position is now handled in bot.py.
    This function focuses on clearing losing positions.
    """
    c.execute("SELECT settled FROM trades WHERE id = ?", (trade_id,))
    if (row := c.fetchone()) and row[0] == 1:
        return True

    # STOP LOSS TRIGGER: Dynamic Headroom
    # We use the $0.30 floor, but ensure at least $0.10 headspace for low-priced entries
    dynamic_trigger = min(STOP_LOSS_PRICE, entry_price - 0.10)

    if not ENABLE_STOP_LOSS or current_price > dynamic_trigger or size == 0:
        return False

    if buy_order_status not in ["FILLED", "MATCHED"]:
        return False

    c.execute("SELECT timestamp FROM trades WHERE id = ?", (trade_id,))
    if row := c.fetchone():
        try:
            # Positions must be at least 30s old to avoid noise
            if (now - datetime.fromisoformat(row[0])).total_seconds() < 30:
                return False
        except:
            pass

    current_spot = get_current_spot_price(symbol)

    # Secondary Safety: Check if we are winning on spot even if midpoint is low
    is_on_losing_side = True
    if current_price >= 0.50:
        is_on_losing_side = False

    if is_on_losing_side and current_spot > 0 and target_price:
        if side == "UP" and current_spot >= target_price:
            is_on_losing_side = False
            log(
                f"ℹ️ [{symbol}] Midpoint is weak ({current_price:.2f}) but Spot is ABOVE target - HOLDING"
            )
        elif side == "DOWN" and current_spot <= target_price:
            is_on_losing_side = False
            log(
                f"ℹ️ [{symbol}] Midpoint is weak ({current_price:.2f}) but Spot is BELOW target - HOLDING"
            )

    if not is_on_losing_side:
        return False

    outcome = "STOP_LOSS"
    if is_reversal:
        outcome = "REVERSAL_STOP_LOSS"

    # Robust size check: fetch actual balance to ensure we sell everything
    # This prevents "Insufficient funds" loops if size is out of sync
    try:
        balance_info = get_balance_allowance(token_id)
        actual_balance = balance_info.get("balance", 0) if balance_info else 0
        if actual_balance < 0.1:
            log(
                f"   ⚠️ [{symbol}] #{trade_id} Stop Loss: Balance is 0 or near 0. Settling as ghost trade."
            )
            c.execute(
                "UPDATE trades SET settled=1, final_outcome='STOP_LOSS_GHOST_FILL', scale_in_order_id=NULL WHERE id=?",
                (trade_id,),
            )
            return True

        if abs(actual_balance - size) > 0.01:
            log(
                f"   📊 [{symbol}] #{trade_id} Sync: Database size {size:.2f} != actual balance {actual_balance:.2f} - Updating sell size."
            )
            size = actual_balance
            c.execute(
                "UPDATE trades SET size = ? WHERE id = ?",
                (size, trade_id),
            )
    except Exception as e:
        log(f"   ⚠️ [{symbol}] Could not verify balance before sell: {e}")

    log(
        f"🛑 [{symbol}] #{trade_id} {outcome}: Midpoint ${current_price:.2f} <= ${dynamic_trigger:.2f} trigger"
    )

    # CANCEL ANY PENDING ORDERS
    if limit_sell_order_id:
        l_status = get_order_status(limit_sell_order_id)
        if l_status in ["FILLED", "MATCHED"]:
            log(
                f"   ℹ️ [{symbol}] #{trade_id} Stop loss skipped: Exit plan already filled."
            )
            c.execute(
                "UPDATE trades SET order_status = 'EXIT_PLAN_FILLED', settled=1, exited_early=1 WHERE id=?",
                (trade_id,),
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
                "UPDATE trades SET settled=1, final_outcome='UNFILLED_NO_BALANCE', scale_in_order_id=NULL WHERE id=?",
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
