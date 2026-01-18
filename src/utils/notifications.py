"""Notification monitoring and processing

IMPORTANT: Fill notifications are CRITICAL for preventing exchange recovery conflict errors.
When a fill notification arrives, we immediately update order_status = 'FILLED' in the DB.
This allows monitor.py to skip get_order() calls on already-filled orders, preventing:
  - "ERROR #40001 canceling statement due to conflict with recovery" (500 errors)
  - Unnecessary API calls that fail during exchange recovery
  - Log spam during active trading

WITHOUT this fix:
  - Monitor.py calls get_order() every 15s on all active orders
  - During fill recovery (first 1-2 minutes after fill), API returns 500 errors
  - Each failed call logs an error (19+ errors per window)

WITH this fix:
  - Fill notification marks order as FILLED immediately
  - Monitor.py sees order_status == 'FILLED' and skips get_order() call
  - No API errors, no log spam, no unnecessary load on exchange
"""

import time
from typing import List, Dict, Optional
from src.utils.logger import log, log_error, send_discord
from src.trading.orders import get_notifications, drop_notifications, SELL
from src.data.db_connection import db_connection
from src.trading.position_manager.shared import _position_check_lock
from src.trading.position_manager.reconciliation import track_recent_fill
from .websocket_manager import ws_manager


def _extract_order_id_from_payload(payload: dict) -> Optional[str]:
    """Extract order ID from notification payload using multiple possible field names"""
    if not isinstance(payload, dict):
        return None

    possible_fields = ["order_id", "orderId", "id", "orderID"]
    for field in possible_fields:
        order_id = payload.get(field)
        if order_id:
            return str(order_id)

    return None


# Notification type constants
NOTIF_ORDER_CANCELLED = 1
NOTIF_ORDER_FILLED = 2
NOTIF_MARKET_RESOLVED = 4

# Track processed notifications to prevent duplicates (notif_id -> timestamp)
_processed_notifications: Dict[str, float] = {}
_NOTIFICATION_CACHE_DURATION = 3600  # Keep for 1 hour


def init_ws_callbacks():
    """Register WebSocket callbacks for real-time updates"""
    ws_manager.register_callback("order", _handle_ws_order_event)


def _handle_ws_order_event(event: str, order: dict):
    """Bridge between WebSocket events and internal handlers"""
    # Map WebSocket event types to internal notification types
    # Polymarket WSS events: "fill", "cancel", etc.
    order_id = order.get("id")
    order_id_short = order_id[:10] if order_id and len(order_id) > 10 else order_id

    if event == "fill":
        log(f"📡 WebSocket: FILL event | Order: {order_id_short}")
        # WebSocket 'order' message has the full order object
        payload = {
            "order_id": order_id,
            "price": order.get("price"),
            "size": order.get("size_matched") or order.get("original_size"),
        }
        _handle_order_fill(payload, int(time.time()))
    elif event == "cancel":
        log(f"📡 WebSocket: CANCEL event | Order: {order_id_short}")
        payload = {"order_id": order_id}
        _handle_order_cancelled(payload, int(time.time()))


