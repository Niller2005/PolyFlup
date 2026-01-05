"""Position monitoring and management"""

import time
import threading
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


# Threading lock to prevent concurrent position checks (prevents database locks)
_position_check_lock = threading.Lock()

# Module-level tracking for exit plan attempts to prevent spamming on errors (e.g. balance errors)
_last_exit_attempt = {}


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
    buy_order_status: str,
) -> bool:
    """Check and execute stop loss if triggered, returns True if position closed"""
    # CRITICAL FIX: Check if already settled in this cycle to prevent duplicate processing
    c.execute("SELECT settled FROM trades WHERE id = ?", (trade_id,))
    row = c.fetchone()
    if row and row[0] == 1:
        return True  # Already settled, skip

    # CRITICAL FIX: Don't attempt stop loss if buy order not filled yet
    if not ENABLE_STOP_LOSS or pnl_pct > -STOP_LOSS_PERCENT or size == 0:
        return False

    # CRITICAL FIX: Only sell if we actually own the tokens (buy order filled)
    if buy_order_status not in ["FILLED", "MATCHED", "matched"]:
        return False

    # CRITICAL FIX: Wait at least 30 seconds after order is placed before attempting stop loss
    # This prevents trying to sell tokens that aren't yet available in wallet (Polymarket API delay)
    # This is especially important for reversal orders
    from datetime import datetime
    from zoneinfo import ZoneInfo

    c.execute("SELECT timestamp FROM trades WHERE id = ?", (trade_id,))
    row = c.fetchone()
    if row:
        trade_timestamp = datetime.fromisoformat(row[0])
        position_age = (now - trade_timestamp).total_seconds()
        if position_age < 30:
            return False  # Too young to sell

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
        f"🛑 [{symbol}] #{trade_id} {sl_label}: {pnl_pct:.1f}% PnL | Spot ${current_spot:,.2f} vs Target ${target_price:,.2f}"
    )

    if limit_sell_order_id:
        if cancel_order(limit_sell_order_id):
            log(
                f"[{symbol}] ⏳ Limit sell order cancelled, waiting for tokens to be freed..."
            )
            time.sleep(2)

    sell_result = sell_position(token_id, size, current_price)

    if not sell_result["success"]:
        error_msg = sell_result.get("error", "Unknown error")

        # If position is old enough (>60s) and still can't sell, the order likely never filled
        # Mark it as settled with a special outcome to prevent infinite retry
        if "balance" in error_msg.lower() or "allowance" in error_msg.lower():
            c.execute("SELECT timestamp FROM trades WHERE id = ?", (trade_id,))
            row = c.fetchone()
            if row:
                trade_timestamp = datetime.fromisoformat(row[0])
                position_age = (now - trade_timestamp).total_seconds()

                if position_age > 60:
                    # CRITICAL: Check actual balance before assuming it's zero
                    # This handles cases where Polymarket reports insufficient balance
                    # because the size is slightly off or tokens haven't settled.
                    balance_info = get_balance_allowance(token_id)
                    actual_balance = (
                        balance_info.get("balance", 0) if balance_info else 0
                    )

                    if actual_balance >= 1.0:
                        log(
                            f"⚠️ [{symbol}] Trade #{trade_id}: Sell failed with balance error, but found {actual_balance:.2f} shares. Updating size and retrying next cycle."
                        )
                        # Update size and bet_usd in DB so next attempt uses correct amount
                        actual_balance = round(actual_balance, 6)
                        new_bet_usd = round(entry_price * actual_balance, 4)
                        c.execute(
                            "UPDATE trades SET size = ?, bet_usd = ? WHERE id = ?",
                            (actual_balance, new_bet_usd, trade_id),
                        )
                        return False  # Keep monitoring, don't settle

                    # Position is old and balance is truly zero/minimal - mark as unfilled
                    log(
                        f"⚠️ [{symbol}] Trade #{trade_id}: Can't sell after {position_age:.0f}s - marking as unfilled/cancelled"
                    )
                    c.execute(
                        """UPDATE trades
                           SET settled=1, final_outcome='UNFILLED_NO_BALANCE', 
                               order_status='UNFILLED'
                           WHERE id=?""",
                        (trade_id,),
                    )
                    return True  # Position closed (marked as unfilled)

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
                    # Pass cursor to avoid opening nested connection (database lock)
                    save_trade(
                        cursor=c,
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
                f"   ✅ Updated exit plan: {new_total_size:.2f} shares @ {EXIT_PRICE_TARGET} | ID: {(new_order_id or '')[:10]}..."
            )
            c.execute(
                "UPDATE trades SET limit_sell_order_id = ? WHERE id = ?",
                (new_order_id, trade_id),
            )
        else:
            log(f"   ⚠️ Failed to update exit plan: {new_exit_order.get('error')}")
            # Clear old order ID since we cancelled it
            c.execute(
                "UPDATE trades SET limit_sell_order_id = NULL WHERE id = ?",
                (trade_id,),
            )
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
    side: str = "",
    pnl_pct: float = 0.0,
    price_change_pct: float = 0.0,
    scaled_in: bool = False,
    scale_in_order_id: Optional[str] = None,
) -> None:
    """Check and manage exit plan limit orders"""
    # CRITICAL FIX: MATCHED orders are filled and ready to sell
    if (
        not ENABLE_EXIT_PLAN
        or buy_order_status not in ["FILLED", "MATCHED", "matched"]
        or size == 0
    ):
        return

    position_age_seconds = (now - datetime.fromisoformat(timestamp)).total_seconds()

    # Check cooldown for this trade
    last_attempt = _last_exit_attempt.get(trade_id, 0)
    if now.timestamp() - last_attempt < 30:
        return

    if not limit_sell_order_id and position_age_seconds >= EXIT_MIN_POSITION_AGE:
        # Update last attempt time
        _last_exit_attempt[trade_id] = now.timestamp()

        # CRITICAL FIX: Check if there's already an open order on the CLOB for this token
        # This handles cases where the database might have lost the order_id after a restart
        try:
            from src.trading.orders import get_orders

            open_orders = get_orders(asset_id=token_id)
            if open_orders:
                # We found open orders! Try to find a SELL order
                for o in open_orders:
                    o_side = (
                        o.get("side") if isinstance(o, dict) else getattr(o, "side", "")
                    )
                    if o_side == "SELL":
                        o_id = (
                            o.get("id") if isinstance(o, dict) else getattr(o, "id", "")
                        )
                        emoji = "📈" if pnl_pct >= 0 else "📉"
                        log(
                            f"  {emoji} [{symbol}] Trade #{trade_id} {side} PnL={price_change_pct:+.1f}% | 🔍 Found existing open SELL order {(o_id or '')[:10]}... on exchange"
                        )
                        c.execute(
                            "UPDATE trades SET limit_sell_order_id = ? WHERE id = ?",
                            (o_id, trade_id),
                        )
                        return  # Found and updated, no need to place new one
        except Exception as e:
            log(f"⚠️ Error checking for existing orders: {e}")

        # CRITICAL FIX: Check if we actually have the tokens before placing exit plan
        # Prevents trying to place orders when tokens aren't settled yet
        balance_info = get_balance_allowance(token_id)
        if balance_info:
            actual_balance = balance_info.get("balance", 0)
            if actual_balance < size:
                if verbose:
                    emoji = "📈" if pnl_pct >= 0 else "📉"
                    log(
                        f"  {emoji} [{symbol}] Trade #{trade_id} {side} PnL={price_change_pct:+.1f}% | ⏳ Tokens not yet in wallet (Balance: {actual_balance:.2f} < Size: {size:.2f})"
                    )
                return  # Don't try to place order if we don't have the tokens

        emoji = "📈" if pnl_pct >= 0 else "📉"
        log(
            f"  {emoji} [{symbol}] Trade #{trade_id} {side} PnL={price_change_pct:+.1f}% | 📉 EXIT PLAN: Placing limit sell order at {EXIT_PRICE_TARGET} for {size} units"
        )
        sell_limit_result = place_limit_order(
            token_id=token_id,
            price=EXIT_PRICE_TARGET,
            size=size,
            side=SELL,
            silent_on_balance_error=True,
            order_type="GTC",  # Good-til-cancelled for exit plan
        )

        # CRITICAL FIX: Always save the order_id if we got one, even if success=False
        # Polymarket API sometimes returns errors but still creates the order
        order_id_to_save = sell_limit_result.get("order_id")

        if sell_limit_result["success"] or order_id_to_save:
            if order_id_to_save:
                limit_sell_order_id = order_id_to_save
                log(
                    f"  {emoji} [{symbol}] Trade #{trade_id} {side} PnL={price_change_pct:+.1f}% | ✅ EXIT PLAN: Limit sell order placed: {limit_sell_order_id}"
                )
                c.execute(
                    "UPDATE trades SET limit_sell_order_id = ? WHERE id = ?",
                    (limit_sell_order_id, trade_id),
                )
            else:
                log(
                    f"  {emoji} [{symbol}] Trade #{trade_id} {side} PnL={price_change_pct:+.1f}% | ⚠️ EXIT PLAN: Order succeeded but no order_id returned"
                )
        else:
            error_msg = sell_limit_result.get("error", "Unknown error")

            # Only log if it's NOT a balance/allowance error (those are expected and retry automatically)
            if (
                "balance" not in error_msg.lower()
                and "allowance" not in error_msg.lower()
            ):
                log(
                    f"  {emoji} [{symbol}] Trade #{trade_id} {side} PnL={price_change_pct:+.1f}% | ⚠️ EXIT PLAN: Failed to place limit sell: {error_msg}"
                )
    elif limit_sell_order_id and position_age_seconds >= EXIT_MIN_POSITION_AGE:
        # Exit plan status will be shown in combined position log (verbose cycle)
        pass

    # Log combined position status on verbose cycles
    if verbose and side:  # Only if we have position data
        emoji = "📈" if pnl_pct > 0 else "📉"
        status_parts = [
            f"{emoji} [{symbol}] Trade #{trade_id} {side} PnL={price_change_pct:+.1f}%"
        ]

        # Add scale-in indicator if position was scaled
        if scaled_in:
            status_parts.append("📊 Scaled in")
        elif scale_in_order_id:
            status_parts.append("📋 Scale-in pending")

        # Add exit plan status
        if limit_sell_order_id:
            status_parts.append(f"⏰ Exit plan active ({position_age_seconds:.0f}s)")
        else:
            status_parts.append(
                f"⏳ Exit plan pending ({position_age_seconds:.0f}s/{EXIT_MIN_POSITION_AGE}s)"
            )

        log("  " + " | ".join(status_parts))


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
    side: str = "",
    price_change_pct: float = 0.0,
) -> None:
    """Check and execute scale in if conditions are met, or monitor pending scale-in orders"""
    if not ENABLE_SCALE_IN:
        return

    emoji = "📈" if price_change_pct >= 0 else "📉"
    summary_prefix = (
        f"  {emoji} [{symbol}] Trade #{trade_id} {side} PnL={price_change_pct:+.1f}%"
    )

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
                            f"{summary_prefix} | ✅ SCALE IN FILLED: {size_matched} shares @ ${scale_price:.4f}"
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
                    log(f"{summary_prefix} | ⚠️ SCALE IN: Order was {status}")
                    # Clear the order ID so it can potentially be re-placed
                    c.execute(
                        "UPDATE trades SET scale_in_order_id = NULL WHERE id = ?",
                        (trade_id,),
                    )
                elif status == "LIVE":
                    # Order is live, waiting for fill
                    log(
                        f"{summary_prefix} | 📋 SCALE IN: Order is LIVE, waiting for fill..."
                    )
                    return  # Don't try to place another one
                elif status in ["DELAYED", "UNMATCHED"]:
                    log(f"{summary_prefix} | ℹ️ SCALE IN: Order status: {status}")
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
        f"{summary_prefix} | 📈 SCALE IN triggered: price=${current_price:.2f}, {time_left_seconds:.0f}s left"
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
            f"{summary_prefix} | ✅ SCALE IN order placed: {additional_size:.2f} shares @ ${scale_price:.2f} (status: {order_status})"
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
            log(
                f"   Monitoring scale-in order: {(new_scale_in_order_id or '')[:10]}..."
            )
    else:
        # Enhanced error reporting
        error_msg = scale_result.get("error", "Unknown error")
        log(f"  📈 [{symbol}] #{trade_id} {side} Scale in failed: {error_msg}")


