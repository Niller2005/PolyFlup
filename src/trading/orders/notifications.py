"""Order notifications management"""

from typing import List
from py_clob_client.clob_types import DropNotificationParams
from src.utils.logger import log
from .client import client

def get_notifications() -> List[dict]:
    """Get all notifications"""
    try:
        notifications = client.get_notifications()
        if not isinstance(notifications, list):
            notifications = [notifications] if notifications else []
        result = []
        for notif in notifications:
            if isinstance(notif, dict):
                result.append(notif)
            else:
                notif_dict = {
                    "id": getattr(notif, "id", None),
                    "owner": getattr(notif, "owner", ""),
                    "payload": getattr(notif, "payload", {}),
                    "timestamp": getattr(notif, "timestamp", None),
                    "type": getattr(notif, "type", None),
                }
                result.append(notif_dict)
        return result
    except Exception as e:
        log(f"⚠️  Error getting notifications: {e}")
        return []

def drop_notifications(notification_ids: List[str]) -> bool:
    """Mark notifications as read"""
    try:
        params: Any = DropNotificationParams(ids=notification_ids)
        client.drop_notifications(params)
        return True
    except Exception as e:
        log(f"⚠️  Error dropping notifications: {e}")
        return False