def process_notifications() -> None:
    """
    Check for and process notifications from the CLOB

    Monitors:
    - Order fills (type 2)
    - Order cancellations (type 1)
    - Market resolutions (type 4)
    """
    try:
        notifications = get_notifications()

        if not notifications:
            return

        # Clean up old processed notifications (older than 1 hour)
        current_time = time.time()
        expired_ids = [
            nid
            for nid, ts in _processed_notifications.items()
            if current_time - ts > _NOTIFICATION_CACHE_DURATION
        ]
        for nid in expired_ids:
            del _processed_notifications[nid]

        # Filter out already-processed notifications
        new_notifications = []
        for notif in notifications:
            notif_id = str(notif.get("id", ""))
            if notif_id and notif_id not in _processed_notifications:
                new_notifications.append(notif)

        if not new_notifications:
            # All notifications were already processed - just drop them silently
            notification_ids = [str(n.get("id")) for n in notifications if n.get("id")]
            if notification_ids:
                drop_notifications(notification_ids)
            return

        processed_ids = []

        # Log batch summary (only for new notifications)
        type_counts = {}
        for notif in new_notifications:
            notif_type = notif.get("type")
            type_name = {
                NOTIF_ORDER_CANCELLED: "CANCELLED",
                NOTIF_ORDER_FILLED: "FILLED",
                NOTIF_MARKET_RESOLVED: "RESOLVED",
            }.get(notif_type, f"UNKNOWN({notif_type})")
            type_counts[type_name] = type_counts.get(type_name, 0) + 1

        counts_str = ", ".join(
            f"{name}: {count}" for name, count in type_counts.items()
        )
        log(f"📬 Processing {len(new_notifications)} notification(s) [{counts_str}]")

        for notif in new_notifications:
            notif_id = notif.get("id")
            notif_type = notif.get("type")
            timestamp = notif.get("timestamp")
            payload = notif.get("payload", {})

            # Extract order_id and payload details for logging
            order_id = _extract_order_id_from_payload(payload)
            order_id_short = (
                order_id[:10] if order_id and len(order_id) > 10 else order_id
            )

            # Extract price and size from payload if available
            payload_price = payload.get("price")
            payload_size = payload.get("size")

            # Type name for display
            type_name = {
                NOTIF_ORDER_CANCELLED: "CANCELLED",
                NOTIF_ORDER_FILLED: "FILLED",
                NOTIF_MARKET_RESOLVED: "RESOLVED",
            }.get(notif_type, f"UNKNOWN({notif_type})")

            # Try to get symbol and additional details from database
            symbol = None
            side = None
            order_type = "entry"  # entry, hedge, exit
            if order_id:
                try:
                    with db_connection() as conn:
                        c = conn.cursor()
                        # Check in entry orders
                        c.execute(
                            "SELECT symbol, side FROM trades WHERE order_id = ? AND settled = 0 LIMIT 1",
                            (order_id,),
                        )
                        row = c.fetchone()
                        if row:
                            symbol, side = row
                            order_type = "entry"
                        else:
                            # Check in hedge orders
                            c.execute(
                                "SELECT symbol, side FROM trades WHERE hedge_order_id = ? AND settled = 0 LIMIT 1",
                                (order_id,),
                            )
                            row = c.fetchone()
                            if row:
                                symbol, side = row
                                order_type = "hedge"
                            else:
                                # Check in exit orders
                                c.execute(
                                    "SELECT symbol, side FROM trades WHERE limit_sell_order_id = ? AND settled = 0 LIMIT 1",
                                    (order_id,),
                                )
                                row = c.fetchone()
                                if row:
                                    symbol, side = row
                                    order_type = "exit"
                except Exception:
                    pass  # Silently fail, we'll just show less info

            # Build new cleaner log format: 🔔 [SYMBOL] SIDE SIZE @ $PRICE STATUS | OrderID: 0x...
            if notif_type == NOTIF_MARKET_RESOLVED:
                # Market resolved - let _handle_market_resolved do detailed logging
                pass
            else:
                # FILLED or CANCELLED
                log_parts = ["🔔"]

                # Symbol with type suffix
                if symbol:
                    suffix = (
                        f" ({order_type})" if order_type in ["hedge", "exit"] else ""
                    )
                    log_parts.append(f"[{symbol}{suffix}]")

                # Side, size, price
                details = []
                if side:
                    details.append(side)
                if payload_size:
                    try:
                        details.append(f"{float(payload_size):.1f}")
                    except:
                        pass
                if payload_price:
                    try:
                        details.append(f"@ ${float(payload_price):.4f}")
                    except:
                        pass

                if details:
                    log_parts.append(" ".join(details))

                # Status
                log_parts.append(type_name)

                # Order ID
                if order_id_short:
                    log_parts.append(f"| OrderID: {order_id_short}")

                log(f"   {' '.join(log_parts)}")

            # Process based on type
            if notif_type == NOTIF_ORDER_FILLED:
                _handle_order_fill(payload, timestamp or 0)
            elif notif_type == NOTIF_ORDER_CANCELLED:
                _handle_order_cancelled(payload, timestamp or 0)
            elif notif_type == NOTIF_MARKET_RESOLVED:
                _handle_market_resolved(payload, timestamp or 0)
            else:
                # Log unknown notification types to investigate
                log(
                    f"   ⚠️  UNKNOWN notification type: {notif_type} | Payload: {payload}"
                )

            if notif_id:
                processed_ids.append(str(notif_id))
                # Mark as processed in our deduplication cache
                _processed_notifications[str(notif_id)] = current_time

        # Mark notifications as read (drop all notifications, not just new ones)
        all_notification_ids = [str(n.get("id")) for n in notifications if n.get("id")]
        if all_notification_ids:
            drop_notifications(all_notification_ids)

    except Exception as e:
        log_error(f"Error processing notifications: {e}")