def _check_take_profit(
    symbol: str,
    trade_id: int,
    token_id: str,
    side: str,
    entry_price: float,
    size: float,
    pnl_pct: float,
    pnl_usd: float,
    current_price: float,
    limit_sell_order_id: Optional[str],
    c: Any,
    conn: Any,
    now: datetime,
    buy_order_status: str,
) -> bool:
    """Check and execute take profit if triggered, returns True if position closed"""
    # CRITICAL FIX: Check if already settled in this cycle to prevent duplicate processing
    c.execute("SELECT settled FROM trades WHERE id = ?", (trade_id,))
    row = c.fetchone()
    if row and row[0] == 1:
        return True  # Already settled, skip

    if not ENABLE_TAKE_PROFIT or pnl_pct < TAKE_PROFIT_PERCENT:
        return False

    # CRITICAL FIX: Only sell if we actually own the tokens (buy order filled)
    if buy_order_status not in ["FILLED", "MATCHED", "matched"]:
        return False

    # CRITICAL FIX: Wait at least 30 seconds after order is placed before attempting take profit
    # This prevents trying to sell tokens that aren't yet available in wallet (Polymarket API delay)
    from datetime import datetime
    from zoneinfo import ZoneInfo

    c.execute("SELECT timestamp FROM trades WHERE id = ?", (trade_id,))
    row = c.fetchone()
    if row:
        trade_timestamp = datetime.fromisoformat(row[0])
        position_age = (now - trade_timestamp).total_seconds()
        if position_age < 30:
            return False  # Too young to sell

    log(f"🎯 [{symbol}] #{trade_id} TAKE PROFIT triggered: {pnl_pct:.1f}% gain")

    if limit_sell_order_id:
        if cancel_order(limit_sell_order_id):
            log(
                f"[{symbol}] ⏳ Limit sell order cancelled, waiting for tokens to be freed..."
            )
            time.sleep(2)

    sell_result = sell_position(token_id, size, current_price)

    if not sell_result["success"]:
        error_msg = sell_result.get("error", "Unknown error")

        # If position is old enough (>60s) and still can't sell, the order likely never filled
        # Mark it as settled with a special outcome to prevent infinite retry
        if "balance" in error_msg.lower() or "allowance" in error_msg.lower():
            c.execute("SELECT timestamp FROM trades WHERE id = ?", (trade_id,))
            row = c.fetchone()
            if row:
                trade_timestamp = datetime.fromisoformat(row[0])
                position_age = (now - trade_timestamp).total_seconds()

                if position_age > 60:
                    # CRITICAL: Check actual balance before assuming it's zero
                    # This handles cases where Polymarket reports insufficient balance
                    # because the size is slightly off or tokens haven't settled.
                    balance_info = get_balance_allowance(token_id)
                    actual_balance = (
                        balance_info.get("balance", 0) if balance_info else 0
                    )

                    if actual_balance >= 1.0:
                        log(
                            f"⚠️ [{symbol}] Trade #{trade_id}: Sell failed with balance error, but found {actual_balance:.2f} shares. Updating size and retrying next cycle."
                        )
                        # Update size and bet_usd in DB so next attempt uses correct amount
                        actual_balance = round(actual_balance, 6)
                        new_bet_usd = round(entry_price * actual_balance, 4)
                        c.execute(
                            "UPDATE trades SET size = ?, bet_usd = ? WHERE id = ?",
                            (actual_balance, new_bet_usd, trade_id),
                        )
                        return False  # Keep monitoring, don't settle

                    # Position is old and balance is truly zero/minimal - mark as unfilled
                    log(
                        f"⚠️ [{symbol}] Trade #{trade_id}: Can't sell after {position_age:.0f}s - marking as unfilled/cancelled"
                    )
                    c.execute(
                        """UPDATE trades
                           SET settled=1, final_outcome='UNFILLED_NO_BALANCE', 
                               order_status='UNFILLED'
                           WHERE id=?""",
                        (trade_id,),
                    )
                    return True  # Position closed (marked as unfilled)

        return False

    c.execute(
        """UPDATE trades
           SET exited_early=1, exit_price=?, pnl_usd=?, roi_pct=?,
               final_outcome='TAKE_PROFIT', settled=1, settled_at=?
           WHERE id=?""",
        (current_price, pnl_usd, pnl_pct, now.isoformat(), trade_id),
    )

    send_discord(f"🎯 **TAKE PROFIT** [{symbol}] {side} closed at {pnl_pct:+.1f}%")

    return True


