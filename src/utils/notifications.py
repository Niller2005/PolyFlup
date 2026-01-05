"""Notification monitoring and processing"""

from typing import List, Dict
from src.utils.logger import log, send_discord
from src.trading.orders import get_notifications, drop_notifications
from src.data.db_connection import db_connection


# Notification type constants
NOTIF_ORDER_CANCELLED = 1
NOTIF_ORDER_FILLED = 2
NOTIF_MARKET_RESOLVED = 4


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
                _handle_order_fill(payload, timestamp)
            elif notif_type == NOTIF_ORDER_CANCELLED:
                _handle_order_cancelled(payload, timestamp)
            elif notif_type == NOTIF_MARKET_RESOLVED:
                _handle_market_resolved(payload, timestamp)

            if notif_id:
                processed_ids.append(str(notif_id))

        # Mark notifications as read
        if processed_ids:
            drop_notifications(processed_ids)

    except Exception as e:
        log(f"⚠️ Error processing notifications: {e}")


def _handle_order_fill(payload: dict, timestamp: int) -> None:
    """Handle order fill notification"""
    try:
        order_id = payload.get("order_id")

        if not order_id:
            return  # Skip if no order ID

        # Update database if this is a tracked order
        with db_connection() as conn:
            c = conn.cursor()

            # Check if this is a buy order
            c.execute(
                "SELECT id, symbol, side, size FROM trades WHERE order_id = ? AND settled = 0",
                (order_id,),
            )
            row = c.fetchone()

            if row:
                trade_id, symbol, trade_side, size = row
                log(f"🔔 [{symbol}] Buy filled: #{trade_id} {trade_side} ({size:.2f})")
                c.execute(
                    "UPDATE trades SET order_status = 'FILLED' WHERE id = ?",
                    (trade_id,),
                )
                # Context manager handles commit automatically
                return  # Found and logged, done

            # Check if this is a limit sell order (exit plan)
            c.execute(
                "SELECT id, symbol, side, size FROM trades WHERE limit_sell_order_id = ? AND settled = 0",
                (order_id,),
            )
            row = c.fetchone()

            if row:
                trade_id, symbol, trade_side, size = row
                log(
                    f"🎯 [{symbol}] Exit filled: #{trade_id} - will be settled on next position check"
                )
                # Mark order status so position manager knows to check it
                c.execute(
                    "UPDATE trades SET order_status = 'EXIT_PLAN_PENDING_SETTLEMENT' WHERE id = ?",
                    (trade_id,),
                )
                # Position manager will handle full settlement with P&L calculation
                return  # Found and logged, done

            # Don't log scale-in fills - position manager already logs them

            # Order not tracked in our database - skip logging (likely old or other trader's order)

    except Exception as e:
        log(f"⚠️ Error handling order fill notification: {e}")


def _handle_order_cancelled(payload: dict, timestamp: int) -> None:
    """Handle order cancellation notification"""
    try:
        order_id = payload.get("order_id")

        if not order_id:
            return

        # Update database
        with db_connection() as conn:
            c = conn.cursor()

            # Check if this is a tracked order
            c.execute(
                "SELECT id, symbol, side FROM trades WHERE order_id = ? AND settled = 0",
                (order_id,),
            )
            row = c.fetchone()

            # Don't log cancellations - position manager already logs them if needed

    except Exception as e:
        log(f"⚠️ Error handling order cancellation notification: {e}")


def _handle_market_resolved(payload: dict, timestamp: int) -> None:
    """Handle market resolution notification"""
    try:
        market_id = payload.get("market_id") or payload.get("condition_id")
        outcome = payload.get("outcome")

        # Don't log - settlement will handle this automatically
        pass

    except Exception as e:
        log(f"⚠️ Error handling market resolution notification: {e}")
