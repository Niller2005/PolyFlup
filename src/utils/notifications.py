"""Notification monitoring and processing"""

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

    # Try multiple possible field names for order ID
    possible_fields = ["order_id", "orderId", "id", "orderID"]

    for field in possible_fields:
        order_id = payload.get(field)
        if order_id:
            return str(order_id)

    return None

    # Try multiple possible field names for order ID
    possible_fields = ["order_id", "orderId", "id", "orderID"]

    for field in possible_fields:
        order_id = payload.get(field)
        if order_id:
            return str(order_id)

    return None

    # Try multiple possible field names for order ID
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
    if event == "fill":
        # WebSocket 'order' message has the full order object
        payload = {
            "order_id": order.get("id"),
            "price": order.get("price"),
            "size": order.get("size_matched") or order.get("original_size"),
        }
        _handle_order_fill(payload, int(time.time()))
    elif event == "cancel":
        payload = {"order_id": order.get("id")}
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

        for notif in notifications:
            notif_id = notif.get("id")
            notif_type = notif.get("type")
            timestamp = notif.get("timestamp")
            payload = notif.get("payload", {})

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
    """Handle order fill notification"""
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
                    "SELECT id, symbol, side, size, entry_price, order_status FROM trades WHERE order_id = ? AND settled = 0",
                    (order_id,),
                )
                row = c.fetchone()

                if row:
                    trade_id, symbol, trade_side, db_size, db_price, db_status = row

                    if db_status == "FILLED":
                        return

                    # Update price/size if provided in notification
                    new_price = float(fill_price) if fill_price else db_price
                    new_size = float(fill_size) if fill_size else db_size

                    # Log with symbol, price, and size
                    order_id_short = order_id[:10] if len(order_id) > 10 else order_id
                    log(
                        f"🔍 [{symbol}] FILL {order_id_short}: {new_size:.2f} @ ${new_price:.4f}"
                    )
                row = c.fetchone()

                if row:
                    trade_id, symbol, trade_side, db_size, db_price, db_status = row

                    if db_status == "FILLED":
                        return

                    # Update price/size if provided in notification
                    new_price = float(fill_price) if fill_price else db_price
                    new_size = float(fill_size) if fill_size else db_size

                    log(
                        f"🔔 [{symbol}] #{trade_id} Buy filled: {trade_side} {new_size:.2f} @ ${new_price:.4f}"
                    )

                    c.execute(
                        "UPDATE trades SET order_status = 'FILLED', entry_price = ?, size = ?, bet_usd = ? * ? WHERE id = ?",
                        (new_price, new_size, new_price, new_size, trade_id),
                    )
                    return  # Found and logged, done

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

                # Check if this is a scale-in order
                c.execute(
                    "SELECT id, symbol, side, size, entry_price, bet_usd, token_id, limit_sell_order_id FROM trades WHERE scale_in_order_id = ? AND settled = 0",
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
                    ) = row
                    new_scale_price = float(fill_price) if fill_price else db_price
                    new_scale_size = float(fill_size) if fill_size else 0
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
                    ) = row
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

                        c.execute(
                            "UPDATE trades SET size=?, bet_usd=?, entry_price=?, scaled_in=1, scale_in_order_id=NULL WHERE id=?",
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
                    "SELECT id, symbol, side FROM trades WHERE order_id = ? AND settled = 0",
                    (order_id,),
                )
                row = c.fetchone()

                if row:
                    trade_id, symbol, side = row
                    order_id_short = order_id[:10] if len(order_id) > 10 else order_id
                    log(f"🔍 [{symbol}] CANCEL {order_id_short}")

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