def check_open_positions(verbose: bool = True, check_orders: bool = False):
    """
    Check open positions and manage them

    Args:
        verbose: If True, log position checks. If False, only log actions (stop loss, etc.)
        check_orders: If True, check status of limit sell orders
    """
    # Use lock to prevent concurrent checks (prevents "database is locked" errors)
    if not _position_check_lock.acquire(blocking=False):
        return  # Another check is already running, skip this one

    try:
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
                log(
                    f"👀 Monitoring {len(open_positions)} position{'s' if len(open_positions) > 1 else ''}..."
                )

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
                    # CRITICAL FIX: Skip if already settled in this cycle
                    # This prevents processing trades that were just marked as settled
                    c.execute("SELECT settled FROM trades WHERE id = ?", (trade_id,))
                    row = c.fetchone()
                    if row and row[0] == 1:
                        continue  # Already settled, skip

                    # 0. Check buy order status if not filled
                    current_buy_status = buy_order_status
                    if buy_order_status != "FILLED" and buy_order_id:
                        current_buy_status = get_order_status(buy_order_id)

                        if current_buy_status in ["FILLED", "MATCHED"]:
                            # Get actual filled size if possible
                            actual_size = size
                            try:
                                order_data = get_order(buy_order_id)
                                if order_data:
                                    actual_size = float(
                                        order_data.get("size_matched", size)
                                    )
                                    fill_price = float(
                                        order_data.get("price", entry_price)
                                    )

                                    # Only update if significantly different to avoid rounding noise
                                    if (
                                        abs(actual_size - size) > 0.001
                                        and actual_size > 0
                                    ) or abs(fill_price - entry_price) > 0.0001:
                                        size = actual_size
                                        entry_price = fill_price
                                        bet_usd = entry_price * size
                                        log(
                                            f"📊 [{symbol}] #{trade_id} Updated: size={size:.2f}, price={entry_price:.4f}"
                                        )
                                        c.execute(
                                            "UPDATE trades SET size = ?, entry_price = ?, bet_usd = ? WHERE id = ?",
                                            (size, entry_price, bet_usd, trade_id),
                                        )
                            except Exception as e:
                                log(
                                    f"⚠️ Error updating matched data for #{trade_id}: {e}"
                                )

                            log(
                                f"✅ [{symbol}] #{trade_id} BUY order has been {current_buy_status}"
                            )
                            c.execute(
                                "UPDATE trades SET order_status = 'FILLED' WHERE id = ?",
                                (trade_id,),
                            )
                            current_buy_status = "FILLED"
                        elif current_buy_status in ["CANCELED", "EXPIRED", "NOT_FOUND"]:
                            log(
                                f"⚠️ [{symbol}] #{trade_id} BUY order was {current_buy_status}. Settling trade."
                            )
                            c.execute(
                                "UPDATE trades SET settled = 1, final_outcome = ? WHERE id = ?",
                                (current_buy_status, trade_id),
                            )
                            continue
                        elif current_buy_status in ["DELAYED", "UNMATCHED"]:
                            # Order is pending, log status but continue monitoring
                            if verbose:
                                log(
                                    f"ℹ️ [{symbol}] #{trade_id} BUY order status: {current_buy_status}"
                                )
                        elif current_buy_status == "LIVE":
                            # Order is live on the book, waiting for fill
                            if verbose:
                                log(
                                    f"📋 [{symbol}] #{trade_id} BUY order is LIVE on the book"
                                )
                        elif current_buy_status == "ERROR":
                            log(f"⚠️ [{symbol}] #{trade_id} Error checking BUY order")

                    # Note: Limit sell order placement moved to 60-second mark (after price checks below)

                    # 2. Check if limit sell order was filled (EXIT PLAN MONITORING)
                    if check_orders and limit_sell_order_id:
                        try:
                            # Use enhanced get_order() for detailed information
                            order_data = get_order(limit_sell_order_id)

                            if order_data:
                                status = order_data.get("status", "").upper()

                                if status in ["FILLED", "MATCHED"]:
                                    # Get actual exit price from order data if available
                                    exit_price = float(
                                        order_data.get("price", EXIT_PRICE_TARGET)
                                    )
                                    size_matched = float(
                                        order_data.get("size_matched", size)
                                    )

                                    # Only settle if significantly filled (or matched)
                                    if (
                                        status == "FILLED"
                                        or size_matched >= size * 0.99
                                    ):
                                        exit_pnl_usd = (
                                            exit_price * size_matched
                                        ) - bet_usd
                                        exit_roi_pct = (
                                            (exit_pnl_usd / bet_usd) * 100
                                            if bet_usd > 0
                                            else 0
                                        )

                                        log(
                                            f"💰 [{symbol}] #{trade_id} {side}: {exit_pnl_usd:+.2f}$ ({exit_roi_pct:+.1f}%)"
                                        )

                                        c.execute(
                                            """UPDATE trades 
                                               SET settled=1, exited_early=1, exit_price=?, 
                                                   pnl_usd=?, roi_pct=?, 
                                                   final_outcome='EXIT_PLAN_FILLED', settled_at=? 
                                               WHERE id=?""",
                                            (
                                                exit_price,
                                                exit_pnl_usd,
                                                exit_roi_pct,
                                                now.isoformat(),
                                                trade_id,
                                            ),
                                        )
                                        send_discord(
                                            f"🎯 **EXIT PLAN SUCCESS** [{symbol}] {side} closed at {exit_price} ({exit_roi_pct:+.1f}%)"
                                        )
                                        continue

                                elif status in ["CANCELED", "EXPIRED"]:
                                    log(
                                        f"⚠️ [{symbol}] #{trade_id} EXIT PLAN: Order was {status}"
                                    )
                                    # Clear the limit_sell_order_id so it can be re-placed
                                    c.execute(
                                        "UPDATE trades SET limit_sell_order_id = NULL WHERE id = ?",
                                        (trade_id,),
                                    )
                                elif status == "LIVE" and verbose:
                                    log(
                                        f"📋 [{symbol}] #{trade_id} EXIT PLAN: Order is LIVE at {EXIT_PRICE_TARGET}"
                                    )
                                else:
                                    # Order not found, might be too old
                                    if verbose:
                                        log(
                                            f"⚠️ [{symbol}] #{trade_id} EXIT PLAN: Could not find limit sell order {(limit_sell_order_id or '')[:10]}..."
                                        )

                        except Exception as e:
                            # Order might be too old or other error, log if significant
                            if "404" not in str(e):
                                log(
                                    f"⚠️ [{symbol}] #{trade_id} Error checking exit plan order: {e}"
                                )

                    # Get current market price and calculate P&L
                    pnl_info = _get_position_pnl(token_id, entry_price, size)
                    if not pnl_info:
                        continue

                    current_price = pnl_info["current_price"]
                    pnl_pct = pnl_info["pnl_pct"]
                    pnl_usd = pnl_info["pnl_usd"]
                    price_change_pct = pnl_info["price_change_pct"]

                    # Will log combined status after exit plan check

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
                        buy_order_status=current_buy_status,
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
                    # Re-fetch limit_sell_order_id in case it was updated by a previous position in this cycle
                    c.execute(
                        "SELECT limit_sell_order_id FROM trades WHERE id = ?",
                        (trade_id,),
                    )
                    row = c.fetchone()
                    if row:
                        limit_sell_order_id = row[0]

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
                        side=side,
                        pnl_pct=pnl_pct,
                        price_change_pct=price_change_pct,
                        scaled_in=scaled_in,
                        scale_in_order_id=scale_in_order_id,
                    )

                    # Re-fetch again after _check_exit_plan in case it was updated
                    c.execute(
                        "SELECT limit_sell_order_id FROM trades WHERE id = ?",
                        (trade_id,),
                    )
                    row = c.fetchone()
                    if row:
                        limit_sell_order_id = row[0]

                    # ============================================================
                    # SCALE IN: Check if conditions are met or monitor pending orders
                    # ============================================================
                    # Re-fetch scale_in_order_id in case it was just placed
                    c.execute(
                        "SELECT scale_in_order_id FROM trades WHERE id = ?", (trade_id,)
                    )
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
                        side=side,
                        price_change_pct=price_change_pct,
                    )

                    # ============================================================
                    # UNFILLED ORDER MANAGEMENT
                    # ============================================================
                    if CANCEL_UNFILLED_ORDERS and current_buy_status.upper() not in [
                        "FILLED",
                        "MATCHED",
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
                                    f"⏰ [{symbol}] #{trade_id} timeout: {position_age_seconds:.0f}s old, P&L: {pnl_pct:+.1f}%"
                                )

                            # Check if we're on the winning side with good P&L
                            if UNFILLED_RETRY_ON_WINNING_SIDE and pnl_pct > 10.0:
                                current_spot = get_current_spot_price(symbol)
                                is_on_winning_side = False

                                if current_spot > 0 and target_price is not None:
                                    if side == "UP" and current_spot >= target_price:
                                        is_on_winning_side = True
                                    elif (
                                        side == "DOWN" and current_spot <= target_price
                                    ):
                                        is_on_winning_side = True

                                if is_on_winning_side:
                                    should_reverse = True
                                    cancel_reason += (
                                        f" - On winning side with {pnl_pct:+.1f}% P&L"
                                    )

                        if should_cancel:
                            # Only log once per trade (check if we already tried)
                            c.execute(
                                "SELECT order_status FROM trades WHERE id = ?",
                                (trade_id,),
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
                                f"🛑 [{symbol}] #{trade_id} CANCELLING unfilled order: {cancel_reason}"
                            )
                            cancel_result = cancel_order(buy_order_id)

                            if cancel_result:
                                log(
                                    f"✅ [{symbol}] #{trade_id} Buy order cancelled successfully"
                                )

                                if should_reverse:
                                    # Try to enter at current market price
                                    log(
                                        f"🔄 [{symbol}] #{trade_id} Retrying entry at current market price"
                                    )

                                    # Get current market price
                                    retry_price = current_price
                                    retry_price = round(
                                        max(0.01, min(0.99, retry_price)), 2
                                    )

                                    retry_result = place_order(
                                        token_id, retry_price, size
                                    )

                                    if retry_result["success"]:
                                        log(
                                            f"✅ [{symbol}] #{trade_id} Retry order placed at ${retry_price:.2f} (was ${entry_price:.2f})"
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
                                        send_discord(
                                            f"🔄 **RETRY** [{symbol}] {side} @ ${retry_price:.2f} (was ${entry_price:.2f}, waited {position_age_seconds:.0f}s)"
                                        )
                                        continue
                                    else:
                                        error_msg = retry_result.get(
                                            "error", "Unknown error"
                                        )
                                        log(
                                            f"⚠️ [{symbol}] #{trade_id} Retry order failed: {error_msg}"
                                        )

                                # Mark as cancelled if not retrying or retry failed
                                c.execute(
                                    "UPDATE trades SET settled = 1, final_outcome = 'CANCELLED_UNFILLED', order_status = 'CANCELED' WHERE id = ?",
                                    (trade_id,),
                                )
                            else:
                                # Cancel failed - likely order already filled or cancelled
                                # Update status so we don't keep trying
                                log(
                                    f"ℹ️ [{symbol}] #{trade_id} Cancel returned False - checking status..."
                                )

                                # Re-check order status
                                actual_status = get_order_status(buy_order_id)
                                log(f"   Order status: {actual_status}")

                                if actual_status.upper() in ["FILLED", "MATCHED"]:
                                    # Order was filled! Update status and continue monitoring
                                    log(
                                        f"✅ [{symbol}] #{trade_id} Order was actually FILLED"
                                    )
                                    c.execute(
                                        "UPDATE trades SET order_status = 'FILLED' WHERE id = ?",
                                        (trade_id,),
                                    )
                                    continue
                                elif actual_status in [
                                    "CANCELED",
                                    "EXPIRED",
                                    "NOT_FOUND",
                                ]:
                                    # Order already cancelled/expired, settle it
                                    log(
                                        f"ℹ️ [{symbol}] #{trade_id} Order already {actual_status}, settling trade"
                                    )
                                    c.execute(
                                        "UPDATE trades SET settled = 1, final_outcome = ?, order_status = ? WHERE id = ?",
                                        (
                                            f"CANCELLED_UNFILLED_{actual_status}",
                                            actual_status,
                                            trade_id,
                                        ),
                                    )
                                    continue
                                elif actual_status == "ERROR":
                                    # Can't determine status (API error) - mark to prevent infinite retry
                                    log(
                                        f"⚠️ [{symbol}] #{trade_id} Can't determine order status, marking as CANCEL_ATTEMPTED"
                                    )
                                    c.execute(
                                        "UPDATE trades SET order_status = 'CANCEL_ATTEMPTED' WHERE id = ?",
                                        (trade_id,),
                                    )
                                    continue
                                else:
                                    # Unknown state (LIVE, DELAYED, etc.), mark to prevent spam
                                    c.execute(
                                        "UPDATE trades SET order_status = 'CANCEL_ATTEMPTED' WHERE id = ?",
                                        (trade_id,),
                                    )
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
                        entry_price=entry_price,
                        size=size,
                        pnl_pct=pnl_pct,
                        pnl_usd=pnl_usd,
                        current_price=current_price,
                        limit_sell_order_id=limit_sell_order_id,
                        c=c,
                        conn=conn,
                        now=now,
                        buy_order_status=current_buy_status,
                    )
                    if closed:
                        continue

                except Exception as e:
                    log(f"⚠️ [{symbol}] #{trade_id} Error checking position: {e}")

    finally:
        _position_check_lock.release()
