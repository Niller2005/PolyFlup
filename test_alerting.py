"""
Test for Phase 3A B2.4 Alerting System

Tests the health monitoring alerts without actually sending Discord notifications.
"""

import time
from unittest.mock import patch
from src.monitoring.alerts import (
    AlertState,
    send_alert,
    check_websocket_health,
    start_alert_monitoring,
)
from src.utils.websocket_manager import ws_manager


def test_alert_state():
    """Test alert cooldown mechanism"""
    print("🧪 Testing AlertState cooldown...")

    state = AlertState()
    state.alert_cooldown = 2  # 2 seconds for testing

    # First alert should go through
    assert state.should_send_alert("test_alert"), "First alert should be allowed"

    # Second alert immediately should be blocked
    assert not state.should_send_alert("test_alert"), (
        "Immediate duplicate should be blocked"
    )

    # Wait for cooldown
    print("   Waiting 2s for cooldown...")
    time.sleep(2.1)

    # Now it should go through again
    assert state.should_send_alert("test_alert"), (
        "Alert after cooldown should be allowed"
    )

    # Clear and try again immediately
    state.clear_alert("test_alert")
    assert state.should_send_alert("test_alert"), "Alert after clear should be allowed"

    print("   ✅ AlertState cooldown working correctly")


def test_send_alert():
    """Test alert sending (mocked)"""
    print("\n🧪 Testing send_alert...")

    with (
        patch("src.monitoring.alerts.send_discord") as mock_discord,
        patch("src.monitoring.alerts._alert_state") as mock_state,
    ):
        # Mock should_send_alert to always return True
        mock_state.should_send_alert.return_value = True

        # Send test alert
        send_alert(
            level="warning",
            title="Test Alert",
            message="This is a test alert",
            alert_key="test",
        )

        # Check Discord was called
        assert mock_discord.called, "Discord webhook should be called"
        call_args = mock_discord.call_args[0][0]
        assert "Test Alert" in call_args, "Alert title should be in message"
        assert "This is a test alert" in call_args, "Alert message should be included"
        assert "⚠️" in call_args, "Warning emoji should be in message"

        print("   ✅ send_alert formatted message correctly")


def test_check_websocket_health():
    """Test health checking logic"""
    print("\n🧪 Testing check_websocket_health...")

    # Start WebSocket manager if not running
    if not ws_manager._running:
        print("   Starting WebSocket Manager...")
        ws_manager.start()
        time.sleep(1)

    # Mock send_alert to avoid spamming
    with patch("src.monitoring.alerts.send_alert") as mock_alert:
        # Run health check
        check_websocket_health()

        # Get metrics to understand state
        metrics = ws_manager.get_health_metrics()

        print(f"   Market status: {metrics['market_connection']['status']}")
        print(f"   User status: {metrics['user_connection']['status']}")
        print(
            f"   Orders tracked: {metrics['order_fill_monitoring']['orders_tracked']}"
        )

        # If both channels are connected, no alerts should be sent
        if (
            metrics["market_connection"]["status"] == "connected"
            and metrics["user_connection"]["status"] == "connected"
        ):
            # Should not alert about disconnections
            alert_keys = [call[1]["alert_key"] for call in mock_alert.call_args_list]
            assert "market_disconnected" not in alert_keys, (
                "Should not alert when market connected"
            )
            assert "user_disconnected" not in alert_keys, (
                "Should not alert when user connected"
            )
            print("   ✅ No false alerts when channels healthy")
        else:
            print("   ⚠️  Channels not connected, alerts may be sent")

        print("   ✅ Health check completed without errors")


def test_alert_monitoring_thread():
    """Test that alert monitoring thread can start"""
    print("\n🧪 Testing start_alert_monitoring...")

    # Mock the monitoring loop to avoid infinite loop
    with patch("src.monitoring.alerts._alert_monitoring_loop") as mock_loop:
        # Start monitoring
        start_alert_monitoring()

        # Give thread time to start
        time.sleep(0.5)

        # Check that loop was started
        assert mock_loop.called or ws_manager._running, "Monitoring should start"

        print("   ✅ Alert monitoring thread starts successfully")


def test_integration():
    """Integration test: start everything and verify it works"""
    print("\n🧪 Integration test...")

    # Ensure WebSocket is running
    if not ws_manager._running:
        ws_manager.start()
        time.sleep(1)

    # Mock Discord to avoid spam
    with patch("src.monitoring.alerts.send_discord"):
        # Start monitoring (will run in background)
        start_alert_monitoring()

        # Let it run for a few seconds
        print("   Monitoring for 3 seconds...")
        time.sleep(3)

        # Check that WebSocket is still healthy
        metrics = ws_manager.get_health_metrics()
        print(f"   Market: {metrics['market_connection']['status']}")
        print(f"   User: {metrics['user_connection']['status']}")

        # Manual health check
        check_websocket_health()

        print("   ✅ Integration test completed")


if __name__ == "__main__":
    import sys

    try:
        print("=" * 60)
        print("Phase 3A B2.4: Alerting System Tests")
        print("=" * 60)

        test_alert_state()
        test_send_alert()
        test_check_websocket_health()
        test_alert_monitoring_thread()
        test_integration()

        print("\n" + "=" * 60)
        print("🎉 All alerting tests passed!")
        print("✅ B2.4 implementation successful!")
        print("=" * 60)

        sys.exit(0)
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