def _handle_order_fill(payload: dict, timestamp: int) -> None:
    """
    Handle order fill notification

    CRITICAL FIX (2026-01-13):
    This function updates order_status = 'FILLED' in the database.
    This prevents monitor.py from calling get_order() on orders that are already filled,
    which eliminates "ERROR #40001 canceling statement due to conflict with recovery"
    errors when the exchange is processing fills.
    """
    try:
        order_id = _extract_order_id_from_payload(payload)
        fill_price = payload.get("price")
        fill_size = payload.get("size")

        if not order_id:
            return  # Skip if no order ID

        # Use the position check lock to prevent race conditions with monitor.py
        with _position_check_lock:
            # Update database if this is a tracked order
            with db_connection() as conn:
                c = conn.cursor()

                # Check if this is a buy order
                c.execute(
                    "SELECT id, symbol, side, size, entry_price, order_status, hedge_order_id FROM trades WHERE order_id = ? AND settled = 0",
                    (order_id,),
                )
                row = c.fetchone()

                if row:
                    (
                        trade_id,
                        symbol,
                        trade_side,
                        db_size,
                        db_price,
                        db_status,
                        hedge_order_id,
                    ) = row

                    # Skip if already filled AND hedge is already placed (nothing to do)
                    if db_status == "FILLED" and hedge_order_id:
                        return

                    # If already FILLED but no hedge, continue to place hedge
                    if db_status == "FILLED":
                        log(
                            f"🔍 [{symbol}] Fill already detected by monitor, placing hedge..."
                        )

                    # CRITICAL: Get actual filled size from exchange if not in notification
                    # Notifications often don't include the actual filled size (partial fills, fees, etc.)
                    new_price = float(fill_price) if fill_price else db_price
                    new_size = float(fill_size) if fill_size else db_size

                    # If notification didn't include size, query exchange for actual filled amount
                    if not fill_size:
                        try:
                            from src.trading.orders import get_order

                            order_data = get_order(order_id)
                            if order_data:
                                actual_matched = float(
                                    order_data.get("size_matched", 0)
                                )
                                if actual_matched > 0:
                                    new_size = actual_matched
                                    log(
                                        f"   📊 [{symbol}] Queried exchange: actual filled size = {new_size:.4f}"
                                    )
                        except Exception as e:
                            log(
                                f"   ⚠️  [{symbol}] Could not verify fill size from exchange: {e}"
                            )

                    new_bet_usd = new_price * new_size

                    # Log fill with trade details
                    order_id_short = order_id[:10] if len(order_id) > 10 else order_id
                    log(
                        f"🔍 [{symbol}] FILL {order_id_short}: {new_size:.2f} @ ${new_price:.4f} (buy order)"
                    )

                    # Update database with fill details - CRITICAL: This prevents get_order() calls
                    c.execute(
                        "UPDATE trades SET order_status = 'FILLED', entry_price = ?, size = ?, bet_usd = ? WHERE id = ?",
                        (new_price, new_size, new_bet_usd, trade_id),
                    )

                    # Track recent fill for balance API cooldown
                    track_recent_fill(order_id, new_price, new_size, timestamp)

                    # NEW: Fetch condition_id from position data for CTF operations
                    try:
                        from eth_account import Account
                        from src.config.settings import PROXY_PK
                        from src.trading.orders.balance_validation import (
                            get_position_from_data_api,
                        )

                        user_address = Account.from_key(PROXY_PK).address
                        log(
                            f"   🔍 [{symbol}] Fetching condition_id for trade #{trade_id}..."
                        )

                        # Get token_id from trade
                        c.execute(
                            "SELECT token_id FROM trades WHERE id = ?", (trade_id,)
                        )
                        token_row = c.fetchone()

                        if token_row:
                            token_id = token_row[0]
                            log(f"   🔍 [{symbol}] Token ID: {token_id}")

                            position_data = get_position_from_data_api(
                                user_address, token_id, symbol
                            )

                            if position_data:
                                log(
                                    f"   🔍 [{symbol}] Position data received: {position_data}"
                                )
                                if position_data.get("condition_id"):
                                    condition_id = position_data["condition_id"]
                                    c.execute(
                                        "UPDATE trades SET condition_id = ? WHERE id = ?",
                                        (condition_id, trade_id),
                                    )
                                    log(
                                        f"   📍 [{symbol}] Stored condition_id: {condition_id[:16]}..."
                                    )
                                else:
                                    log(
                                        f"   ⚠️  [{symbol}] Position data missing condition_id field"
                                    )
                            else:
                                log(
                                    f"   ⚠️  [{symbol}] No position data returned from Data API"
                                )
                        else:
                            log(
                                f"   ⚠️  [{symbol}] No token_id found for trade #{trade_id}"
                            )
                    except Exception as e:
                        log_error(
                            f"[{symbol}] Error fetching condition_id after fill: {e}"
                        )

                    # Hedge is placed atomically during execute_trade()
                    # No fallback hedge placement - if atomic pair fails, both orders are cancelled
                    return  # Found and processed, done

                # Check if this is a limit sell order (exit plan)
                c.execute(
                    "SELECT id, symbol, side, size FROM trades WHERE limit_sell_order_id = ? AND settled = 0",
                    (order_id,),
                )
                row = c.fetchone()

                if row:
                    trade_id, symbol, trade_side, size = row

                    # Get price from notification if available
                    exit_price = float(fill_price) if fill_price else "?"
                    exit_size = float(fill_size) if fill_size else size
                    order_id_short = order_id[:10] if len(order_id) > 10 else order_id

                    if exit_price != "?":
                        log(
                            f"🔍 [{symbol}] FILL {order_id_short}: {exit_size:.2f} @ ${exit_price:.4f} (exit)"
                        )
                    else:
                        log(
                            f"🔍 [{symbol}] FILL {order_id_short}: {exit_size:.2f} (exit)"
                        )

                    # Mark order status so position manager knows to check it
                    c.execute(
                        "UPDATE trades SET order_status = 'EXIT_PLAN_PENDING_SETTLEMENT' WHERE id = ?",
                        (trade_id,),
                    )
                    return

                # Check if this is a hedge order fill
                c.execute(
                    "SELECT id, symbol, side, entry_price, size, order_id, order_status, token_id FROM trades WHERE hedge_order_id = ? AND settled = 0",
                    (order_id,),
                )
                row = c.fetchone()

                if row:
                    (
                        trade_id,
                        symbol,
                        trade_side,
                        entry_price,
                        size,
                        entry_order_id,
                        entry_status,
                        token_id,
                    ) = row
                    order_id_short = order_id[:10] if len(order_id) > 10 else order_id

                    # CRITICAL: Check if entry order has filled
                    # If hedge filled but entry not filled (or cancelled), we have unhedged exposure
                    if entry_status not in ["FILLED", "HEDGED"] or not entry_order_id:
                        log(
                            f"🚨 [{symbol}] Hedge filled but entry NOT filled (status: {entry_status})"
                        )

                        # STEP 1: Cancel the unfilled entry order (if still open)
                        if entry_order_id and entry_status not in ["CANCELLED"]:
                            try:
                                from src.trading.orders import cancel_order

                                log(
                                    f"   🚫 [{symbol}] Cancelling unfilled entry order..."
                                )
                                cancel_result = cancel_order(entry_order_id)
                                if cancel_result:
                                    log(
                                        f"   ✅ [{symbol}] Entry order cancelled successfully"
                                    )
                                else:
                                    log(
                                        f"   ⚠️  [{symbol}] Failed to cancel entry order"
                                    )
                            except Exception as e:
                                log_error(
                                    f"[{symbol}] Error cancelling entry order: {e}"
                                )

                        # STEP 2: Emergency sell the filled hedge position
                        from src.trading.execution import (
                            emergency_sell_position,
                            get_token_ids,
                        )

                        log(
                            f"   🚨 [{symbol}] Emergency selling filled hedge position..."
                        )

                        # Determine hedge token_id (opposite of entry)
                        up_id, down_id = get_token_ids(symbol)
                        if up_id and down_id:
                            hedge_token_id = down_id if trade_side == "UP" else up_id
                            # Calculate hedge price as complement of entry price
                            hedge_price = 0.99 - entry_price if entry_price else None

                            sell_success = emergency_sell_position(
                                symbol,
                                hedge_token_id,
                                size,
                                reason="hedge filled but entry not filled",
                                entry_price=hedge_price,  # Use calculated hedge price
                            )

                            if sell_success:
                                log(f"   ✅ [{symbol}] Hedge position emergency sold")
                                # Send Discord alert
                                send_discord(
                                    f"🚨 **[{symbol}] #{trade_id} Hedge filled but entry failed!** Entry cancelled, hedge emergency sold."
                                )
                            else:
                                log(
                                    f"   ⚠️  [{symbol}] Emergency sell failed - hedge position still open on exchange"
                                )
                                # Send Discord alert for manual intervention
                                send_discord(
                                    f"🚨 **[{symbol}] #{trade_id} CRITICAL: Hedge filled but entry failed!** Entry cancelled but emergency sell failed. **MANUAL ACTION REQUIRED: Sell hedge position manually on Polymarket.**"
                                )

                            # Mark trade as cancelled
                            c.execute(
                                "UPDATE trades SET order_status = 'CANCELLED', hedge_order_id = NULL WHERE id = ?",
                                (trade_id,),
                            )
                            return

                    # CRITICAL: Verify actual hedge fill size from exchange
                    # Hedge orders can partially fill, which would leave position unhedged
                    hedge_filled_size = float(fill_size) if fill_size else size

                    # If notification didn't include size, query exchange for actual filled amount
                    if not fill_size:
                        try:
                            from src.trading.orders import get_order

                            order_data = get_order(order_id)
                            if order_data:
                                actual_matched = float(
                                    order_data.get("size_matched", 0)
                                )
                                if actual_matched > 0:
                                    hedge_filled_size = actual_matched
                                    log(
                                        f"   📊 [{symbol}] Queried exchange: hedge filled {hedge_filled_size:.4f} shares"
                                    )
                        except Exception as e:
                            log(
                                f"   ⚠️  [{symbol}] Could not verify hedge fill size from exchange: {e}"
                            )

                    # Check if hedge is fully filled
                    # Use >= comparison with 0.01 tolerance (99%+ filled = fully hedged)
                    # This handles partial fills like 9.994/10.0 shares (99.94% filled)
                    is_fully_hedged = hedge_filled_size >= (size - 0.01)

                    if is_fully_hedged:
                        log(
                            f"🛡️  [{symbol}] HEDGE FILL {order_id_short}: {hedge_filled_size:.1f} shares | Position is now HEDGED"
                        )
                    else:
                        log(
                            f"⚠️  [{symbol}] PARTIAL HEDGE FILL {order_id_short}: {hedge_filled_size:.1f}/{size:.1f} shares | Position PARTIALLY hedged"
                        )
                        # TODO: Place additional hedge order for remaining shares

                    # Mark trade as hedged (or partially hedged)
                    c.execute(
                        "UPDATE trades SET is_hedged = ?, order_status = ? WHERE id = ?",
                        (
                            1 if is_fully_hedged else 0,
                            "HEDGED" if is_fully_hedged else "PARTIAL_HEDGE",
                            trade_id,
                        ),
                    )

                    # Cancel exit plan order ONLY if fully hedged (partial hedge still needs stop-loss)
                    if is_fully_hedged:
                        c.execute(
                            "SELECT limit_sell_order_id FROM trades WHERE id = ?",
                            (trade_id,),
                        )
                        exit_row = c.fetchone()
                        if exit_row and exit_row[0]:
                            exit_order_id = exit_row[0]
                            try:
                                from src.trading.orders import cancel_order

                                cancel_result = cancel_order(exit_order_id)
                                if cancel_result:
                                    log(
                                        f"   🚫 [{symbol}] Exit plan cancelled (position fully hedged)"
                                    )
                                    c.execute(
                                        "UPDATE trades SET limit_sell_order_id = NULL WHERE id = ?",
                                        (trade_id,),
                                    )
                            except Exception as cancel_error:
                                log_error(
                                    f"[{symbol}] Error cancelling exit plan: {cancel_error}"
                                )
                    else:
                        log(
                            f"   ⚠️  [{symbol}] Exit plan KEPT (hedge only partially filled, still need stop-loss protection)"
                        )

                    # Merge hedged position immediately to free capital (only if fully hedged)
                    if is_fully_hedged:
                        try:
                            from src.config.settings import ENABLE_CTF_MERGE
                            from src.trading.ctf_operations import merge_hedged_position

                            if ENABLE_CTF_MERGE:
                                # Get condition_id from trade
                                c.execute(
                                    "SELECT condition_id, slug FROM trades WHERE id = ?",
                                    (trade_id,),
                                )
                                cond_row = c.fetchone()

                                condition_id = cond_row[0] if cond_row else None
                                slug = cond_row[1] if cond_row else None

                                # If no condition_id, try to fetch it from API now
                                # (may have become available since trade creation)
                                if not condition_id and slug:
                                    log(
                                        f"   🔍 [{symbol}] Fetching condition_id from API for merge..."
                                    )
                                    try:
                                        import requests
                                        from src.config.settings import GAMMA_API_BASE

                                        r = requests.get(
                                            f"{GAMMA_API_BASE}/markets/slug/{slug}",
                                            timeout=5,
                                        )
                                        if r.status_code == 200:
                                            data = r.json()
                                            api_condition_id = (
                                                data.get("conditionId")
                                                or data.get("condition_id")
                                                or ""
                                            )
                                            if (
                                                api_condition_id
                                                and api_condition_id
                                                != "0x" + ("0" * 64)
                                            ):
                                                condition_id = api_condition_id
                                                # Update database
                                                c.execute(
                                                    "UPDATE trades SET condition_id = ? WHERE id = ?",
                                                    (condition_id, trade_id),
                                                )
                                                log(
                                                    f"   ✅ [{symbol}] Retrieved condition_id: {condition_id[:16]}..."
                                                )
                                    except Exception as fetch_err:
                                        log(
                                            f"   ⚠️  [{symbol}] Failed to fetch condition_id: {fetch_err}"
                                        )

                                if condition_id:
                                    # Use actual hedge filled size (minimum of entry and hedge fills)
                                    merge_size = min(hedge_filled_size, size)
                                    amount = int(merge_size * 1_000_000)

                                    log(
                                        f"   🔄 [{symbol}] Merging {merge_size:.1f} hedged position to free capital..."
                                    )

                                    # Merge the hedged position
                                    tx_hash = merge_hedged_position(
                                        trade_id, symbol, condition_id, amount
                                    )

                                    if tx_hash:
                                        # Store transaction hash
                                        c.execute(
                                            "UPDATE trades SET merge_tx_hash = ? WHERE id = ?",
                                            (tx_hash, trade_id),
                                        )
                                        log(
                                            f"   ✅ [{symbol}] Merge successful! Tx: {tx_hash[:16]}..."
                                        )
                                        log(
                                            f"   💰 [{symbol}] Capital freed: ${merge_size:.2f} USDC"
                                        )
                                    else:
                                        log(
                                            f"   ⚠️  [{symbol}] Merge transaction failed, position will settle normally"
                                        )
                                else:
                                    log(
                                        f"   ⚠️  [{symbol}] No condition_id found (market too new), merge will happen at settlement"
                                    )
                        except Exception as e:
                            log_error(f"[{symbol}] Error merging hedged position: {e}")

                    return

                # Check if this is a scale-in order
                c.execute(
                    "SELECT id, symbol, side, size, entry_price, bet_usd, token_id, limit_sell_order_id, scale_in_order_id FROM trades WHERE scale_in_order_id = ? AND settled = 0",
                    (order_id,),
                )
                row = c.fetchone()

                if row:
                    (
                        trade_id,
                        symbol,
                        trade_side,
                        db_size,
                        db_price,
                        db_bet,
                        token_id,
                        l_sell_id,
                        existing_scale_in_id,
                    ) = row

                    # Skip if this fill has already been processed
                    # (scale_in_order_id matches, meaning this is a duplicate notification)
                    if (
                        existing_scale_in_id
                        and existing_scale_in_id.lower() == order_id.lower()
                    ):
                        return

                    new_scale_price = float(fill_price) if fill_price else db_price
                    new_scale_size = float(fill_size) if fill_size else 0

                    if new_scale_size > 0:
                        order_id_short = (
                            order_id[:10] if len(order_id) > 10 else order_id
                        )
                        log(
                            f"🔍 [{symbol}] FILL {order_id_short}: {new_scale_size:.2f} @ ${new_scale_price:.4f} (scale-in)"
                        )

                        # Track this fill to prevent race condition cancellations
                        track_recent_fill(
                            order_id, new_scale_price, new_scale_size, timestamp
                        )

                        # Immediate update of averages
                        new_total_size = db_size + new_scale_size
                        new_total_bet = db_bet + (new_scale_size * new_scale_price)
                        new_avg_price = new_total_bet / new_total_size

                        # CRITICAL: Keep scale_in_order_id set to track this order
                        # This prevents duplicate notifications from causing infinite scale-in loops
                        c.execute(
                            "UPDATE trades SET size=?, bet_usd=?, entry_price=?, scaled_in=1 WHERE id=?",
                            (new_total_size, new_total_bet, new_avg_price, trade_id),
                        )
                    return

                # Order not tracked in our database - skip logging (likely old or other trader's order)

    except Exception as e:
        log_error(f"Error handling order fill notification: {e}")


