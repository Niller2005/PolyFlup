"""
Phase 3A (B2.2): Health Monitoring API

Flask API to expose WebSocket connection health and order monitoring metrics.
Provides endpoints for:
- WebSocket connection status (market + user channels)
- Order fill monitoring statistics
- Overall system health

Usage:
    # Run standalone:
    python -m src.api.health

    # Or import and integrate with existing server:
    from src.api.health import app
    app.run(host='0.0.0.0', port=5001)

Endpoints:
    GET /health/websocket - WebSocket connection health
    GET /health - Overall system health
"""

from flask import Flask, jsonify
from flask_cors import CORS
import time
from datetime import datetime
from src.utils.websocket_manager import ws_manager

app = Flask(__name__)
CORS(app)  # Allow cross-origin requests from UI


def calculate_uptime_percentage(connection_metrics: dict) -> float:
    """
    Calculate uptime percentage based on connection/disconnection history.

    Simple heuristic: If currently connected, assume high uptime.
    If disconnected, calculate based on reconnection frequency.
    """
    if connection_metrics["status"] == "connected":
        # Connected: assume good uptime (could be improved with historical tracking)
        total_reconnects = connection_metrics["total_disconnections"]
        if total_reconnects == 0:
            return 100.0
        elif total_reconnects <= 2:
            return 98.0
        elif total_reconnects <= 5:
            return 95.0
        else:
            return 90.0
    else:
        # Disconnected: lower uptime
        return 0.0


def format_timestamp(ts: float) -> str:
    """Format Unix timestamp to human-readable string"""
    if ts is None:
        return "Never"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


@app.route("/health/websocket")
def websocket_health():
    """
    Get WebSocket connection health status.

    Returns:
        JSON with market/user channel status, message counts, and uptime
    """
    metrics = ws_manager.get_health_metrics()

    market = metrics["market_connection"]
    user = metrics["user_connection"]
    fills = metrics["order_fill_monitoring"]

    # Calculate uptime
    market_uptime = calculate_uptime_percentage(market)
    user_uptime = calculate_uptime_percentage(user)

    # Calculate fill success rate
    fills_total = fills["orders_tracked"]
    fills_detected = fills["fills_detected"]
    fills_timeout = fills["timeouts"]
    fallbacks = fills["fallback_to_polling"]

    success_rate = (fills_detected / fills_total * 100) if fills_total > 0 else 0
    timeout_rate = (fills_timeout / fills_total * 100) if fills_total > 0 else 0
    fallback_rate = (fallbacks / fills_total * 100) if fills_total > 0 else 0

    # Determine overall WebSocket health status
    if market_uptime > 95 and user_uptime > 95:
        overall_status = "healthy"
    elif market_uptime > 80 or user_uptime > 80:
        overall_status = "degraded"
    else:
        overall_status = "unhealthy"

    return jsonify(
        {
            "status": overall_status,
            "timestamp": datetime.utcnow().isoformat(),
            "market_connection": {
                "status": market["status"],
                "uptime_percentage": market_uptime,
                "messages_received": market["messages_received"],
                "total_connections": market["total_connections"],
                "total_disconnections": market["total_disconnections"],
                "last_connected_at": format_timestamp(market["last_connected_at"]),
                "last_disconnected_at": format_timestamp(
                    market["last_disconnected_at"]
                ),
                "last_message_at": format_timestamp(market["last_message_at"]),
            },
            "user_connection": {
                "status": user["status"],
                "uptime_percentage": user_uptime,
                "messages_received": user["messages_received"],
                "total_connections": user["total_connections"],
                "total_disconnections": user["total_disconnections"],
                "last_connected_at": format_timestamp(user["last_connected_at"]),
                "last_disconnected_at": format_timestamp(user["last_disconnected_at"]),
                "last_message_at": format_timestamp(user["last_message_at"]),
            },
            "order_fill_monitoring": {
                "orders_tracked": fills_total,
                "fills_detected": fills_detected,
                "timeouts": fills_timeout,
                "fallback_to_polling": fallbacks,
                "success_rate_percent": round(success_rate, 1),
                "timeout_rate_percent": round(timeout_rate, 1),
                "fallback_rate_percent": round(fallback_rate, 1),
            },
        }
    )


@app.route("/health")
def overall_health():
    """
    Get overall system health.

    Returns:
        JSON with component health status and summary
    """
    # Get WebSocket health
    ws_health_response = websocket_health()
    ws_health = ws_health_response.get_json()

    # Determine overall system status
    # For now, system health = WebSocket health (can expand later)
    system_status = ws_health["status"]

    return jsonify(
        {
            "status": system_status,
            "timestamp": datetime.utcnow().isoformat(),
            "components": {
                "websocket": {
                    "status": ws_health["status"],
                    "market_channel": ws_health["market_connection"]["status"],
                    "user_channel": ws_health["user_connection"]["status"],
                    "order_monitoring": {
                        "success_rate": ws_health["order_fill_monitoring"][
                            "success_rate_percent"
                        ],
                        "fallback_rate": ws_health["order_fill_monitoring"][
                            "fallback_rate_percent"
                        ],
                    },
                },
                # Placeholder for future components
                "database": {
                    "status": "unknown",
                    "message": "Database health check not yet implemented",
                },
                "trading_bot": {
                    "status": "unknown",
                    "message": "Bot health check not yet implemented",
                },
            },
            "summary": {
                "total_components": 3,
                "healthy": 1 if system_status == "healthy" else 0,
                "degraded": 1 if system_status == "degraded" else 0,
                "unhealthy": 1 if system_status == "unhealthy" else 0,
            },
        }
    )


@app.route("/health/websocket/raw")
def websocket_health_raw():
    """
    Get raw WebSocket health metrics (no formatting).

    Useful for debugging and scripting.
    """
    metrics = ws_manager.get_health_metrics()
    return jsonify(metrics)


if __name__ == "__main__":
    # Run as standalone server
    print("🚀 Starting Health Monitoring API on http://0.0.0.0:5001")
    print("📊 Endpoints:")
    print("   GET /health - Overall system health")
    print("   GET /health/websocket - WebSocket connection health")
    print("   GET /health/websocket/raw - Raw WebSocket metrics")

    # Start WebSocket manager if not already running
    if not ws_manager._running:
        print("🔌 Starting WebSocket Manager...")
        ws_manager.start()
        time.sleep(2)  # Give it time to connect

    app.run(host="0.0.0.0", port=5001, debug=False)
