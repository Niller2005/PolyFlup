"""
Async-to-Sync Bridge for WebSocket Order Fill Monitoring

Phase 2 WebSocket Integration - Eliminates polling delays by using WebSocket
fill notifications instead of repeated get_order() API calls.

This module provides synchronous wrappers around the async WebSocket wait
methods, allowing existing synchronous trading code to benefit from real-time
WebSocket notifications without refactoring to async/await.

Performance Impact:
- Polling approach: 5-second delays, 120s max wait (24 API calls)
- WebSocket approach: Instant notification, 1-5s typical wait (0 API calls)
- Expected improvement: 75-90% faster order monitoring

Usage Examples:
    # Single order monitoring (e.g., emergency sell)
    order_data = wait_for_order_fill_sync(order_id, timeout=30)
    if order_data:
        print("Order filled!")

    # Batch order monitoring (e.g., atomic hedging)
    results = wait_for_batch_fill_sync(
        [entry_order_id, hedge_order_id],
        timeout=120
    )
    entry_filled = results.get(entry_order_id) is not None
    hedge_filled = results.get(hedge_order_id) is not None
"""

import asyncio
import time
from typing import List, Dict, Optional
from src.utils.logger import log, log_error
from src.utils.websocket_manager import ws_manager


def wait_for_order_fill_sync(
    order_id: str, timeout: float = 120, fallback_to_polling: bool = True
) -> Optional[dict]:
    """
    Synchronous wrapper for async WebSocket order fill waiting.

    Waits for a specific order to fill via WebSocket notification. If WebSocket
    is unavailable or times out, optionally falls back to polling.

    Args:
        order_id: The order ID to wait for
        timeout: Maximum seconds to wait (default 120)
        fallback_to_polling: If True and WebSocket fails, fall back to polling
                            (default True for safety)

    Returns:
        Order data dict when filled, or None on timeout/failure

    Example:
        # Wait for emergency sell to fill
        order_data = wait_for_order_fill_sync(order_id, timeout=10)
        if order_data:
            size_matched = float(order_data.get("size_matched", 0))
            print(f"Order filled: {size_matched} shares")
        else:
            print("Order not filled, cancelling...")
    """
    order_id_short = order_id[:10] if order_id and len(order_id) > 10 else order_id

    # Check if WebSocket is running
    if not ws_manager._running or not ws_manager._loop:
        if fallback_to_polling:
            log(
                f"   ⚠️  WebSocket not running for {order_id_short}, falling back to polling"
            )
            return _poll_order_fill(order_id, timeout)
        else:
            log(
                f"   ❌ WebSocket not running for {order_id_short}, cannot wait for fill"
            )
            return None

    # Try WebSocket waiting
    try:
        # Get the WebSocket event loop (runs in separate thread)
        loop = ws_manager._loop

        # Schedule the async wait in the WebSocket event loop
        future = asyncio.run_coroutine_threadsafe(
            ws_manager.wait_for_order_fill(order_id, timeout), loop
        )

        # Wait for result (blocking call in this thread)
        # Add 1s grace period to timeout for scheduling overhead
        order_data = future.result(timeout=timeout + 1)

        if order_data:
            log(f"   🎯 WebSocket fill detected for {order_id_short}")
            return order_data
        else:
            log(f"   ⏱️  WebSocket timeout for {order_id_short} after {timeout}s")
            return None

    except asyncio.TimeoutError:
        log(f"   ⏱️  WebSocket timeout for {order_id_short} after {timeout}s")
        return None

    except Exception as e:
        log_error(f"WebSocket wait error for {order_id_short}: {e}")

        # Fall back to polling if WebSocket fails
        if fallback_to_polling:
            log(f"   🔄 Falling back to polling for {order_id_short}")
            return _poll_order_fill(order_id, timeout)
        else:
            return None


