"""Trade execution utilities"""

import time
from typing import Optional, Dict, Any
from src.utils.logger import log, log_error, send_discord
from src.data.database import save_trade
from src.trading.orders import (
    place_order,
    place_batch_orders,
    get_order,
    get_balance_allowance,
    get_clob_client,
    cancel_order,
    place_limit_order,
    BUY,
    SELL,
)
from src.data.market_data import get_token_ids


def emergency_sell_position(
    symbol: str,
    token_id: str,
    size: float,
    reason: str,
    entry_order_id: Optional[str] = None,
    entry_price: Optional[float] = None,
) -> bool:
    """
    Emergency sell position using progressive pricing strategy.
    Uses orderbook bid prices when available, falls back to entry_price reference when not.

    Args:
        symbol: Trading symbol
        token_id: Token ID to sell
        size: Number of shares to sell
        reason: Reason for emergency exit
        entry_order_id: Entry order ID to look up any existing exit orders (optional)
        entry_price: Entry fill price for price reference fallback (optional)

    Returns True if sell order placed successfully, False otherwise.
    """
    try:
        # CRITICAL: Wait for balance API to sync after order fill
        # The entry order just filled, but balance API has 1-3s lag before shares are available
        log(f"   ⏳ [{symbol}] Waiting 3s for balance API to sync after fill...")
        time.sleep(3)

        # Query actual position size from exchange before selling
        # Order fill may be partial or have rounding, so we need exact balance
        from src.trading.orders.positions import get_balance_allowance

        balance_info = get_balance_allowance(token_id)
        if balance_info:
            actual_size = balance_info.get("balance", 0)
            if actual_size > 0 and abs(actual_size - size) > 0.01:
                log(
                    f"   ⚠️  [{symbol}] Adjusting sell size: requested {size:.2f}, actual balance {actual_size:.2f}"
                )
                size = actual_size
            elif actual_size <= 0:
                log(
                    f"   ⚠️  [{symbol}] No balance to sell (balance: {actual_size:.2f}), position may already be closed"
                )
                return False
        else:
            log(
                f"   ⚠️  [{symbol}] Could not verify balance, using requested size {size:.2f}"
            )

        # CRITICAL: Cancel any existing exit order first to free up shares
        # Look up trade by entry_order_id since trade might be saved to DB between
        # atomic pair placement and emergency sell (WebSocket can trigger exit plan)
        if entry_order_id:
            try:
                from src.data.db_connection import db_connection

                with db_connection() as conn:
                    c = conn.cursor()
                    c.execute(
                        "SELECT id, limit_sell_order_id FROM trades WHERE order_id = ? AND settled = 0",
                        (entry_order_id,),
                    )
                    row = c.fetchone()

                    if row and row[1]:
                        trade_id = row[0]
                        exit_order_id = row[1]
                        log(
                            f"   🚫 [{symbol}] Found existing exit order {exit_order_id[:10]} for trade #{trade_id} - cancelling to free shares"
                        )

                        cancel_result = cancel_order(exit_order_id)
                        if cancel_result:
                            log(f"   ✅ [{symbol}] Exit order cancelled successfully")
                            # Clear the exit order from database
                            c.execute(
                                "UPDATE trades SET limit_sell_order_id = NULL WHERE id = ?",
                                (trade_id,),
                            )
                        else:
                            log(
                                f"   ⚠️  [{symbol}] Failed to cancel exit order (may already be filled)"
                            )
            except Exception as cancel_exit_err:
                log_error(
                    f"[{symbol}] Error cancelling exit order before emergency sell: {cancel_exit_err}"
                )
                # Continue anyway - maybe the exit order is already filled

        # TIME-BASED PROGRESSIVE PRICING STRATEGY
        # Start at best bid for optimal price discovery, progressively lower with wait periods
        # This gives maker orders time to be taken while maximizing recovery value
        # Better than MARKET order which crosses spread instantly without price improvement
        log(
            f"   🚨 [{symbol}] EMERGENCY SELL: Starting progressive pricing for {size:.2f} shares due to {reason}"
        )
        from src.utils.websocket_manager import ws_manager

        best_bid = None
        orderbook_available = False

        # 1. Try WebSocket cache (fastest, real-time)
        ws_bid, _ = ws_manager.get_bid_ask(token_id)
        if ws_bid and ws_bid > 0.01:
            best_bid = ws_bid
            orderbook_available = True
            log(
                f"   📊 [{symbol}] Orderbook available: best bid = ${best_bid:.2f} (from WebSocket)"
            )
        else:
            # 2. Fallback to CLOB API orderbook query
            clob_client = get_clob_client()

            try:
                orderbook = clob_client.get_order_book(token_id)

                if orderbook:
                    # Handle both dict and OrderBookSummary object responses
                    if isinstance(orderbook, dict):
                        bids = orderbook.get("bids", []) or []
                    else:
                        bids = getattr(orderbook, "bids", []) or []

                    if bids and len(bids) > 0:
                        # Handle both dict and object bid entries
                        if hasattr(bids[0], "price"):
                            best_bid = float(bids[0].price)
                        elif isinstance(bids[0], dict):
                            best_bid = float(bids[0].get("price", 0))

                        if best_bid and best_bid > 0.01:
                            orderbook_available = True
                            log(
                                f"   📊 [{symbol}] Orderbook available: best bid = ${best_bid:.2f} (from API)"
                            )
                        else:
                            log(
                                f"   ⚠️  [{symbol}] Orderbook has invalid best bid: ${best_bid:.2f}"
                            )
                    else:
                        log(f"   ⚠️  [{symbol}] Orderbook has no bids (empty book)")
                else:
                    log(
                        f"   ⚠️  [{symbol}] Orderbook unavailable - skipping progressive pricing"
                    )
            except Exception as book_err:
                log(f"   ⚠️  [{symbol}] Error fetching orderbook: {book_err}")

        # TIME-BASED PROGRESSIVE PRICING: Place GTC orders with wait periods
        # Strategy: Start aggressive, progressively lower price with increasing wait times
        # This gives maker orders time to be taken while maximizing recovery value
        if orderbook_available and best_bid:
            from src.config.settings import (
                EMERGENCY_SELL_ENABLE_PROGRESSIVE,
                EMERGENCY_SELL_WAIT_SHORT,
                EMERGENCY_SELL_WAIT_MEDIUM,
                EMERGENCY_SELL_WAIT_LONG,
                EMERGENCY_SELL_FALLBACK_PRICE,
            )

            # Define pricing strategy: (price_description, price_value, wait_seconds, order_type)
            # Start with FOK for immediate attempts, then use GTC with wait periods
            attempts = [
                ("best bid (FOK)", best_bid, 0, "FOK"),  # Try instant fill first
                (
                    f"best bid - $0.01 (GTC {EMERGENCY_SELL_WAIT_SHORT}s)",
                    max(0.01, best_bid - 0.01),
                    EMERGENCY_SELL_WAIT_SHORT,
                    "GTC",
                ),
                (
                    f"best bid - $0.02 (GTC {EMERGENCY_SELL_WAIT_SHORT}s)",
                    max(0.01, best_bid - 0.02),
                    EMERGENCY_SELL_WAIT_SHORT,
                    "GTC",
                ),
                (
                    f"best bid - $0.03 (GTC {EMERGENCY_SELL_WAIT_SHORT}s)",
                    max(0.01, best_bid - 0.03),
                    EMERGENCY_SELL_WAIT_SHORT,
                    "GTC",
                ),
                (
                    f"best bid - $0.05 (GTC {EMERGENCY_SELL_WAIT_MEDIUM}s)",
                    max(0.01, best_bid - 0.05),
                    EMERGENCY_SELL_WAIT_MEDIUM,
                    "GTC",
                ),
                (
                    f"best bid - $0.10 (GTC {EMERGENCY_SELL_WAIT_LONG}s)",
                    max(0.01, best_bid - 0.10),
                    EMERGENCY_SELL_WAIT_LONG,
                    "GTC",
                ),
                (
                    f"$0.30 (GTC {EMERGENCY_SELL_WAIT_LONG}s)",
                    0.30,
                    EMERGENCY_SELL_WAIT_LONG,
                    "GTC",
                ),
                (
                    f"$0.20 (GTC {EMERGENCY_SELL_WAIT_LONG}s)",
                    0.20,
                    EMERGENCY_SELL_WAIT_LONG,
                    "GTC",
                ),
                (
                    f"$0.15 (GTC {EMERGENCY_SELL_WAIT_LONG}s)",
                    0.15,
                    EMERGENCY_SELL_WAIT_LONG,
                    "GTC",
                ),
            ]

            last_order_id = None

            for attempt_name, attempt_price, wait_seconds, order_type in attempts:
                # Cancel previous GTC order before placing new one
                if last_order_id and order_type == "GTC":
                    try:
                        cancel_order(last_order_id)
                        log(
                            f"   🚫 [{symbol}] Cancelled previous attempt {last_order_id[:10]}"
                        )
                    except Exception as cancel_err:
                        log(
                            f"   ⚠️  [{symbol}] Could not cancel {last_order_id[:10]}: {cancel_err}"
                        )

                log(
                    f"   🚨 [{symbol}] EMERGENCY SELL: Trying {size:.2f} shares at ${attempt_price:.2f} ({attempt_name})"
                )

                from src.trading.orders.limit import place_limit_order

                result = place_limit_order(
                    token_id=token_id,
                    price=attempt_price,
                    size=size,
                    side=SELL,
                    order_type=order_type,
                )

                if not result.get("success"):
                    log(
                        f"   ⚠️  [{symbol}] Order placement failed: {result.get('error', 'unknown')}"
                    )
                    continue

                order_id = result.get("order_id", "unknown")
                last_order_id = order_id

                # FOK orders either fill immediately or fail - no need to wait
                if order_type == "FOK":
                    # Check if filled (FOK returns success only if filled)
                    log(
                        f"   ✅ [{symbol}] Emergency sell filled: {size:.2f} @ ${attempt_price:.2f} ({attempt_name}) (ID: {order_id[:10] if order_id != 'unknown' else order_id})"
                    )
                    return True

                # GTC orders need time to be taken - wait and check status
                log(
                    f"   ⏱️  [{symbol}] Waiting {wait_seconds}s for GTC order to fill..."
                )

                # Poll order status during wait period (check every second)
                for elapsed in range(1, wait_seconds + 1):
                    time.sleep(1)

                    try:
                        order_status = get_order(order_id)
                        if order_status:
                            filled_size = float(order_status.get("size_matched", 0))
                            if filled_size >= (size - 0.01):
                                log(
                                    f"   ✅ [{symbol}] Emergency sell filled after {elapsed}s: {size:.2f} @ ${attempt_price:.2f} (ID: {order_id[:10]})"
                                )
                                return True
                    except Exception as poll_err:
                        log(f"   ⚠️  [{symbol}] Error checking order status: {poll_err}")

                # Order didn't fill in time - will cancel and try next price
                log(
                    f"   ⏰ [{symbol}] Order at ${attempt_price:.2f} not filled after {wait_seconds}s"
                )

            # Cancel last attempt before final fallback
            if last_order_id:
                try:
                    cancel_order(last_order_id)
                    log(f"   🚫 [{symbol}] Cancelled last attempt {last_order_id[:10]}")
                except Exception as cancel_err:
                    log(
                        f"   ⚠️  [{symbol}] Could not cancel {last_order_id[:10]}: {cancel_err}"
                    )
        elif entry_price and entry_price > 0.01:
            # FALLBACK STRATEGY: Use entry price as reference when orderbook unavailable
            # Entry filled at X, so opposite side should have liquidity near complement price
            # For hedged pairs: if entry @ $0.30, hedge @ $0.69 (sum = $0.99)
            log(
                f"   ⚠️  [{symbol}] Orderbook unavailable - using entry price ${entry_price:.2f} as reference"
            )

            from src.config.settings import (
                EMERGENCY_SELL_WAIT_SHORT,
                EMERGENCY_SELL_WAIT_MEDIUM,
                EMERGENCY_SELL_WAIT_LONG,
            )

            # Calculate complement price (opposite side of the hedge)
            # If selling entry: try near entry_price
            # If selling hedge: try near (0.99 - entry_price)
            # Start at entry_price and work down since we're selling
            reference_price = min(0.99, max(0.01, entry_price))

            attempts = [
                (
                    f"${reference_price:.2f} (entry ref, FOK)",
                    reference_price,
                    0,
                    "FOK",
                ),
                (
                    f"${reference_price - 0.01:.2f} (GTC {EMERGENCY_SELL_WAIT_SHORT}s)",
                    max(0.01, reference_price - 0.01),
                    EMERGENCY_SELL_WAIT_SHORT,
                    "GTC",
                ),
                (
                    f"${reference_price - 0.02:.2f} (GTC {EMERGENCY_SELL_WAIT_SHORT}s)",
                    max(0.01, reference_price - 0.02),
                    EMERGENCY_SELL_WAIT_SHORT,
                    "GTC",
                ),
                (
                    f"${reference_price - 0.05:.2f} (GTC {EMERGENCY_SELL_WAIT_MEDIUM}s)",
                    max(0.01, reference_price - 0.05),
                    EMERGENCY_SELL_WAIT_MEDIUM,
                    "GTC",
                ),
                (
                    f"${reference_price - 0.10:.2f} (GTC {EMERGENCY_SELL_WAIT_LONG}s)",
                    max(0.01, reference_price - 0.10),
                    EMERGENCY_SELL_WAIT_LONG,
                    "GTC",
                ),
                (
                    f"$0.30 (GTC {EMERGENCY_SELL_WAIT_LONG}s)",
                    0.30,
                    EMERGENCY_SELL_WAIT_LONG,
                    "GTC",
                ),
                (
                    f"$0.20 (GTC {EMERGENCY_SELL_WAIT_LONG}s)",
                    0.20,
                    EMERGENCY_SELL_WAIT_LONG,
                    "GTC",
                ),
                (
                    f"$0.15 (GTC {EMERGENCY_SELL_WAIT_LONG}s)",
                    0.15,
                    EMERGENCY_SELL_WAIT_LONG,
                    "GTC",
                ),
            ]

            last_order_id = None

            for attempt_name, attempt_price, wait_seconds, order_type in attempts:
                # Cancel previous GTC order before placing new one
                if last_order_id and order_type == "GTC":
                    try:
                        cancel_order(last_order_id)
                        log(
                            f"   🚫 [{symbol}] Cancelled previous attempt {last_order_id[:10]}"
                        )
                    except Exception as cancel_err:
                        log(
                            f"   ⚠️  [{symbol}] Could not cancel {last_order_id[:10]}: {cancel_err}"
                        )

                log(
                    f"   🚨 [{symbol}] EMERGENCY SELL: Trying {size:.2f} shares at ${attempt_price:.2f} ({attempt_name})"
                )

                from src.trading.orders.limit import place_limit_order

                result = place_limit_order(
                    token_id=token_id,
                    price=attempt_price,
                    size=size,
                    side=SELL,
                    order_type=order_type,
                )

                if not result.get("success"):
                    log(
                        f"   ⚠️  [{symbol}] Order placement failed: {result.get('error', 'unknown')}"
                    )
                    continue

                order_id = result.get("order_id", "unknown")
                last_order_id = order_id

                # FOK orders either fill immediately or fail - no need to wait
                if order_type == "FOK":
                    log(
                        f"   ✅ [{symbol}] Emergency sell filled: {size:.2f} @ ${attempt_price:.2f} ({attempt_name}) (ID: {order_id[:10] if order_id != 'unknown' else order_id})"
                    )
                    return True

                # GTC orders need time to be taken - wait and check status
                log(
                    f"   ⏱️  [{symbol}] Waiting {wait_seconds}s for GTC order to fill..."
                )

                # Poll order status during wait period (check every second)
                for elapsed in range(1, wait_seconds + 1):
                    time.sleep(1)

                    try:
                        order_status = get_order(order_id)
                        if order_status:
                            filled_size = float(order_status.get("size_matched", 0))
                            if filled_size >= (size - 0.01):
                                log(
                                    f"   ✅ [{symbol}] Emergency sell filled after {elapsed}s: {size:.2f} @ ${attempt_price:.2f} (ID: {order_id[:10]})"
                                )
                                return True
                    except Exception as poll_err:
                        log(f"   ⚠️  [{symbol}] Error checking order status: {poll_err}")

                # Order didn't fill in time - will cancel and try next price
                log(
                    f"   ⏰ [{symbol}] Order at ${attempt_price:.2f} not filled after {wait_seconds}s"
                )

            # Cancel last attempt before final fallback
            if last_order_id:
                try:
                    cancel_order(last_order_id)
                    log(f"   🚫 [{symbol}] Cancelled last attempt {last_order_id[:10]}")
                except Exception as cancel_err:
                    log(
                        f"   ⚠️  [{symbol}] Could not cancel {last_order_id[:10]}: {cancel_err}"
                    )
        else:
            log(
                f"   ⚠️  [{symbol}] Orderbook unavailable and no entry price reference - going to final fallback"
            )

        # FINAL FALLBACK: If all attempts failed, place GTC order at conservative price
        # Use configurable fallback price (default $0.10) - this will eventually fill
        from src.config.settings import EMERGENCY_SELL_FALLBACK_PRICE

        fallback_price = EMERGENCY_SELL_FALLBACK_PRICE
        log(
            f"   🚨 [{symbol}] EMERGENCY SELL (FINAL FALLBACK): Placing GTC at ${fallback_price:.2f} due to {reason}"
        )

        from src.trading.orders.limit import place_limit_order

        result = place_limit_order(
            token_id=token_id,
            price=fallback_price,
            size=size,
            side=SELL,
            order_type="GTC",  # Good-til-cancelled: will sit on book until filled
        )

        if result.get("success"):
            order_id = result.get("order_id", "unknown")
            log(
                f"   ✅ [{symbol}] Emergency sell order placed (GTC fallback): {size:.2f} @ ${fallback_price:.2f} (ID: {order_id[:10] if order_id != 'unknown' else order_id})"
            )
            return True
        else:
            log_error(
                f"[{symbol}] Emergency sell failed (all attempts): {result.get('error', 'unknown error')}"
            )
            return False

    except Exception as e:
        log_error(f"[{symbol}] Emergency sell exception: {e}")
        return False


