"""
Phase 3A (B2.4): Health Monitoring Alerts

Monitors WebSocket health and sends alerts when issues are detected.
Integrates with Discord webhook for notifications.

Alert Triggers:
- WebSocket disconnected for >60 seconds
- High fallback-to-polling rate (>20%)
- Low order fill success rate (<80%)
- Frequent reconnections (>5 in 5 minutes)

Usage:
    # Start alert monitoring
    from src.monitoring.alerts import start_alert_monitoring
    start_alert_monitoring()

    # Or check manually
    from src.monitoring.alerts import check_websocket_health
    check_websocket_health()
"""

import time
import threading
from typing import Dict, Optional
from src.utils.websocket_manager import ws_manager
from src.utils.logger import send_discord, log


class AlertState:
    """Track alert state to avoid spamming duplicate alerts"""

    def __init__(self):
        self.last_alert_times: Dict[str, float] = {}
        self.alert_cooldown = 300  # 5 minutes between duplicate alerts
        self._lock = threading.Lock()

    def should_send_alert(self, alert_key: str) -> bool:
        """
        Check if enough time has passed to send this alert again.
        Prevents alert spam by enforcing cooldown period.
        """
        with self._lock:
            last_time = self.last_alert_times.get(alert_key, 0)
            current_time = time.time()

            if current_time - last_time >= self.alert_cooldown:
                self.last_alert_times[alert_key] = current_time
                return True
            return False

    def clear_alert(self, alert_key: str):
        """Clear alert state (for recovery notifications)"""
        with self._lock:
            self.last_alert_times.pop(alert_key, None)


# Global alert state
_alert_state = AlertState()


def send_alert(level: str, title: str, message: str, alert_key: str):
    """
    Send alert to Discord webhook if cooldown period has passed.

    Args:
        level: Alert severity ("critical", "warning", "info")
        title: Alert title
        message: Alert message
        alert_key: Unique key for deduplication
    """
    if not _alert_state.should_send_alert(alert_key):
        # Cooldown period not elapsed, skip
        return

    # Emoji mapping
    emoji = {
        "critical": "🚨",
        "warning": "⚠️",
        "info": "ℹ️",
        "recovery": "✅",
    }.get(level, "📢")

    # Format Discord message
    discord_message = f"{emoji} **{title}**\n{message}"

    # Log locally
    log(f"🔔 ALERT [{level.upper()}]: {title} - {message}")

    # Send to Discord
    send_discord(discord_message)


