"""Position scaling logic with comprehensive audit trail"""

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
from src.trading.logic import MIN_SIZE


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
    buy_order_status,
    confidence=0.0,
    target_price=None,
    verbose=False,
):
    if not ENABLE_SCALE_IN:
        return

    # Scale-in gating: Skip if USDC balance is too low (< $5.00 minimum)
    bal_info = get_balance_allowance()
    usdc_balance = bal_info.get("balance", 0) if bal_info else 0
    if usdc_balance < 5.0:
        if verbose:
            log(
                f"   ⏭️  [{symbol}] #{trade_id} Scale-in skipped: USDC balance too low (${usdc_balance:.2f} < $5.00 minimum)"
            )
        return

    # Early exit: Do not scale in to unfilled orders
    if buy_order_status not in ["FILLED", "MATCHED"]:
        return
    if scale_in_id:
        try:
            # Check status every cycle when an order is pending to ensure fast exit plan updates
            o_data = get_order(scale_in_id)
            if o_data and o_data.get("status", "").upper() in ["FILLED", "MATCHED"]:
                s_price = float(o_data.get("price", current_price))
                s_matched = float(o_data.get("size_matched", 0))
                if s_matched > 0:
                    # AUDIT: Scale-in order filled (delayed detection)
                    log(
                        f"📈 [{symbol}] Trade #{trade_id} {side} | 🎯 SCALE-IN FILLED (Delayed): +{s_matched:.2f} shares @ ${s_price:.4f} | OrderID: {scale_in_id[:10]}"
                    )
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
                    return
            elif o_data and o_data.get("status", "").upper() in ["CANCELED", "EXPIRED"]:
                # AUDIT: Scale-in order cancelled/expired
                log(
                    f"📈 [{symbol}] Trade #{trade_id} {side} | 🧹 SCALE-IN CANCELLED: Order {scale_in_id[:10]} status changed to {o_data.get('status', '').upper()}"
                )
                c.execute(
                    "UPDATE trades SET scale_in_order_id = NULL WHERE id = ?",
                    (trade_id,),
                )
        except Exception as e:
            # AUDIT: Error checking scale-in order status
            log(
                f"📈 [{symbol}] Trade #{trade_id} {side} | ⚠️ SCALE-IN STATUS ERROR: Failed to check order {scale_in_id[:10]}: {e}"
            )
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

    # Winning side check using Polymarket outcome prices (separate for UP/DOWN tokens)
    is_on_winning_side = False
    if target_price:
        from src.data.market_data import get_outcome_prices
        from src.utils.websocket_manager import ws_manager

        # Get outcome prices for both UP and DOWN tokens
        outcome_data = get_outcome_prices(symbol)
        if not outcome_data:
            if verbose:
                log(
                    f"   ⏳ [{symbol}] Scale-in skipped: Could not fetch outcome prices"
                )
            return

        # Use the specific price for the side we're checking
        if side == "UP":
            is_on_winning_side = outcome_data.get("up_wins", False)
        elif side == "DOWN":
            is_on_winning_side = outcome_data.get("down_wins", False)

        # Only scale in if we're on winning side
        if not is_on_winning_side:
            if verbose:
                up_price = outcome_data.get("up_price")
                down_price = outcome_data.get("down_price")
                if side == "UP":
                    log(
                        f"   ⏳ [{symbol}] Scale-in skipped: UP price ${up_price:.2f} on LOSING side (UP needs >= $0.50)"
                    )
                else:
                    log(
                        f"   ⏳ [{symbol}] Scale-in skipped: DOWN price ${down_price:.2f} on LOSING side (DOWN needs <= $0.50)"
                    )
            return

    s_size = size * SCALE_IN_MULTIPLIER

    # MIN_SIZE check for scale-in - try bumping to MIN_SIZE if affordable
    if s_size < MIN_SIZE:
        min_size_cost = MIN_SIZE * current_price
        bal_info = get_balance_allowance()
        if bal_info:
            usdc_balance = bal_info.get("balance", 0)
            if usdc_balance >= min_size_cost:
                if verbose:
                    log(
                        f"   📈 [{symbol}] #{trade_id} Bumping scale-in to {MIN_SIZE} shares (${min_size_cost:.2f})"
                    )
                s_size = MIN_SIZE
            else:
                if verbose:
                    log(
                        f"   ⏭️  [{symbol}] #{trade_id} scale-in size {s_size:.2f} < {MIN_SIZE}. Cannot afford ${min_size_cost:.2f}. Have ${usdc_balance:.2f}. Skipping."
                    )
                return
        else:
            if verbose:
                log(
                    f"   ⏭️  [{symbol}] #{trade_id} scale-in size {s_size:.2f} < {MIN_SIZE}. Skipping, trying again next window."
                )
            return

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

    # Check USDC balance before attempt for better debugging
    bal_info = get_balance_allowance()
    usdc_balance = bal_info.get("balance", 0) if bal_info else 0

    # AUDIT: Scale-in order placement initiated
    log(
        f"📈 [{symbol}] Trade #{trade_id} {side} | 🎯 SCALE-IN PLACEMENT: Initiating maker order | size={s_size:.2f}, price=${maker_price:.2f}, time_left={t_left:.0f}s, confidence={confidence:.2f} | 💰 USDC: ${usdc_balance:.2f}"
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
                        # AUDIT: Scale-in order placed on book
                        log(
                            f"📈 [{symbol}] Trade #{trade_id} {side} | ⏳ SCALE-IN PLACED: Maker order pending on book | OrderID: {oid[:10]}, size={s_size:.2f}, price=${maker_price:.2f}, status={status}"
                        )
                        return
            except Exception as e:
                # AUDIT: Error checking immediate fill status
                log(
                    f"📈 [{symbol}] Trade #{trade_id} {side} | ⚠️ SCALE-IN IMMEDIATE CHECK ERROR: Failed to verify order {oid[:10]} status: {e}"
                )
                pass

        if actual_s_size > 0:
            # AUDIT: Scale-in order filled immediately
            log(
                f"📈 [{symbol}] Trade #{trade_id} {side} | ✅ SCALE-IN FILLED (Immediate): {actual_s_size:.2f} shares @ ${actual_s_price:.4f} | OrderID: {oid[:10]}"
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
                # AUDIT: Scale-in order placement failed - no order ID
                log(
                    f"📈 [{symbol}] Trade #{trade_id} {side} | ❌ SCALE-IN PLACEMENT FAILED: No order ID returned"
                )
    else:
        # AUDIT: Scale-in order placement failed
        error_msg = res.get("error", "Unknown error")
        log(
            f"📈 [{symbol}] Trade #{trade_id} {side} | ❌ SCALE-IN PLACEMENT FAILED: {error_msg}"
        )
