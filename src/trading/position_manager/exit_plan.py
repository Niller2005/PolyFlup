"""Exit plan management logic"""

from datetime import datetime
from src.config.settings import (
    ENABLE_EXIT_PLAN,
    EXIT_PRICE_TARGET,
    EXIT_MIN_POSITION_AGE,
)
from src.utils.logger import log
from src.trading.orders import (
    get_order_status,
    get_order,
    cancel_order,
    place_limit_order,
    get_balance_allowance,
    get_orders,
    truncate_float,
    SELL,
)
from .shared import _last_exit_attempt


def _update_exit_plan_after_scale_in(
    symbol, trade_id, token_id, new_size, old_order_id, c, conn
) -> bool:
    """Updates exit plan order with new size after scale-in. Returns True if successful."""
    if not old_order_id:
        # This is fine, maybe exit plan wasn't placed yet (age < 60s)
        return False

    if not ENABLE_EXIT_PLAN:
        return False

    status = get_order_status(old_order_id)
    if status in ["FILLED", "MATCHED"]:
        log(
            f"   ℹ️ [{symbol}] #{trade_id} Exit plan already filled, no need to update for scale-in."
        )
        return True

    log(
        f"   🔄 [{symbol}] #{trade_id} Updating exit plan: {old_order_id[:10]}... -> New size {new_size:.2f}"
    )

    # If we successfully cancel (or it's already gone), try to place new one
    if cancel_order(old_order_id):
        # Clear the old ID first to avoid being stuck if placement fails
        c.execute(
            "UPDATE trades SET limit_sell_order_id = NULL WHERE id = ?",
            (trade_id,),
        )

        # Ensure size is truncated correctly
        truncated_size = truncate_float(new_size, 2)

        res = place_limit_order(token_id, EXIT_PRICE_TARGET, truncated_size, SELL, True)
        if res["success"]:
            new_oid = res.get("order_id")
            c.execute(
                "UPDATE trades SET limit_sell_order_id = ? WHERE id = ?",
                (new_oid, trade_id),
            )
            oid_str = str(new_oid)[:10] if new_oid else "N/A"
            log(
                f"   ✅ [{symbol}] #{trade_id} Updated exit plan for new size: {truncated_size:.2f} (ID: {oid_str}...)"
            )
            return True
        else:
            log(
                f"   ❌ [{symbol}] Failed to place new exit plan after cancel: {res.get('error')}"
            )
            return False
    return False


def _update_exit_plan_to_new_price(
    symbol, trade_id, token_id, size, old_order_id, new_price, c, conn
) -> bool:
    """Updates exit plan order with new price. Returns True if successful."""
    if not old_order_id:
        return False

    if get_order_status(old_order_id) in ["FILLED", "MATCHED"]:
        return True

    if cancel_order(old_order_id):
        # Clear the old ID first
        c.execute(
            "UPDATE trades SET limit_sell_order_id = NULL WHERE id = ?",
            (trade_id,),
        )

        res = place_limit_order(token_id, new_price, size, SELL, True)
        if res["success"]:
            new_oid = res.get("order_id")
            c.execute(
                "UPDATE trades SET limit_sell_order_id = ? WHERE id = ?",
                (new_oid, trade_id),
            )
            return True
        else:
            log(
                f"   ❌ [{symbol}] #{trade_id} Failed to update exit plan price: {res.get('error')}"
            )
            return False
    return False


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

            _last_exit_attempt[trade_id] = now.timestamp()

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
                            return
            except Exception as e:
                if verbose:
                    log(f"   ⚠️ [{symbol}] Error checking existing orders: {e}")

            balance_info = get_balance_allowance(token_id)
            actual_bal = balance_info.get("balance", 0) if balance_info else 0

            if actual_bal < size:
                if actual_bal > 0:
                    # ALWAYS update size if we have some balance but less than expected
                    # This fixes the "stuck" exit plan due to minor rounding differences
                    diff = size - actual_bal
                    log(
                        f"   🔧 [{symbol}] #{trade_id} Adjusting size to match balance: {size:.4f} -> {actual_bal:.4f} (diff: {diff:.6f})"
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
                    _update_exit_plan_after_scale_in(
                        symbol, trade_id, token_id, size, limit_sell_id, c, conn
                    )
            elif o_status in ["CANCELED", "EXPIRED"]:
                log(
                    f"   ⚠️ [{symbol}] #{trade_id} Exit plan order was {o_status}. Clearing from DB to allow retry."
                )
                c.execute(
                    "UPDATE trades SET limit_sell_order_id = NULL WHERE id = ?",
                    (trade_id,),
                )
                limit_sell_id = None
        else:
            # Order not found on exchange
            log(
                f"   ⚠️ [{symbol}] #{trade_id} Exit plan order {limit_sell_id[:10]}... not found on exchange. Clearing."
            )
            c.execute(
                "UPDATE trades SET limit_sell_order_id = NULL WHERE id = ?", (trade_id,)
            )
            limit_sell_id = None

    if verbose and side:
        if buy_status == "EXIT_PLAN_PENDING_SETTLEMENT":
            return  # Don't log monitoring status if already filled and waiting for settlement

        status = f"Trade #{trade_id} {side} PnL={price_change_pct:+.1f}%"
        if scaled_in:
            status += " | 📊 Scaled in"
        if limit_sell_id:
            status += f" | ⏰ Exit active ({age:.0f}s)"
        else:
            wait_text = ""
            if age < EXIT_MIN_POSITION_AGE:
                wait_text = f" (Waiting {EXIT_MIN_POSITION_AGE - age:.0f}s)"
            status += f" | ⏳ Exit pending ({age:.0f}s){wait_text}"
        log(f"  {'📈' if pnl_pct > 0 else '📉'} [{symbol}] {status}")