def _handle_order_cancelled(payload: dict, timestamp: int) -> None:
    """Handle order cancellation notification"""
    try:
        order_id = _extract_order_id_from_payload(payload)

        if not order_id:
            return

        # Use the position check lock to prevent race conditions with monitor.py
        with _position_check_lock:
            # Update database
            with db_connection() as conn:
                c = conn.cursor()

                # Check if this is a tracked entry order
                c.execute(
                    "SELECT id, symbol, side, order_status FROM trades WHERE order_id = ? AND settled = 0",
                    (order_id,),
                )
                row = c.fetchone()

                if row:
                    trade_id, symbol, side, current_status = row
                    order_id_short = order_id[:10] if len(order_id) > 10 else order_id
                    log(
                        f"🔍 [{symbol}] CANCEL {order_id_short} | Previous status: {current_status}"
                    )

                    # CRITICAL: Check if hedge order exists and has filled
                    # If entry cancelled but hedge filled, we have unhedged exposure
                    c.execute(
                        "SELECT hedge_order_id, token_id, size, entry_price FROM trades WHERE id = ?",
                        (trade_id,),
                    )
                    hedge_check = c.fetchone()

                    if hedge_check:
                        hedge_order_id, token_id, size, entry_price = hedge_check

                        if hedge_order_id:
                            # Check if hedge order has filled
                            try:
                                from src.trading.orders import get_order

                                hedge_order = get_order(hedge_order_id)
                                if hedge_order:
                                    hedge_status = hedge_order.get("status", "").upper()

                                    if hedge_status == "MATCHED":
                                        # Hedge filled but entry cancelled - UNHEDGED EXPOSURE!
                                        log(
                                            f"🚨 [{symbol}] Entry cancelled but hedge FILLED - Emergency selling hedge position!"
                                        )

                                        # Emergency sell the hedge position
                                        from src.trading.execution import (
                                            emergency_sell_position,
                                        )

                                        # Need to determine hedge token_id
                                        # Entry was for token_id, hedge is opposite
                                        from src.trading.execution import get_token_ids

                                        up_id, down_id = get_token_ids(symbol)
                                        if up_id and down_id:
                                            # If entry was UP, hedge is DOWN and vice versa
                                            hedge_token_id = (
                                                down_id if side == "UP" else up_id
                                            )
                                            # Calculate hedge price as complement
                                            hedge_price = (
                                                0.99 - entry_price
                                                if entry_price
                                                else None
                                            )

                                            emergency_sell_position(
                                                symbol,
                                                hedge_token_id,
                                                size,
                                                reason="entry order cancelled after hedge filled",
                                                entry_price=hedge_price,  # Use calculated hedge price
                                            )

                                        # Send Discord alert
                                        send_discord(
                                            f"🚨 **[{symbol}] #{trade_id} Entry cancelled after hedge filled!** Attempted emergency sell of hedge position."
                                        )

                                    else:
                                        # Hedge not filled yet - just cancel it
                                        log(
                                            f"   🚫 [{symbol}] Cancelling unfilled hedge order..."
                                        )
                                        from src.trading.orders import cancel_order

                                        cancel_result = cancel_order(hedge_order_id)
                                        if cancel_result:
                                            log(
                                                f"   ✅ [{symbol}] Hedge order cancelled successfully"
                                            )
                                        else:
                                            log(
                                                f"   ⚠️  [{symbol}] Failed to cancel hedge order"
                                            )

                            except Exception as e:
                                log_error(
                                    f"[{symbol}] Error checking/handling hedge after entry cancel: {e}"
                                )

                    # Clear order_id and mark as cancelled to prevent monitoring ghost orders
                    c.execute(
                        "UPDATE trades SET order_id = NULL, order_status = 'CANCELLED' WHERE id = ?",
                        (trade_id,),
                    )
                    return

                # Check if this is a hedge order cancellation
                c.execute(
                    "SELECT id, symbol, side, entry_price, size, order_status, order_id, token_id FROM trades WHERE hedge_order_id = ? AND settled = 0",
                    (order_id,),
                )
                hedge_row = c.fetchone()

                if hedge_row:
                    (
                        trade_id,
                        symbol,
                        side,
                        entry_price,
                        size,
                        current_status,
                        entry_order_id,
                        token_id,
                    ) = hedge_row
                    order_id_short = order_id[:10] if len(order_id) > 10 else order_id
                    log(
                        f"⚠️  [{symbol}] HEDGE CANCEL {order_id_short} | Entry status: {current_status}"
                    )

                    # CRITICAL: Check if entry order has filled
                    # If entry filled but hedge cancelled, we have unhedged exposure
                    if current_status == "FILLED" and entry_order_id:
                        # Verify entry is actually still filled (not cancelled after)
                        try:
                            from src.trading.orders import get_order

                            entry_order = get_order(entry_order_id)
                            if entry_order:
                                entry_status = entry_order.get("status", "").upper()

                                if entry_status == "MATCHED":
                                    # Entry filled but hedge cancelled - UNHEDGED EXPOSURE!
                                    log(
                                        f"🚨 [{symbol}] Entry filled but hedge CANCELLED - Emergency selling entry position!"
                                    )

                                    # Emergency sell the entry position
                                    from src.trading.execution import (
                                        emergency_sell_position,
                                    )

                                    emergency_sell_position(
                                        symbol,
                                        token_id,
                                        size,
                                        reason="hedge cancelled after entry filled",
                                        entry_price=entry_price,  # Use entry price as reference
                                    )

                                    # Send Discord alert
                                    send_discord(
                                        f"🚨 **[{symbol}] #{trade_id} Hedge cancelled after entry filled!** Emergency sold entry position."
                                    )

                                    # Clear hedge_order_id and mark as cancelled
                                    c.execute(
                                        "UPDATE trades SET hedge_order_id = NULL, order_status = 'CANCELLED' WHERE id = ?",
                                        (trade_id,),
                                    )
                                    return

                        except Exception as e:
                            log_error(
                                f"[{symbol}] Error checking entry status after hedge cancel: {e}"
                            )

                    # Clear hedge_order_id
                    c.execute(
                        "UPDATE trades SET hedge_order_id = NULL WHERE id = ?",
                        (trade_id,),
                    )

                    # Attempt to re-place hedge order if position is still FILLED and not emergency sold
                    if current_status == "FILLED" and entry_order_id:
                        log(
                            f"   ⚠️  [{symbol}] Entry not yet filled - hedge cancel OK, waiting for entry"
                        )
                    return

                # Check if this is an exit order cancellation
                c.execute(
                    "SELECT id, symbol FROM trades WHERE limit_sell_order_id = ? AND settled = 0",
                    (order_id,),
                )
                exit_row = c.fetchone()

                if exit_row:
                    trade_id, symbol = exit_row
                    order_id_short = order_id[:10] if len(order_id) > 10 else order_id
                    log(f"🔍 [{symbol}] EXIT CANCEL {order_id_short}")
                    # Exit cancellations are handled by position manager
                    return

                # Order not tracked in any capacity
                order_id_short = order_id[:10] if len(order_id) > 10 else order_id
                log(f"🔍 CANCEL {order_id_short} (not tracked)")

    except Exception as e:
        log_error(f"Error handling order cancellation notification: {e}")