def wait_for_batch_fill_sync(
    order_ids: List[str],
    timeout: float = 120,
    require_all: bool = True,
    fallback_to_polling: bool = True,
) -> Dict[str, Optional[dict]]:
    """
    Synchronous wrapper for async WebSocket batch order fill waiting.

    Waits for multiple orders to fill via WebSocket notifications. Optimized
    for atomic hedging where entry + hedge are placed simultaneously.

    Args:
        order_ids: List of order IDs to wait for
        timeout: Maximum seconds to wait (default 120)
        require_all: If True, wait for ALL orders to fill (default True)
                    If False, return as soon as ANY order fills
        fallback_to_polling: If True and WebSocket fails, fall back to polling
                            (default True for safety)

    Returns:
        Dict mapping order_id -> order_data (or None if not filled)

    Example:
        # Wait for atomic hedge pair to fill
        results = wait_for_batch_fill_sync(
            [entry_order_id, hedge_order_id],
            timeout=120,
            require_all=True
        )

        entry_data = results.get(entry_order_id)
        hedge_data = results.get(hedge_order_id)

        if entry_data and hedge_data:
            print("Both orders filled - trade complete!")
        else:
            print("Timeout - cancelling unfilled orders")
    """
    if not order_ids:
        return {}

    order_ids_short = [oid[:10] if len(oid) > 10 else oid for oid in order_ids]

    # Check if WebSocket is running
    if not ws_manager._running or not ws_manager._loop:
        if fallback_to_polling:
            log(
                f"   ⚠️  WebSocket not running for batch {order_ids_short}, falling back to polling"
            )
            return _poll_batch_fill(order_ids, timeout, require_all)
        else:
            log(
                f"   ❌ WebSocket not running for batch {order_ids_short}, cannot wait for fills"
            )
            return {order_id: None for order_id in order_ids}

    # Try WebSocket waiting
    try:
        # Get the WebSocket event loop (runs in separate thread)
        loop = ws_manager._loop

        # Schedule the async wait in the WebSocket event loop
        future = asyncio.run_coroutine_threadsafe(
            ws_manager.wait_for_orders_fill(order_ids, timeout, require_all), loop
        )

        # Wait for result (blocking call in this thread)
        # Add 1s grace period to timeout for scheduling overhead
        results = future.result(timeout=timeout + 1)

        # Log results
        filled_count = sum(1 for data in results.values() if data is not None)
        total_count = len(order_ids)

        if filled_count == total_count:
            log(f"   🎯 WebSocket: All {total_count} orders filled!")
        elif filled_count > 0:
            log(
                f"   🎯 WebSocket: {filled_count}/{total_count} orders filled (partial)"
            )
        else:
            log(f"   ⏱️  WebSocket: No orders filled after {timeout}s")

        return results

    except asyncio.TimeoutError:
        log(f"   ⏱️  WebSocket batch timeout after {timeout}s")
        return {order_id: None for order_id in order_ids}

    except Exception as e:
        log_error(f"WebSocket batch wait error: {e}")

        # Fall back to polling if WebSocket fails
        if fallback_to_polling:
            log(f"   🔄 Falling back to polling for batch {order_ids_short}")
            return _poll_batch_fill(order_ids, timeout, require_all)
        else:
            return {order_id: None for order_id in order_ids}


def _poll_order_fill(order_id: str, timeout: float) -> Optional[dict]:
    """
    Fallback polling implementation for single order monitoring.

    Used when WebSocket is unavailable or fails. Polls get_order() every 5
    seconds until order fills or timeout is reached.

    Args:
        order_id: Order ID to poll
        timeout: Maximum seconds to wait

    Returns:
        Order data dict when filled, or None on timeout
    """
    from src.trading.orders import get_order

    poll_interval = 5  # seconds
    start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            order_data = get_order(order_id)
            if order_data:
                size_matched = float(order_data.get("size_matched", 0))
                original_size = float(order_data.get("original_size", 0))

                # Check if filled (with 0.01 tolerance)
                if size_matched >= (original_size - 0.01):
                    return order_data

            # Wait before next poll
            time.sleep(poll_interval)

        except Exception as e:
            log_error(f"Error polling order {order_id[:10]}: {e}")
            time.sleep(poll_interval)

    # Timeout reached
    return None


def _poll_batch_fill(
    order_ids: List[str], timeout: float, require_all: bool
) -> Dict[str, Optional[dict]]:
    """
    Fallback polling implementation for batch order monitoring.

    Used when WebSocket is unavailable or fails. Polls get_order() for all
    orders every 5 seconds until conditions met or timeout reached.

    Args:
        order_ids: List of order IDs to poll
        timeout: Maximum seconds to wait
        require_all: If True, wait for ALL orders to fill
                    If False, return as soon as ANY order fills

    Returns:
        Dict mapping order_id -> order_data (or None if not filled)
    """
    from src.trading.orders import get_order

    poll_interval = 5  # seconds
    start_time = time.time()
    results = {order_id: None for order_id in order_ids}

    while time.time() - start_time < timeout:
        try:
            # Poll all unfilled orders
            for order_id in order_ids:
                if results[order_id] is not None:
                    continue  # Already filled

                order_data = get_order(order_id)
                if order_data:
                    size_matched = float(order_data.get("size_matched", 0))
                    original_size = float(order_data.get("original_size", 0))

                    # Check if filled (with 0.01 tolerance)
                    if size_matched >= (original_size - 0.01):
                        results[order_id] = order_data

            # Check exit conditions
            filled_count = sum(1 for data in results.values() if data is not None)

            if require_all and filled_count == len(order_ids):
                # All orders filled
                return results
            elif not require_all and filled_count > 0:
                # At least one order filled
                return results

            # Wait before next poll
            time.sleep(poll_interval)

        except Exception as e:
            log_error(f"Error polling batch orders: {e}")
            time.sleep(poll_interval)

    # Timeout reached
    return results
