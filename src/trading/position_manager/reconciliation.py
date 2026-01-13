"""Order reconciliation logic with comprehensive audit trail to prevent race conditions"""

import time
from typing import Dict, Any, Optional
from src.utils.logger import log
from src.trading.orders import get_order_status, get_order

# Track recently filled orders to prevent race conditions with position monitor
_recently_filled_orders: Dict[str, Dict[str, Any]] = {}


def track_recent_fill(
    order_id: str, price: float = None, size: float = None, timestamp: int = None
):
    """Track a recently filled order to prevent race condition cancellations"""
    if not order_id:
        return

    order_id_str = str(order_id)

    if timestamp is None:
        timestamp = int(time.time())

    _recently_filled_orders[order_id_str] = {
        "timestamp": timestamp,
        "price": price,
        "size": size,
    }

    # AUDIT: Recent fill tracking
    log(
        f"   🔍 RECONCILIATION AUDIT: Tracking recent fill for order {order_id_str[:10]} | size={size}, price=${price}, timestamp={timestamp}"
    )

    # Clean up old entries
    _cleanup_recent_fills()


def is_recently_filled(order_id: str, max_age_seconds: int = 300) -> bool:
    """Check if an order was recently filled (within last 5 minutes by default)"""
    if not order_id:
        return False

    order_id_str = str(order_id)
    _cleanup_recent_fills()

    fill_data = _recently_filled_orders.get(order_id_str)
    if not fill_data:
        return False

    current_time = int(time.time())
    age_seconds = current_time - fill_data["timestamp"]

    # AUDIT: Recent fill check
    is_recent = age_seconds <= max_age_seconds
    if is_recent:
        log(
            f"   🔍 RECONCILIATION AUDIT: Order {order_id_str[:10]} recently filled {age_seconds}s ago (within {max_age_seconds}s threshold)"
        )

    return is_recent


def get_recent_fill_data(order_id: str) -> Optional[Dict[str, Any]]:
    """Get fill data for a recently filled order"""
    _cleanup_recent_fills()
    return _recently_filled_orders.get(str(order_id))


def _cleanup_recent_fills():
    """Clean up recent fills older than 10 minutes"""
    current_time = int(time.time())
    cutoff_time = current_time - 600  # 10 minutes ago

    # Remove old entries
    old_orders = [
        order_id
        for order_id, data in _recently_filled_orders.items()
        if data["timestamp"] < cutoff_time
    ]

    for order_id in old_orders:
        # AUDIT: Cleaning up old fill tracking
        del _recently_filled_orders[order_id]
        log(
            f"   🧹 RECONCILIATION AUDIT: Cleaned up old fill tracking for order {order_id[:10]}"
        )


def safe_cancel_order(order_id: str, context: str = "") -> bool:
    """
    Safely cancel an order with verification to prevent race conditions

    Args:
        order_id: The order ID to cancel
        context: Context for logging (e.g., "scale-in", "exit-plan")

    Returns:
        True if order was cancelled or already filled/cancelled, False if error
    """
    if not order_id:
        return True

    order_id_str = str(order_id)
    # AUDIT: Safe cancellation process initiated
    log(
        f"   🔍 RECONCILIATION AUDIT: [{context}] Starting safe cancellation for order {order_id_str[:10]}"
    )

    try:
        # First check if this order was recently filled (race condition protection)
        if is_recently_filled(order_id):
            # AUDIT: Race condition prevention - order was recently filled
            fill_data = get_recent_fill_data(order_id)
            fill_size = fill_data.get("size", "unknown") if fill_data else "unknown"
            fill_price = fill_data.get("price", "unknown") if fill_data else "unknown"
            log(
                f"   ✅ [{context}] RACE CONDITION PREVENTED: Order {order_id_str[:10]} was recently filled (size={fill_size}, price=${fill_price}), skipping cancellation"
            )
            return True

        # Check current order status
        status = get_order_status(order_id)

        # AUDIT: Order status verification
        log(
            f"   🔍 RECONCILIATION AUDIT: [{context}] Order {order_id_str[:10]} current status: {status}"
        )

        # Don't try to cancel already completed orders
        if status in ["FILLED", "MATCHED", "CANCELED", "EXPIRED", "NOT_FOUND"]:
            if status in ["FILLED", "MATCHED"]:
                # AUDIT: Order already filled, track for race condition prevention
                track_recent_fill(order_id)
                log(
                    f"   ✅ [{context}] Order {order_id_str[:10]} already filled (Status: {status}), skipping cancellation"
                )
            elif status in ["CANCELED", "EXPIRED"]:
                log(
                    f"   🧹 [{context}] Order {order_id_str[:10]} already cancelled/expired (Status: {status}), no action needed"
                )
            return True

        # Get order details for additional verification
        order_data = get_order(order_id)
        if order_data:
            size = order_data.get("size_matched", 0)
            if float(size) > 0:
                # AUDIT: Order has matched size, track as filled
                track_recent_fill(order_id, size=size)
                log(
                    f"   ✅ [{context}] Order {order_id_str[:10]} has matched size {size}, skipping cancellation"
                )
                return True

        # Safe to cancel - attempt cancellation
        from src.trading.orders import cancel_order

        # AUDIT: Attempting order cancellation
        log(
            f"   🔧 [{context}] Attempting cancellation for order {order_id_str[:10]} (Status: {status})"
        )

        result = cancel_order(order_id)

        if result:
            # AUDIT: Cancellation successful
            log(
                f"   ✅ [{context}] Successfully cancelled order {order_id_str[:10]} (Status: {status})"
            )
        else:
            # AUDIT: Cancellation failed
            log(
                f"   ❌ [{context}] Failed to cancel order {order_id_str[:10]} (Status: {status})"
            )

        return result

    except Exception as e:
        # AUDIT: Error during cancellation process
        log(
            f"   ❌ [{context}] ERROR in safe_cancel_order for {order_id_str[:10]}: {e}"
        )
        return False