def _handle_market_resolved(payload: dict, timestamp: int) -> None:
    """
    Handle market resolution notification with automatic gasless redemption.

    Shows detailed trade info and triggers redemption if conditions are met.
    """
    try:
        from src.data.database import db_connection
        from src.trading.ctf_operations import redeem_winning_tokens

        market_id = payload.get("market_id") or payload.get("condition_id")
        outcome = payload.get("outcome")  # "YES" or "NO"

        if not market_id:
            return

        # Find trade(s) associated with this market
        with db_connection() as conn:
            c = conn.cursor()

            # Try to find trade by condition_id first
            c.execute(
                """
                SELECT id, symbol, side, size, entry_price, bet_usd, 
                       exited_early, merge_tx_hash, redeem_tx_hash, condition_id, slug
                FROM trades 
                WHERE condition_id = ? AND settled = 0
                ORDER BY id DESC
                LIMIT 5
            """,
                (market_id,),
            )

            trades = c.fetchall()

            if not trades:
                # If not found by condition_id, might be NULL in DB
                # Try updating condition_id from API for recent unsettled trades
                log("   🔔 MARKET RESOLVED (condition_id not in DB, updating...)")

                # Find recent unsettled trades and update their condition_id
                c.execute(
                    """
                    SELECT id, symbol, slug
                    FROM trades
                    WHERE settled = 0
                        AND condition_id IS NULL
                        AND datetime(timestamp) > datetime('now', '-1 hour')
                    ORDER BY id DESC
                    LIMIT 10
                    """
                )

                pending_trades = c.fetchall()

                if pending_trades:
                    import requests
                    from src.config.settings import GAMMA_API_BASE

                    for tid, sym, slug in pending_trades:
                        try:
                            r = requests.get(
                                f"{GAMMA_API_BASE}/markets/slug/{slug}", timeout=3
                            )
                            if r.status_code == 200:
                                data = r.json()
                                api_condition_id = (
                                    data.get("conditionId")
                                    or data.get("condition_id")
                                    or ""
                                )
                                if api_condition_id and api_condition_id != "0x" + (
                                    "0" * 64
                                ):
                                    c.execute(
                                        "UPDATE trades SET condition_id = ? WHERE id = ?",
                                        (api_condition_id, tid),
                                    )
                                    if api_condition_id == market_id:
                                        log(
                                            f"   ✅ Updated condition_id for #{tid} {sym}"
                                        )
                        except Exception:
                            pass

                    # Retry query with updated condition_id
                    c.execute(
                        """
                        SELECT id, symbol, side, size, entry_price, bet_usd, 
                               exited_early, merge_tx_hash, redeem_tx_hash, condition_id, slug
                        FROM trades 
                        WHERE condition_id = ? AND settled = 0
                        ORDER BY id DESC
                        LIMIT 5
                    """,
                        (market_id,),
                    )

                    trades = c.fetchall()

                if not trades:
                    # Still not found - old trade
                    return

            for trade_data in trades:
                (
                    trade_id,
                    symbol,
                    side,
                    size,
                    entry_price,
                    bet_usd,
                    exited_early,
                    merge_tx_hash,
                    redeem_tx_hash,
                    condition_id,
                    slug,
                ) = trade_data

                # Determine if this side won
                won = (outcome == "YES" and side == "UP") or (
                    outcome == "NO" and side == "DOWN"
                )

                # Calculate estimated PnL
                final_price = 1.0 if won else 0.0
                est_pnl = (final_price * size) - bet_usd
                roi_pct = (est_pnl / bet_usd * 100) if bet_usd > 0 else 0

                # Build detailed notification
                outcome_emoji = "💰" if won else "💀"
                status_text = f"{'WON' if won else 'LOST'} ({outcome})"

                log("")
                log(
                    f"   🔔 {outcome_emoji} [{symbol}] #{trade_id} MARKET RESOLVED → {status_text}"
                )
                log(f"      Position: {side} {size:.1f} shares @ ${entry_price:.4f}")
                log(f"      Est PnL: ${est_pnl:+.2f} ({roi_pct:+.1f}%)")

                # Check if we should redeem
                should_redeem = (
                    not exited_early  # Didn't exit early
                    and not merge_tx_hash  # Wasn't merged (hedged)
                    and not redeem_tx_hash  # Not already redeemed
                    and condition_id  # Have condition_id
                    and won  # Only redeem winners (losers have $0 value)
                )

                if should_redeem:
                    log(f"      💎 Redeeming winning tokens gaslessly...")
                    try:
                        tx_hash = redeem_winning_tokens(trade_id, symbol, condition_id)

                        if tx_hash:
                            # Update database
                            c.execute(
                                "UPDATE trades SET redeem_tx_hash = ? WHERE id = ?",
                                (tx_hash, trade_id),
                            )
                            log(f"      ✅ Redeemed! Tx: {tx_hash[:16]}...")
                        else:
                            log(
                                f"      ⚠️  Redemption failed (will retry in settlement)"
                            )
                    except Exception as e:
                        log_error(f"      ❌ Redemption error: {e}")
                elif not won:
                    log(
                        f"      ⚠️  Losing position - tokens worthless, no redemption needed"
                    )
                elif merge_tx_hash:
                    log(f"      ✅ Already merged (hedged position)")
                elif redeem_tx_hash:
                    log(f"      ✅ Already redeemed: {redeem_tx_hash[:16]}...")
                elif exited_early:
                    log(f"      ✅ Exited early - no tokens to redeem")
                else:
                    log(f"      ⚠️  Missing condition_id - cannot redeem (old trade)")

                log("")

    except Exception as e:
        log_error(f"Error handling market resolution notification: {e}")