def place_entry_and_hedge_atomic(
    symbol: str,
    entry_token_id: str,
    entry_side: str,
    entry_price: float,
    entry_size: float,
) -> tuple[Optional[dict], Optional[dict]]:
    """
    Place entry and hedge orders simultaneously using batch order API.
    This eliminates timing gaps and improves hedge fill rates.

    Returns:
        (entry_result, hedge_result) - Both are dicts with success, order_id, status
    """
    try:
        # Get token IDs
        up_id, down_id = get_token_ids(symbol)
        if not up_id or not down_id:
            return None, None

        # Determine hedge token and price
        if entry_side == "UP":
            hedge_token_id = down_id
            hedge_side = "DOWN"
        else:
            hedge_token_id = up_id
            hedge_side = "UP"

        # CRITICAL: Use MAKER pricing with POST_ONLY for both entry and hedge
        # Strategy: Entry at MAKER (bid+2¢) + Hedge at MAKER (calculated)
        # postOnly=True ensures orders won't cross spread (maker-only)
        # Maker orders earn 0.15% rebate instead of paying 1.54% taker fee
        # Combined must be <= $0.99 to guarantee profit on merge
        # Hedge price = $0.99 - entry_price

        hedge_price = round(0.99 - entry_price, 2)
        hedge_price = max(0.01, min(0.99, hedge_price))

        final_combined = entry_price + hedge_price

        log(
            f"   📊 [{symbol}] Both orders using MAKER (POST_ONLY) pricing: Entry ${entry_price:.2f} + Hedge ${hedge_price:.2f} (combined ${final_combined:.2f})"
        )

        # Create batch order - both use POST_ONLY for maker rebates
        # postOnly=True ensures orders are rejected if they would cross the spread
        orders = [
            {
                "token_id": entry_token_id,
                "price": entry_price,
                "size": entry_size,
                "side": BUY,
                "post_only": True,  # POST_ONLY: Earn 0.15% rebate, ensure whole number fills
            },
            {
                "token_id": hedge_token_id,
                "price": hedge_price,
                "size": entry_size,
                "side": BUY,
                "post_only": True,  # POST_ONLY: Earn 0.15% rebate
            },
        ]

        log(
            f"   🔄 [{symbol}] Placing ATOMIC entry+hedge: {entry_side} {entry_size:.1f} @ ${entry_price:.2f} + {hedge_side} {entry_size:.1f} @ ${hedge_price:.2f} (combined ${final_combined:.2f})"
        )

        # Submit both orders simultaneously
        results = place_batch_orders(orders)

        if len(results) < 2:
            log_error(f"[{symbol}] Batch order returned insufficient results")
            return None, None

        entry_result = results[0]
        hedge_result = results[1]

        # DEBUG: Log API responses to understand success/failure detection
        log(
            f"   🐛 [{symbol}] DEBUG Entry result: success={entry_result.get('success')}, order_id={entry_result.get('order_id')}, error={entry_result.get('error')}"
        )
        log(
            f"   🐛 [{symbol}] DEBUG Hedge result: success={hedge_result.get('success')}, order_id={hedge_result.get('order_id')}, error={hedge_result.get('error')}"
        )

        entry_success = entry_result.get("success")
        hedge_success = hedge_result.get("success")

        # CRITICAL: Handle order placement failures
        # If one succeeds and other fails, clean up the successful one

        # Case 1: Entry failed (e.g., POST_ONLY crossed book)
        if not entry_success and hedge_success:
            hedge_order_id = hedge_result.get("order_id")
            log(f"   ❌ [{symbol}] Entry order failed: {entry_result.get('error')}")
            hedge_id_display = hedge_order_id[:10] if hedge_order_id else "unknown"
            log(
                f"   ⚠️  [{symbol}] Hedge placed but entry failed - cancelling hedge {hedge_id_display}"
            )

            if not hedge_order_id:
                log_error(
                    f"[{symbol}] Cannot cancel hedge - order ID not returned from API"
                )
                return None, None

            try:
                cancel_order(hedge_order_id)
                log(f"   ✅ [{symbol}] Hedge order cancelled successfully")
            except Exception as cancel_err:
                log_error(
                    f"[{symbol}] Failed to cancel hedge order {hedge_order_id[:10]}: {cancel_err}"
                )
                # Try to sell the hedge if it filled quickly
                log(f"   💥 [{symbol}] Attempting emergency sell of hedge")
                time.sleep(2)  # Wait for potential fill
                hedge_token_id = hedge_result.get("token_id") or (
                    up_id if hedge_side == "UP" else down_id
                )
                emergency_sell_position(
                    symbol=symbol,
                    token_id=hedge_token_id,
                    size=entry_size,
                    reason="hedge without entry",
                    entry_order_id=hedge_order_id,
                    entry_price=hedge_price,  # Use hedge price as reference
                )
            return None, None

        # Case 2: Entry succeeded, hedge failed - RETRY HEDGE
        if entry_success and not hedge_success:
            entry_order_id = entry_result.get("order_id")
            log(f"   ❌ [{symbol}] Hedge order failed: {hedge_result.get('error')}")
            log(f"   🔄 [{symbol}] Entry succeeded - retrying hedge with fresh pricing")

            if not entry_order_id:
                log_error(
                    f"[{symbol}] Cannot manage entry - order ID not returned from API"
                )
                return None, None

            # Retry hedge up to 3 times with fresh orderbook pricing
            MAX_HEDGE_RETRIES = 3
            hedge_placed = False

            for retry in range(1, MAX_HEDGE_RETRIES + 1):
                log(f"   🔄 [{symbol}] Hedge retry {retry}/{MAX_HEDGE_RETRIES}")

                # Get fresh orderbook for hedge pricing
                try:
                    from src.utils.websocket_manager import ws_manager
                    from src.trading.orders import get_clob_client

                    hedge_bid = None
                    hedge_ask = None

                    # 1. Try WebSocket cache first (fastest, real-time)
                    ws_bid, ws_ask = ws_manager.get_bid_ask(hedge_token_id)
                    if ws_bid and ws_ask and ws_bid > 0.01 and ws_ask > 0.01:
                        hedge_bid = ws_bid
                        hedge_ask = ws_ask
                        log(
                            f"   📊 [{symbol}] Using WebSocket prices: bid=${hedge_bid:.2f}, ask=${hedge_ask:.2f}"
                        )
                    else:
                        # 2. Fallback to CLOB API orderbook query
                        client = get_clob_client()
                        book = client.get_order_book(hedge_token_id)

                        # Parse orderbook (handle both dict and object formats)
                        if isinstance(book, dict):
                            bids = book.get("bids", []) or []
                            asks = book.get("asks", []) or []
                        else:
                            bids = getattr(book, "bids", []) or []
                            asks = getattr(book, "asks", []) or []

                        if bids and asks:
                            # Get best bid/ask (last element is best price)
                            hedge_bid = float(
                                bids[-1].price
                                if hasattr(bids[-1], "price")
                                else bids[-1].get("price", 0)
                            )
                            hedge_ask = float(
                                asks[-1].price
                                if hasattr(asks[-1], "price")
                                else asks[-1].get("price", 0)
                            )
                            log(
                                f"   📊 [{symbol}] Using API prices: bid=${hedge_bid:.2f}, ask=${hedge_ask:.2f}"
                            )

                    if (
                        hedge_bid
                        and hedge_ask
                        and hedge_bid > 0.01
                        and hedge_ask > 0.01
                    ):
                        # CRITICAL: Maintain profitability - combined must be <= $0.99
                        # Calculate max hedge price from entry price
                        max_hedge_price = round(0.99 - entry_price, 2)
                        max_hedge_price = max(0.01, min(0.99, max_hedge_price))

                        # Try market pricing first (bid + 2¢), but cap at max_hedge_price
                        retry_hedge_price = round(hedge_bid + 0.02, 2)
                        retry_hedge_price = min(retry_hedge_price, max_hedge_price)
                        retry_hedge_price = max(0.01, min(0.99, retry_hedge_price))

                        combined_price = entry_price + retry_hedge_price

                        log(
                            f"   💹 [{symbol}] Hedge retry price: ${retry_hedge_price:.2f} (bid ${hedge_bid:.2f}, combined ${combined_price:.2f})"
                        )

                        # Place single hedge order
                        hedge_order = [
                            {
                                "token_id": hedge_token_id,
                                "price": retry_hedge_price,
                                "size": entry_size,
                                "side": BUY,
                                "post_only": True,
                            }
                        ]

                        retry_results = place_batch_orders(hedge_order)
                        if retry_results and retry_results[0].get("success"):
                            hedge_result = retry_results[0]
                            log(
                                f"   ✅ [{symbol}] Hedge retry succeeded: {hedge_side} {entry_size:.1f} @ ${retry_hedge_price:.2f}"
                            )
                            hedge_placed = True
                            break
                        else:
                            error = (
                                retry_results[0].get("error")
                                if retry_results
                                else "unknown"
                            )
                            log(f"   ⚠️  [{symbol}] Hedge retry {retry} failed: {error}")
                            time.sleep(1)  # Brief pause before next retry
                    else:
                        log(f"   ⚠️  [{symbol}] Empty orderbook for hedge retry")
                        time.sleep(1)
                except Exception as e:
                    log_error(f"[{symbol}] Error during hedge retry {retry}: {e}")
                    time.sleep(1)

            # If all retries failed, cancel entry and emergency sell if needed
            if not hedge_placed:
                entry_id_display = entry_order_id[:10] if entry_order_id else "unknown"
                log(
                    f"   ❌ [{symbol}] All hedge retries failed - cancelling entry {entry_id_display}"
                )
                try:
                    cancel_order(entry_order_id)
                    log(f"   ✅ [{symbol}] Entry order cancelled successfully")
                except Exception as cancel_err:
                    log_error(f"[{symbol}] Failed to cancel entry: {cancel_err}")
                    # Try to sell the entry if it filled quickly
                    log(f"   💥 [{symbol}] Attempting emergency sell of entry")
                    time.sleep(2)
                    emergency_sell_position(
                        symbol=symbol,
                        token_id=entry_token_id,
                        size=entry_size,
                        reason="entry without hedge after retries",
                        entry_order_id=entry_order_id,
                        entry_price=entry_price,  # Use entry price as reference
                    )
                return None, None

        # Case 3: Both failed
        if not entry_success and not hedge_success:
            log(
                f"   ❌ [{symbol}] Both orders failed - Entry: {entry_result.get('error')}, Hedge: {hedge_result.get('error')}"
            )
            return None, None

        # Log results for successful placements (both orders succeeded)
        log(
            f"   ✅ [{symbol}] Entry order placed: {entry_side} {entry_size:.1f} @ ${entry_price:.2f} (ID: {entry_result.get('order_id', 'unknown')[:10]})"
        )
        log(
            f"   ✅ [{symbol}] Hedge order placed: {hedge_side} {entry_size:.1f} @ ${hedge_price:.2f} (ID: {hedge_result.get('order_id', 'unknown')[:10]})"
        )

        # MONITOR: Poll both orders for fill status
        # If BOTH fill within timeout, save trade. Otherwise cancel BOTH.
        from src.config.settings import (
            HEDGE_FILL_TIMEOUT_SECONDS,
            HEDGE_POLL_INTERVAL_SECONDS,
        )

        entry_order_id = entry_result.get("order_id")
        hedge_order_id = hedge_result.get("order_id")

        if not entry_order_id or not hedge_order_id:
            log(f"   ⚠️  [{symbol}] Missing order IDs, skipping monitoring")
            return None, None

        log(
            f"   ⏱️  [{symbol}] Monitoring fills for {HEDGE_FILL_TIMEOUT_SECONDS}s (polling every {HEDGE_POLL_INTERVAL_SECONDS}s)..."
        )

        elapsed = 0
        both_filled = False

        while elapsed < HEDGE_FILL_TIMEOUT_SECONDS:
            time.sleep(HEDGE_POLL_INTERVAL_SECONDS)
            elapsed += HEDGE_POLL_INTERVAL_SECONDS

            try:
                # Check both orders
                entry_status = get_order(entry_order_id)
                hedge_status = get_order(hedge_order_id)

                entry_filled_size = 0.0
                hedge_filled_size = 0.0

                if entry_status:
                    entry_filled_size = float(entry_status.get("size_matched", 0))

                if hedge_status:
                    hedge_filled_size = float(hedge_status.get("size_matched", 0))

                entry_filled = entry_filled_size >= (entry_size - 0.01)
                hedge_filled = hedge_filled_size >= (entry_size - 0.01)

                # SUCCESS: Both filled!
                if entry_filled and hedge_filled:
                    log(
                        f"   ✅ [{symbol}] Both orders filled after {elapsed}s - trade complete!"
                    )
                    both_filled = True
                    break

                # Log partial fills
                if entry_filled_size > 0 or hedge_filled_size > 0:
                    log(
                        f"   ⏳ [{symbol}] Partial fills ({elapsed}s): Entry {entry_filled_size:.2f}/{entry_size:.1f}, Hedge {hedge_filled_size:.2f}/{entry_size:.1f}"
                    )

            except Exception as poll_err:
                log(f"   ⚠️  [{symbol}] Error polling order status: {poll_err}")

        # TIMEOUT: Cancel BOTH orders if not both filled
        if not both_filled:
            log(
                f"   ❌ [{symbol}] TIMEOUT: Both orders not filled after {HEDGE_FILL_TIMEOUT_SECONDS}s - cancelling both"
            )

            # Check final fill status before cancelling
            try:
                final_entry_status = get_order(entry_order_id)
                final_hedge_status = get_order(hedge_order_id)

                final_entry_filled_size = 0.0
                final_hedge_filled_size = 0.0

                if final_entry_status:
                    final_entry_filled_size = float(
                        final_entry_status.get("size_matched", 0)
                    )

                if final_hedge_status:
                    final_hedge_filled_size = float(
                        final_hedge_status.get("size_matched", 0)
                    )

                final_entry_filled = final_entry_filled_size >= (entry_size - 0.01)
                final_hedge_filled = final_hedge_filled_size >= (entry_size - 0.01)

                # CRITICAL: Handle partial fills with emergency sell
                # NEW BEHAVIOR: Emergency sell BOTH positions on ANY partial fill
                # to prevent orphaned positions
                if final_entry_filled and not final_hedge_filled:
                    # Entry filled but hedge didn't (or partially filled)
                    log(
                        f"   🚨 [{symbol}] CRITICAL: Entry filled ({final_entry_filled_size:.2f}) but hedge timed out (filled {final_hedge_filled_size:.2f}/{entry_size:.1f})"
                    )

                    # Emergency sell entry position
                    log(
                        f"   💥 [{symbol}] Emergency selling entry position ({final_entry_filled_size:.2f} shares)"
                    )
                    emergency_sell_position(
                        symbol=symbol,
                        token_id=entry_token_id,
                        size=final_entry_filled_size,
                        reason="hedge timeout after entry fill",
                        entry_order_id=entry_order_id,
                        entry_price=entry_price,  # Use entry price as reference
                    )

                    # If hedge partially filled, emergency sell those shares too
                    if final_hedge_filled_size > 0.01:
                        log(
                            f"   💥 [{symbol}] Emergency selling partial hedge position ({final_hedge_filled_size:.2f} shares)"
                        )
                        emergency_sell_position(
                            symbol=symbol,
                            token_id=hedge_token_id,
                            size=final_hedge_filled_size,
                            reason="partial hedge fill cleanup",
                            entry_order_id=hedge_order_id,
                            entry_price=hedge_price,  # Use hedge price as reference
                        )

                    # Cancel unfilled hedge
                    try:
                        cancel_order(hedge_order_id)
                        log(f"   ✅ [{symbol}] Hedge order cancelled")
                    except Exception as e:
                        log_error(f"[{symbol}] Error cancelling hedge: {e}")

                elif final_hedge_filled and not final_entry_filled:
                    # Hedge filled but entry didn't (or partially filled)
                    log(
                        f"   🚨 [{symbol}] CRITICAL: Hedge filled ({final_hedge_filled_size:.2f}) but entry timed out (filled {final_entry_filled_size:.2f}/{entry_size:.1f})"
                    )

                    # Emergency sell hedge position
                    log(
                        f"   💥 [{symbol}] Emergency selling hedge position ({final_hedge_filled_size:.2f} shares)"
                    )
                    emergency_sell_position(
                        symbol=symbol,
                        token_id=hedge_token_id,
                        size=final_hedge_filled_size,
                        reason="entry timeout after hedge fill",
                        entry_order_id=hedge_order_id,
                        entry_price=hedge_price,  # Use hedge price as reference
                    )

                    # If entry partially filled, emergency sell those shares too
                    if final_entry_filled_size > 0.01:
                        log(
                            f"   💥 [{symbol}] Emergency selling partial entry position ({final_entry_filled_size:.2f} shares)"
                        )
                        emergency_sell_position(
                            symbol=symbol,
                            token_id=entry_token_id,
                            size=final_entry_filled_size,
                            reason="partial entry fill cleanup",
                            entry_order_id=entry_order_id,
                            entry_price=entry_price,  # Use entry price as reference
                        )

                    # Cancel unfilled entry
                    try:
                        cancel_order(entry_order_id)
                        log(f"   ✅ [{symbol}] Entry order cancelled")
                    except Exception as e:
                        log_error(f"[{symbol}] Error cancelling entry: {e}")

                else:
                    # Neither filled or both partially filled - cancel both and sell any partials
                    log(
                        f"   🚨 [{symbol}] TIMEOUT: Neither order fully filled - cleaning up"
                    )

                    # Sell any partial entry fills
                    if final_entry_filled_size > 0.01:
                        log(
                            f"   💥 [{symbol}] Emergency selling partial entry position ({final_entry_filled_size:.2f} shares)"
                        )
                        emergency_sell_position(
                            symbol=symbol,
                            token_id=entry_token_id,
                            size=final_entry_filled_size,
                            reason="partial entry fill cleanup",
                            entry_order_id=entry_order_id,
                            entry_price=entry_price,  # Use entry price as reference
                        )

                    # Sell any partial hedge fills
                    if final_hedge_filled_size > 0.01:
                        log(
                            f"   💥 [{symbol}] Emergency selling partial hedge position ({final_hedge_filled_size:.2f} shares)"
                        )
                        emergency_sell_position(
                            symbol=symbol,
                            token_id=hedge_token_id,
                            size=final_hedge_filled_size,
                            reason="partial hedge fill cleanup",
                            entry_order_id=hedge_order_id,
                            entry_price=hedge_price,  # Use hedge price as reference
                        )

                    # Cancel entry
                    try:
                        cancel_order(entry_order_id)
                        log(f"   ✅ [{symbol}] Entry order cancelled")
                    except Exception as e:
                        log_error(f"[{symbol}] Error cancelling entry: {e}")

                    # Cancel hedge
                    try:
                        cancel_order(hedge_order_id)
                        log(f"   ✅ [{symbol}] Hedge order cancelled")
                    except Exception as e:
                        log_error(f"[{symbol}] Error cancelling hedge: {e}")

            except Exception as timeout_err:
                log_error(
                    f"[{symbol}] Error handling timeout with partial fills: {timeout_err}"
                )

            log(f"   🚫 [{symbol}] Trade skipped - atomic pair failed")
            return None, None

        return entry_result, hedge_result

    except Exception as e:
        log_error(f"[{symbol}] Error in atomic entry+hedge placement: {e}")
        return None, None


