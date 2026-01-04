"""Position monitoring and management"""

import time
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, Any
from src.data.db_connection import db_connection
from src.config.settings import (
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
    UNFILLED_TIMEOUT_SECONDS,
    UNFILLED_RETRY_ON_WINNING_SIDE,
    ENABLE_EXIT_PLAN,
    EXIT_PRICE_TARGET,
    EXIT_MIN_POSITION_AGE,
    EXIT_CHECK_INTERVAL,
    EXIT_AGGRESSIVE_MODE,
)
from src.utils.logger import log, send_discord
from src.trading.orders import (
    get_clob_client,
    sell_position,
    place_order,
    place_limit_order,
    cancel_order,
    get_order_status,
    get_order,
    get_midpoint,
    get_balance_allowance,
    get_notifications,
    drop_notifications,
    BUY,
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


def get_exit_plan_stats():
    """Get statistics on exit plan performance"""
    try:
        with db_connection() as conn:
            c = conn.cursor()

            # Count exit plan successes vs natural settlements
            c.execute("""
                SELECT 
                    COUNT(CASE WHEN final_outcome = 'EXIT_PLAN_FILLED' THEN 1 END) as exit_plan_successes,
                    COUNT(CASE WHEN final_outcome = 'RESOLVED' AND exited_early = 0 THEN 1 END) as natural_settlements,
                    COUNT(CASE WHEN final_outcome = 'LIMIT_SELL_099' THEN 1 END) as legacy_limit_sells,
                    AVG(CASE WHEN final_outcome = 'EXIT_PLAN_FILLED' THEN roi_pct END) as avg_exit_plan_roi,
                    AVG(CASE WHEN final_outcome = 'RESOLVED' AND exited_early = 0 THEN roi_pct END) as avg_natural_roi
                FROM trades 
                WHERE settled = 1 
                AND datetime(timestamp) >= datetime('now', '-7 days')
            """)

            stats = c.fetchone()

            if stats and any(stats):
                (
                    exit_successes,
                    natural_settlements,
                    legacy_sells,
                    avg_exit_roi,
                    avg_natural_roi,
                ) = stats
                total_exits = exit_successes + natural_settlements + legacy_sells

                if total_exits > 0:
                    exit_rate = (exit_successes / total_exits) * 100
                    log(
                        f"📊 EXIT PLAN STATS (7d): {exit_successes} successful exits ({exit_rate:.1f}%), "
                        f"{natural_settlements} natural settlements, {legacy_sells} legacy | "
                        f"Avg ROI: Exit {avg_exit_roi or 0:.1f}%, Natural {avg_natural_roi or 0:.1f}%"
                    )

                    return {
                        "exit_plan_successes": exit_successes,
                        "natural_settlements": natural_settlements,
                        "legacy_limit_sells": legacy_sells,
                        "exit_success_rate": exit_rate,
                        "avg_exit_plan_roi": avg_exit_roi or 0,
                        "avg_natural_roi": avg_natural_roi or 0,
                    }

            return None

    except Exception as e:
        log(f"⚠️ Error getting exit plan stats: {e}")
        return None


def recover_open_positions():
    """
    Recover and log open positions from database on bot startup
    This ensures positions are monitored after a restart
    """
    with db_connection() as conn:
        c = conn.cursor()
        now = datetime.now(tz=ZoneInfo("UTC"))

        # Get all unsettled trades that are still in their window
        c.execute(
            """SELECT id, symbol, side, entry_price, size, bet_usd, window_end, order_status, timestamp
               FROM trades 
               WHERE settled = 0 
               AND exited_early = 0
               AND datetime(window_end) > datetime(?)""",
            (now.isoformat(),),
        )
        open_positions = c.fetchall()

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
        timestamp,
    ) in open_positions:
        window_end_dt = datetime.fromisoformat(window_end)
        time_left = (window_end_dt - now).total_seconds() / 60.0  # minutes

        log(
            f"  [{symbol}] Trade #{trade_id} {side}: ${bet_usd:.2f} @ ${entry_price:.4f} | Status: {order_status} | {time_left:.0f}m left"
        )

    log("=" * 90)
    log(f"✓ Position monitoring ACTIVE for {len(open_positions)} positions")
    log("=" * 90)