def check_websocket_health():
    """
    Check WebSocket health and send alerts if issues detected.

    Called periodically by monitoring loop or manually for testing.
    """
    metrics = ws_manager.get_health_metrics()

    market = metrics["market_connection"]
    user = metrics["user_connection"]
    fills = metrics["order_fill_monitoring"]

    current_time = time.time()

    # Alert 1: Market channel disconnected for >60 seconds
    if market["status"] == "disconnected":
        if market["last_disconnected_at"]:
            disconnect_duration = current_time - market["last_disconnected_at"]
            if disconnect_duration > 60:
                send_alert(
                    level="warning",
                    title="WebSocket Market Channel Down",
                    message=f"Market channel has been disconnected for {int(disconnect_duration)} seconds.\n"
                    f"Reconnections attempted: {market['total_disconnections']}\n"
                    f"Last connected: {format_timestamp(market['last_connected_at'])}",
                    alert_key="market_disconnected",
                )
    else:
        # Recovery: Clear alert state when reconnected
        _alert_state.clear_alert("market_disconnected")

    # Alert 2: User channel disconnected for >60 seconds
    if user["status"] == "disconnected":
        if user["last_disconnected_at"]:
            disconnect_duration = current_time - user["last_disconnected_at"]
            if disconnect_duration > 60:
                send_alert(
                    level="warning",
                    title="WebSocket User Channel Down",
                    message=f"User channel has been disconnected for {int(disconnect_duration)} seconds.\n"
                    f"Order fill monitoring is unavailable (using polling fallback).\n"
                    f"Reconnections attempted: {user['total_disconnections']}\n"
                    f"Last connected: {format_timestamp(user['last_connected_at'])}",
                    alert_key="user_disconnected",
                )
    else:
        # Recovery: Clear alert state when reconnected
        _alert_state.clear_alert("user_disconnected")

    # Alert 3: High fallback rate (>20%)
    if fills["orders_tracked"] >= 10:  # Only alert if we have enough data
        fallback_rate = fills["fallback_to_polling"] / fills["orders_tracked"]
        if fallback_rate > 0.2:
            send_alert(
                level="warning",
                title="High WebSocket Fallback Rate",
                message=f"WebSocket order monitoring is falling back to polling at a high rate.\n"
                f"Fallback rate: {fallback_rate * 100:.1f}% ({fills['fallback_to_polling']}/{fills['orders_tracked']} orders)\n"
                f"This may indicate WebSocket connectivity issues or performance problems.\n"
                f"Phase 2 performance benefits are reduced.",
                alert_key="high_fallback_rate",
            )
        else:
            # Recovery: Clear alert if fallback rate drops
            _alert_state.clear_alert("high_fallback_rate")

    # Alert 4: Low fill success rate (<80%)
    if fills["orders_tracked"] >= 10:  # Only alert if we have enough data
        success_rate = fills["fills_detected"] / fills["orders_tracked"]
        if success_rate < 0.8:
            send_alert(
                level="warning",
                title="Low WebSocket Fill Detection Rate",
                message=f"WebSocket is detecting fills at a low rate.\n"
                f"Success rate: {success_rate * 100:.1f}% ({fills['fills_detected']}/{fills['orders_tracked']} orders)\n"
                f"Timeouts: {fills['timeouts']}\n"
                f"This may indicate WebSocket message delivery issues.",
                alert_key="low_fill_success_rate",
            )
        else:
            # Recovery: Clear alert if success rate improves
            _alert_state.clear_alert("low_fill_success_rate")

    # Alert 5: Frequent reconnections (>5 in short period)
    # This is harder to track without historical data, so we'll check total reconnects
    # and alert if it seems excessive relative to connection time
    total_reconnects = market["total_disconnections"] + user["total_disconnections"]
    if total_reconnects >= 5:
        # Check if any channel is currently having issues
        if market["status"] == "disconnected" or user["status"] == "disconnected":
            send_alert(
                level="warning",
                title="Frequent WebSocket Reconnections",
                message=f"WebSocket connections are unstable.\n"
                f"Market channel reconnects: {market['total_disconnections']}\n"
                f"User channel reconnects: {user['total_disconnections']}\n"
                f"This may indicate network issues or server instability.",
                alert_key="frequent_reconnections",
            )


def format_timestamp(ts: Optional[float]) -> str:
    """Format Unix timestamp to human-readable string"""
    if ts is None:
        return "Never"
    from datetime import datetime

    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _alert_monitoring_loop():
    """
    Background monitoring loop that checks health periodically.

    Runs every 30 seconds to check for health issues.
    """
    log("🔔 Alert monitoring started (checks every 30s)")

    while True:
        try:
            # Check health every 30 seconds
            time.sleep(30)

            # Only check if WebSocket is running
            if ws_manager._running:
                check_websocket_health()
        except Exception as e:
            log(f"⚠️  Alert monitoring error: {e}")
            # Continue running even if check fails
            time.sleep(30)


def start_alert_monitoring():
    """
    Start background alert monitoring.

    Launches a daemon thread that periodically checks WebSocket health
    and sends alerts when issues are detected.
    """
    if not ws_manager._running:
        log("⚠️  WebSocket manager not running, starting it first...")
        ws_manager.start()
        time.sleep(2)

    # Start monitoring thread
    monitor_thread = threading.Thread(target=_alert_monitoring_loop, daemon=True)
    monitor_thread.start()

    log("✅ Alert monitoring thread started")


# Optional: Auto-start if imported
# Uncomment to enable auto-start when module is imported
# start_alert_monitoring()
