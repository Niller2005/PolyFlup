"""Position scaling logic"""

from src.config.settings import (
    ENABLE_SCALE_IN,
    SCALE_IN_MIN_PRICE,
    SCALE_IN_MAX_PRICE,
    SCALE_IN_TIME_LEFT,
    SCALE_IN_MULTIPLIER,
)
from src.utils.logger import log
from src.trading.orders import get_order, place_order
from .exit_plan import _update_exit_plan_after_scale_in

from src.data.market_data import get_current_spot_price


def _check_scale_in(
    symbol,
    trade_id,
    token_id,
    entry,
    size,
    bet,
    scaled_in,
    scale_in_id,
    t_left,
    current_price,
    check_orders,
    c,
    conn,
    side,
    price_change_pct,
    target_price=None,
    verbose=False,
):
    if not ENABLE_SCALE_IN:
        return
    if scale_in_id and check_orders:
        try:
            o_data = get_order(scale_in_id)
            if o_data and o_data.get("status", "").upper() in ["FILLED", "MATCHED"]:
                s_price = float(o_data.get("price", current_price))
                s_matched = float(o_data.get("size_matched", 0))
                if s_matched > 0:
                    new_size, new_bet = size + s_matched, bet + (s_matched * s_price)
                    c.execute(
                        "SELECT limit_sell_order_id FROM trades WHERE id = ?",
                        (trade_id,),
                    )
                    ls_row = c.fetchone()
                    l_sell_id = ls_row[0] if ls_row else None
                    c.execute(
                        "UPDATE trades SET size=?, bet_usd=?, entry_price=?, scaled_in=1, scale_in_order_id=NULL WHERE id=?",
                        (new_size, new_bet, new_bet / new_size, trade_id),
                    )
                    _update_exit_plan_after_scale_in(
                        symbol, trade_id, token_id, new_size, l_sell_id, c, conn
                    )
                    return
            elif o_data and o_data.get("status", "").upper() in ["CANCELED", "EXPIRED"]:
                c.execute(
                    "UPDATE trades SET scale_in_order_id = NULL WHERE id = ?",
                    (trade_id,),
                )
        except:
            pass

    if scaled_in or scale_in_id:
        return

    # Check if we are in the time window for scale-in
    if t_left > SCALE_IN_TIME_LEFT:
        return  # Too early to scale in

    if t_left <= 0:
        return  # Window expired

    # Price range check
    if not (SCALE_IN_MIN_PRICE <= current_price <= SCALE_IN_MAX_PRICE):
        if verbose:
            log(
                f"   ⏳ [{symbol}] Scale-in skipped: price ${current_price:.2f} outside range (${SCALE_IN_MIN_PRICE}-${SCALE_IN_MAX_PRICE})"
            )
        return

    # Winning side check (Spot price confirmation)
    if target_price:
        current_spot = get_current_spot_price(symbol)
        if current_spot > 0:
            is_on_winning_side = False
            if side == "UP" and current_spot >= target_price:
                is_on_winning_side = True
            elif side == "DOWN" and current_spot <= target_price:
                is_on_winning_side = True

            if not is_on_winning_side:
                if verbose:
                    log(
                        f"   ⏳ [{symbol}] Scale-in skipped: Midpoint range OK (${current_price:.2f}) but Spot (${current_spot:.2f}) on LOSING side of target (${target_price:.2f})"
                    )
                return

    s_size = size * SCALE_IN_MULTIPLIER
    s_price = round(max(0.01, min(0.99, current_price)), 2)
    log(
        f"📈 [{symbol}] Trade #{trade_id} {side} | 📈 SCALE IN triggered: price=${s_price:.2f}, {t_left:.0f}s left"
    )
    res = place_order(token_id, s_price, s_size)
    if res["success"]:
        if res["status"].upper() in ["FILLED", "MATCHED"]:
            log(
                f"📈 [{symbol}] Trade #{trade_id} {side} | ✅ SCALE IN order FILLED: {s_size:.2f} shares @ ${s_price:.2f}"
            )
            new_size, new_bet = size + s_size, bet + (s_size * s_price)
            # Fetch existing exit plan order ID to update it
            c.execute(
                "SELECT limit_sell_order_id FROM trades WHERE id = ?",
                (trade_id,),
            )
            ls_row = c.fetchone()
            l_sell_id = ls_row[0] if ls_row else None

            c.execute(
                "UPDATE trades SET size=?, bet_usd=?, entry_price=?, scaled_in=1, scale_in_order_id=NULL WHERE id=?",
                (new_size, new_bet, new_bet / new_size, trade_id),
            )
            # CRITICAL: Update exit plan to cover the new total size
            _update_exit_plan_after_scale_in(
                symbol, trade_id, token_id, new_size, l_sell_id, c, conn
            )
        else:
            log(
                f"📈 [{symbol}] Trade #{trade_id} {side} | ✅ SCALE IN order placed: {s_size:.2f} shares @ ${s_price:.2f} (status: {res['status']})"
            )
            c.execute(
                "UPDATE trades SET scale_in_order_id = ? WHERE id = ?",
                (res["order_id"], trade_id),
            )
