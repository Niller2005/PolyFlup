"""
Test for Phase 3A B2.2 Health API

Tests the Flask health monitoring endpoints.
"""

import sys
import time
import requests
from threading import Thread


def test_health_api():
    """Test health API endpoints"""
    print("🧪 Testing Health API...")

    # Import and start the Flask app in a thread
    from src.api.health import app
    from src.utils.websocket_manager import ws_manager

    # Start WebSocket manager
    if not ws_manager._running:
        print("   Starting WebSocket Manager...")
        ws_manager.start()
        time.sleep(1)

    # Start Flask in background thread
    def run_app():
        app.run(host="127.0.0.1", port=5001, debug=False, use_reloader=False)

    flask_thread = Thread(target=run_app, daemon=True)
    flask_thread.start()

    # Give Flask time to start
    time.sleep(2)

    print("\n📡 Testing endpoints...")

    # Test 1: /health/websocket
    print("\n1. Testing GET /health/websocket")
    try:
        response = requests.get("http://127.0.0.1:5001/health/websocket", timeout=5)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

        data = response.json()
        assert "status" in data, "Missing 'status' field"
        assert "market_connection" in data, "Missing 'market_connection' field"
        assert "user_connection" in data, "Missing 'user_connection' field"
        assert "order_fill_monitoring" in data, "Missing 'order_fill_monitoring' field"

        print(f"   ✅ Status: {data['status']}")
        print(
            f"   Market: {data['market_connection']['status']} ({data['market_connection']['messages_received']} msgs)"
        )
        print(
            f"   User: {data['user_connection']['status']} ({data['user_connection']['messages_received']} msgs)"
        )
        print(
            f"   Orders: {data['order_fill_monitoring']['orders_tracked']} tracked, "
            f"{data['order_fill_monitoring']['success_rate_percent']}% success"
        )
    except Exception as e:
        print(f"   ❌ Failed: {e}")
        return False

    # Test 2: /health
    print("\n2. Testing GET /health")
    try:
        response = requests.get("http://127.0.0.1:5001/health", timeout=5)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

        data = response.json()
        assert "status" in data, "Missing 'status' field"
        assert "components" in data, "Missing 'components' field"
        assert "websocket" in data["components"], "Missing 'websocket' component"

        print(f"   ✅ System Status: {data['status']}")
        print(f"   WebSocket: {data['components']['websocket']['status']}")
        print(f"   Summary: {data['summary']}")
    except Exception as e:
        print(f"   ❌ Failed: {e}")
        return False

    # Test 3: /health/websocket/raw
    print("\n3. Testing GET /health/websocket/raw")
    try:
        response = requests.get("http://127.0.0.1:5001/health/websocket/raw", timeout=5)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

        data = response.json()
        assert "market_connection" in data, "Missing 'market_connection' field"
        assert "user_connection" in data, "Missing 'user_connection' field"
        assert "order_fill_monitoring" in data, "Missing 'order_fill_monitoring' field"

        print(f"   ✅ Raw metrics retrieved successfully")
        print(
            f"   Market reconnects: {data['market_connection']['total_disconnections']}"
        )
        print(f"   User reconnects: {data['user_connection']['total_disconnections']}")
    except Exception as e:
        print(f"   ❌ Failed: {e}")
        return False

    print("\n🎉 All health API tests passed!")
    return True


if __name__ == "__main__":
    try:
        success = test_health_api()
        if success:
            print("\n✅ B2.2 implementation successful!")
            sys.exit(0)
        else:
            print("\n❌ Some tests failed")
            sys.exit(1)
    except KeyboardInterrupt:
        print("\n⚠️  Test interrupted")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