def _get_position_pnl(token_id: str, entry_price: float, size: float) -> Optional[dict]:
    """Get current market price and calculate P&L"""
    # Try to get midpoint price first (more accurate)
    current_price = get_midpoint(token_id)

    # Fallback to calculating from order book if midpoint fails
    if current_price is None:
        client = get_clob_client()
        book = client.get_order_book(token_id)
        if isinstance(book, dict):
            bids = book.get("bids", []) or []
            asks = book.get("asks", []) or []
        else:
            bids = getattr(book, "bids", []) or []
            asks = getattr(book, "asks", []) or []

        if not bids or not asks:
            return None

        best_bid = float(
            bids[-1].price if hasattr(bids[-1], "price") else bids[-1].get("price", 0)
        )
        best_ask = float(
            asks[-1].price if hasattr(asks[-1], "price") else asks[-1].get("price", 0)
        )
        current_price = (best_bid + best_ask) / 2.0

    price_change_pct = ((current_price - entry_price) / entry_price) * 100
    current_value = current_price * size
    pnl_usd = current_value - (entry_price * size)
    pnl_pct = (pnl_usd / (entry_price * size)) * 100 if size > 0 else 0

    return {
        "current_price": current_price,
        "pnl_pct": pnl_pct,
        "pnl_usd": pnl_usd,
        "price_change_pct": price_change_pct,
    }


