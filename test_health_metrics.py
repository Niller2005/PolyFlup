"""Quick test for Phase 3A B2.1 health metrics"""
import time
from src.utils.websocket_manager import ws_manager

def test_health_metrics_structure():
    """Verify health metrics structure exists"""
    print("Testing health metrics structure...")
    
    # Check attributes exist
    assert hasattr(ws_manager, 'health_metrics'), "health_metrics attribute missing"
    assert hasattr(ws_manager, '_metrics_lock'), "_metrics_lock attribute missing"
    assert hasattr(ws_manager, 'get_health_metrics'), "get_health_metrics method missing"
    
    # Check structure
    metrics = ws_manager.get_health_metrics()
    assert 'market_connection' in metrics, "market_connection missing"
    assert 'user_connection' in metrics, "user_connection missing"
    assert 'order_fill_monitoring' in metrics, "order_fill_monitoring missing"
    
    # Check market connection fields
    market = metrics['market_connection']
    assert 'status' in market
    assert 'total_connections' in market
    assert 'total_disconnections' in market
    assert 'messages_received' in market
    assert market['status'] == 'disconnected'  # Not started yet
    
    # Check order monitoring fields
    fills = metrics['order_fill_monitoring']
    assert 'orders_tracked' in fills
    assert 'fills_detected' in fills
    assert 'timeouts' in fills
    assert 'fallback_to_polling' in fills
    
    print("✅ Health metrics structure test passed!")
    print(f"   Market status: {market['status']}")
    print(f"   User status: {metrics['user_connection']['status']}")
    print(f"   Orders tracked: {fills['orders_tracked']}")
    return True

if __name__ == "__main__":
    try:
        test_health_metrics_structure()
        print("\n🎉 All tests passed! B2.1 implementation successful.")
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
