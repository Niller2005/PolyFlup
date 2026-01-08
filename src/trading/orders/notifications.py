"""Order notifications management with comprehensive audit trail"""

from typing import List, Any
from py_clob_client.clob_types import DropNotificationParams
from src.utils.logger import log
from .client import client


def _extract_order_id_from_payload(payload: dict) -> str:
    """Extract order ID from notification payload using multiple possible field names"""
    if not isinstance(payload, dict):
        return "unknown"

    # Try multiple possible field names for order ID
    possible_fields = ["orderId", "order_id", "id", "orderID"]

    for field in possible_fields:
        order_id = payload.get(field)
        if order_id and order_id != "unknown":
            return str(order_id)

    return "unknown"


def get_notifications() -> List[dict]:
    """Get all notifications with simplified audit trail"""
    try:
        notifications = client.get_notifications()
        if not isinstance(notifications, list):
            notifications = [notifications] if notifications else []

        # Only log if there are notifications to process
        if len(notifications) > 0:
            log(f"🔍 NOTIFICATIONS: {len(notifications)} new")

        result = []
        notification_summary = []

        for i, notif in enumerate(notifications):
            if isinstance(notif, dict):
                result.append(notif)
                # Collect summary info for batch logging
                notification_type = notif.get("type", "unknown")
                payload = notif.get("payload", {})
                order_id = _extract_order_id_from_payload(payload)
                notification_summary.append(
                    f"#{i + 1}: Type {notification_type}, Order {order_id[:10] if order_id != 'unknown' else 'unknown'}"
                )
            else:
                notif_dict = {
                    "id": getattr(notif, "id", None),
                    "owner": getattr(notif, "owner", ""),
                    "payload": getattr(notif, "payload", {}),
                    "timestamp": getattr(notif, "timestamp", None),
                    "type": getattr(notif, "type", None),
                }
                result.append(notif_dict)
                # Collect summary info for batch logging
                notification_type = getattr(notif, "type", "unknown")
                payload = getattr(notif, "payload", {})
                order_id = _extract_order_id_from_payload(payload)
                notification_summary.append(
                    f"#{i + 1}: Type {notification_type}, Order {order_id[:10] if order_id != 'unknown' else 'unknown'}"
                )

        # Only log processing details if there are notifications
        if len(notifications) > 0:
            if len(notifications) <= 3:
                # For small batches, log each notification
                for summary in notification_summary:
                    log(f"🔍 NOTIFICATION: {summary}")
            else:
                # For large batches, log summary only
                log(f"🔍 NOTIFICATIONS: Processed {len(notifications)}")
        return result

    except Exception as e:
        log(f"❌ NOTIFICATION ERROR: {e}")
        return []


def drop_notifications(notification_ids: List[str]) -> bool:
    """Mark notifications as read with simplified logging"""
    if not notification_ids:
        return True

    try:
        params: Any = DropNotificationParams(ids=notification_ids)
        result = client.drop_notifications(params)

        # Only log if there are notifications to drop
        if len(notification_ids) > 0:
            log(f"🧹 NOTIFICATIONS: Dropped {len(notification_ids)}")
        return True

    except Exception as e:
        log(f"❌ NOTIFICATION ERROR: {e}")
        return False
