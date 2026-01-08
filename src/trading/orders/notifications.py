"""Order notifications management with comprehensive audit trail"""

from typing import List
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
    """Get all notifications with audit trail"""
    try:
        # AUDIT: Starting notification retrieval
        log("🔍 NOTIFICATION AUDIT: Starting notification retrieval process")

        notifications = client.get_notifications()
        if not isinstance(notifications, list):
            notifications = [notifications] if notifications else []

        # AUDIT: Notification retrieval results
        log(f"🔍 NOTIFICATION AUDIT: Retrieved {len(notifications)} notifications")

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

        # AUDIT: Batch notification processing summary
        if len(notifications) <= 3:
            # For small batches, log each notification
            for summary in notification_summary:
                log(f"🔍 NOTIFICATION AUDIT: Processed notification {summary}")
        else:
            # For large batches, log summary only
            log(
                f"🔍 NOTIFICATION AUDIT: Processed {len(notifications)} notifications - {len([s for s in notification_summary if 'Type' in s])} processed successfully"
            )

        # AUDIT: Notification processing complete
        log(
            f"✅ NOTIFICATION AUDIT: Successfully processed {len(result)} notifications"
        )
        return result

    except Exception as e:
        # AUDIT: Notification retrieval error
        log(f"❌ NOTIFICATION AUDIT ERROR: Failed to get notifications: {e}")
        return []


def drop_notifications(notification_ids: List[str]) -> bool:
    """Mark notifications as read with audit trail"""
    if not notification_ids:
        # AUDIT: No notifications to drop
        log("🔍 NOTIFICATION AUDIT: No notification IDs provided for dropping")
        return True

    try:
        # AUDIT: Starting notification drop process
        log(
            f"🧹 NOTIFICATION AUDIT: Starting to drop {len(notification_ids)} notifications"
        )

        # Log first few notification IDs for audit (don't log all for privacy/security)
        sample_ids = [nid[:10] for nid in notification_ids[:3]]
        log(
            f"🧹 NOTIFICATION AUDIT: Sample notification IDs to drop: {', '.join(sample_ids)}{'...' if len(notification_ids) > 3 else ''}"
        )

        params: Any = DropNotificationParams(ids=notification_ids)
        result = client.drop_notifications(params)

        # AUDIT: Notification drop successful
        log(
            f"✅ NOTIFICATION AUDIT: Successfully dropped {len(notification_ids)} notifications"
        )
        return True

    except Exception as e:
        # AUDIT: Notification drop error
        log(f"❌ NOTIFICATION AUDIT ERROR: Failed to drop notifications: {e}")
        return False
