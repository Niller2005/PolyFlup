"""Notification monitoring and processing"""

import sqlite3
from typing import List, Dict
from src.config.settings import DB_FILE
from src.utils.logger import log, send_discord
from src.trading.orders import get_notifications, drop_notifications


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
        price = payload.get("price")
        size = payload.get("size")
        side = payload.get("side")

        if order_id:
            log(f"🔔 Order filled: {side} {size} @ ${price} | ID: {order_id[:10]}...")

            # Update database if this is a tracked order
            conn = sqlite3.connect(DB_FILE, timeout=30.0)
            c = conn.cursor()

            # Check if this is a buy order
            c.execute(
                "SELECT id, symbol, side FROM trades WHERE order_id = ? AND settled = 0",
                (order_id,),
            )
            row = c.fetchone()

            if row:
                trade_id, symbol, trade_side = row
                log(
                    f"  ✅ Buy order for trade #{trade_id} [{symbol}] {trade_side} filled"
                )
                c.execute(
                    "UPDATE trades SET order_status = 'FILLED' WHERE id = ?",
                    (trade_id,),
                )
                conn.commit()

            # Check if this is a limit sell order (exit plan)
            c.execute(
                "SELECT id, symbol, side FROM trades WHERE limit_sell_order_id = ? AND settled = 0",
                (order_id,),
            )
            row = c.fetchone()

            if row:
                trade_id, symbol, trade_side = row
                log(
                    f"  🎯 Exit plan filled for trade #{trade_id} [{symbol}] {trade_side}"
                )
                # Position manager will handle settlement

            # Check if this is a scale-in order
            c.execute(
                "SELECT id, symbol, side FROM trades WHERE scale_in_order_id = ? AND settled = 0",
                (order_id,),
            )
            row = c.fetchone()

            if row:
                trade_id, symbol, trade_side = row
                log(
                    f"  📈 Scale-in filled for trade #{trade_id} [{symbol}] {trade_side}"
                )
                # Position manager will handle position update

            conn.close()

    except Exception as e:
        log(f"⚠️ Error handling order fill notification: {e}")


def _handle_order_cancelled(payload: dict, timestamp: int) -> None:
    """Handle order cancellation notification"""
    try:
        order_id = payload.get("order_id")

        if order_id:
            log(f"🔔 Order cancelled: {order_id[:10]}...")

            # Update database
            conn = sqlite3.connect(DB_FILE, timeout=30.0)
            c = conn.cursor()

            # Check if this is a tracked order
            c.execute(
                "SELECT id, symbol FROM trades WHERE order_id = ? AND settled = 0",
                (order_id,),
            )
            row = c.fetchone()

            if row:
                trade_id, symbol = row
                log(f"  ℹ️ Buy order for trade #{trade_id} [{symbol}] was cancelled")

            conn.close()

    except Exception as e:
        log(f"⚠️ Error handling order cancellation notification: {e}")


def _handle_market_resolved(payload: dict, timestamp: int) -> None:
    """Handle market resolution notification"""
    try:
        market_id = payload.get("market_id") or payload.get("condition_id")
        outcome = payload.get("outcome")

        if market_id:
            log(f"🔔 Market resolved: {market_id[:10]}... → Outcome: {outcome}")
            # Settlement will handle this automatically

    except Exception as e:
        log(f"⚠️ Error handling market resolution notification: {e}")