def execute_trade(
    trade_params: Dict[str, Any], is_reversal: bool = False, cursor=None
) -> Optional[int]:
    """
    Execute a trade and save to database.
    Returns trade_id if successful, None otherwise.
    """
    symbol = trade_params["symbol"]
    side = trade_params["side"]
    token_id = trade_params["token_id"]
    price = trade_params["price"]
    size = trade_params["size"]

    # Pre-flight balance check - MUST include hedge cost!
    # This prevents entry if we can't afford the full position + hedge
    # CRITICAL: Protects against unhedged positions due to insufficient balance
    entry_cost = size * price
    hedge_cost = size * (0.99 - price)  # Actual hedge cost (always $0.99 combined)
    total_cost_needed = entry_cost + hedge_cost

    bal_info = get_balance_allowance()
    if bal_info:
        usdc_balance = bal_info.get("balance", 0)
        log(
            f"   💰 [{symbol}] Balance check: ${usdc_balance:.2f} available, ${total_cost_needed:.2f} needed (entry ${entry_cost:.2f} + hedge ${hedge_cost:.2f})"
        )
        if usdc_balance < total_cost_needed:
            log(
                f"   ❌ [{symbol}] REJECTED: Insufficient funds (Need ${total_cost_needed:.2f}, Have ${usdc_balance:.2f})"
            )
            return None

    # ATOMIC PLACEMENT: Place entry and hedge simultaneously (unless reversal)
    hedge_order_id = None
    actual_size = size
    actual_price = price

    if not is_reversal:
        entry_result, hedge_result = place_entry_and_hedge_atomic(
            symbol, token_id, side, price, size
        )

        if not entry_result or not entry_result.get("success"):
            log(
                f"[{symbol}] ❌ Entry order failed: {entry_result.get('error') if entry_result else 'unknown error'}"
            )
            return None

        result = entry_result
        order_id = result.get("order_id")
        actual_status = result.get("status", "UNKNOWN")

        # Store hedge order ID if successful
        if hedge_result and hedge_result.get("success"):
            hedge_order_id = hedge_result.get("order_id")
        else:
            log(
                f"   ⚠️  [{symbol}] Hedge order failed, position will be unhedged: {hedge_result.get('error') if hedge_result else 'unknown error'}"
            )
    else:
        # Reversal trades don't get hedged - place single order
        result = place_order(token_id, price, size)

        if not result["success"]:
            log(f"[{symbol}] ❌ Order failed: {result.get('error')}")
            return None

        order_id = result["order_id"]
        actual_status = result["status"]

    # Sync actual fill details
    # Try to sync execution details immediately if filled
    if actual_status.upper() in ["FILLED", "MATCHED"]:
        try:
            if order_id:
                o_data = get_order(order_id)
                if o_data:
                    sz_m = float(o_data.get("size_matched", 0))
                    pr_m = float(o_data.get("price", 0))
                    if sz_m > 0:
                        actual_size = sz_m
                        if pr_m > 0:
                            actual_price = pr_m
                        trade_params["bet_usd"] = actual_size * actual_price
        except Exception as e:
            log_error(f"[{symbol}] Could not sync execution details immediately: {e}")

    # Discord notification
    reversal_prefix = "🔄 REVERSAL " if is_reversal else ""
    send_discord(
        f"**{reversal_prefix}[{symbol}] {side} ${trade_params['bet_usd']:.2f}** | Confidence {trade_params['confidence']:.1%} | Price {actual_price:.4f}"
    )

    try:
        raw_scores = trade_params.get("raw_scores", {})
        trade_id = save_trade(
            cursor=cursor,
            symbol=symbol,
            window_start=trade_params["window_start"].isoformat()
            if hasattr(trade_params["window_start"], "isoformat")
            else trade_params["window_start"],
            window_end=trade_params["window_end"].isoformat()
            if hasattr(trade_params["window_end"], "isoformat")
            else trade_params["window_end"],
            slug=trade_params["slug"],
            token_id=token_id,
            side=side,
            edge=trade_params["confidence"],
            price=actual_price,
            size=actual_size,
            bet_usd=trade_params["bet_usd"],
            p_yes=trade_params.get("p_up", 0.5),
            best_bid=trade_params.get("best_bid"),
            best_ask=trade_params.get("best_ask"),
            imbalance=trade_params.get("imbalance", 0.5),
            funding_bias=trade_params.get("funding_bias", 0.0),
            order_status=actual_status,
            order_id=order_id,
            limit_sell_order_id=None,
            is_reversal=is_reversal,
            target_price=trade_params.get("target_price"),
            up_total=raw_scores.get("up_total"),
            down_total=raw_scores.get("down_total"),
            momentum_score=raw_scores.get("momentum_score"),
            momentum_dir=raw_scores.get("momentum_dir"),
            flow_score=raw_scores.get("flow_score"),
            flow_dir=raw_scores.get("flow_dir"),
            divergence_score=raw_scores.get("divergence_score"),
            divergence_dir=raw_scores.get("divergence_dir"),
            vwm_score=raw_scores.get("vwm_score"),
            vwm_dir=raw_scores.get("vwm_dir"),
            pm_mom_score=raw_scores.get("pm_mom_score"),
            pm_mom_dir=raw_scores.get("pm_mom_dir"),
            adx_score=raw_scores.get("adx_score"),
            adx_dir=raw_scores.get("adx_dir"),
            lead_lag_bonus=raw_scores.get("lead_lag_bonus"),
            additive_confidence=raw_scores.get("additive_confidence"),
            additive_bias=raw_scores.get("additive_bias"),
            bayesian_confidence=raw_scores.get("bayesian_confidence"),
            bayesian_bias=raw_scores.get("bayesian_bias"),
            market_prior_p_up=raw_scores.get("market_prior_p_up"),
            condition_id=trade_params.get("condition_id"),
        )

        # Update hedge_order_id and hedge verification status now that we have trade_id
        if hedge_order_id:
            # Check if hedge was already verified during placement (immediate verification ran)
            # Note: place_hedge_order runs 2-second verification but couldn't update DB with trade_id=0
            # Now we need to check again and update the DB properly
            # CRITICAL: Also verify entry order is still valid before marking as HEDGED
            try:
                # Check BOTH entry and hedge orders
                entry_status = get_order(order_id) if order_id else None
                hedge_status = get_order(hedge_order_id)

                entry_filled = False
                hedge_filled = False
                entry_cancelled = False

                if entry_status:
                    entry_filled_size = float(entry_status.get("size_matched", 0))
                    entry_order_status = entry_status.get("status", "").upper()
                    # Entry is valid if filled or partially filled
                    entry_filled = entry_filled_size >= actual_size - 0.01
                    entry_cancelled = entry_order_status in ["CANCELLED", "CANCELED"]

                    if entry_cancelled:
                        log(
                            f"   ⚠️  [{symbol}] #{trade_id} Entry order was cancelled - position is NOT hedged"
                        )

                if hedge_status:
                    hedge_filled_size = float(hedge_status.get("size_matched", 0))
                    # Use 0.01 tolerance (99%+ filled = fully hedged)
                    hedge_filled = hedge_filled_size >= actual_size - 0.01

                # ONLY mark as hedged if BOTH entry AND hedge are filled
                if entry_filled and hedge_filled and not entry_cancelled:
                    if cursor:
                        cursor.execute(
                            "UPDATE trades SET hedge_order_id = ?, is_hedged = 1, order_status = 'HEDGED' WHERE id = ?",
                            (hedge_order_id, trade_id),
                        )
                        log(
                            f"   ✅ [{symbol}] #{trade_id} Hedge verified filled - position fully hedged"
                        )
                    else:
                        from src.data.db_connection import db_connection

                        with db_connection() as conn:
                            c = conn.cursor()
                            c.execute(
                                "UPDATE trades SET hedge_order_id = ?, is_hedged = 1, order_status = 'HEDGED' WHERE id = ?",
                                (hedge_order_id, trade_id),
                            )
                            log(
                                f"   ✅ [{symbol}] #{trade_id} Hedge verified filled - position fully hedged"
                            )
                else:
                    # At least one order is not filled/cancelled - save hedge_order_id but don't mark as hedged
                    if cursor:
                        cursor.execute(
                            "UPDATE trades SET hedge_order_id = ? WHERE id = ?",
                            (hedge_order_id, trade_id),
                        )
                    else:
                        from src.data.db_connection import db_connection

                        with db_connection() as conn:
                            c = conn.cursor()
                            c.execute(
                                "UPDATE trades SET hedge_order_id = ? WHERE id = ?",
                                (hedge_order_id, trade_id),
                            )

                    # Log specific reason why not hedged
                    if entry_cancelled:
                        log(
                            f"   ⚠️  [{symbol}] #{trade_id} Entry order cancelled - NOT marking as hedged"
                        )
                    elif not entry_filled:
                        log(
                            f"   ⚠️  [{symbol}] #{trade_id} Entry not yet filled - NOT marking as hedged"
                        )
                    elif not hedge_filled:
                        log(
                            f"   ⚠️  [{symbol}] #{trade_id} Hedge not yet filled - NOT marking as hedged"
                        )
            except Exception as verify_err:
                log_error(f"[{symbol}] Error verifying hedge after save: {verify_err}")
                # Still save hedge_order_id even if verification failed
                if cursor:
                    cursor.execute(
                        "UPDATE trades SET hedge_order_id = ? WHERE id = ?",
                        (hedge_order_id, trade_id),
                    )
                else:
                    from src.data.db_connection import db_connection

                    with db_connection() as conn:
                        c = conn.cursor()
                        c.execute(
                            "UPDATE trades SET hedge_order_id = ? WHERE id = ?",
                            (hedge_order_id, trade_id),
                        )

        emoji = trade_params.get("emoji", "🚀")
        entry_type = trade_params.get("entry_type", "Trade")
        log(
            f"{emoji} [{symbol}] {entry_type}: {trade_params.get('core_summary', '')} | #{trade_id} {side} ${trade_params['bet_usd']:.2f} @ {actual_price:.4f} | ID: {order_id[:10] if order_id else 'N/A'}"
        )
        return trade_id
    except Exception as e:
        log_error(f"[{symbol}] Trade completion error: {e}")
        return None