def _check_stop_loss(
    symbol: str,
    trade_id: int,
    token_id: str,
    side: str,
    entry_price: float,
    size: float,
    pnl_pct: float,
    pnl_usd: float,
    current_price: float,
    target_price: Optional[float],
    limit_sell_order_id: Optional[str],
    is_reversal: bool,
    c: Any,
    conn: Any,
    now: datetime,
) -> bool:
    """Check and execute stop loss if triggered, returns True if position closed"""
    if not ENABLE_STOP_LOSS or pnl_pct > -STOP_LOSS_PERCENT or size == 0:
        return False

    stop_threshold = -STOP_LOSS_PERCENT
    sl_label = "STOP LOSS"

    if pnl_pct >= 20.0:
        is_moving_against = (side == "UP" and current_price < entry_price) or (
            side == "DOWN" and current_price > entry_price
        )

        if is_moving_against:
            stop_threshold = -5.0
            sl_label = "BREAKEVEN PROTECTION"

    current_spot = get_current_spot_price(symbol)
    is_on_losing_side = False

    if current_spot > 0 and target_price is not None:
        if side == "UP" and current_spot < target_price:
            is_on_losing_side = True
        elif side == "DOWN" and current_spot > target_price:
            is_on_losing_side = True
    else:
        is_on_losing_side = True

    if not is_on_losing_side:
        log(
            f"ℹ️ [{symbol}] PnL is bad ({pnl_pct:.1f}%) but price is on WINNING side of target - HOLDING (Spot ${current_spot:,.2f} vs Target ${target_price:,.2f})"
        )
        return False

    log(
        f"🛑 {sl_label} trade #{trade_id}: {pnl_pct:.1f}% PnL (Threshold: {stop_threshold}%) | Spot ${current_spot:,.2f} vs Target ${target_price:,.2f}"
    )

    if limit_sell_order_id:
        if cancel_order(limit_sell_order_id):
            log(
                f"[{symbol}] ⏳ Limit sell order cancelled, waiting for tokens to be freed..."
            )
            time.sleep(2)

    sell_result = sell_position(token_id, size, current_price)

    if not sell_result["success"]:
        return False

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

    send_discord(f"🛑 **STOP LOSS** [{symbol}] {side} closed at {pnl_pct:+.1f}%")

    if not is_reversal and ENABLE_REVERSAL:
        opposite_side = "DOWN" if side == "UP" else "UP"
        log(f"🔄 Reversing [{symbol}] {side} → {opposite_side} to get on winning side")

        up_id, down_id = get_token_ids(symbol.split("-")[0])
        if up_id and down_id:
            opposite_token = down_id if side == "UP" else up_id
            opposite_price = 1.0 - current_price
            # Round to minimum tick size (0.01)
            opposite_price = round(max(0.01, min(0.99, opposite_price)), 2)

            # Use enhanced place_order with better error handling
            reverse_result = place_order(opposite_token, opposite_price, size)

            if reverse_result["success"]:
                send_discord(
                    f"🔄 **REVERSED** [{symbol}] {side} → {opposite_side} (Target: ${target_price:,.2f}, Spot: ${current_spot:,.2f})"
                )

                try:
                    window_start, window_end = get_window_times(symbol.split("-")[0])
                    bet_usd_effective = size * opposite_price
                    save_trade(
                        symbol=symbol,
                        window_start=window_start.isoformat(),
                        window_end=window_end.isoformat(),
                        slug=get_current_slug(symbol.split("-")[0]),
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
                # Enhanced error reporting
                error_msg = reverse_result.get("error", "Unknown error")
                log(f"⚠️ Reversal order failed for [{symbol}]: {error_msg}")
                send_discord(
                    f"⚠️ **REVERSAL FAILED** [{symbol}] {side} → {opposite_side}: {error_msg}"
                )

    return True


def _update_exit_plan_after_scale_in(
    symbol: str,
    trade_id: int,
    token_id: str,
    new_total_size: float,
    limit_sell_order_id: Optional[str],
    c: Any,
    conn: Any,
) -> None:
    """Update exit plan limit order after position size changes from scale-in"""
    if not limit_sell_order_id or not ENABLE_EXIT_PLAN:
        return

    log(f"   🔄 Updating exit plan order (size changed to {new_total_size:.2f})")

    # Cancel old exit plan order
    cancel_result = cancel_order(limit_sell_order_id)

    if cancel_result:
        # Place new exit plan order with updated size
        new_exit_order = place_limit_order(
            token_id=token_id,
            price=EXIT_PRICE_TARGET,
            size=new_total_size,
            side=SELL,
            silent_on_balance_error=True,
            order_type="GTC",
        )

        if new_exit_order["success"]:
            new_order_id = new_exit_order["order_id"]
            log(
                f"   ✅ Updated exit plan: {new_total_size:.2f} shares @ {EXIT_PRICE_TARGET} | ID: {new_order_id[:10]}..."
            )
            c.execute(
                "UPDATE trades SET limit_sell_order_id = ? WHERE id = ?",
                (new_order_id, trade_id),
            )
            conn.commit()
        else:
            log(f"   ⚠️ Failed to update exit plan: {new_exit_order.get('error')}")
            # Clear old order ID since we cancelled it
            c.execute(
                "UPDATE trades SET limit_sell_order_id = NULL WHERE id = ?",
                (trade_id,),
            )
            conn.commit()
    else:
        log(f"   ⚠️ Failed to cancel old exit plan order")


def _check_exit_plan(
    symbol: str,
    trade_id: int,
    token_id: str,
    size: float,
    buy_order_status: str,
    limit_sell_order_id: Optional[str],
    timestamp: str,
    c: Any,
    conn: Any,
    now: datetime,
    verbose: bool = False,
) -> None:
    """Check and manage exit plan limit orders"""
    if not ENABLE_EXIT_PLAN or buy_order_status != "FILLED" or size == 0:
        return

    position_age_seconds = (now - datetime.fromisoformat(timestamp)).total_seconds()

    if (
        not limit_sell_order_id
        and position_age_seconds >= EXIT_MIN_POSITION_AGE
        and position_age_seconds > 60
    ):
        log(
            f"[{symbol}] 📉 EXIT PLAN: Placing limit sell order at {EXIT_PRICE_TARGET} for {size} units (position age: {position_age_seconds:.0f}s, min age: {EXIT_MIN_POSITION_AGE}s)"
        )
        sell_limit_result = place_limit_order(
            token_id=token_id,
            price=EXIT_PRICE_TARGET,
            size=size,
            side=SELL,
            silent_on_balance_error=True,
            order_type="GTC",  # Good-til-cancelled for exit plan
        )
        if sell_limit_result["success"]:
            limit_sell_order_id = sell_limit_result["order_id"]
            log(
                f"[{symbol}] ✅ EXIT PLAN: Limit sell order placed at {EXIT_PRICE_TARGET}: {limit_sell_order_id}"
            )
            c.execute(
                "UPDATE trades SET limit_sell_order_id = ? WHERE id = ?",
                (limit_sell_order_id, trade_id),
            )
            conn.commit()
        else:
            error_msg = sell_limit_result.get("error", "Unknown error")
            log(
                f"[{symbol}] ⚠️ EXIT PLAN: Failed to place limit sell at {EXIT_PRICE_TARGET}: {error_msg} (will retry next cycle)"
            )
    elif limit_sell_order_id and position_age_seconds >= EXIT_MIN_POSITION_AGE + 60:
        # Only log monitoring message on verbose cycles (every 60s)
        if verbose:
            log(
                f"[{symbol}] ⏰ EXIT PLAN: Position age {position_age_seconds:.0f}s - monitoring limit sell order {limit_sell_order_id}"
            )


def _check_scale_in(
    symbol: str,
    trade_id: int,
    token_id: str,
    entry_price: float,
    size: float,
    bet_usd: float,
    scaled_in: bool,
    scale_in_order_id: Optional[str],
    time_left_seconds: float,
    current_price: float,
    check_orders: bool,
    c: Any,
    conn: Any,
) -> None:
    """Check and execute scale in if conditions are met, or monitor pending scale-in orders"""
    if not ENABLE_SCALE_IN:
        return

    # First, check if there's a pending scale-in order to monitor
    if scale_in_order_id and check_orders:
        try:
            order_data = get_order(scale_in_order_id)

            if order_data:
                status = order_data.get("status", "").upper()

                if status == "FILLED":
                    # Scale-in order filled! Update position
                    scale_price = order_data.get("price", current_price)
                    size_matched = order_data.get("size_matched", 0)

                    if size_matched > 0:
                        log(
                            f"✅ SCALE IN FILLED for trade #{trade_id}: {size_matched} shares @ ${scale_price:.4f}"
                        )

                        new_total_size = size + size_matched
                        additional_bet = size_matched * scale_price
                        new_total_bet = bet_usd + additional_bet
                        new_avg_price = new_total_bet / new_total_size

                        # Get current limit_sell_order_id before updating
                        c.execute(
                            "SELECT limit_sell_order_id FROM trades WHERE id = ?",
                            (trade_id,),
                        )
                        row = c.fetchone()
                        current_limit_sell_id = row[0] if row else None

                        c.execute(
                            """UPDATE trades
                               SET size=?, bet_usd=?, entry_price=?, scaled_in=1, scale_in_order_id=NULL
                               WHERE id=?""",
                            (new_total_size, new_total_bet, new_avg_price, trade_id),
                        )
                        conn.commit()

                        log(
                            f"   Size: {size:.2f} → {new_total_size:.2f} (+{size_matched:.2f})"
                        )
                        log(f"   Avg price: ${entry_price:.4f} → ${new_avg_price:.4f}")

                        # CRITICAL: Update exit plan order with new size
                        _update_exit_plan_after_scale_in(
                            symbol=symbol,
                            trade_id=trade_id,
                            token_id=token_id,
                            new_total_size=new_total_size,
                            limit_sell_order_id=current_limit_sell_id,
                            c=c,
                            conn=conn,
                        )

                        send_discord(
                            f"📈 **SCALE IN FILLED** [{symbol}] +${additional_bet:.2f} @ ${scale_price:.2f}"
                        )
                        return

                elif status in ["CANCELED", "EXPIRED"]:
                    log(f"⚠️ SCALE IN: Order for trade #{trade_id} was {status}")
                    # Clear the order ID so it can potentially be re-placed
                    c.execute(
                        "UPDATE trades SET scale_in_order_id = NULL WHERE id = ?",
                        (trade_id,),
                    )
                    conn.commit()
                elif status == "LIVE":
                    # Order is live, waiting for fill
                    log(
                        f"📋 SCALE IN: Order for trade #{trade_id} is LIVE, waiting for fill..."
                    )
                    return  # Don't try to place another one
                elif status in ["DELAYED", "UNMATCHED"]:
                    log(f"ℹ️ SCALE IN: Order for trade #{trade_id} status: {status}")
                    return  # Still pending
        except Exception as e:
            log(f"⚠️ Error checking scale-in order {scale_in_order_id}: {e}")

    # If already scaled in or there's a pending order, don't place new one
    if scaled_in or scale_in_order_id:
        return

    # Check if conditions are met to place a new scale-in order
    if (
        time_left_seconds > SCALE_IN_TIME_LEFT
        or time_left_seconds <= 0
        or not (SCALE_IN_MIN_PRICE <= current_price <= SCALE_IN_MAX_PRICE)
    ):
        return

    log(
        f"📈 SCALE IN triggered for trade #{trade_id}: price=${current_price:.2f}, {time_left_seconds:.0f}s left"
    )

    additional_size = size * SCALE_IN_MULTIPLIER

    # Round price to minimum tick size (0.01) before placing order
    scale_price = round(max(0.01, min(0.99, current_price)), 2)
    additional_bet = additional_size * scale_price

    # Use enhanced place_order with validation and retry logic
    scale_result = place_order(token_id, scale_price, additional_size)

    if scale_result["success"]:
        # Save the order ID so we can monitor it
        new_scale_in_order_id = scale_result["order_id"]
        order_status = scale_result["status"]

        log(
            f"✅ SCALE IN order placed for trade #{trade_id}: {additional_size:.2f} shares @ ${scale_price:.2f} (status: {order_status})"
        )

        # If order filled immediately, update position now
        if order_status == "matched":
            new_total_size = size + additional_size
            new_total_bet = bet_usd + additional_bet
            new_avg_price = new_total_bet / new_total_size

            c.execute(
                """UPDATE trades
                   SET size=?, bet_usd=?, entry_price=?, scaled_in=1, scale_in_order_id=NULL
                   WHERE id=?""",
                (new_total_size, new_total_bet, new_avg_price, trade_id),
            )
            conn.commit()

            log(f"   Size: {size:.2f} → {new_total_size:.2f} (+{additional_size:.2f})")
            log(f"   Avg price: ${entry_price:.4f} → ${new_avg_price:.4f}")

            # CRITICAL: Check if exit plan order exists and update it
            c.execute(
                "SELECT limit_sell_order_id FROM trades WHERE id = ?", (trade_id,)
            )
            row = c.fetchone()
            current_limit_sell_id = row[0] if row else None

            _update_exit_plan_after_scale_in(
                symbol=symbol,
                trade_id=trade_id,
                token_id=token_id,
                new_total_size=new_total_size,
                limit_sell_order_id=current_limit_sell_id,
                c=c,
                conn=conn,
            )

            send_discord(
                f"📈 **SCALED IN** [{symbol}] +${additional_bet:.2f} @ ${scale_price:.2f} ({time_left_seconds:.0f}s left)"
            )
        else:
            # Order is live/pending, save ID to monitor it
            c.execute(
                "UPDATE trades SET scale_in_order_id = ? WHERE id = ?",
                (new_scale_in_order_id, trade_id),
            )
            conn.commit()
            log(f"   Monitoring scale-in order: {new_scale_in_order_id[:10]}...")
    else:
        # Enhanced error reporting
        error_msg = scale_result.get("error", "Unknown error")
        log(f"⚠️ Scale in failed for trade #{trade_id}: {error_msg}")
        # Don't send Discord for scale-in failures (too noisy), just log it


def _check_take_profit(
    symbol: str,
    trade_id: int,
    token_id: str,
    side: str,
    size: float,
    pnl_pct: float,
    pnl_usd: float,
    current_price: float,
    limit_sell_order_id: Optional[str],
    c: Any,
    conn: Any,
    now: datetime,
) -> bool:
    """Check and execute take profit if triggered, returns True if position closed"""
    if not ENABLE_TAKE_PROFIT or pnl_pct < TAKE_PROFIT_PERCENT:
        return False

    log(f"🎯 TAKE PROFIT triggered for trade #{trade_id}: {pnl_pct:.1f}% gain")

    if limit_sell_order_id:
        if cancel_order(limit_sell_order_id):
            log(
                f"[{symbol}] ⏳ Limit sell order cancelled, waiting for tokens to be freed..."
            )
            time.sleep(2)

    sell_result = sell_position(token_id, size, current_price)

    if not sell_result["success"]:
        return False

    c.execute(
        """UPDATE trades
           SET exited_early=1, exit_price=?, pnl_usd=?, roi_pct=?,
               final_outcome='TAKE_PROFIT', settled=1, settled_at=?
           WHERE id=?""",
        (current_price, pnl_usd, pnl_pct, now.isoformat(), trade_id),
    )
    conn.commit()

    send_discord(f"🎯 **TAKE PROFIT** [{symbol}] {side} closed at {pnl_pct:+.1f}%")

    return True


def check_open_positions(verbose: bool = True, check_orders: bool = False):
    """
    Check open positions and manage them

    Args:
        verbose: If True, log position checks. If False, only log actions (stop loss, etc.)
        check_orders: If True, check status of limit sell orders
    """
    if not any(
        [
            ENABLE_STOP_LOSS,
            ENABLE_TAKE_PROFIT,
            ENABLE_SCALE_IN,
            ENABLE_REVERSAL,
            ENABLE_EXIT_PLAN,
            CANCEL_UNFILLED_ORDERS,
        ]
    ):
        if not check_orders:
            return

    with db_connection() as conn:
        c = conn.cursor()
    now = datetime.now(tz=ZoneInfo("UTC"))

    # Get unsettled trades that are still in their window
    c.execute(
        """SELECT id, symbol, slug, token_id, side, entry_price, size, bet_usd, window_end, scaled_in, is_reversal, target_price, limit_sell_order_id, order_id, order_status, timestamp, scale_in_order_id
           FROM trades 
           WHERE settled = 0 
           AND exited_early = 0
           AND datetime(window_end) > datetime(?)""",
        (now.isoformat(),),
    )
    open_positions = c.fetchall()

    if not open_positions:
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
        timestamp,
        scale_in_order_id,
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
                elif current_buy_status in ["CANCELED", "EXPIRED", "NOT_FOUND"]:
                    log(
                        f"⚠️ BUY order for trade #{trade_id} was {current_buy_status}. Settling trade."
                    )
                    c.execute(
                        "UPDATE trades SET settled = 1, final_outcome = ? WHERE id = ?",
                        (current_buy_status, trade_id),
                    )
                    conn.commit()
                    continue
                elif current_buy_status in ["DELAYED", "UNMATCHED"]:
                    # Order is pending, log status but continue monitoring
                    if verbose:
                        log(
                            f"ℹ️ BUY order for trade #{trade_id} status: {current_buy_status}"
                        )
                elif current_buy_status == "LIVE":
                    # Order is live on the book, waiting for fill
                    if verbose:
                        log(f"📋 BUY order for trade #{trade_id} is LIVE on the book")
                elif current_buy_status == "ERROR":
                    log(f"⚠️ Error checking BUY order for trade #{trade_id}")

            # Note: Limit sell order placement moved to 60-second mark (after price checks below)

            # 2. Check if limit sell order was filled (EXIT PLAN MONITORING)
            if check_orders and limit_sell_order_id:
                try:
                    # Use enhanced get_order() for detailed information
                    order_data = get_order(limit_sell_order_id)

                    if order_data:
                        status = order_data.get("status", "").upper()

                        if status == "FILLED":
                            # Get actual exit price from order data if available
                            exit_price = order_data.get("price", EXIT_PRICE_TARGET)
                            size_matched = order_data.get("size_matched", size)

                            log(
                                f"🎯 EXIT PLAN SUCCESS: Trade #{trade_id} filled at {exit_price}! (matched {size_matched} shares)"
                            )

                            pnl_usd = (exit_price * size_matched) - bet_usd
                            roi_pct = (pnl_usd / bet_usd) * 100 if bet_usd > 0 else 0

                            c.execute(
                                """UPDATE trades 
                                   SET settled=1, exited_early=1, exit_price=?, 
                                       pnl_usd=?, roi_pct=?, 
                                       final_outcome='EXIT_PLAN_FILLED', settled_at=? 
                                   WHERE id=?""",
                                (
                                    exit_price,
                                    pnl_usd,
                                    roi_pct,
                                    now.isoformat(),
                                    trade_id,
                                ),
                            )
                            conn.commit()
                            send_discord(
                                f"🎯 **EXIT PLAN SUCCESS** [{symbol}] {side} closed at {exit_price} ({roi_pct:+.1f}%)"
                            )
                            continue
                        elif status in ["CANCELED", "EXPIRED"]:
                            log(
                                f"⚠️ EXIT PLAN: Limit sell order for trade #{trade_id} was {status}"
                            )
                            # Clear the limit_sell_order_id so it can be re-placed
                            c.execute(
                                "UPDATE trades SET limit_sell_order_id = NULL WHERE id = ?",
                                (trade_id,),
                            )
                            conn.commit()
                        elif status == "LIVE" and verbose:
                            log(
                                f"📋 EXIT PLAN: Limit sell order for trade #{trade_id} is LIVE at {EXIT_PRICE_TARGET}"
                            )
                    else:
                        # Order not found, might be too old
                        if verbose:
                            log(
                                f"⚠️ EXIT PLAN: Could not find limit sell order {limit_sell_order_id[:10]}..."
                            )

                except Exception as e:
                    # Order might be too old or other error, log if significant
                    if "404" not in str(e):
                        log(
                            f"⚠️ Error checking exit plan order {limit_sell_order_id}: {e}"
                        )

            # Get current market price and calculate P&L
            pnl_info = _get_position_pnl(token_id, entry_price, size)
            if not pnl_info:
                continue

            current_price = pnl_info["current_price"]
            pnl_pct = pnl_info["pnl_pct"]
            pnl_usd = pnl_info["pnl_usd"]
            price_change_pct = pnl_info["price_change_pct"]

            if verbose:
                log(
                    f"  [{symbol}] Trade #{trade_id} {side}: Entry=${entry_price:.4f} Current=${current_price:.4f} PnL={price_change_pct:+.1f}%"
                )

            # ============================================================
            # PRIORITY #1: STOP LOSS (checked FIRST, before everything else)
            # ============================================================
            closed = _check_stop_loss(
                symbol=symbol,
                trade_id=trade_id,
                token_id=token_id,
                side=side,
                entry_price=entry_price,
                size=size,
                pnl_pct=pnl_pct,
                pnl_usd=pnl_usd,
                current_price=current_price,
                target_price=target_price,
                limit_sell_order_id=limit_sell_order_id,
                is_reversal=is_reversal,
                c=c,
                conn=conn,
                now=now,
            )
            if closed:
                continue

            # ============================================================
            # PRIORITY #2: Other Risk Management (after stop loss)
            # ============================================================

            # Calculate time left until window ends
            if isinstance(window_end, str):
                window_end_dt = datetime.fromisoformat(window_end)
            else:
                window_end_dt = window_end

            time_left_seconds = (window_end_dt - now).total_seconds()

            # ============================================================
            # EXIT PLAN: Check and manage limit sell orders
            # ============================================================
            _check_exit_plan(
                symbol=symbol,
                trade_id=trade_id,
                token_id=token_id,
                size=size,
                buy_order_status=current_buy_status,
                limit_sell_order_id=limit_sell_order_id,
                timestamp=timestamp,
                c=c,
                conn=conn,
                now=now,
                verbose=verbose,
            )
            c.execute(
                "SELECT limit_sell_order_id FROM trades WHERE id = ?", (trade_id,)
            )
            row = c.fetchone()
            if row:
                limit_sell_order_id = row[0]

            # ============================================================
            # SCALE IN: Check if conditions are met or monitor pending orders
            # ============================================================
            # Re-fetch scale_in_order_id in case it was just placed
            c.execute("SELECT scale_in_order_id FROM trades WHERE id = ?", (trade_id,))
            row = c.fetchone()
            if row:
                scale_in_order_id = row[0]

            _check_scale_in(
                symbol=symbol,
                trade_id=trade_id,
                token_id=token_id,
                entry_price=entry_price,
                size=size,
                bet_usd=bet_usd,
                scaled_in=scaled_in,
                scale_in_order_id=scale_in_order_id,
                time_left_seconds=time_left_seconds,
                current_price=current_price,
                check_orders=check_orders,
                c=c,
                conn=conn,
            )

            # ============================================================
            # UNFILLED ORDER MANAGEMENT
            # ============================================================
            if CANCEL_UNFILLED_ORDERS and current_buy_status not in [
                "FILLED",
                "matched",
            ]:
                should_cancel = False
                cancel_reason = ""
                should_reverse = False

                # Calculate position age
                position_age_seconds = (
                    now - datetime.fromisoformat(timestamp)
                ).total_seconds()

                # Reason 1: Price moved away (losing side)
                if price_change_pct <= -UNFILLED_CANCEL_THRESHOLD:
                    should_cancel = True
                    cancel_reason = f"Price moved away ({price_change_pct:.1f}% < -{UNFILLED_CANCEL_THRESHOLD}%)"

                # Reason 2: Timeout - order unfilled for too long
                elif position_age_seconds > UNFILLED_TIMEOUT_SECONDS:
                    should_cancel = True
                    cancel_reason = f"Order unfilled for {position_age_seconds:.0f}s (timeout: {UNFILLED_TIMEOUT_SECONDS}s)"

                    if verbose:
                        log(
                            f"⏰ [{symbol}] Trade #{trade_id} timeout: {position_age_seconds:.0f}s old, P&L: {pnl_pct:+.1f}%"
                        )

                    # Check if we're on the winning side with good P&L
                    if UNFILLED_RETRY_ON_WINNING_SIDE and pnl_pct > 10.0:
                        current_spot = get_current_spot_price(symbol)
                        is_on_winning_side = False

                        if current_spot > 0 and target_price is not None:
                            if side == "UP" and current_spot >= target_price:
                                is_on_winning_side = True
                            elif side == "DOWN" and current_spot <= target_price:
                                is_on_winning_side = True

                        if is_on_winning_side:
                            should_reverse = True
                            cancel_reason += (
                                f" - On winning side with {pnl_pct:+.1f}% P&L"
                            )

                if should_cancel:
                    # Only log once per trade (check if we already tried)
                    c.execute(
                        "SELECT order_status FROM trades WHERE id = ?", (trade_id,)
                    )
                    status_row = c.fetchone()
                    current_status = status_row[0] if status_row else None

                    # Skip if we already attempted cancellation (status will be different from 'live')
                    if current_status and current_status.upper() not in [
                        "LIVE",
                        "DELAYED",
                        "UNMATCHED",
                    ]:
                        continue

                    log(
                        f"🛑 CANCELLING unfilled order for trade #{trade_id}: {cancel_reason}"
                    )
                    cancel_result = cancel_order(buy_order_id)

                    if cancel_result:
                        log(f"✅ Buy order cancelled successfully")

                        if should_reverse:
                            # Try to enter at current market price
                            log(
                                f"🔄 Retrying [{symbol}] {side} at current market price"
                            )

                            # Get current market price
                            retry_price = current_price
                            retry_price = round(max(0.01, min(0.99, retry_price)), 2)

                            retry_result = place_order(token_id, retry_price, size)

                            if retry_result["success"]:
                                log(
                                    f"✅ Retry order placed at ${retry_price:.2f} (was ${entry_price:.2f})"
                                )
                                # Update the trade with new order info
                                c.execute(
                                    """UPDATE trades 
                                       SET order_id = ?, order_status = ?, entry_price = ?
                                       WHERE id = ?""",
                                    (
                                        retry_result["order_id"],
                                        retry_result["status"],
                                        retry_price,
                                        trade_id,
                                    ),
                                )
                                conn.commit()
                                send_discord(
                                    f"🔄 **RETRY** [{symbol}] {side} @ ${retry_price:.2f} (was ${entry_price:.2f}, waited {position_age_seconds:.0f}s)"
                                )
                                continue
                            else:
                                error_msg = retry_result.get("error", "Unknown error")
                                log(f"⚠️ Retry order failed: {error_msg}")

                        # Mark as cancelled if not retrying or retry failed
                        c.execute(
                            "UPDATE trades SET settled = 1, final_outcome = 'CANCELLED_UNFILLED', order_status = 'CANCELED' WHERE id = ?",
                            (trade_id,),
                        )
                        conn.commit()
                    else:
                        # Cancel failed - likely order already filled or cancelled
                        # Update status so we don't keep trying
                        log(
                            f"ℹ️ Cancel returned False - order may already be filled/cancelled, checking status..."
                        )

                        # Re-check order status
                        actual_status = get_order_status(buy_order_id)
                        log(f"   Order status: {actual_status}")

                        if actual_status in ["FILLED", "matched"]:
                            # Order was filled! Update status and continue monitoring
                            log(f"✅ Order was actually FILLED, updating database")
                            c.execute(
                                "UPDATE trades SET order_status = 'FILLED' WHERE id = ?",
                                (trade_id,),
                            )
                            conn.commit()
                            continue
                        elif actual_status in ["CANCELED", "EXPIRED", "NOT_FOUND"]:
                            # Order already cancelled/expired, settle it
                            log(f"ℹ️ Order already {actual_status}, settling trade")
                            c.execute(
                                "UPDATE trades SET settled = 1, final_outcome = ?, order_status = ? WHERE id = ?",
                                (
                                    f"CANCELLED_UNFILLED_{actual_status}",
                                    actual_status,
                                    trade_id,
                                ),
                            )
                            conn.commit()
                            continue
                        elif actual_status == "ERROR":
                            # Can't determine status (API error) - mark to prevent infinite retry
                            log(
                                f"⚠️ Can't determine order status, marking as CANCEL_ATTEMPTED to prevent spam"
                            )
                            c.execute(
                                "UPDATE trades SET order_status = 'CANCEL_ATTEMPTED' WHERE id = ?",
                                (trade_id,),
                            )
                            conn.commit()
                            continue
                        else:
                            # Unknown state (LIVE, DELAYED, etc.), mark to prevent spam
                            c.execute(
                                "UPDATE trades SET order_status = 'CANCEL_ATTEMPTED' WHERE id = ?",
                                (trade_id,),
                            )
                            conn.commit()
                            continue

                    continue

            # ============================================================
            # TAKE PROFIT: Check if profit target is hit
            # ============================================================
            closed = _check_take_profit(
                symbol=symbol,
                trade_id=trade_id,
                token_id=token_id,
                side=side,
                size=size,
                pnl_pct=pnl_pct,
                pnl_usd=pnl_usd,
                current_price=current_price,
                limit_sell_order_id=limit_sell_order_id,
                c=c,
                conn=conn,
                now=now,
            )
            if closed:
                continue

        except Exception as e:
            log(f"⚠️ Error checking position #{trade_id}: {e}")

        conn.commit()
