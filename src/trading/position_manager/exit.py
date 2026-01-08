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
    get_enhanced_balance_allowance,
    get_orders,
    truncate_float,
    SELL,
)
from src.trading.logic import MIN_SIZE

from .shared import _last_exit_attempt


def get_optimal_exit_price(
    entry_price: float, confidence: float, current_price: float, side: str
) -> float:
    """
    Calculate optimal exit price based on confidence and market conditions.
    For high confidence trades, use 0.999 to maximize profit.
    """
    # High confidence threshold for 0.999 exit price
    HIGH_CONFIDENCE_THRESHOLD = 0.85

    # Only use 0.999 for high confidence trades that are winning
    if confidence >= HIGH_CONFIDENCE_THRESHOLD:
        if side == "UP" and current_price >= entry_price:
            return 0.999  # High confidence UP trade thats winning
        elif side == "DOWN" and current_price <= entry_price:
            return 0.999  # High confidence DOWN trade thats winning

    # Default to standard exit price target
    return EXIT_PRICE_TARGET


def _check_exit_plan(
    user_address,
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
    cur_p,
    check_orders=False,
    confidence=0.0,
    is_scoring=None,
    last_scale_in_at=None,
):
    if not ENABLE_EXIT_PLAN or buy_status not in ["FILLED", "MATCHED"] or size == 0:
        return False

    # Early check: Skip exit plans for positions less than MIN_SIZE
    if size < MIN_SIZE:
        if verbose:
            log(
                f"   ⏭️  [{symbol}] #{trade_id} Position size {size:.4f} < {MIN_SIZE}. Skipping exit plan."
            )
        return False

    repaired = False
    # Use enhanced balance validation for better reliability
    c.execute("SELECT timestamp FROM trades WHERE id = ?", (trade_id,))
    trade_timestamp = None
    if row := c.fetchone():
        try:
            trade_timestamp = datetime.fromisoformat(row[0])
        except:
            pass
    
    trade_age_seconds = (now - trade_timestamp).total_seconds() if trade_timestamp else 0
    enhanced_balance_info = get_enhanced_balance_allowance(token_id, symbol, user_address, trade_age_seconds)
    actual_bal = enhanced_balance_info.get("balance", 0)

    try:
        age = (now - datetime.fromisoformat(ts)).total_seconds()
    except:
        age = 0
    last_att = _last_exit_attempt.get(trade_id, 0)
    on_cd = now.timestamp() - last_att < 10

    if not limit_sell_id:
        if True:  # Removed EXIT_MIN_POSITION_AGE check for immediate placement
            if on_cd:
                if verbose:
                    log(
                        f"   ⏳ [{symbol}] Exit plan cooldown: {10 - (now.timestamp() - last_att):.0f}s left (Trade age: {age:.0f}s)"
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
                    log(f"   ⚠️  [{symbol}] Error checking existing orders: {e}")

            # BI-DIRECTIONAL HEALING: Sync DB size with actual wallet balance
            # Add 60s cooldown after buy/scale-in to allow API to sync balance
            # BUT: If balance is HIGHER than size, we can sync immediately (we bought more)
            scale_in_age = 999
            if last_scale_in_at:
                try:
                    scale_in_age = (
                        now - datetime.fromisoformat(last_scale_in_at)
                    ).total_seconds()
                except:
                    pass

            needs_sync = False
            if actual_bal > size + 0.0001:
                needs_sync = True  # Always sync if we have more tokens than DB thinks
            elif age > 60 and scale_in_age > 60 and abs(actual_bal - size) > 0.0001:
                needs_sync = True  # Periodic sync for other cases

            if needs_sync:
                if actual_bal > 0:
                    log(
                        f"   🔧 [{symbol}] #{trade_id} Syncing size to match balance: {size:.4f} -> {actual_bal:.4f}"
                    )
                    c.execute(
                        "UPDATE trades SET size = ?, bet_usd = ? * entry_price WHERE id = ?",
                        (actual_bal, actual_bal, trade_id),
                    )
                    size = actual_bal
                else:
                    # Balance is 0
                    if age > 600:  # Increased from 300 to 600 seconds (10 minutes)
                        log(
                            f"   ⚠️  [{symbol}] #{trade_id} has 0 balance after 5m. Settling as ghost trade."
                        )
                        c.execute(
                            "UPDATE trades SET settled=1, final_outcome='GHOST_TRADE_ZERO_BAL', pnl_usd=0.0, roi_pct=0.0 WHERE id=?",
                            (trade_id,),
                        )
                        return
                    if verbose:
                        log(
                            f"   ⏳ [{symbol}] #{trade_id} Exit pending: Balance is 0.0 (waiting for API sync...)"
                        )
                    return

            # Ensure we don't try to sell more than we actually have, even if threshold didn't trigger
            sell_size = truncate_float(min(size, actual_bal), 2)
            # CRITICAL FIX: Validate balance data before using it
            # If we have a significant position in DB but balance shows near zero,
            # it is likely a timing/API issue - use DB size instead
            if size >= MIN_SIZE and actual_bal < 0.1 and age < 600:  # 10 minute grace period (increased from 5 minutes)
                log(
                    f"   ⚠️  [{symbol}] #{trade_id} Balance sync shows near-zero ({actual_bal:.4f}) "
                    f"for active position ({size:.2f}). Using DB size (age: {age:.0f}s)."
                )
                # Use DB size for the actual sell (this fixes the scaled-in exit plan repair issue)
                sell_size = truncate_float(size, 2)

            if sell_size < MIN_SIZE:
                if actual_bal >= MIN_SIZE:
                    sell_size = MIN_SIZE
                    log(
                        f"   📈 [{symbol}] #{trade_id} Bumping sell size to {MIN_SIZE} shares"
                    )
                else:
                    log(
                        f"   ⏭️  [{symbol}] #{trade_id} size {sell_size} < {MIN_SIZE}. Skipping, trying again next window."
                    )
                    return

            res = place_limit_order(
                token_id,
                get_optimal_exit_price(entry, confidence, cur_p, side),
                sell_size,
                SELL,
            )
            if res["success"] or res.get("order_id"):
                oid = res.get("order_id")
                c.execute(
                    "UPDATE trades SET limit_sell_order_id = ? WHERE id = ?",
                    (oid, trade_id),
                )
                limit_sell_id = oid
                _last_exit_attempt[trade_id] = now.timestamp()
                repaired = True
            else:
                err = res.get("error", "Unknown error")
                log(f"   ❌ [{symbol}] Failed to place exit plan: {err}")
                if "Insufficient funds" in str(err):
                    # Clear cooldown to retry immediately after balance re-sync in next cycle
                    _last_exit_attempt.pop(trade_id, None)
                else:
                    _last_exit_attempt[trade_id] = now.timestamp()

    elif limit_sell_id:
        # 1-SECOND CYCLE HEALING: Ensure exit plan size matches current trade size
        o_data = get_order(limit_sell_id)
        if o_data:
            o_status = o_data.get("status", "").upper()
            o_size = float(
                o_data.get("original_size", 0)
            )  # Define o_size for all cases

            if o_status == "LIVE":
                # BI-DIRECTIONAL HEALING: Sync DB size with actual wallet balance
                scale_in_age = 999
                if last_scale_in_at:
                    try:
                        scale_in_age = (
                            now - datetime.fromisoformat(last_scale_in_at)
                        ).total_seconds()
                    except:
                        pass

                needs_sync = False
                if actual_bal > size + 0.0001:
                    needs_sync = (
                        True  # Always sync if we have more tokens than DB thinks
                    )
                elif age > 60 and scale_in_age > 60 and abs(actual_bal - size) > 0.0001:
                    needs_sync = (
                        True  # Periodic sync for other cases (e.g. fewer tokens)
                    )

                if needs_sync and actual_bal > 0:
                    log(
                        f"   🔧 [{symbol}] #{trade_id} Syncing size to match balance (active order): {size:.4f} -> {actual_bal:.4f}"
                    )
                    c.execute(
                        "UPDATE trades SET size = ?, bet_usd = ? * entry_price WHERE id = ?",
                        (actual_bal, actual_bal, trade_id),
                    )
                    size = actual_bal

                target_size = truncate_float(min(size, actual_bal), 2)
            else:
                # For non-LIVE orders, use current size as target
                target_size = truncate_float(size, 2)

            # CRITICAL FIX: Validate balance data before using it for active orders
            # If balance shows near zero for active position, likely API timing issue
            if size >= MIN_SIZE and actual_bal < 0.1 and age < 60:
                log(
                    f"   ⚠️  [{symbol}] #{trade_id} Balance sync shows near-zero ({actual_bal:.4f}) "
                    f"for active order position ({size:.2f}). Using DB size (age: {age:.0f}s)."
                )
                target_size = truncate_float(size, 2)
                if truncate_float(o_size, 2) != target_size:
                    # Repair needed
                    cancel_order(limit_sell_id)
                    sell_size = target_size
                    if sell_size < MIN_SIZE:
                        if actual_bal >= MIN_SIZE:
                            sell_size = MIN_SIZE
                            log(
                                f"   📈 [{symbol}] #{trade_id} Bumping exit plan size to {MIN_SIZE} shares"
                            )
                        else:
                            log(
                                f"   ⏭️  [{symbol}] #{trade_id} size {sell_size} < {MIN_SIZE}. Skipping, trying again next window."
                            )
                            c.execute(
                                "UPDATE trades SET limit_sell_order_id = NULL WHERE id = ?",
                                (trade_id,),
                            )
                            return True

                    res = place_limit_order(
                        token_id,
                        get_optimal_exit_price(entry, confidence, cur_p, side),
                        sell_size,
                        SELL,
                    )
                    if res["success"] or res.get("order_id"):
                        new_oid = res.get("order_id")
                        c.execute(
                            "UPDATE trades SET limit_sell_order_id = ? WHERE id = ?",
                            (new_oid, trade_id),
                        )
                        log(
                            f"   🔧 [{symbol}] #{trade_id} Exit plan size repaired: {target_size} -> {sell_size}"
                        )
                        repaired = True
                        limit_sell_id = new_oid
                    else:
                        # If replacement fails, clear from DB to allow retry in next cycle
                        c.execute(
                            "UPDATE trades SET limit_sell_order_id = NULL WHERE id = ?",
                            (trade_id,),
                        )
                        limit_sell_id = None

                    _last_exit_attempt[trade_id] = now.timestamp()
                    return repaired

            elif check_orders:
                # Other maintenance checks only on 10s cycle
                if o_status in ["FILLED", "MATCHED"]:
                    pass
                elif o_status in ["CANCELED", "EXPIRED"]:
                    log(
                        f"   ⚠️  [{symbol}] #{trade_id} Exit plan order was {o_status}. Clearing from DB to allow retry."
                    )
                    c.execute(
                        "UPDATE trades SET limit_sell_order_id = NULL WHERE id = ?",
                        (trade_id,),
                    )
                    limit_sell_id = None
                    _last_exit_attempt.pop(trade_id, None)
        else:
            # Order not found on exchange
            if check_orders:
                log(
                    f"   ⚠️  [{symbol}] #{trade_id} Exit plan order {limit_sell_id[:10]}... not found on exchange. Clearing."
                )
                c.execute(
                    "UPDATE trades SET limit_sell_order_id = NULL WHERE id = ?",
                    (trade_id,),
                )
                limit_sell_id = None
                _last_exit_attempt.pop(trade_id, None)

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

    return repaired
