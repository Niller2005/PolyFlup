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
    get_current_positions,
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
from src.utils.websocket_manager import ws_manager


# Threading lock to prevent concurrent position checks (prevents database locks)
_position_check_lock = threading.Lock()

# Module-level tracking for exit plan attempts to prevent spamming on errors (e.g. balance errors)
_last_exit_attempt = {}


def sync_positions_with_exchange(user_address: str):
    """
    Sync database state with actual positions on the exchange.
    Ensures size and entry prices are accurate and handles missing/extra positions.
    """
    log(f"🔄 Syncing positions with exchange for {user_address[:10]}...")

    try:
        # 1. Get positions from Gamma API
        exchange_positions = get_current_positions(user_address)

        # Create a map of token_id -> position_data for easy lookup
        pos_map = {
            p.get("assetId"): p
            for p in exchange_positions
            if float(p.get("size", 0)) > 0
        }

        with db_connection() as conn:
            c = conn.cursor()
            now = datetime.now(tz=ZoneInfo("UTC"))

            # 2. Get all open trades from DB
            c.execute(
                "SELECT id, symbol, side, size, token_id, entry_price FROM trades WHERE settled = 0"
            )
            db_trades = c.fetchall()

            db_token_ids = []

            for trade_id, symbol, side, db_size, token_id, db_entry in db_trades:
                db_token_ids.append(token_id)

                if token_id in pos_map:
                    pos = pos_map[token_id]
                    actual_size = float(pos.get("size", 0))
                    actual_price = float(pos.get("avgPrice", db_entry))

                    # Check for significant size mismatch
                    if abs(actual_size - db_size) > 0.001:
                        log(
                            f"   📊 [{symbol}] #{trade_id} Sync: Size mismatch {db_size:.2f} -> {actual_size:.2f}"
                        )
                        c.execute(
                            "UPDATE trades SET size = ?, bet_usd = ? * ? WHERE id = ?",
                            (actual_size, actual_size, actual_price, trade_id),
                        )

                    # Check for entry price mismatch
                    if abs(actual_price - db_entry) > 0.0001:
                        log(
                            f"   📊 [{symbol}] #{trade_id} Sync: Price mismatch ${db_entry:.4f} -> ${actual_price:.4f}"
                        )
                        c.execute(
                            "UPDATE trades SET entry_price = ?, bet_usd = ? * ? WHERE id = ?",
                            (actual_price, actual_size, actual_price, trade_id),
                        )
                else:
                    # Trade is open in DB but not on exchange
                    # Check age - give it 2 minutes to fill before marking as closed
                    c.execute("SELECT timestamp FROM trades WHERE id = ?", (trade_id,))
                    ts_row = c.fetchone()
                    if ts_row:
                        trade_ts = datetime.fromisoformat(ts_row[0])
                        age_mins = (now - trade_ts).total_seconds() / 60.0

                        if age_mins > 2.0:
                            log(
                                f"   ⚠️ [{symbol}] #{trade_id} exists in DB but not on exchange (size 0). Marking as settled/unfilled."
                            )
                            c.execute(
                                "UPDATE trades SET settled = 1, final_outcome = 'SYNC_MISSING' WHERE id = ?",
                                (trade_id,),
                            )

            # 3. Check for untracked positions
            for token_id, pos in pos_map.items():
                if token_id not in db_token_ids:
                    log(
                        f"   ⚠️ Found UNTRACKED position: {pos.get('size')} shares of {token_id[:10]}..."
                    )

        log("✓ Position sync complete")
    except Exception as e:
        log(f"⚠️ Error during position sync: {e}")


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
            """SELECT id, symbol, side, entry_price, size, bet_usd, window_end, order_status, timestamp, token_id
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

    # Prepare for WebSocket subscription
    tokens_to_subscribe = []

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
        token_id,
    ) in open_positions:
        window_end_dt = datetime.fromisoformat(window_end)
        time_left = (window_end_dt - now).total_seconds() / 60.0  # minutes

        log(
            f"  [{symbol}] Trade #{trade_id} {side}: ${bet_usd:.2f} @ ${entry_price:.4f} | Status: {order_status} | {time_left:.0f}m left"
        )

        if token_id:
            tokens_to_subscribe.append(token_id)

    # Register tokens for real-time updates
    if tokens_to_subscribe:
        ws_manager.subscribe_to_prices(tokens_to_subscribe)

    log("=" * 90)
    log(f"✓ Position monitoring ACTIVE for {len(open_positions)} positions")
    log("=" * 90)


def _get_position_pnl(token_id: str, entry_price: float, size: float) -> Optional[dict]:
    """Get current market price and calculate P&L"""
    # 1. Try to get price from WebSocket cache (instant)
    current_price = ws_manager.get_price(token_id)

    # 2. Fallback to midpoint API (more accurate but slower)
    if current_price is None:
        current_price = get_midpoint(token_id)

    # 3. Last fallback to order book calculation
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
        if "balance" in error_msg.lower() or "allowance" in error_msg.lower():
            c.execute("SELECT timestamp FROM trades WHERE id = ?", (trade_id,))
            row = c.fetchone()
            if row:
                trade_timestamp = datetime.fromisoformat(row[0])
                position_age = (now - trade_timestamp).total_seconds()

                if position_age > 60:
                    balance_info = get_balance_allowance(token_id)
                    actual_balance = (
                        balance_info.get("balance", 0) if balance_info else 0
                    )

                    if actual_balance >= 1.0:
                        log(
                            f"⚠️ [{symbol}] Trade #{trade_id}: Sell failed with balance error, but found {actual_balance:.2f} shares. Updating size and retrying next cycle."
                        )
                        actual_balance = round(actual_balance, 6)
                        new_bet_usd = round(entry_price * actual_balance, 4)
                        c.execute(
                            "UPDATE trades SET size = ?, bet_usd = ? WHERE id = ?",
                            (actual_balance, new_bet_usd, trade_id),
                        )
                        return False

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
                    return True

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
            opposite_price = round(max(0.01, min(0.99, opposite_price)), 2)

            reverse_result = place_order(opposite_token, opposite_price, size)

            if reverse_result["success"]:
                send_discord(
                    f"🔄 **REVERSED** [{symbol}] {side} → {opposite_side} (Target: ${target_price:,.2f}, Spot: ${current_spot:,.2f})"
                )

                try:
                    window_start, window_end = get_window_times(symbol.split("-")[0])
                    bet_usd_effective = size * opposite_price
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

    status = get_order_status(limit_sell_order_id)
    if status in ["FILLED", "MATCHED"]:
        log(f"   🎯 Old exit plan already filled, skipping update")
        return

    cancel_result = cancel_order(limit_sell_order_id)

    if cancel_result:
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
    entry_price: float = 0.0,
) -> None:
    """Check and manage exit plan limit orders"""
    if (
        not ENABLE_EXIT_PLAN
        or buy_order_status not in ["FILLED", "MATCHED", "matched"]
        or size == 0
    ):
        return

    position_age_seconds = (now - datetime.fromisoformat(timestamp)).total_seconds()

    last_attempt = _last_exit_attempt.get(trade_id, 0)
    on_cooldown = now.timestamp() - last_attempt < 30

    should_attempt = (
        not limit_sell_order_id
        and position_age_seconds >= EXIT_MIN_POSITION_AGE
        and not on_cooldown
    )

    if should_attempt:
        _last_exit_attempt[trade_id] = now.timestamp()

        try:
            from src.trading.orders import get_orders

            open_orders = get_orders(asset_id=token_id)
            if open_orders:
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
                        limit_sell_order_id = o_id
                        should_attempt = False
        except Exception as e:
            log(f"⚠️ Error checking for existing orders: {e}")

    if should_attempt:
        balance_info = get_balance_allowance(token_id)
        if balance_info:
            actual_balance = balance_info.get("balance", 0)
            if actual_balance < size:
                if verbose or (actual_balance == 0 and position_age_seconds > 120):
                    emoji = "📈" if pnl_pct >= 0 else "📉"
                    log(
                        f"  {emoji} [{symbol}] Trade #{trade_id} {side} PnL={price_change_pct:+.1f}% | ⏳ Tokens not yet in wallet (Balance: {actual_balance:.2f} < Size: {size:.2f})"
                    )
                if actual_balance > 0 and abs(actual_balance - size) > 0.001:
                    log(
                        f"   🔄 Fixing size mismatch for #{trade_id}: {size:.2f} -> {actual_balance:.2f}"
                    )
                    c.execute(
                        "UPDATE trades SET size = ? WHERE id = ?",
                        (actual_balance, trade_id),
                    )
                    size = actual_balance

                if actual_balance == 0 and position_age_seconds > 300:
                    log(
                        f"   ⚠️ Trade #{trade_id} has 0 balance after 5m. Marking as UNFILLED."
                    )
                    c.execute(
                        "UPDATE trades SET settled=1, final_outcome='UNFILLED_TIMEOUT', order_status='UNFILLED' WHERE id=?",
                        (trade_id,),
                    )
                    return

                should_attempt = False

    if should_attempt:
        emoji = "📈" if pnl_pct >= 0 else "📉"
        log(
            f"  {emoji} [{symbol}] Trade #{trade_id} {side} PnL={price_change_pct:+.1f}% | 📉 EXIT PLAN: Placing limit sell order at {EXIT_PRICE_TARGET} for {size} units"
        )
        sell_limit_result = place_limit_order(
            token_id=token_id,
            price=EXIT_PRICE_TARGET,
            size=size,
            side=SELL,
            silent_on_balance_error=False,
            order_type="GTC",
        )

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
            if "balance" in error_msg.lower() or "allowance" in error_msg.lower():
                balance_info = get_balance_allowance(token_id)
                actual_balance = balance_info.get("balance", 0) if balance_info else 0
                if actual_balance > 0 and abs(actual_balance - size) > 0.001:
                    log(
                        f"   🔄 Fixing size mismatch for #{trade_id}: {size:.2f} -> {actual_balance:.2f} (after error)"
                    )
                    c.execute(
                        "UPDATE trades SET size = ? WHERE id = ?",
                        (actual_balance, trade_id),
                    )
                else:
                    log(
                        f"  {emoji} [{symbol}] Trade #{trade_id} {side} | ⚠️ EXIT PLAN: Failed due to balance (Wallet: {actual_balance:.2f}, DB: {size:.2f})"
                    )
            else:
                log(
                    f"  {emoji} [{symbol}] Trade #{trade_id} {side} PnL={price_change_pct:+.1f}% | ⚠️ EXIT PLAN: Failed to place limit sell: {error_msg}"
                )

    if verbose and side:
        emoji = "📈" if pnl_pct > 0 else "📉"
        status_parts = [
            f"{emoji} [{symbol}] Trade #{trade_id} {side} PnL={price_change_pct:+.1f}%"
        ]

        if scaled_in:
            status_parts.append("📊 Scaled in")
        elif scale_in_order_id:
            status_parts.append("📋 Scale-in pending")

        if limit_sell_order_id:
            status_parts.append(f"⏰ Exit plan active ({position_age_seconds:.0f}s)")
        else:
            age_text = f"{position_age_seconds:.0f}s/{EXIT_MIN_POSITION_AGE}s"
            if on_cooldown:
                cooldown_left = max(0, 30 - (now.timestamp() - last_attempt))
                status_parts.append(
                    f"⏳ Exit plan cooldown ({cooldown_left:.0f}s left) | Age: {age_text}"
                )
            else:
                status_parts.append(f"⏳ Exit plan pending ({age_text})")

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

    if scale_in_order_id and check_orders:
        try:
            order_data = get_order(scale_in_order_id)
            if order_data:
                status = order_data.get("status", "").upper()
                if status in ["FILLED", "MATCHED"]:
                    scale_price = float(order_data.get("price", current_price))
                    size_matched = float(order_data.get("size_matched", 0))

                    if size_matched > 0:
                        log(
                            f"{summary_prefix} | ✅ SCALE IN FILLED: {size_matched} shares @ ${scale_price:.4f}"
                        )
                        new_total_size = size + size_matched
                        additional_bet = size_matched * scale_price
                        new_total_bet = bet_usd + additional_bet
                        new_avg_price = new_total_bet / new_total_size

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
                        _update_exit_plan_after_scale_in(
                            symbol,
                            trade_id,
                            token_id,
                            new_total_size,
                            current_limit_sell_id,
                            c,
                            conn,
                        )
                        send_discord(
                            f"📈 **SCALE IN FILLED** [{symbol}] +${additional_bet:.2f} @ ${scale_price:.2f}"
                        )
                        return
                elif status in ["CANCELED", "EXPIRED"]:
                    log(f"{summary_prefix} | ⚠️ SCALE IN: Order was {status}")
                    c.execute(
                        "UPDATE trades SET scale_in_order_id = NULL WHERE id = ?",
                        (trade_id,),
                    )
                elif status == "LIVE":
                    log(
                        f"{summary_prefix} | 📋 SCALE IN: Order is LIVE, waiting for fill..."
                    )
                    return
                elif status in ["DELAYED", "UNMATCHED"]:
                    log(f"{summary_prefix} | ℹ️ SCALE IN: Order status: {status}")
                    return
        except Exception as e:
            log(f"⚠️ Error checking scale-in order {scale_in_order_id}: {e}")

    if scaled_in or scale_in_order_id:
        return

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
    scale_price = round(max(0.01, min(0.99, current_price)), 2)
    additional_bet = additional_size * scale_price
    scale_result = place_order(token_id, scale_price, additional_size)

    if scale_result["success"]:
        new_scale_in_order_id = scale_result["order_id"]
        order_status = scale_result["status"]
        log(
            f"{summary_prefix} | ✅ SCALE IN order placed: {additional_size:.2f} shares @ ${scale_price:.2f} (status: {order_status})"
        )

        if order_status.lower() in ["filled", "matched"]:
            new_total_size = size + additional_size
            new_total_bet = bet_usd + additional_bet
            new_avg_price = new_total_bet / new_total_size
            c.execute(
                """UPDATE trades
                   SET size=?, bet_usd=?, entry_price=?, scaled_in=1, scale_in_order_id=NULL
                   WHERE id=?""",
                (new_total_size, new_total_bet, new_avg_price, trade_id),
            )
            c.execute(
                "SELECT limit_sell_order_id FROM trades WHERE id = ?", (trade_id,)
            )
            row = c.fetchone()
            current_limit_sell_id = row[0] if row else None
            _update_exit_plan_after_scale_in(
                symbol,
                trade_id,
                token_id,
                new_total_size,
                current_limit_sell_id,
                c,
                conn,
            )
            send_discord(
                f"📈 **SCALED IN** [{symbol}] +${additional_bet:.2f} @ ${scale_price:.2f}"
            )
        else:
            c.execute(
                "UPDATE trades SET scale_in_order_id = ? WHERE id = ?",
                (new_scale_in_order_id, trade_id),
            )
    else:
        log(
            f"  📈 [{symbol}] #{trade_id} {side} Scale in failed: {scale_result.get('error')}"
        )


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
    c.execute("SELECT settled FROM trades WHERE id = ?", (trade_id,))
    row = c.fetchone()
    if row and row[0] == 1:
        return True

    if not ENABLE_TAKE_PROFIT or pnl_pct < TAKE_PROFIT_PERCENT:
        return False

    if buy_order_status not in ["FILLED", "MATCHED", "matched"]:
        return False

    c.execute("SELECT timestamp FROM trades WHERE id = ?", (trade_id,))
    row = c.fetchone()
    if row:
        trade_timestamp = datetime.fromisoformat(row[0])
        position_age = (now - trade_timestamp).total_seconds()
        if position_age < 30:
            return False

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
        if "balance" in error_msg.lower() or "allowance" in error_msg.lower():
            c.execute("SELECT timestamp FROM trades WHERE id = ?", (trade_id,))
            row = c.fetchone()
            if row:
                trade_timestamp = datetime.fromisoformat(row[0])
                position_age = (now - trade_timestamp).total_seconds()
                if position_age > 60:
                    balance_info = get_balance_allowance(token_id)
                    actual_balance = (
                        balance_info.get("balance", 0) if balance_info else 0
                    )
                    if actual_balance >= 1.0:
                        log(
                            f"⚠️ [{symbol}] Trade #{trade_id}: Sell failed with balance error, but found {actual_balance:.2f} shares. Updating size."
                        )
                        actual_balance = round(actual_balance, 6)
                        new_bet_usd = round(entry_price * actual_balance, 4)
                        c.execute(
                            "UPDATE trades SET size = ?, bet_usd = ? WHERE id = ?",
                            (actual_balance, new_bet_usd, trade_id),
                        )
                        return False
                    c.execute(
                        "UPDATE trades SET settled=1, final_outcome='UNFILLED_NO_BALANCE', order_status='UNFILLED' WHERE id=?",
                        (trade_id,),
                    )
                    return True
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
    """Check open positions and manage them"""
    if not _position_check_lock.acquire(blocking=False):
        return

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
                    c.execute("SELECT settled FROM trades WHERE id = ?", (trade_id,))
                    row = c.fetchone()
                    if row and row[0] == 1:
                        continue

                    current_buy_status = buy_order_status
                    if buy_order_id and (
                        buy_order_status != "FILLED" or entry_price == 0
                    ):
                        current_buy_status = get_order_status(buy_order_id)
                        if current_buy_status in ["FILLED", "MATCHED"]:
                            order_data = get_order(buy_order_id)
                            if order_data:
                                actual_size = float(
                                    order_data.get("size_matched", size)
                                )
                                fill_price = float(order_data.get("price", entry_price))
                                if (
                                    abs(actual_size - size) > 0.001 and actual_size > 0
                                ) or abs(fill_price - entry_price) > 0.0001:
                                    size, entry_price = actual_size, fill_price
                                    bet_usd = entry_price * size
                                    c.execute(
                                        "UPDATE trades SET size = ?, entry_price = ?, bet_usd = ? WHERE id = ?",
                                        (size, entry_price, bet_usd, trade_id),
                                    )
                            c.execute(
                                "UPDATE trades SET order_status = 'FILLED' WHERE id = ?",
                                (trade_id,),
                            )
                            current_buy_status = "FILLED"
                        elif current_buy_status in ["CANCELED", "EXPIRED", "NOT_FOUND"]:
                            c.execute(
                                "UPDATE trades SET settled = 1, final_outcome = ? WHERE id = ?",
                                (current_buy_status, trade_id),
                            )
                            continue

                    if check_orders and limit_sell_order_id:
                        order_data = get_order(limit_sell_order_id)
                        if order_data:
                            status = order_data.get("status", "").upper()
                            if status in ["FILLED", "MATCHED"]:
                                exit_price = float(
                                    order_data.get("price", EXIT_PRICE_TARGET)
                                )
                                size_matched = float(
                                    order_data.get("size_matched", size)
                                )
                                if (
                                    status in ["FILLED", "MATCHED"]
                                    or size_matched >= size * 0.99
                                ):
                                    exit_pnl_usd = (exit_price * size_matched) - bet_usd
                                    exit_roi_pct = (
                                        (exit_pnl_usd / bet_usd) * 100
                                        if bet_usd > 0
                                        else 0
                                    )
                                    log(
                                        f"💰 [{symbol}] #{trade_id} {side}: {exit_pnl_usd:+.2f}$ ({exit_roi_pct:+.1f}%)"
                                    )
                                    c.execute(
                                        "UPDATE trades SET settled=1, exited_early=1, exit_price=?, pnl_usd=?, roi_pct=?, final_outcome='EXIT_PLAN_FILLED', settled_at=? WHERE id=?",
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
                                c.execute(
                                    "UPDATE trades SET limit_sell_order_id = NULL WHERE id = ?",
                                    (trade_id,),
                                )

                    pnl_info = _get_position_pnl(token_id, entry_price, size)
                    if not pnl_info:
                        continue

                    current_price, pnl_pct, pnl_usd, price_change_pct = (
                        pnl_info["current_price"],
                        pnl_info["pnl_pct"],
                        pnl_info["pnl_usd"],
                        pnl_info["price_change_pct"],
                    )
                    if _check_stop_loss(
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
                        current_buy_status,
                    ):
                        continue

                    if isinstance(window_end, str):
                        window_end_dt = datetime.fromisoformat(window_end)
                    else:
                        window_end_dt = window_end
                    time_left_seconds = (window_end_dt - now).total_seconds()

                    c.execute(
                        "SELECT scale_in_order_id FROM trades WHERE id = ?", (trade_id,)
                    )
                    row = c.fetchone()
                    if row:
                        scale_in_order_id = row[0]
                    _check_scale_in(
                        symbol,
                        trade_id,
                        token_id,
                        entry_price,
                        size,
                        bet_usd,
                        scaled_in,
                        scale_in_order_id,
                        time_left_seconds,
                        current_price,
                        check_orders,
                        c,
                        conn,
                        side,
                        price_change_pct,
                    )

                    c.execute(
                        "SELECT size, entry_price, bet_usd, scaled_in FROM trades WHERE id = ?",
                        (trade_id,),
                    )
                    row = c.fetchone()
                    if row:
                        size, entry_price, bet_usd, scaled_in = row

                    c.execute(
                        "SELECT limit_sell_order_id FROM trades WHERE id = ?",
                        (trade_id,),
                    )
                    row = c.fetchone()
                    if row:
                        limit_sell_order_id = row[0]
                    _check_exit_plan(
                        symbol,
                        trade_id,
                        token_id,
                        size,
                        current_buy_status,
                        limit_sell_order_id,
                        timestamp,
                        c,
                        conn,
                        now,
                        verbose,
                        side,
                        pnl_pct,
                        price_change_pct,
                        scaled_in,
                        scale_in_order_id,
                        entry_price,
                    )

                    if CANCEL_UNFILLED_ORDERS and current_buy_status.upper() not in [
                        "FILLED",
                        "MATCHED",
                    ]:
                        age_sec = (
                            now - datetime.fromisoformat(timestamp)
                        ).total_seconds()
                        if (
                            price_change_pct <= -UNFILLED_CANCEL_THRESHOLD
                            or age_sec > UNFILLED_TIMEOUT_SECONDS
                        ):
                            c.execute(
                                "SELECT order_status FROM trades WHERE id = ?",
                                (trade_id,),
                            )
                            s_row = c.fetchone()
                            if (
                                s_row
                                and s_row[0]
                                and s_row[0].upper() in ["LIVE", "DELAYED", "UNMATCHED"]
                            ):
                                if cancel_order(buy_order_id):
                                    c.execute(
                                        "UPDATE trades SET settled = 1, final_outcome = 'CANCELLED_UNFILLED', order_status = 'CANCELED' WHERE id = ?",
                                        (trade_id,),
                                    )

                    if _check_take_profit(
                        symbol,
                        trade_id,
                        token_id,
                        side,
                        entry_price,
                        size,
                        pnl_pct,
                        pnl_usd,
                        current_price,
                        limit_sell_order_id,
                        c,
                        conn,
                        now,
                        current_buy_status,
                    ):
                        continue
                except Exception as e:
                    log(f"⚠️ [{symbol}] #{trade_id} Error checking position: {e}")
    finally:
        _position_check_lock.release()
