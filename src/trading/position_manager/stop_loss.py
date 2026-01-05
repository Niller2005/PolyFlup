"""Stop loss and reversal logic"""

import time
from datetime import datetime
from src.config.settings import ENABLE_STOP_LOSS, STOP_LOSS_PERCENT, ENABLE_REVERSAL
from src.utils.logger import log, send_discord
from src.trading.orders import (
    sell_position,
    cancel_order,
    get_balance_allowance,
    place_order,
)
from src.data.market_data import get_current_spot_price, get_window_times, get_current_slug, get_token_ids
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
):
    """Check and execute stop loss with REVERSAL support"""
    c.execute("SELECT settled FROM trades WHERE id = ?", (trade_id,))
    if (row := c.fetchone()) and row[0] == 1:
        return True
    if not ENABLE_STOP_LOSS or pnl_pct > -STOP_LOSS_PERCENT or size == 0:
        return False
    if buy_order_status not in ["FILLED", "MATCHED"]:
        return False
    c.execute("SELECT timestamp FROM trades WHERE id = ?", (trade_id,))
    if row := c.fetchone():
        try:
            if (now - datetime.fromisoformat(row[0])).total_seconds() < 30:
                return False
        except:
            pass

    current_spot = get_current_spot_price(symbol)
    is_on_losing_side = True
    if current_spot > 0 and target_price:
        if side == "UP" and current_spot >= target_price:
            is_on_losing_side = False
        elif side == "DOWN" and current_spot <= target_price:
            is_on_losing_side = False

    if not is_on_losing_side:
        log(f"ℹ️ [{symbol}] PnL is bad ({pnl_pct:.1f}%) but on WINNING side - HOLDING")
        return False

    log(f"🛑 [{symbol}] #{trade_id} STOP LOSS: {pnl_pct:.1f}% PnL")
    if limit_sell_order_id:
        if cancel_order(limit_sell_order_id):
            time.sleep(2)

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
                "UPDATE trades SET settled=1, final_outcome='UNFILLED_NO_BALANCE' WHERE id=?",
                (trade_id,),
            )
            return True
        return False

    c.execute(
        "UPDATE trades SET exited_early=1, exit_price=?, pnl_usd=?, roi_pct=?, final_outcome='STOP_LOSS', settled=1, settled_at=? WHERE id=?",
        (current_price, pnl_usd, pnl_pct, now.isoformat(), trade_id),
    )
    send_discord(f"🛑 STOP LOSS [{symbol}] {side} closed at {pnl_pct:+.1f}%")

    # REVERSAL LOGIC
    if not is_reversal and ENABLE_REVERSAL:
        opposite_side = "DOWN" if side == "UP" else "UP"
        log(f"🔄 Reversing [{symbol}] {side} → {opposite_side}")
        up_id, down_id = get_token_ids(symbol.split("-")[0])
        if up_id and down_id:
            opposite_token = down_id if side == "UP" else up_id
            opp_price = round(max(0.01, min(0.99, 1.0 - current_price)), 2)
            rev_res = place_order(opposite_token, opp_price, size)
            if rev_res["success"]:
                log(f"🚀 [{symbol}] Reversal order placed: {opposite_side} @ {opp_price}")
                send_discord(f"🔄 **REVERSED** [{symbol}] {side} → {opposite_side}")
                try:
                    w_start, w_end = get_window_times(symbol.split("-")[0])
                    save_trade(
                        cursor=c,
                        symbol=symbol,
                        window_start=w_start.isoformat(),
                        window_end=w_end.isoformat(),
                        slug=get_current_slug(symbol.split("-")[0]),
                        token_id=opposite_token,
                        side=opposite_side,
                        edge=0.0,
                        price=opp_price,
                        size=size,
                        bet_usd=size * opp_price,
                        p_yes=opp_price if opposite_side == "UP" else 1.0 - opp_price,
                        order_status=rev_res["status"],
                        order_id=rev_res["order_id"],
                        is_reversal=True,
                        target_price=target_price,
                    )
                except Exception as e:
                    log(f"⚠️ DB Error (reversal): {e}")
            else:
                log(f"⚠️ Reversal failed: {rev_res.get('error')}")

    return True
