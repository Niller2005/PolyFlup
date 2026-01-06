"""Position scaling logic"""

from src.config.settings import (
    ENABLE_SCALE_IN,
    SCALE_IN_MIN_PRICE,
    SCALE_IN_MAX_PRICE,
    SCALE_IN_TIME_LEFT,
    SCALE_IN_MULTIPLIER,
)
from src.utils.logger import log
from src.trading.orders import (
    get_order,
    place_order,
    place_market_order,
    get_balance_allowance,
)
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
    if scale_in_id:
        try:
            # Check status every cycle when an order is pending to ensure fast exit plan updates
            o_data = get_order(scale_in_id)
            if o_data and o_data.get("status", "").upper() in ["FILLED", "MATCHED"]:
                s_price = float(o_data.get("price", current_price))
                s_matched = float(o_data.get("size_matched", 0))
                if s_matched > 0:
                    new_size, new_bet = size + s_matched, bet + (s_matched * s_price)
                    c.execute(
                        "UPDATE trades SET size=?, bet_usd=?, entry_price=?, scaled_in=1, scale_in_order_id=NULL WHERE id=?",
                        (new_size, new_bet, new_bet / new_size, trade_id),
                    )
                    log(
                        f"📈 [{symbol}] #{trade_id} Scale-in filled (delayed): +{s_matched:.2f} shares"
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

    # Pre-flight balance check to prevent insufficient funds loop
    est_cost = s_size * current_price
    bal_info = get_balance_allowance()
    if bal_info:
        usdc_balance = bal_info.get("balance", 0)
        if usdc_balance < est_cost:
            if verbose:
                log(
                    f"   ⏳ [{symbol}] Scale-in skipped: Insufficient funds (Need ${est_cost:.2f}, Have ${usdc_balance:.2f})"
                )
            return

    log(
        f"📈 [{symbol}] Trade #{trade_id} {side} | 📈 SCALE IN triggered (Market Order): size={s_size:.2f}, {t_left:.0f}s left"
    )

    # Use MARKET order for scale-in to ensure immediate fill and exit plan update
    res = place_market_order(token_id, s_size, side="BUY", order_type="FAK")

    if res["success"]:
        # Market orders fill immediately (FAK fills what it can)
        # We need to get the actual filled details
        oid = res.get("order_id")
        actual_s_size = s_size
        actual_s_price = current_price

        if oid:
            try:
                o_data = get_order(oid)
                if o_data:
                    actual_s_size = float(o_data.get("size_matched", s_size))
                    actual_s_price = float(o_data.get("price", current_price))
            except:
                pass

        if actual_s_size > 0:
            log(
                f"📈 [{symbol}] Trade #{trade_id} {side} | ✅ SCALE IN Market order filled: {actual_s_size:.2f} shares @ ${actual_s_price:.4f}"
            )
            new_size, new_bet = (
                size + actual_s_size,
                bet + (actual_s_size * actual_s_price),
            )

            c.execute(
                "UPDATE trades SET size=?, bet_usd=?, entry_price=?, scaled_in=1, scale_in_order_id=NULL WHERE id=?",
                (new_size, new_bet, new_bet / new_size, trade_id),
            )
        else:
            log(
                f"📈 [{symbol}] Trade #{trade_id} {side} | ⚠️ SCALE IN Market order filled 0 shares."
            )
    else:
        log(
            f"📈 [{symbol}] Trade #{trade_id} {side} | ❌ SCALE IN Market order failed: {res.get('error')}"
        )
