"""Position scaling logic"""

from datetime import datetime
from zoneinfo import ZoneInfo
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
    confidence=0.0,
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
                    c.execute(
                        "UPDATE trades SET size=size+?, bet_usd=bet_usd+?, entry_price=(bet_usd+?)/(size+?), scaled_in=1, scale_in_order_id=NULL, last_scale_in_at=? WHERE id=?",
                        (
                            s_matched,
                            s_matched * s_price,
                            s_matched * s_price,
                            s_matched,
                            datetime.now(tz=ZoneInfo("UTC")).isoformat(),
                            trade_id,
                        ),
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

    # Dynamic time threshold based on confidence and midpoint (current_price)
    effective_time_left = SCALE_IN_TIME_LEFT
    if confidence >= 0.9 and current_price >= 0.8:
        effective_time_left = max(effective_time_left, 720)  # Up to 12 minutes left
    elif confidence >= 0.8 and current_price >= 0.7:
        effective_time_left = max(effective_time_left, 540)  # Up to 9 minutes left
    elif confidence >= 0.7 and current_price >= 0.65:
        effective_time_left = max(effective_time_left, 420)  # Up to 7 minutes left

    # Check if we are in the time window for scale-in
    if t_left > effective_time_left:
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

    # Use MAKER order (limit) for scale-in to avoid fees and earn rebates
    from src.utils.websocket_manager import ws_manager

    bid, _ = ws_manager.get_bid_ask(token_id)

    # Fallback to current_price (midpoint) if WS bid not available
    maker_price = bid if bid else current_price
    maker_price = max(0.01, min(0.99, round(maker_price, 2)))

    log(
        f"📈 [{symbol}] Trade #{trade_id} {side} | 📈 SCALE IN triggered (Maker Order): size={s_size:.2f}, price=${maker_price:.2f}, {t_left:.0f}s left"
    )

    # Use LIMIT order for scale-in to ensure maker fill
    res = place_order(token_id, maker_price, s_size)

    if res["success"]:
        oid = res.get("order_id")
        actual_s_size = 0.0
        actual_s_price = maker_price

        if oid:
            try:
                # Check if it filled immediately (limit orders can hit the spread)
                o_data = get_order(oid)
                if o_data:
                    actual_s_size = float(o_data.get("size_matched", 0))
                    actual_s_price = float(o_data.get("price", maker_price))
                    status = o_data.get("status", "").upper()

                    if status not in ["FILLED", "MATCHED"] and actual_s_size < s_size:
                        # Order is pending on the book (MAKER)
                        c.execute(
                            "UPDATE trades SET scale_in_order_id = ? WHERE id = ?",
                            (oid, trade_id),
                        )
                        log(
                            f"📈 [{symbol}] Trade #{trade_id} {side} | ⏳ SCALE IN order pending on book (Maker): {s_size:.2f} shares @ ${maker_price:.2f}"
                        )
                        return
            except:
                pass

        if actual_s_size > 0:
            log(
                f"📈 [{symbol}] Trade #{trade_id} {side} | ✅ SCALE IN order filled: {actual_s_size:.2f} shares @ ${actual_s_price:.4f}"
            )
            c.execute(
                "UPDATE trades SET size=size+?, bet_usd=bet_usd+?, entry_price=(bet_usd+?)/(size+?), scaled_in=1, scale_in_order_id=NULL, last_scale_in_at=? WHERE id=?",
                (
                    actual_s_size,
                    actual_s_size * actual_s_price,
                    actual_s_size * actual_s_price,
                    actual_s_size,
                    datetime.now(tz=ZoneInfo("UTC")).isoformat(),
                    trade_id,
                ),
            )
        else:
            # If not filled immediately and we have an ID, it's already handled above
            if not oid:
                log(
                    f"📈 [{symbol}] Trade #{trade_id} {side} | ⚠️  SCALE IN order failed to return ID."
                )
    else:
        log(
            f"📈 [{symbol}] Trade #{trade_id} {side} | ❌ SCALE IN order failed: {res.get('error')}"
        )
