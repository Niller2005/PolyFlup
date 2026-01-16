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

        processed_ids = []

        # Log batch summary
        type_counts = {}
        for notif in notifications:
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
        log(f"📬 Processing {len(notifications)} notification(s) [{counts_str}]")

        for notif in notifications:
            notif_id = notif.get("id")
            notif_type = notif.get("type")
            timestamp = notif.get("timestamp")
            payload = notif.get("payload", {})

            # Extract order_id for logging
            order_id = _extract_order_id_from_payload(payload)
            order_id_short = (
                order_id[:10] if order_id and len(order_id) > 10 else order_id
            )

            # Log individual notification
            type_name = {
                NOTIF_ORDER_CANCELLED: "CANCELLED",
                NOTIF_ORDER_FILLED: "FILLED",
                NOTIF_MARKET_RESOLVED: "RESOLVED",
            }.get(notif_type, f"UNKNOWN({notif_type})")

            log(
                f"   📨 Notification: {type_name} | Order: {order_id_short or 'N/A'} | ID: {notif_id}"
            )

            # Process based on type
            if notif_type == NOTIF_ORDER_FILLED:
                _handle_order_fill(payload, timestamp or 0)
            elif notif_type == NOTIF_ORDER_CANCELLED:
                _handle_order_cancelled(payload, timestamp or 0)
            elif notif_type == NOTIF_MARKET_RESOLVED:
                _handle_market_resolved(payload, timestamp or 0)

            if notif_id:
                processed_ids.append(str(notif_id))

        # Mark notifications as read
        if processed_ids:
            drop_notifications(processed_ids)

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

                    # Update price/size if provided in notification
                    new_price = float(fill_price) if fill_price else db_price
                    new_size = float(fill_size) if fill_size else db_size
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
                    track_recent_fill(trade_id, new_price, new_size, timestamp)

                    # NEW: Fetch condition_id from position data for CTF operations
                    try:
                        from eth_account import Account
                        from src.config.settings import PROXY_PK
                        from src.trading.orders.balance_validation import (
                            get_position_from_data_api,
                        )

                        user_address = Account.from_key(PROXY_PK).address

                        # Get token_id from trade
                        c.execute(
                            "SELECT token_id FROM trades WHERE id = ?", (trade_id,)
                        )
                        token_row = c.fetchone()

                        if token_row:
                            token_id = token_row[0]
                            position_data = get_position_from_data_api(
                                user_address, token_id, symbol
                            )

                            if position_data and position_data.get("condition_id"):
                                condition_id = position_data["condition_id"]
                                c.execute(
                                    "UPDATE trades SET condition_id = ? WHERE id = ?",
                                    (condition_id, trade_id),
                                )
                                log(
                                    f"   📍 [{symbol}] Stored condition_id: {condition_id[:16]}..."
                                )
                    except Exception as e:
                        log_error(
                            f"[{symbol}] Error fetching condition_id after fill: {e}"
                        )

                    # NEW: Place hedge order immediately after entry fill
                    # We place the hedge order here to ensure fast execution after fill
                    try:
                        from src.trading.execution import place_hedge_order

                        # Get trade details
                        c.execute(
                            "SELECT symbol, side, entry_price, size FROM trades WHERE id = ?",
                            (trade_id,),
                        )
                        row = c.fetchone()

                        if row:
                            symbol, side, entry_price, size = row

                            # Place hedge order
                            hedge_order_id = place_hedge_order(
                                trade_id, symbol, side, entry_price, size
                            )

                            # Update trade with hedge order ID
                            if hedge_order_id:
                                c.execute(
                                    "UPDATE trades SET hedge_order_id = ? WHERE id = ?",
                                    (hedge_order_id, trade_id),
                                )
                                conn.commit()
                    except Exception as e:
                        log_error(
                            f"[{symbol}] Error placing hedge order after fill: {e}"
                        )

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
                    "SELECT id, symbol, side, entry_price, size FROM trades WHERE hedge_order_id = ? AND settled = 0",
                    (order_id,),
                )
                row = c.fetchone()

                if row:
                    trade_id, symbol, trade_side, entry_price, size = row
                    order_id_short = order_id[:10] if len(order_id) > 10 else order_id

                    log(
                        f"🛡️  [{symbol}] HEDGE FILL {order_id_short}: {size:.1f} shares | Position is now HEDGED"
                    )

                    # Mark trade as hedged
                    c.execute(
                        "UPDATE trades SET is_hedged = 1, order_status = 'HEDGED' WHERE id = ?",
                        (trade_id,),
                    )

                    # Cancel exit plan order if it exists (hedge now provides full protection)
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

                    # NEW: Merge hedged position immediately to free capital
                    try:
                        from src.config.settings import ENABLE_CTF_MERGE
                        from src.trading.ctf_operations import merge_hedged_position

                        if ENABLE_CTF_MERGE:
                            # Get condition_id from trade
                            c.execute(
                                "SELECT condition_id FROM trades WHERE id = ?",
                                (trade_id,),
                            )
                            cond_row = c.fetchone()

                            if cond_row and cond_row[0]:
                                condition_id = cond_row[0]

                                # Convert size to USDC amount (6 decimals)
                                amount = int(size * 1_000_000)

                                log(
                                    f"   🔄 [{symbol}] Merging {size:.1f} hedged position to free capital..."
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
                                        f"   💰 [{symbol}] Capital freed: ${size:.2f} USDC"
                                    )
                                else:
                                    log(
                                        f"   ⚠️  [{symbol}] Merge transaction failed, position will settle normally"
                                    )
                            else:
                                log(
                                    f"   ⚠️  [{symbol}] No condition_id found, skipping merge"
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

                # Check if this is a tracked order
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

                    # Clear order_id and mark as cancelled to prevent monitoring ghost orders
                    c.execute(
                        "UPDATE trades SET order_id = NULL, order_status = 'CANCELLED' WHERE id = ?",
                        (trade_id,),
                    )
                else:
                    # Order not found in database - might be a hedge or exit order
                    order_id_short = order_id[:10] if len(order_id) > 10 else order_id
                    log(f"🔍 CANCEL {order_id_short} (not tracked as entry order)")

    except Exception as e:
        log_error(f"Error handling order cancellation notification: {e}")


def _handle_market_resolved(payload: dict, timestamp: int) -> None:
    """Handle market resolution notification"""
    try:
        market_id = payload.get("market_id") or payload.get("condition_id")
        outcome = payload.get("outcome")

        # Don't log - settlement will handle this automatically
        pass

    except Exception as e:
        log_error(f"Error handling market resolution notification: {e}")
