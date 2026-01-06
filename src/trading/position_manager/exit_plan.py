"""Exit plan management logic"""

from datetime import datetime
from src.config.settings import (
    ENABLE_EXIT_PLAN,
    EXIT_PRICE_TARGET,
    EXIT_MIN_POSITION_AGE,
)
from src.utils.logger import log
from src.trading.orders import (
    get_order,
    cancel_order,
    place_limit_order,
    get_balance_allowance,
    get_orders,
    truncate_float,
    SELL,
)


from .shared import _last_exit_attempt


def _check_exit_plan(
    symbol,
    trade_id,
    token_id,
    size,
    buy_status,
    limit_sell_id,
    ts,
    c,
    conn,
    now,
    verbose,
    side,
    pnl_pct,
    price_change_pct,
    scaled_in,
    scale_in_id,
    entry,
    current_price,
    check_orders=False,
    is_scoring=None,
):
    if not ENABLE_EXIT_PLAN or buy_status not in ["FILLED", "MATCHED"] or size == 0:
        return
    try:
        age = (now - datetime.fromisoformat(ts)).total_seconds()
    except:
        age = 0
    last_att = _last_exit_attempt.get(trade_id, 0)
    on_cd = now.timestamp() - last_att < 30

    if not limit_sell_id:
        if age >= EXIT_MIN_POSITION_AGE:
            if on_cd:
                if verbose:
                    log(
                        f"   ⏳ [{symbol}] Exit plan cooldown: {30 - (now.timestamp() - last_att):.0f}s left (Trade age: {age:.0f}s)"
                    )
                return

            # Check for existing SELL orders on exchange before placing new one
            try:
                open_orders = get_orders(asset_id=token_id)
                for o in open_orders:
                    o_side = (
                        o.get("side") if isinstance(o, dict) else getattr(o, "side", "")
                    )
                    if o_side == "SELL":
                        oid = (
                            o.get("id") if isinstance(o, dict) else getattr(o, "id", "")
                        )
                        if oid:
                            oid_str = str(oid)
                            log(
                                f"   📥 [{symbol}] Found existing exit order on exchange: {oid_str[:10]}... Adopting."
                            )
                            c.execute(
                                "UPDATE trades SET limit_sell_order_id = ? WHERE id = ?",
                                (oid_str, trade_id),
                            )
                            _last_exit_attempt[trade_id] = now.timestamp()
                            return
            except Exception as e:
                if verbose:
                    log(f"   ⚠️ [{symbol}] Error checking existing orders: {e}")

            balance_info = get_balance_allowance(token_id)
            actual_bal = balance_info.get("balance", 0) if balance_info else 0

            # BI-DIRECTIONAL HEALING: Sync DB size with actual wallet balance
            if abs(actual_bal - size) > 0.01:
                if actual_bal > 0:
                    log(
                        f"   🔧 [{symbol}] #{trade_id} Syncing size to match balance: {size:.2f} -> {actual_bal:.2f}"
                    )
                    c.execute(
                        "UPDATE trades SET size = ?, bet_usd = ? * entry_price WHERE id = ?",
                        (actual_bal, actual_bal, trade_id),
                    )
                    size = actual_bal
                else:
                    # Balance is 0
                    if age > 300:
                        log(
                            f"   ⚠️ [{symbol}] #{trade_id} has 0 balance after 5m. Settling as ghost trade."
                        )
                        c.execute(
                            "UPDATE trades SET settled=1, final_outcome='GHOST_TRADE_ZERO_BAL' WHERE id=?",
                            (trade_id,),
                        )
                        return
                    if verbose:
                        log(
                            f"   ⏳ [{symbol}] #{trade_id} Exit pending: Balance is 0.0 (waiting for API sync...)"
                        )
                    return

            res = place_limit_order(token_id, EXIT_PRICE_TARGET, size, SELL)
            if res["success"] or res.get("order_id"):
                oid = res.get("order_id")
                c.execute(
                    "UPDATE trades SET limit_sell_order_id = ? WHERE id = ?",
                    (oid, trade_id),
                )
                limit_sell_id = oid
                _last_exit_attempt[trade_id] = now.timestamp()
            else:
                err = res.get("error", "Unknown error")
                log(f"   ❌ [{symbol}] Failed to place exit plan: {err}")
                if "Insufficient funds" in str(err):
                    # Clear cooldown to retry immediately after balance re-sync in next cycle
                    _last_exit_attempt.pop(trade_id, None)
                else:
                    _last_exit_attempt[trade_id] = now.timestamp()

    elif check_orders:
        # Existing exit plan - verify it's correct and still active
        o_data = get_order(limit_sell_id)
        if o_data:
            o_status = o_data.get("status", "").upper()
            if o_status in ["FILLED", "MATCHED"]:
                # Will be handled by the next cycle's main loop check
                pass
            elif o_status == "LIVE":
                # Self-healing: Ensure exit plan size matches current trade size
                o_size = float(o_data.get("original_size", 0))
                if truncate_float(o_size, 2) != truncate_float(size, 2):
                    log(
                        f"   🔧 [{symbol}] #{trade_id} Exit plan size mismatch: {o_size} != {size}. Repairing..."
                    )
                    # Clear from DB immediately to allow retry in next cycle, cancel fire-and-forget
                    cancel_order(limit_sell_id)
                    c.execute(
                        "UPDATE trades SET limit_sell_order_id = NULL WHERE id = ?",
                        (trade_id,),
                    )
                    _last_exit_attempt.pop(trade_id, None)  # Retry immediately
                    return  # Exit early to avoid further checks on cancelled order
            elif o_status in ["CANCELED", "EXPIRED"]:
                log(
                    f"   ⚠️ [{symbol}] #{trade_id} Exit plan order was {o_status}. Clearing from DB to allow retry."
                )
                c.execute(
                    "UPDATE trades SET limit_sell_order_id = NULL WHERE id = ?",
                    (trade_id,),
                )
                limit_sell_id = None
                _last_exit_attempt.pop(trade_id, None)  # Retry immediately
        else:
            # Order not found on exchange
            log(
                f"   ⚠️ [{symbol}] #{trade_id} Exit plan order {limit_sell_id[:10]}... not found on exchange. Clearing."
            )
            c.execute(
                "UPDATE trades SET limit_sell_order_id = NULL WHERE id = ?", (trade_id,)
            )
            limit_sell_id = None
            _last_exit_attempt.pop(trade_id, None)  # Retry immediately

    if verbose and side:
        if buy_status == "EXIT_PLAN_PENDING_SETTLEMENT":
            return  # Don't log monitoring status if already filled and waiting for settlement

        status = f"Trade #{trade_id} {side} PnL={price_change_pct:+.1f}%"
        if scaled_in:
            status += " | 📊 Scaled in"
        if limit_sell_id:
            status += " | ⏰ Exit active"
        else:
            status += " | ⏳ Exit pending"
        log(f"  {'📈' if pnl_pct > 0 else '📉'} [{symbol}] {status}")