def verify_order_unfilled(order_id: str, max_retries: int = 2) -> bool:
    """
    Verify that an order is actually unfilled before operations that assume it's open

    Args:
        order_id: The order ID to verify
        max_retries: Maximum number of status checks

    Returns:
        True if order is confirmed unfilled/open, False if filled/cancelled/error
    """
    if not order_id:
        return False

    order_id_str = str(order_id)
    # AUDIT: Starting order verification process
    log(
        f"   🔍 RECONCILIATION AUDIT: Starting verification for order {order_id_str[:10]} (max_retries={max_retries})"
    )

    # First check recent fills cache
    if is_recently_filled(order_id):
        # AUDIT: Order found in recent fills cache
        log(f"   ⚠️  RECONCILIATION AUDIT: Order {order_id_str[:10]} found in recent fills cache, treating as filled")
        return False

    # Check status with retries and audit trail
    for attempt in range(max_retries):
        try:
            # AUDIT: Verification attempt
            log(f"   🔍 RECONCILIATION AUDIT: Verification attempt {attempt + 1}/{max_retries} for order {order_id_str[:10]}")

            status = get_order_status(order_id)

            # AUDIT: Status check result
            log(f"   🔍 RECONCILIATION AUDIT: Order {order_id_str[:10]} status on attempt {attempt + 1}: {status}")

            # Order is definitely completed
            if status in ["FILLED", "MATCHED", "CANCELED", "EXPIRED", "NOT_FOUND"]:
                # AUDIT: Order confirmed as completed
                log(f"   ✅ RECONCILIATION AUDIT: Order {order_id_str[:10]} confirmed as completed (Status: {status})")

                # Track filled orders for race condition prevention
                if status in ["FILLED", "MATCHED"]:
                    track_recent_fill(order_id)

                return False

            # Order appears to be open
            if status in ["LIVE", "PENDING", "OPEN"]:
                # AUDIT: Order confirmed as open
                log(f"   ✅ RECONCILIATION AUDIT: Order {order_id_str[:10]} confirmed as open (Status: {status})")
                return True

            # Unknown status, try again
            if attempt < max_retries - 1:
                # AUDIT: Retrying verification
                log(f"   ⏳ RECONCILIATION AUDIT: Unknown status for {order_id_str[:10]}, retrying in 0.1s...")
                time.sleep(0.1)  # Brief delay before retry

        except Exception as e:
            if attempt < max_retries - 1:
                # AUDIT: Error on attempt, retrying
                log(f"   ⚠️  RECONCILIATION AUDIT: Error on attempt {attempt + 1} for {order_id_str[:10]}: {e}, retrying...")
                time.sleep(0.1)  # Brief delay before retry
            else:
                # AUDIT: Final verification failure
                log(f"   ❌ RECONCILIATION AUDIT: Failed to verify order status for {order_id_str[:10]} after {max_retries} attempts: {e}")
                return False

    # If we get here, we couldn't confirm status - assume it's unfilled (safer)
    # AUDIT: Inconclusive verification, assuming unfilled for safety
    log(f"   ⚠️  RECONCILIATION AUDIT: Inconclusive verification for {order_id_str[:10]} after {max_retries} attempts, assuming unfilled for safety")
    return True

            # Unknown status, try again
            if attempt < max_retries - 1:
                # AUDIT: Retrying verification
                log(
                    f"   ⏳ RECONCILIATION AUDIT: Unknown status for {order_id[:10]}, retrying in 0.1s..."
                )
                time.sleep(0.1)  # Brief delay before retry

        except Exception as e:
            if attempt < max_retries - 1:
                # AUDIT: Error on attempt, retrying
                log(
                    f"   ⚠️  RECONCILIATION AUDIT: Error on attempt {attempt + 1} for {order_id[:10]}: {e}, retrying..."
                )
                time.sleep(0.1)  # Brief delay before retry
            else:
                # AUDIT: Final verification failure
                log(
                    f"   ❌ RECONCILIATION AUDIT: Failed to verify order status for {order_id[:10]} after {max_retries} attempts: {e}"
                )
                return False

    # If we get here, we couldn't confirm status - assume it's unfilled (safer)
    # AUDIT: Inconclusive verification, assuming unfilled for safety
    log(
        f"   ⚠️  RECONCILIATION AUDIT: Inconclusive verification for {order_id[:10]} after {max_retries} attempts, assuming unfilled for safety"
    )
    return True
