"""Trade execution utilities"""

import time
from typing import Optional, Dict, Any
from src.utils.logger import fmt_num, log, log_error, send_discord
from src.data.database import save_trade
from src.trading.orders import (
    place_order,
    place_batch_orders,
    get_order,
    get_balance_allowance,
    get_clob_client,
    cancel_order,
    BUY,
    SELL,
    MIN_ORDER_SIZE,
)
from src.data.market_data import get_token_ids

# Track POST_ONLY failures per symbol to switch to GTC after repeated crossing
# Key: symbol, Value: consecutive failure count
_post_only_failures: Dict[str, int] = {}
_last_post_only_attempt: Dict[str, float] = {}  # Timestamp of last attempt per symbol
_trade_count_since_post_only: Dict[str, int] = {}  # Trades since switching to GTC

MAX_POST_ONLY_ATTEMPTS = 3  # Switch to GTC after this many failures
POST_ONLY_RESET_TIMEOUT = 300  # seconds (5 minutes) - reset counter if market settles
POST_ONLY_RETRY_INTERVAL = 10  # Try POST_ONLY every 10th trade after threshold


def _book_top(token_id: str, fresh: bool = False) -> tuple:
    """(best_bid, best_ask) from the websocket cache (unless `fresh`), else the CLOB book.

    Quote maker orders from a fresh book: a stale cached top can sit on the wrong side of
    a moved market and the POST_ONLY order then crosses and is rejected.
    """
    from src.utils.websocket_manager import ws_manager

    bid, ask = (None, None) if fresh else ws_manager.get_bid_ask(token_id)
    if bid and ask and bid > 0.0 and ask > 0.0:
        return float(bid), float(ask)
    try:
        from src.trading.orders import get_clob_client

        book = get_clob_client().get_order_book(token_id)
        bids = [float(x["price"]) for x in (book.get("bids") or [])]
        asks = [float(x["price"]) for x in (book.get("asks") or [])]
        return (max(bids) if bids else None), (min(asks) if asks else None)
    except Exception:
        return None, None


def should_use_post_only(symbol: str) -> bool:
    """
    Determine if POST_ONLY should be used for this symbol based on failure history.

    Smart retry logic:
    - Use POST_ONLY if under failure threshold
    - Reset counter if 5+ minutes elapsed (market likely settled)
    - Test POST_ONLY every 10th trade after threshold (auto-recovery)

    Args:
        symbol: Trading symbol

    Returns:
        bool: True if POST_ONLY should be used, False for GTC
    """
    import time

    # Check if enough time has passed to reset counter (market settled)
    last_attempt = _last_post_only_attempt.get(symbol, 0)
    if last_attempt and (time.time() - last_attempt) > POST_ONLY_RESET_TIMEOUT:
        _post_only_failures[symbol] = 0
        _trade_count_since_post_only[symbol] = 0
        log(
            f"   🔄 [{symbol}] POST_ONLY failure counter reset (market settled after 5min)"
        )

    failures = _post_only_failures.get(symbol, 0)

    # Under threshold: use POST_ONLY
    if failures < MAX_POST_ONLY_ATTEMPTS:
        _last_post_only_attempt[symbol] = time.time()
        return True

    # Over threshold: test POST_ONLY periodically for auto-recovery
    trade_count = _trade_count_since_post_only.get(symbol, 0)
    if trade_count % POST_ONLY_RETRY_INTERVAL == 0:
        log(
            f"   🔄 [{symbol}] Testing POST_ONLY again (periodic retry, failures: {failures})"
        )
        _last_post_only_attempt[symbol] = time.time()
        return True

    # Default: use GTC (taker)
    return False


def record_post_only_result(symbol: str, success: bool) -> None:
    """
    Record the result of a POST_ONLY order attempt.

    Args:
        symbol: Trading symbol
        success: True if both orders placed successfully, False if crossed
    """
    if success:
        # Reset counter on success
        _post_only_failures[symbol] = 0
        _trade_count_since_post_only[symbol] = 0
        log(f"   ✅ [{symbol}] POST_ONLY success - earning maker rebate share")
    else:
        # Increment failure counter
        _post_only_failures[symbol] = _post_only_failures.get(symbol, 0) + 1
        _trade_count_since_post_only[symbol] = (
            _trade_count_since_post_only.get(symbol, 0) + 1
        )
        failures = _post_only_failures[symbol]

        if failures >= MAX_POST_ONLY_ATTEMPTS:
            log(
                f"   🔄 [{symbol}] Switching to GTC (taker) after {failures} POST_ONLY failures"
            )
        else:
            log(
                f"   ⚠️  [{symbol}] POST_ONLY crossing failure #{failures}/{MAX_POST_ONLY_ATTEMPTS}"
            )


def _wait_for_orders_hybrid(
    order_ids: list, timeout: float, symbol: str
) -> Dict[str, Optional[dict]]:
    """
    Hybrid monitoring: Try WebSocket first, fall back to polling.

    Phase 2 WebSocket integration - uses WebSocket for instant notifications
    when available, falls back to polling for reliability.

    Args:
        order_ids: List of order IDs to monitor
        timeout: Maximum seconds to wait
        symbol: Trading symbol (for logging)

    Returns:
        Dict mapping order_id -> order_data (or None if not filled)
    """
    from src.config.settings import ENABLE_WEBSOCKET_MONITORING
    from src.utils.websocket_manager import ws_manager

    # Try WebSocket path if enabled and running
    if ENABLE_WEBSOCKET_MONITORING and ws_manager._running:
        try:
            from src.trading.orders.async_monitoring import wait_for_batch_fill_sync

            log(f"   🎯 [{symbol}] Using WebSocket monitoring")
            results = wait_for_batch_fill_sync(
                order_ids,
                timeout=timeout,
                require_all=True,
                fallback_to_polling=False,  # We handle fallback here
            )

            # Check if any fills detected
            filled_count = sum(1 for data in results.values() if data is not None)
            if filled_count > 0:
                return results  # WebSocket detected fills!

            # WebSocket timed out, but might have missed notifications
            # Fall through to polling to verify
            log(f"   ⚠️  [{symbol}] WebSocket timeout, verifying with polling...")

        except Exception as e:
            log(f"   ⚠️  [{symbol}] WebSocket error: {e}, falling back to polling")

    # Polling fallback - always verify final state with get_order()
    results = {}
    for order_id in order_ids:
        try:
            order_data = get_order(order_id)
            results[order_id] = order_data
        except Exception as e:
            log(f"   ⚠️  [{symbol}] Error getting order {order_id[:10]}: {e}")
            results[order_id] = None

    return results


def _record_exit_price(
    entry_order_id: Optional[str], exit_price: float, symbol: str
) -> None:
    """
    Record the exit price in the database for P&L tracking.
    Used when emergency selling a position (pre-settlement or partial fill cleanup).

    Args:
        entry_order_id: The order_id or hedge_order_id of the trade being sold
        exit_price: The actual price the position was sold at
        symbol: Trading symbol (for logging)
    """
    if not entry_order_id:
        return

    try:
        from src.data.db_connection import db_connection

        with db_connection() as conn:
            c = conn.cursor()

            # Check if this is an entry order or hedge order
            c.execute(
                "SELECT id, order_id, hedge_order_id FROM trades WHERE order_id = ? OR hedge_order_id = ?",
                (entry_order_id, entry_order_id),
            )
            result = c.fetchone()

            if not result:
                log(
                    f"   ⚠️  [{symbol}] No trade found for order_id {entry_order_id[:10]}..."
                )
                return

            trade_id, order_id, hedge_order_id = result

            # Determine if this is entry or hedge exit
            if entry_order_id == order_id:
                # Exiting entry side
                c.execute(
                    "UPDATE trades SET exit_price = ?, exited_early = 1 WHERE id = ?",
                    (exit_price, trade_id),
                )
                log(
                    f"   💾 [{symbol}] #{trade_id} Recorded ENTRY exit price ${exit_price:.2f}"
                )
            elif entry_order_id == hedge_order_id:
                # Exiting hedge side
                c.execute(
                    "UPDATE trades SET hedge_exit_price = ?, hedge_exited_early = 1 WHERE id = ?",
                    (exit_price, trade_id),
                )
                log(
                    f"   💾 [{symbol}] #{trade_id} Recorded HEDGE exit price ${exit_price:.2f}"
                )

    except Exception as e:
        log_error(f"[{symbol}] Failed to record exit price: {e}")


def emergency_sell_position(
    symbol: str,
    token_id: str,
    size: float,
    reason: str,
    entry_order_id: Optional[str] = None,
    entry_price: Optional[float] = None,
    window_end: Optional[str] = None,
) -> bool:
    """
    Emergency sell position using time-aware progressive pricing strategy.
    Uses orderbook bid prices when available, falls back to entry_price reference when not.

    Adjusts urgency based on time remaining in window:
    - Early window (>600s): Patient - smaller drops, longer waits
    - Mid window (300-600s): Balanced - moderate drops
    - Late window (<300s): Aggressive - rapid drops to ensure liquidation

    Args:
        symbol: Trading symbol
        token_id: Token ID to sell
        size: Number of shares to sell
        reason: Reason for emergency exit
        entry_order_id: Entry order ID to look up any existing exit orders (optional)
        entry_price: Entry fill price for price reference fallback (optional)
        window_end: Window end time (ISO format) for time-aware pricing (optional)

    Returns True if sell order placed successfully, False otherwise.
    """
    try:
        # OPTIMIZED: Smart balance sync polling instead of fixed 3s wait
        # The entry order just filled, but balance API has 1-3s lag before shares are available
        # Poll with exponential backoff up to 3s max
        from src.trading.orders.positions import get_balance_allowance

        MAX_BALANCE_WAIT = 3.0  # seconds
        POLL_INTERVAL = 0.1  # seconds (100ms)
        balance_start = time.time()
        actual_size = 0
        balance_synced = False

        log(
            f"   ⏳ [{symbol}] Polling balance API for sync (max {MAX_BALANCE_WAIT}s)..."
        )

        while time.time() - balance_start < MAX_BALANCE_WAIT:
            balance_info = get_balance_allowance(token_id)
            if balance_info:
                actual_size = balance_info.get("balance", 0)
                if actual_size > 0:
                    # Balance synced and non-zero!
                    elapsed = time.time() - balance_start
                    log(
                        f"   ✅ [{symbol}] Balance synced after {elapsed:.2f}s: {actual_size:.2f} shares"
                    )
                    balance_synced = True
                    break
            time.sleep(POLL_INTERVAL)

        if not balance_synced:
            log(
                f"   ⚠️  [{symbol}] Balance sync timeout after {MAX_BALANCE_WAIT}s, proceeding anyway"
            )
            # One final check
            balance_info = get_balance_allowance(token_id)
            if balance_info:
                actual_size = balance_info.get("balance", 0)

        # Validate and adjust size
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
            # Balance matches requested size
            size = actual_size

        # TIME-AWARE URGENCY: Calculate time remaining in window
        # Adjust pricing strategy based on urgency:
        # - Early (>600s): Patient strategy (small drops, long waits)
        # - Mid (300-600s): Balanced strategy (moderate drops)
        # - Late (<300s): Aggressive strategy (rapid drops - current behavior)
        time_remaining = None
        urgency_level = "AGGRESSIVE"  # Default to aggressive (safe fallback)

        if window_end:
            try:
                from datetime import datetime, timezone

                if isinstance(window_end, str):
                    # Parse ISO format: "2026-01-18T23:15:00-05:00"
                    window_end_dt = datetime.fromisoformat(window_end)
                else:
                    window_end_dt = window_end

                now = datetime.now(timezone.utc)
                time_remaining = (window_end_dt - now).total_seconds()

                if time_remaining > 600:
                    urgency_level = "PATIENT"
                elif time_remaining > 300:
                    urgency_level = "BALANCED"
                else:
                    urgency_level = "AGGRESSIVE"

                log(
                    f"   ⏰ [{symbol}] Time remaining: {int(time_remaining)}s → {urgency_level} pricing strategy"
                )
            except Exception as e:
                log(
                    f"   ⚠️  [{symbol}] Could not calculate time remaining: {e}, using AGGRESSIVE strategy"
                )

        # CHECK MIN_ORDER_SIZE: If position is too small to sell, hold it if winning
        # This prevents orphaned small positions from causing emergency sell failures
        if size < MIN_ORDER_SIZE:
            log(
                f"   ⚠️  [{symbol}] Position size {size:.2f} < minimum {MIN_ORDER_SIZE} - cannot place sell order"
            )

            # Check if winning - if so, hold through resolution
            if entry_price and entry_price > 0.01:
                from src.utils.websocket_manager import ws_manager

                current_bid, current_ask = ws_manager.get_bid_ask(token_id)
                if (
                    current_bid
                    and current_ask
                    and current_bid > 0.01
                    and current_ask > 0.01
                ):
                    current_mid = (current_bid + current_ask) / 2

                    if current_mid > entry_price:
                        log(
                            f"   🎉 [{symbol}] Small position is WINNING! Entry ${entry_price:.2f}, now ${current_mid:.2f} (+${current_mid - entry_price:.2f})"
                        )
                        log(
                            f"   🎯 [{symbol}] HOLDING through resolution - position too small to sell but profitable"
                        )
                        return True  # Hold winning position
                    else:
                        log(
                            f"   😔 [{symbol}] Small position is LOSING! Entry ${entry_price:.2f}, now ${current_mid:.2f} (-${entry_price - current_mid:.2f})"
                        )
                        log(
                            f"   🔒 [{symbol}] Position ORPHANED - too small to sell, will lose on resolution"
                        )
                        return False  # Can't sell, position will be orphaned
                else:
                    log(
                        f"   ⚠️  [{symbol}] Cannot determine if winning (no market price), position ORPHANED"
                    )
                    return False
            else:
                log(f"   🔒 [{symbol}] Position ORPHANED - too small to sell")
                return False

        # SMART POSITION HOLD: Check if filled price is still reasonable vs current market
        # If position is on winning side, HOLD it as directional bet instead of emergency sell
        # This prevents dumping positions that are actually favorable
        if entry_price and entry_price > 0.01:
            from src.utils.websocket_manager import ws_manager
            from src.config.settings import (
                EMERGENCY_SELL_HOLD_IF_WINNING,
                EMERGENCY_SELL_PRICE_TOLERANCE_PCT,
                EMERGENCY_SELL_MIN_PROFIT_CENTS,
            )

            # Skip this check if feature is disabled
            if not EMERGENCY_SELL_HOLD_IF_WINNING:
                log(
                    f"   ⚠️  [{symbol}] Smart position hold disabled, proceeding with emergency sell"
                )
            else:
                # Get current market bid/ask for this token
                current_bid, current_ask = ws_manager.get_bid_ask(token_id)

                if (
                    current_bid
                    and current_ask
                    and current_bid > 0.01
                    and current_ask > 0.01
                ):
                    current_mid = (current_bid + current_ask) / 2
                    price_diff = abs(current_mid - entry_price)
                    price_diff_pct = (
                        (price_diff / entry_price) * 100 if entry_price > 0 else 100
                    )

                    log(
                        f"   📊 [{symbol}] Price check: Entry ${entry_price:.2f}, Current mid ${current_mid:.2f}, Diff ${price_diff:.2f} ({price_diff_pct:.1f}%)"
                    )

                    # If price hasn't moved much (within tolerance %), position is still reasonable - HOLD IT
                    # This means market agrees with our entry price, so keep the directional bet
                    if price_diff_pct <= EMERGENCY_SELL_PRICE_TOLERANCE_PCT:
                        log(
                            f"   ✅ [{symbol}] Position still reasonable! Entry ${entry_price:.2f} vs market ${current_mid:.2f} (within {EMERGENCY_SELL_PRICE_TOLERANCE_PCT}%)"
                        )
                        log(
                            f"   🎯 [{symbol}] HOLDING UNHEDGED POSITION - Taking directional bet (market supports our price)"
                        )
                        return True  # Return success without selling

                    # If current price is HIGHER than entry (we're winning), definitely HOLD
                    min_profit = (
                        EMERGENCY_SELL_MIN_PROFIT_CENTS / 100
                    )  # Convert cents to dollars
                    if current_mid > entry_price + min_profit:
                        log(
                            f"   🎉 [{symbol}] Position is WINNING! Entry ${entry_price:.2f}, now ${current_mid:.2f} (+${current_mid - entry_price:.2f})"
                        )
                        log(
                            f"   🎯 [{symbol}] HOLDING UNHEDGED POSITION - Already in profit!"
                        )
                        return True  # Return success without selling

                    # If current price is significantly lower, we're on losing side - proceed with emergency sell
                    if current_mid < entry_price - min_profit:
                        log(
                            f"   ⚠️  [{symbol}] Position is LOSING! Entry ${entry_price:.2f}, now ${current_mid:.2f} (-${entry_price - current_mid:.2f})"
                        )
                        log(
                            f"   💥 [{symbol}] Proceeding with emergency sell to cut losses"
                        )
                        # Fall through to emergency sell logic
                    else:
                        log(
                            f"   ⚠️  [{symbol}] Price moved {price_diff_pct:.1f}% but within neutral zone, proceeding with emergency sell"
                        )
                else:
                    log(
                        f"   ⚠️  [{symbol}] Could not get current market price (bid={current_bid}, ask={current_ask}), proceeding with emergency sell"
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
                        # CLOB V2 returns levels worst-first, so take the max price
                        # (bids[0] is the WORST bid and made this look like an empty book)
                        best_bid = max(
                            float(b.price if hasattr(b, "price") else b.get("price", 0))
                            for b in bids
                        )

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
        # Urgency adapts based on time remaining in window
        if orderbook_available and best_bid:
            from src.config.settings import (
                EMERGENCY_SELL_WAIT_SHORT,
                EMERGENCY_SELL_WAIT_MEDIUM,
                EMERGENCY_SELL_WAIT_LONG,
                EMERGENCY_SELL_FALLBACK_PRICE,
            )

            # Define TIME-AWARE pricing strategy based on urgency level
            # Format: (price_description, price_value, wait_seconds, order_type)
            if urgency_level == "PATIENT":
                # Early window (>600s): Small drops, instant FOK feedback
                # Goal: Maximize recovery value with fast iteration
                # FOK = Fill-Or-Kill: executes immediately or rejects (no wait/cancel needed)
                attempts = [
                    ("best bid (FOK)", best_bid, 0, "FOK"),
                    ("bid - $0.01 (FOK)", max(0.01, best_bid - 0.01), 0, "FOK"),
                    ("bid - $0.02 (FOK)", max(0.01, best_bid - 0.02), 0, "FOK"),
                    ("bid - $0.03 (FOK)", max(0.01, best_bid - 0.03), 0, "FOK"),
                    ("bid - $0.05 (FOK)", max(0.01, best_bid - 0.05), 0, "FOK"),
                    ("bid - $0.10 (FOK)", max(0.01, best_bid - 0.10), 0, "FOK"),
                    ("$0.30 (FOK)", 0.30, 0, "FOK"),
                    ("$0.20 (FOK)", 0.20, 0, "FOK"),
                    ("$0.15 (FOK)", 0.15, 0, "FOK"),
                    ("$0.10 (GTC fallback)", 0.10, 0, "GTC"),  # Final GTC fallback
                ]
            elif urgency_level == "BALANCED":
                # Mid window (300-600s): Moderate drops, FOK for speed
                # Goal: Balance recovery value with liquidation speed
                attempts = [
                    ("best bid (FOK)", best_bid, 0, "FOK"),
                    ("bid - $0.01 (FOK)", max(0.01, best_bid - 0.01), 0, "FOK"),
                    ("bid - $0.02 (FOK)", max(0.01, best_bid - 0.02), 0, "FOK"),
                    ("bid - $0.05 (FOK)", max(0.01, best_bid - 0.05), 0, "FOK"),
                    ("bid - $0.10 (FOK)", max(0.01, best_bid - 0.10), 0, "FOK"),
                    ("$0.30 (FOK)", 0.30, 0, "FOK"),
                    ("$0.20 (FOK)", 0.20, 0, "FOK"),
                    ("$0.15 (FOK)", 0.15, 0, "FOK"),
                    ("$0.10 (GTC fallback)", 0.10, 0, "GTC"),
                ]
            else:  # AGGRESSIVE (default)
                # Late window (<300s) or unknown: Rapid FOK attempts
                # Goal: Ensure liquidation before resolution with fast iteration
                attempts = [
                    ("best bid (FOK)", best_bid, 0, "FOK"),
                    ("bid - $0.01 (FOK)", max(0.01, best_bid - 0.01), 0, "FOK"),
                    ("bid - $0.02 (FOK)", max(0.01, best_bid - 0.02), 0, "FOK"),
                    ("bid - $0.05 (FOK)", max(0.01, best_bid - 0.05), 0, "FOK"),
                    ("bid - $0.10 (FOK)", max(0.01, best_bid - 0.10), 0, "FOK"),
                    ("$0.30 (FOK)", 0.30, 0, "FOK"),
                    ("$0.20 (FOK)", 0.20, 0, "FOK"),
                    ("$0.15 (FOK)", 0.15, 0, "FOK"),
                    ("$0.10 (GTC fallback)", 0.10, 0, "GTC"),
                ]

            last_order_id = None

            # EMERGENCY SELL TIMEOUT: Prevent excessive blocking during high volatility
            MAX_EMERGENCY_SELL_TIME = 20  # seconds (reduced from 45s due to FOK speed)
            emergency_start_time = time.time()

            for attempt_name, attempt_price, wait_seconds, order_type in attempts:
                # Check total elapsed time to prevent excessive blocking
                elapsed_total = time.time() - emergency_start_time
                if elapsed_total > MAX_EMERGENCY_SELL_TIME:
                    log(
                        f"   ⏰ [{symbol}] Emergency sell timeout ({MAX_EMERGENCY_SELL_TIME}s elapsed) - going to fallback"
                    )
                    break

                # Cancel previous GTC order before placing new one (FOK orders auto-reject, no cancel needed)
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

                # Round to nearest $0.01 (Polymarket requirement)
                attempt_price = round(attempt_price, 2)

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
                    # FOK rejection is normal - just means no takers at this price
                    if order_type == "FOK":
                        log(
                            f"   ⏭️  [{symbol}] FOK rejected at ${attempt_price:.2f} - trying next price"
                        )
                    else:
                        log(
                            f"   ⚠️  [{symbol}] Order placement failed: {result.get('error', 'unknown')}"
                        )
                    continue

                order_id = result.get("order_id", "unknown")
                last_order_id = order_id

                # FOK orders either fill immediately or fail - no need to wait
                if order_type == "FOK":
                    # Success = filled (FOK only returns success if completely filled)
                    log(
                        f"   ✅ [{symbol}] Emergency sell filled instantly: {size:.2f} @ ${attempt_price:.2f} ({attempt_name}) (ID: {order_id[:10] if order_id != 'unknown' else order_id})"
                    )
                    # Record exit price in database for P&L tracking
                    _record_exit_price(entry_order_id, attempt_price, symbol)
                    return True

                # GTC fallback order - place and wait briefly
                log(
                    f"   ⏱️  [{symbol}] GTC fallback placed at ${attempt_price:.2f}, waiting 10s..."
                )

                # Phase 2: Hybrid WebSocket/polling monitoring for GTC fill
                try:
                    from src.trading.orders.async_monitoring import (
                        wait_for_order_fill_sync,
                    )
                    from src.config.settings import ENABLE_WEBSOCKET_MONITORING
                    from src.utils.websocket_manager import ws_manager

                    if ENABLE_WEBSOCKET_MONITORING and ws_manager._running:
                        # Try WebSocket first (instant notification)
                        log(f"   🎯 [{symbol}] Monitoring GTC fill via WebSocket...")
                        order_status = wait_for_order_fill_sync(
                            order_id, timeout=10, fallback_to_polling=False
                        )
                    else:
                        # Polling fallback
                        order_status = get_order(order_id)
                        time.sleep(10)  # Wait 10s like before

                    if order_status:
                        filled_size = float(order_status.get("size_matched", 0))
                        if filled_size >= (size - 0.01):
                            log(
                                f"   ✅ [{symbol}] GTC fallback filled: {size:.2f} @ ${attempt_price:.2f} (ID: {order_id[:10]})"
                            )
                            # Record exit price in database for P&L tracking
                            _record_exit_price(entry_order_id, attempt_price, symbol)
                            return True

                except Exception as ws_err:
                    log(f"   ⚠️  [{symbol}] Error monitoring GTC order: {ws_err}")

                # GTC didn't fill - continue to final fallback
                log(
                    f"   ⏰ [{symbol}] GTC fallback at ${attempt_price:.2f} not filled after 10s"
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

            # Calculate reference price based on entry
            # Start at entry_price and work down since we're selling
            reference_price = min(0.99, max(0.01, entry_price))

            # TIME-AWARE fallback pricing (FOK for fast iteration)
            if urgency_level == "PATIENT":
                attempts = [
                    (
                        f"${reference_price:.2f} (entry ref, FOK)",
                        reference_price,
                        0,
                        "FOK",
                    ),
                    (
                        f"${reference_price - 0.01:.2f} (FOK)",
                        max(0.01, reference_price - 0.01),
                        0,
                        "FOK",
                    ),
                    (
                        f"${reference_price - 0.02:.2f} (FOK)",
                        max(0.01, reference_price - 0.02),
                        0,
                        "FOK",
                    ),
                    (
                        f"${reference_price - 0.03:.2f} (FOK)",
                        max(0.01, reference_price - 0.03),
                        0,
                        "FOK",
                    ),
                    (
                        f"${reference_price - 0.05:.2f} (FOK)",
                        max(0.01, reference_price - 0.05),
                        0,
                        "FOK",
                    ),
                    (
                        f"${reference_price - 0.10:.2f} (FOK)",
                        max(0.01, reference_price - 0.10),
                        0,
                        "FOK",
                    ),
                    ("$0.30 (FOK)", 0.30, 0, "FOK"),
                    ("$0.20 (FOK)", 0.20, 0, "FOK"),
                    ("$0.15 (FOK)", 0.15, 0, "FOK"),
                    ("$0.10 (GTC fallback)", 0.10, 0, "GTC"),
                ]
            elif urgency_level == "BALANCED":
                attempts = [
                    (
                        f"${reference_price:.2f} (entry ref, FOK)",
                        reference_price,
                        0,
                        "FOK",
                    ),
                    (
                        f"${reference_price - 0.01:.2f} (FOK)",
                        max(0.01, reference_price - 0.01),
                        0,
                        "FOK",
                    ),
                    (
                        f"${reference_price - 0.02:.2f} (FOK)",
                        max(0.01, reference_price - 0.02),
                        0,
                        "FOK",
                    ),
                    (
                        f"${reference_price - 0.05:.2f} (FOK)",
                        max(0.01, reference_price - 0.05),
                        0,
                        "FOK",
                    ),
                    (
                        f"${reference_price - 0.10:.2f} (FOK)",
                        max(0.01, reference_price - 0.10),
                        0,
                        "FOK",
                    ),
                    ("$0.30 (FOK)", 0.30, 0, "FOK"),
                    ("$0.20 (FOK)", 0.20, 0, "FOK"),
                    ("$0.15 (FOK)", 0.15, 0, "FOK"),
                    ("$0.10 (GTC fallback)", 0.10, 0, "GTC"),
                ]
            else:  # AGGRESSIVE
                attempts = [
                    (
                        f"${reference_price:.2f} (entry ref, FOK)",
                        reference_price,
                        0,
                        "FOK",
                    ),
                    (
                        f"${reference_price - 0.01:.2f} (FOK)",
                        max(0.01, reference_price - 0.01),
                        0,
                        "FOK",
                    ),
                    (
                        f"${reference_price - 0.02:.2f} (FOK)",
                        max(0.01, reference_price - 0.02),
                        0,
                        "FOK",
                    ),
                    (
                        f"${reference_price - 0.05:.2f} (FOK)",
                        max(0.01, reference_price - 0.05),
                        0,
                        "FOK",
                    ),
                    (
                        f"${reference_price - 0.10:.2f} (FOK)",
                        max(0.01, reference_price - 0.10),
                        0,
                        "FOK",
                    ),
                    ("$0.30 (FOK)", 0.30, 0, "FOK"),
                    ("$0.20 (FOK)", 0.20, 0, "FOK"),
                    ("$0.15 (FOK)", 0.15, 0, "FOK"),
                    ("$0.10 (GTC fallback)", 0.10, 0, "GTC"),
                ]

            last_order_id = None

            # EMERGENCY SELL TIMEOUT: Prevent excessive blocking during high volatility
            MAX_EMERGENCY_SELL_TIME = 20  # seconds (reduced from 45s due to FOK speed)
            emergency_start_time = time.time()

            for attempt_name, attempt_price, wait_seconds, order_type in attempts:
                # Check total elapsed time to prevent excessive blocking
                elapsed_total = time.time() - emergency_start_time
                if elapsed_total > MAX_EMERGENCY_SELL_TIME:
                    log(
                        f"   ⏰ [{symbol}] Emergency sell timeout ({MAX_EMERGENCY_SELL_TIME}s elapsed) - going to fallback"
                    )
                    break

                # Cancel previous GTC order before placing new one (FOK orders auto-reject, no cancel needed)
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

                # Round to nearest $0.01 (Polymarket requirement)
                attempt_price = round(attempt_price, 2)

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
                    # Record exit price in database for P&L tracking
                    _record_exit_price(entry_order_id, attempt_price, symbol)
                    return True

                # GTC orders need time to be taken - wait and check status
                log(
                    f"   ⏱️  [{symbol}] Waiting {wait_seconds}s for GTC order to fill..."
                )

                # Phase 2: Hybrid WebSocket/polling monitoring for GTC fill
                try:
                    from src.trading.orders.async_monitoring import (
                        wait_for_order_fill_sync,
                    )
                    from src.config.settings import ENABLE_WEBSOCKET_MONITORING
                    from src.utils.websocket_manager import ws_manager

                    if ENABLE_WEBSOCKET_MONITORING and ws_manager._running:
                        # Try WebSocket first (instant notification)
                        log(
                            f"   🎯 [{symbol}] Monitoring emergency sell via WebSocket..."
                        )
                        order_status = wait_for_order_fill_sync(
                            order_id, timeout=wait_seconds, fallback_to_polling=False
                        )
                    else:
                        # Polling fallback
                        order_status = get_order(order_id)
                        time.sleep(wait_seconds)

                    if order_status:
                        filled_size = float(order_status.get("size_matched", 0))
                        if filled_size >= (size - 0.01):
                            log(
                                f"   ✅ [{symbol}] Emergency sell filled: {size:.2f} @ ${attempt_price:.2f} (ID: {order_id[:10]})"
                            )
                            # Record exit price in database for P&L tracking
                            _record_exit_price(entry_order_id, attempt_price, symbol)
                            return True

                except Exception as ws_err:
                    log(f"   ⚠️  [{symbol}] Error monitoring emergency sell: {ws_err}")

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


def calculate_adaptive_combined_threshold(
    entry_token_id: str, hedge_token_id: str
) -> float:
    """
    Calculate combined price threshold based on current market spreads.

    Dynamically adjusts threshold to balance profitability vs execution success:
    - Tight threshold (1.5¢ edge) when spreads are narrow (liquid markets)
    - Wide threshold (5¢ edge) when spreads are wide (illiquid markets)

    Formula: 1.00 - (entry_spread + hedge_spread) - ADAPTIVE_THRESHOLD_BUFFER

    Args:
        entry_token_id: Token ID for entry side
        hedge_token_id: Token ID for hedge side

    Returns:
        Adaptive threshold between ADAPTIVE_THRESHOLD_MIN and ADAPTIVE_THRESHOLD_MAX
    """
    from src.utils.websocket_manager import ws_manager
    from src.config.settings import (
        ENABLE_ADAPTIVE_THRESHOLD,
        COMBINED_PRICE_THRESHOLD,
        ADAPTIVE_THRESHOLD_MIN,
        ADAPTIVE_THRESHOLD_MAX,
        ADAPTIVE_THRESHOLD_BUFFER,
    )

    # If adaptive threshold disabled, use static threshold
    if not ENABLE_ADAPTIVE_THRESHOLD:
        return COMBINED_PRICE_THRESHOLD

    try:
        # Get current bid/ask spreads from websocket
        entry_bid, entry_ask = ws_manager.get_bid_ask(entry_token_id)
        hedge_bid, hedge_ask = ws_manager.get_bid_ask(hedge_token_id)

        # If websocket data unavailable, fall back to static threshold
        if not all([entry_bid, entry_ask, hedge_bid, hedge_ask]):
            return COMBINED_PRICE_THRESHOLD

        # Type assertion: all values are non-None after the check above
        assert entry_bid is not None and entry_ask is not None
        assert hedge_bid is not None and hedge_ask is not None

        # Calculate spreads
        entry_spread = entry_ask - entry_bid
        hedge_spread = hedge_ask - hedge_bid
        total_spread = entry_spread + hedge_spread

        # Calculate adaptive threshold: 1.00 - spreads - buffer
        # This ensures we leave enough room for both orders to fill
        adaptive = 1.00 - total_spread - ADAPTIVE_THRESHOLD_BUFFER

        # Clamp to configured range
        adaptive = max(ADAPTIVE_THRESHOLD_MIN, min(ADAPTIVE_THRESHOLD_MAX, adaptive))

        return round(adaptive, 3)

    except Exception as e:
        # On error, fall back to static threshold
        from src.utils.logger import log

        log(f"⚠️  Adaptive threshold calculation error: {e}")
        return COMBINED_PRICE_THRESHOLD


def place_entry_and_hedge_atomic(
    symbol: str,
    entry_token_id: str,
    entry_side: str,
    entry_price: float,
    entry_size: float,
    window_end: Optional[str] = None,
    confidence: Optional[float] = None,
) -> tuple[Optional[dict], Optional[dict], Optional[float]]:
    """
    Place entry and hedge orders simultaneously using batch order API.
    This eliminates timing gaps and improves hedge fill rates.

    Args:
        window_end: Window end time (ISO format) for time-aware emergency sell pricing
        confidence: Original signal confidence (0.0-1.0) for deciding whether to hold unhedged position

    Returns:
        (entry_result, hedge_result, hedge_price) - Results are dicts with success, order_id, status; hedge_price is the calculated price
    """
    try:
        # Get token IDs
        up_id, down_id = get_token_ids(symbol)
        if not up_id or not down_id:
            return None, None, None

        # Determine hedge token and price
        if entry_side == "UP":
            hedge_token_id = down_id
            hedge_side = "DOWN"
        else:
            hedge_token_id = up_id
            hedge_side = "UP"

        # PRE-FLIGHT LIQUIDITY CHECK: Skip trade if market is too illiquid
        # Prevents catastrophic emergency liquidations (40-60% losses)
        from src.trading.orders import check_atomic_hedge_liquidity

        is_liquid, skip_reason, liquidity_metrics = check_atomic_hedge_liquidity(
            entry_token_id, hedge_token_id, entry_size
        )

        if not is_liquid:
            log(f"   ⏭️  [{symbol}] SKIPPING TRADE: {skip_reason}")
            if liquidity_metrics:
                log(
                    f"   📊 [{symbol}] Liquidity metrics: entry_spread={fmt_num(liquidity_metrics.get('entry_spread'))}, "
                    f"hedge_spread={fmt_num(liquidity_metrics.get('hedge_spread'))}, "
                    f"entry_depth={fmt_num(liquidity_metrics.get('entry_depth'), 1)}, "
                    f"hedge_depth={fmt_num(liquidity_metrics.get('hedge_depth'), 1)}"
                )
            return None, None, None

        # PRICING (CLOB V2 fees): a taker leg pays 0.07*p*(1-p) per share, so 2 taker legs near
        # 50/50 cost ~3.5¢ and a "0.97 combined" pair loses money. Gate on NET edge after fees.
        #   1. Taker pair from the ASK side, only if net edge after fees >= MIN_NET_EDGE
        #   2. Otherwise a maker pair: POST_ONLY bids on both legs (no fees, earns rebate)
        from src.config.settings import (
            ENABLE_MAKER_PAIR,
            MAKER_PAIR_IMPROVE_TICKS,
            MAKER_PAIR_PRICE_BAND,
            MIN_NET_EDGE,
            TAKER_FEE_RATE,
        )
        from src.trading.edge import maker_pair_prices, meets_edge, pair_net_edge

        combined_price_threshold = calculate_adaptive_combined_threshold(
            entry_token_id, hedge_token_id
        )

        from src.trading.orders.market_info import find_optimal_atomic_hedge_prices

        optimal_entry, optimal_hedge, metadata = find_optimal_atomic_hedge_prices(
            entry_token_id=entry_token_id,
            hedge_token_id=hedge_token_id,
            min_size=entry_size,
            max_combined=combined_price_threshold,
        )

        entry_price = hedge_price = final_combined = 0.0
        use_post_only = False
        mode = None

        if optimal_entry and optimal_hedge and metadata:
            taker_net = pair_net_edge(
                optimal_entry, optimal_hedge, True, True, TAKER_FEE_RATE
            )
            if meets_edge(taker_net, MIN_NET_EDGE):
                mode = "TAKER"
                entry_price, hedge_price = optimal_entry, optimal_hedge
                final_combined = metadata["combined_price"]
                log(
                    f"   🎯 [{symbol}] OPTIMAL PRICING: {entry_side} ${entry_price:.2f} + {hedge_side} ${hedge_price:.2f}"
                )
                log(
                    f"   📊 [{symbol}] Combined ${final_combined:.2f}, net edge {taker_net * 100:+.1f}¢/sh after taker fees, entry depth: {metadata['entry_depth']:.1f}, hedge depth: {metadata['hedge_depth']:.1f}"
                )
            else:
                log(
                    f"   ⏭️  [{symbol}] Taker pair ${optimal_entry:.2f}+${optimal_hedge:.2f}=${optimal_entry + optimal_hedge:.2f} has net edge {taker_net * 100:+.1f}¢/sh after fees (< {MIN_NET_EDGE * 100:.1f}¢) - not taking"
                )

        if mode is None and ENABLE_MAKER_PAIR:
            e_bid, e_ask = _book_top(entry_token_id, fresh=True)
            h_bid, h_ask = _book_top(hedge_token_id, fresh=True)
            quote = maker_pair_prices(e_bid, h_bid, MAKER_PAIR_IMPROVE_TICKS)
            band_ok = bool(
                quote
                and all(MAKER_PAIR_PRICE_BAND <= q <= 1 - MAKER_PAIR_PRICE_BAND for q in quote)
            )
            if quote and not band_ok:
                log(
                    f"   ⏭️  [{symbol}] Maker pair skipped: bids ${quote[0]:.2f}/${quote[1]:.2f} outside [{MAKER_PAIR_PRICE_BAND:.2f}, {1 - MAKER_PAIR_PRICE_BAND:.2f}] (window already decided)"
                )
            elif quote and e_ask and h_ask:
                q_entry = min(quote[0], round(e_ask - 0.01, 2))  # never cross the book
                q_hedge = min(quote[1], round(h_ask - 0.01, 2))
                maker_net = pair_net_edge(q_entry, q_hedge, False, False, TAKER_FEE_RATE)
                if meets_edge(maker_net, MIN_NET_EDGE) and q_entry >= 0.01 and q_hedge >= 0.01:
                    mode = "MAKER"
                    entry_price, hedge_price = q_entry, q_hedge
                    final_combined = q_entry + q_hedge
                    use_post_only = True
                    log(
                        f"   💹 [{symbol}] Maker pair: bids {entry_side} ${q_entry:.2f} + {hedge_side} ${q_hedge:.2f} = ${final_combined:.2f}, net edge {maker_net * 100:+.1f}¢/sh, fees $0.00"
                    )
                else:
                    log(
                        f"   ⏭️  [{symbol}] Maker pair ${q_entry:.2f}+${q_hedge:.2f}=${q_entry + q_hedge:.2f} has net edge {maker_net * 100:+.1f}¢/sh (< {MIN_NET_EDGE * 100:.1f}¢)"
                    )

        if mode is None:
            log(
                f"   ⏭️  [{symbol}] No entry+hedge pair with net edge ≥ {MIN_NET_EDGE * 100:.1f}¢/sh after fees - skipping"
            )
            return None, None, None

        if use_post_only:
            log(f"   💸 [{symbol}] Using POST_ONLY (maker: no fee, earns rebate share)")
        else:
            log(f"   💸 [{symbol}] Using GTC (taker: pays 0.07·p·(1−p) per share per leg)")

        # Create batch order with smart POST_ONLY/GTC selection
        # POST_ONLY: no fee + rebate share, may rest unfilled; GTC: taker fee 0.07*p*(1-p) per share
        orders = [
            {
                "token_id": entry_token_id,
                "price": entry_price,
                "size": entry_size,
                "side": BUY,
                "post_only": use_post_only,
            },
            {
                "token_id": hedge_token_id,
                "price": hedge_price,
                "size": entry_size,
                "side": BUY,
                "post_only": use_post_only,
            },
        ]

        log(
            f"   🔄 [{symbol}] Placing ATOMIC entry+hedge: {entry_side} {entry_size:.1f} @ ${entry_price:.2f} + {hedge_side} {entry_size:.1f} @ ${hedge_price:.2f} (combined ${final_combined:.2f})"
        )

        # Submit both orders simultaneously
        results = place_batch_orders(orders)

        if len(results) < 2:
            log_error(f"[{symbol}] Batch order returned insufficient results")
            return None, None, None

        entry_result = results[0]
        hedge_result = results[1]

        entry_success = entry_result.get("success")
        hedge_success = hedge_result.get("success")

        # Check for POST_ONLY crossing errors and track failures
        entry_error = entry_result.get("error", "")
        hedge_error = hedge_result.get("error", "")

        # Track POST_ONLY crossing failures using smart retry logic
        post_only_crossed = (
            "order crosses book" in entry_error.lower()
            or "order crosses book" in hedge_error.lower()
        )

        if use_post_only:
            if post_only_crossed:
                record_post_only_result(symbol, success=False)
            elif entry_success and hedge_success:
                record_post_only_result(symbol, success=True)

        # CRITICAL: Handle order placement failures
        # If one succeeds and other fails, clean up the successful one

        # Case 1: Entry failed (e.g., POST_ONLY crossed book)
        if not entry_success and hedge_success:
            hedge_order_id = hedge_result.get("order_id")
            log(
                f"   ❌ [{symbol}] {entry_side} order failed: {entry_result.get('error')}"
            )
            hedge_id_display = hedge_order_id[:10] if hedge_order_id else "unknown"
            log(
                f"   ⚠️  [{symbol}] {hedge_side} placed but {entry_side} failed - cancelling {hedge_side} {hedge_id_display}"
            )

            if not hedge_order_id:
                log_error(
                    f"[{symbol}] Cannot cancel {hedge_side} - order ID not returned from API"
                )
                return None, None, None

            try:
                cancel_order(hedge_order_id)
                log(f"   ✅ [{symbol}] {hedge_side} order cancelled successfully")
            except Exception as cancel_err:
                log_error(
                    f"[{symbol}] Failed to cancel {hedge_side} order {hedge_order_id[:10]}: {cancel_err}"
                )
                # Try to sell the hedge if it filled quickly
                log(f"   💥 [{symbol}] Attempting emergency sell of {hedge_side}")
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
                    window_end=window_end,
                )
            return None, None, None

        # Case 2: Entry succeeded, hedge failed - RETRY HEDGE
        if entry_success and not hedge_success:
            entry_order_id = entry_result.get("order_id")
            log(
                f"   ❌ [{symbol}] {hedge_side} order failed: {hedge_result.get('error')}"
            )
            log(
                f"   🔄 [{symbol}] {entry_side} succeeded - retrying {hedge_side} with fresh pricing"
            )

            if not entry_order_id:
                log_error(
                    f"[{symbol}] Cannot manage {entry_side} - order ID not returned from API"
                )
                return None, None, None

            # Retry hedge up to 3 times with fresh orderbook pricing
            MAX_HEDGE_RETRIES = 3
            hedge_placed = False

            for retry in range(1, MAX_HEDGE_RETRIES + 1):
                log(f"   🔄 [{symbol}] {hedge_side} retry {retry}/{MAX_HEDGE_RETRIES}")

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
                        # CRITICAL: Maintain profitability - combined must be <= COMBINED_PRICE_THRESHOLD
                        from src.config.settings import COMBINED_PRICE_THRESHOLD

                        # Calculate max hedge price from entry price
                        max_hedge_price = round(
                            COMBINED_PRICE_THRESHOLD - entry_price, 2
                        )
                        max_hedge_price = max(0.01, min(0.99, max_hedge_price))

                        # Try market pricing first (bid + 2¢), but cap at max_hedge_price
                        retry_hedge_price = round(hedge_bid + 0.02, 2)
                        retry_hedge_price = min(retry_hedge_price, max_hedge_price)
                        if use_post_only:
                            # bid+2c crosses a 1-2 tick spread and POST_ONLY is rejected every retry
                            retry_hedge_price = min(retry_hedge_price, round(hedge_ask - 0.01, 2))
                        retry_hedge_price = max(0.01, min(0.99, retry_hedge_price))

                        combined_price = entry_price + retry_hedge_price

                        # Validate combined price doesn't exceed threshold
                        if combined_price > COMBINED_PRICE_THRESHOLD:
                            log_error(
                                f"   ❌ [{symbol}] Hedge retry rejected: Combined ${combined_price:.2f} > ${COMBINED_PRICE_THRESHOLD:.2f}"
                            )
                            # Skip retry, will remain unhedged
                            continue

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
                                "post_only": use_post_only,  # Match entry order type (POST_ONLY or GTC)
                            }
                        ]

                        retry_results = place_batch_orders(hedge_order)
                        if retry_results and retry_results[0].get("success"):
                            hedge_result = retry_results[0]
                            hedge_price = retry_hedge_price  # the saved trade must record the price actually placed
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
                            log(
                                f"   ⚠️  [{symbol}] {hedge_side} retry {retry} failed: {error}"
                            )
                            time.sleep(1)  # Brief pause before next retry
                    else:
                        log(f"   ⚠️  [{symbol}] Empty orderbook for {hedge_side} retry")
                        time.sleep(1)
                except Exception as e:
                    log_error(
                        f"[{symbol}] Error during {hedge_side} retry {retry}: {e}"
                    )
                    time.sleep(1)

            # If all retries failed, cancel entry and emergency sell if needed
            if not hedge_placed:
                entry_id_display = entry_order_id[:10] if entry_order_id else "unknown"
                log(
                    f"   ❌ [{symbol}] All {hedge_side} retries failed - cancelling {entry_side} {entry_id_display}"
                )
                try:
                    cancel_order(entry_order_id)
                    log(f"   ✅ [{symbol}] {entry_side} order cancelled successfully")
                except Exception as cancel_err:
                    log_error(f"[{symbol}] Failed to cancel {entry_side}: {cancel_err}")
                    # Try to sell the entry if it filled quickly
                    log(f"   💥 [{symbol}] Attempting emergency sell of {entry_side}")
                    time.sleep(2)
                    emergency_sell_position(
                        symbol=symbol,
                        token_id=entry_token_id,
                        size=entry_size,
                        reason="entry without hedge after retries",
                        entry_order_id=entry_order_id,
                        entry_price=entry_price,
                        window_end=window_end,
                    )
                return None, None, None

        # Case 3: Both failed
        if not entry_success and not hedge_success:
            log(
                f"   ❌ [{symbol}] Both orders failed - Entry: {entry_result.get('error')}, Hedge: {hedge_result.get('error')}"
            )
            return None, None, None

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
            HEDGE_GTC_FALLBACK_SECONDS,
        )
        from src.markets import scale_seconds

        # Timeouts are tuned for 15m windows; shorter windows get proportionally less
        HEDGE_FILL_TIMEOUT_SECONDS = int(
            scale_seconds(HEDGE_FILL_TIMEOUT_SECONDS, symbol, floor=3 * HEDGE_POLL_INTERVAL_SECONDS)
        )
        HEDGE_GTC_FALLBACK_SECONDS = int(
            scale_seconds(HEDGE_GTC_FALLBACK_SECONDS, symbol, floor=HEDGE_POLL_INTERVAL_SECONDS)
        )

        entry_order_id = entry_result.get("order_id")
        hedge_order_id = hedge_result.get("order_id")

        if not entry_order_id or not hedge_order_id:
            log(f"   ⚠️  [{symbol}] Missing order IDs, skipping monitoring")
            return None, None, None

        log(
            f"   ⏱️  [{symbol}] Monitoring fills for {HEDGE_FILL_TIMEOUT_SECONDS}s (GTC fallback @ {HEDGE_GTC_FALLBACK_SECONDS}s, polling every {HEDGE_POLL_INTERVAL_SECONDS}s)"
        )

        elapsed = 0
        both_filled = False
        gtc_switched = False  # Track if we've switched hedge to GTC
        gtc_vetoed = False

        def _gtc_hedge_worth_it() -> bool:
            """Entry filled, hedge not: only pay the taker fee if it beats flattening/holding."""
            nonlocal gtc_vetoed
            if gtc_vetoed:
                return False
            from src.trading.edge import flatten_ev, hold_ev, taker_hedge_ev

            e_bid, e_ask = _book_top(entry_token_id, fresh=True)
            _, h_ask = _book_top(hedge_token_id, fresh=True)
            if not h_ask:
                gtc_vetoed = True
                log(f"   ✋ [{symbol}] No {hedge_side} ask to hedge against - keeping resting hedge")
                return False
            taker = taker_hedge_ev(entry_price, h_ask)
            flat = flatten_ev(entry_price, e_bid) if e_bid else float("-inf")
            hold = hold_ev(entry_price, (e_bid + e_ask) / 2) if e_bid and e_ask else float("-inf")
            log(
                f"   🧭 [{symbol}] Leg-risk EV/sh: taker-hedge {taker * 100:+.1f}¢ (ask ${h_ask:.2f}) | flatten {flat * 100:+.1f}¢ | hold {hold * 100:+.1f}¢ (market-implied)"
            )
            if taker >= max(flat, hold):
                return True
            gtc_vetoed = True
            log(
                f"   ✋ [{symbol}] Taker hedge not worth the fee - keeping resting {hedge_side} bid until timeout"
            )
            return False

        trail_state = {"since": None, "last": 0.0, "capped": False, "moves": 0}

        def _trail_unfilled_leg(
            entry_done: bool, hedge_done: bool, entry_got: float, hedge_got: float
        ) -> None:
            """One leg filled: follow the market with the other leg to get the best lock.

            The unfilled leg is re-quoted at best bid (+ up to TRAIL_MAX_TICKS as time passes),
            never above `1 - filled_price - MIN_NET_EDGE`, so the lock stays >= MIN_NET_EDGE.
            """
            nonlocal entry_order_id, hedge_order_id, entry_price, hedge_price
            from src.config.settings import (
                ENABLE_TRAILING,
                MIN_NET_EDGE,
                TRAIL_MAX_TICKS,
                TRAIL_MIN_REPRICE_SECONDS,
            )
            from src.trading.edge import trail_target

            if not ENABLE_TRAILING or not use_post_only or entry_done == hedge_done:
                return
            trailing_entry = hedge_done  # hedge filled first -> move the entry leg
            if (entry_got if trailing_entry else hedge_got) > 0.01:
                return  # partially filled: leave it alone
            now = time.time()
            if trail_state["since"] is None:
                trail_state["since"] = now
            if now - trail_state["last"] < TRAIL_MIN_REPRICE_SECONDS:
                return

            leg = entry_side if trailing_entry else hedge_side
            filled_side = hedge_side if trailing_entry else entry_side
            filled_price = hedge_price if trailing_entry else entry_price
            cur_price = entry_price if trailing_entry else hedge_price
            token = entry_token_id if trailing_entry else hedge_token_id
            old_id = entry_order_id if trailing_entry else hedge_order_id

            bid, ask = _book_top(token, fresh=True)
            if not bid or not ask:
                return
            waited = now - trail_state["since"]
            step = max(1.0, HEDGE_FILL_TIMEOUT_SECONDS / (TRAIL_MAX_TICKS + 1))
            ticks = min(TRAIL_MAX_TICKS, int(waited // step))
            target, cap = trail_target(filled_price, bid, ask, ticks, MIN_NET_EDGE)
            trail_state["last"] = now

            if bid > cap + 1e-9 and not trail_state["capped"]:
                trail_state["capped"] = True
                log(
                    f"   🎣 [{symbol}] Trail capped: {leg} best bid ${bid:.2f} is above the ${cap:.2f} cap ({filled_side} filled @ ${filled_price:.2f}, min lock {MIN_NET_EDGE * 100:.1f}¢) - cannot chase"
                )
            if target is None or abs(target - cur_price) < 0.005:
                return

            log(
                f"   🎣 [{symbol}] Trailing {leg}: {filled_side} filled @ ${filled_price:.2f} → cap ${cap:.2f} | bid ${bid:.2f} ask ${ask:.2f} | ${cur_price:.2f} → ${target:.2f} (lock {(1 - filled_price - target) * 100:+.1f}¢)"
            )
            cancel_order(old_id)
            matched = float((get_order(old_id) or {}).get("size_matched") or 0)
            if matched > 0.01:  # filled while we were repricing: keep it, next poll sees the fill
                log(f"   ✅ [{symbol}] {leg} filled during reprice - keeping it")
                return
            res = place_batch_orders(
                [{"token_id": token, "price": target, "size": entry_size, "side": BUY, "post_only": True}]
            )
            if not (res and res[0].get("success") and res[0].get("order_id")):
                log(f"   ⚠️  [{symbol}] Trail reprice failed ({res[0].get('error') if res else 'no response'}) - restoring ${cur_price:.2f}")
                res = place_batch_orders(
                    [{"token_id": token, "price": cur_price, "size": entry_size, "side": BUY, "post_only": True}]
                )
                target = cur_price
                if not (res and res[0].get("success") and res[0].get("order_id")):
                    log_error(f"[{symbol}] Could not restore {leg} order after trail failure")
                    return
            new_id = res[0]["order_id"]
            trail_state["moves"] += 1
            if trailing_entry:
                entry_order_id, entry_price = new_id, target
                entry_result["order_id"] = new_id
            else:
                hedge_order_id, hedge_price = new_id, target
                hedge_result["order_id"] = new_id

        while elapsed < HEDGE_FILL_TIMEOUT_SECONDS:
            time.sleep(HEDGE_POLL_INTERVAL_SECONDS)
            elapsed += HEDGE_POLL_INTERVAL_SECONDS

            try:
                # Phase 2: Hybrid WebSocket/polling monitoring
                # Try WebSocket first (instant), fall back to polling (reliable)
                results = _wait_for_orders_hybrid(
                    [entry_order_id, hedge_order_id],
                    timeout=HEDGE_POLL_INTERVAL_SECONDS,  # Check every 5s
                    symbol=symbol,
                )
                entry_status = results.get(entry_order_id)
                hedge_status = results.get(hedge_order_id)

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
                    # Status was captured at placement ("live"); mark filled so the saved trade
                    # syncs its real (possibly trailed) fill price from the exchange
                    entry_result["status"] = "MATCHED"
                    hedge_result["status"] = "MATCHED"
                    if trail_state["moves"]:
                        log(
                            f"   🎣 [{symbol}] Locked {(1 - entry_price - hedge_price) * 100:+.1f}¢/sh after {trail_state['moves']} trail move(s): {entry_side} ${entry_price:.2f} + {hedge_side} ${hedge_price:.2f}"
                        )

                    # Reset POST_ONLY failure counter on successful atomic placement
                    if symbol in _post_only_failures:
                        old_count = _post_only_failures[symbol]
                        del _post_only_failures[symbol]
                        log(
                            f"   🔄 [{symbol}] POST_ONLY failure counter reset (was {old_count})"
                        )

                    break

                _trail_unfilled_leg(
                    entry_filled, hedge_filled, entry_filled_size, hedge_filled_size
                )

                # GTC FALLBACK: If entry filled but hedge not after 30s, switch hedge to GTC
                # This pays taker fee (~$0.04) but prevents emergency liquidation loss (~$1.90)
                if (
                    elapsed >= HEDGE_GTC_FALLBACK_SECONDS
                    and entry_filled
                    and not hedge_filled
                    and not gtc_switched
                    and use_post_only  # Only switch if we started with POST_ONLY
                    and _gtc_hedge_worth_it()
                ):
                    log(
                        f"   ⚠️  [{symbol}] Entry filled but hedge timeout after {elapsed}s - switching hedge to GTC (taker)"
                    )

                    try:
                        # Cancel existing POST_ONLY hedge order
                        cancel_order(hedge_order_id)
                        log(
                            f"   🚫 [{symbol}] Cancelled POST_ONLY hedge order {hedge_order_id[:10]}"
                        )

                        # Get fresh hedge pricing (slightly more aggressive to ensure fill)
                        from src.utils.websocket_manager import ws_manager
                        from decimal import Decimal, ROUND_HALF_UP

                        hedge_bid, hedge_ask = ws_manager.get_bid_ask(hedge_token_id)

                        # If no websocket data, use original price + 1¢
                        if not hedge_bid or not hedge_ask or hedge_ask < 0.02:
                            gtc_hedge_price = float(
                                Decimal(str(min(0.99, hedge_price + 0.01))).quantize(
                                    Decimal("0.01"), rounding=ROUND_HALF_UP
                                )
                            )
                            log(
                                f"   📊 [{symbol}] Using fallback pricing: ${gtc_hedge_price:.2f}"
                            )
                        else:
                            # Use ask price - 1¢ to cross spread but not overpay
                            gtc_hedge_price = float(
                                Decimal(str(hedge_ask - 0.01)).quantize(
                                    Decimal("0.01"), rounding=ROUND_HALF_UP
                                )
                            )
                            gtc_hedge_price = max(
                                0.01, min(0.99, gtc_hedge_price)
                            )  # Clamp to valid range
                            log(
                                f"   📊 [{symbol}] Hedge GTC pricing: ask=${hedge_ask:.2f} → ${gtc_hedge_price:.2f}"
                            )

                        # Verify combined price still profitable
                        combined_with_gtc = entry_price + gtc_hedge_price
                        if combined_with_gtc > 0.99:
                            log(
                                f"   ⚠️  [{symbol}] GTC hedge price ${gtc_hedge_price:.2f} + entry ${entry_price:.2f} = ${combined_with_gtc:.2f} > $0.99 - adjusting"
                            )
                            gtc_hedge_price = float(
                                Decimal(str(0.98 - entry_price)).quantize(
                                    Decimal("0.01"), rounding=ROUND_HALF_UP
                                )
                            )
                            gtc_hedge_price = max(0.01, min(0.99, gtc_hedge_price))
                            log(
                                f"   🔧 [{symbol}] Adjusted GTC hedge to ${gtc_hedge_price:.2f} (combined ${entry_price + gtc_hedge_price:.2f})"
                            )

                        # Calculate remaining hedge size (in case of partial fill)
                        remaining_hedge_size = entry_size - hedge_filled_size

                        # Place GTC hedge order (will pay taker fee but guarantees fill)
                        from src.trading.orders.limit import place_limit_order

                        gtc_result = place_limit_order(
                            token_id=hedge_token_id,
                            price=gtc_hedge_price,
                            size=remaining_hedge_size,
                            side=BUY,
                            order_type="GTC",  # Good-til-cancelled = taker order
                        )

                        if gtc_result.get("success"):
                            hedge_order_id = gtc_result.get("order_id")
                            hedge_price = gtc_hedge_price  # Update for DB tracking
                            gtc_switched = True

                            from src.config.settings import TAKER_FEE_RATE
                            from src.trading.edge import taker_fee

                            taker_fee_cost = remaining_hedge_size * taker_fee(
                                gtc_hedge_price, TAKER_FEE_RATE
                            )
                            taker_fee_pct = (
                                taker_fee_cost / (remaining_hedge_size * gtc_hedge_price) * 100
                                if remaining_hedge_size * gtc_hedge_price
                                else 0.0
                            )

                            log(
                                f"   ✅ [{symbol}] Hedge switched to GTC @ ${gtc_hedge_price:.2f} (ID: {hedge_order_id[:10]})"
                            )
                            log(
                                f"   💸 [{symbol}] Will pay taker fee: ${taker_fee_cost:.3f} ({taker_fee_pct}% of ${remaining_hedge_size * gtc_hedge_price:.2f})"
                            )
                            log(
                                f"   💰 [{symbol}] Combined cost: ${entry_price + gtc_hedge_price:.2f} (entry + hedge + fee vs ${0.99:.2f} settlement)"
                            )
                        else:
                            log_error(
                                f"[{symbol}] GTC hedge placement failed: {gtc_result.get('error')}"
                            )
                            # Fall through to normal timeout handling

                    except Exception as gtc_err:
                        log_error(f"[{symbol}] Error switching to GTC: {gtc_err}")
                        # Fall through to normal timeout handling

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

                # CRITICAL: Handle partial fills with smart hold logic
                # Check which position is more profitable and hold the winner
                if final_entry_filled and not final_hedge_filled:
                    # Entry filled but hedge didn't (or partially filled)
                    log(
                        f"   🚨 [{symbol}] CRITICAL: Entry filled ({final_entry_filled_size:.2f}) but hedge timed out (filled {final_hedge_filled_size:.2f}/{entry_size:.1f})"
                    )

                    # CRITICAL: Cancel unfilled hedge order IMMEDIATELY to prevent race condition
                    # If we wait until after emergency sell (can take 60+ seconds), the hedge could fill during that time!
                    try:
                        cancel_order(hedge_order_id)
                        log(
                            f"   🚫 [{symbol}] {hedge_side} order cancelled IMMEDIATELY (prevent race condition)"
                        )
                    except Exception as e:
                        log_error(f"[{symbol}] Error cancelling {hedge_side}: {e}")

                    # SMART DECISION: Check which position is more profitable
                    # CRITICAL: Can only hold ONE side (holding both = 100% loss on losing side)
                    from src.utils.websocket_manager import ws_manager
                    from src.config.settings import (
                        EMERGENCY_SELL_HOLD_IF_WINNING,
                        EMERGENCY_SELL_MIN_PROFIT_CENTS,
                    )

                    entry_profit = 0.0
                    hedge_profit = 0.0
                    entry_mid = 0.0
                    hedge_mid = 0.0

                    if EMERGENCY_SELL_HOLD_IF_WINNING:
                        # Check entry position profit
                        entry_bid, entry_ask = ws_manager.get_bid_ask(entry_token_id)
                        if entry_bid and entry_ask and entry_bid > 0.01:
                            entry_mid = (entry_bid + entry_ask) / 2
                            entry_profit = entry_mid - entry_price
                            log(
                                f"   📊 [{symbol}] {entry_side}: Filled ${entry_price:.2f}, now ${entry_mid:.2f} ({entry_profit:+.2f})"
                            )

                        # Check hedge position profit (if partially filled)
                        if final_hedge_filled_size > 0.01:
                            hedge_bid, hedge_ask = ws_manager.get_bid_ask(
                                hedge_token_id
                            )
                            if hedge_bid and hedge_ask and hedge_bid > 0.01:
                                hedge_mid = (hedge_bid + hedge_ask) / 2
                                hedge_profit = hedge_mid - hedge_price
                                log(
                                    f"   📊 [{symbol}] {hedge_side}: Filled ${hedge_price:.2f}, now ${hedge_mid:.2f} ({hedge_profit:+.2f})"
                                )

                    # CRITICAL LOGIC: Only hold the MORE profitable side (can't hold both!)
                    # If entry is more profitable → hold entry, sell hedge
                    # If hedge is more profitable → hold hedge, sell entry
                    # If neither profitable → sell both
                    min_profit = EMERGENCY_SELL_MIN_PROFIT_CENTS / 100

                    # FALLBACK: If orderbook unavailable, infer winner from entry prices
                    # Lower entry price = cheaper = more profit potential
                    if (
                        entry_mid == 0.0
                        and hedge_mid == 0.0
                        and final_hedge_filled_size > 0.01
                    ):
                        # Use entry prices as proxy for profitability
                        # Cheaper entry = higher profit potential in binary market
                        if entry_price < hedge_price:
                            # Entry cheaper → entry is winning
                            entry_profit = 0.10  # Fake profit to trigger hold logic
                            log(
                                f"   🔮 [{symbol}] Orderbook unavailable - inferring from entry prices: {entry_side} ${entry_price:.2f} < {hedge_side} ${hedge_price:.2f} → {entry_side} is winning"
                            )
                        else:
                            # Hedge cheaper → hedge is winning
                            hedge_profit = 0.10  # Fake profit to trigger hold logic
                            log(
                                f"   🔮 [{symbol}] Orderbook unavailable - inferring from entry prices: {hedge_side} ${hedge_price:.2f} < {entry_side} ${entry_price:.2f} → {hedge_side} is winning"
                            )

                    if (
                        EMERGENCY_SELL_HOLD_IF_WINNING
                        and entry_profit > min_profit
                        and entry_profit > hedge_profit
                    ):
                        # Entry is clearly winning - HOLD IT
                        log(
                            f"   🎯 [{symbol}] {entry_side} is MORE PROFITABLE (+${entry_profit:.2f}) - HOLDING {entry_side}, selling {hedge_side}"
                        )
                        # Keep entry, sell hedge (if any)
                        if final_hedge_filled_size > 0.01:
                            if final_hedge_filled_size < MIN_ORDER_SIZE:
                                log_error(
                                    f"   ⚠️  [{symbol}] Partial {hedge_side} fill ({final_hedge_filled_size:.2f} shares) is below minimum order size ({MIN_ORDER_SIZE})"
                                )
                                log_error(
                                    f"   🔒 [{symbol}] Cannot emergency sell - HOLDING {entry_side}, keeping orphaned {hedge_side}"
                                )
                                # Partial hedge too small to sell, but entry is winning so keep it
                            else:
                                log(
                                    f"   💥 [{symbol}] Emergency selling partial {hedge_side} position ({final_hedge_filled_size:.2f} shares)"
                                )
                                emergency_sell_position(
                                    symbol=symbol,
                                    token_id=hedge_token_id,
                                    size=final_hedge_filled_size,
                                    reason=f"holding winning {entry_side}, liquidating {hedge_side}",
                                    entry_order_id=hedge_order_id,
                                    entry_price=hedge_price,
                                    window_end=window_end,
                                )
                    elif (
                        EMERGENCY_SELL_HOLD_IF_WINNING
                        and final_hedge_filled_size > 0.01
                        and hedge_profit > min_profit
                        and hedge_profit > entry_profit
                    ):
                        # Hedge is clearly winning - HOLD IT
                        log(
                            f"   🎯 [{symbol}] {hedge_side} is MORE PROFITABLE (+${hedge_profit:.2f}) - HOLDING {hedge_side}, selling {entry_side}"
                        )
                        # Keep hedge, sell entry
                        if final_entry_filled_size < MIN_ORDER_SIZE:
                            log_error(
                                f"   ⚠️  [{symbol}] {entry_side} fill ({final_entry_filled_size:.2f} shares) is below minimum order size ({MIN_ORDER_SIZE})"
                            )
                            log_error(
                                f"   🔒 [{symbol}] Cannot emergency sell - HOLDING {hedge_side}, keeping orphaned {entry_side}"
                            )
                            # Entry too small to sell, but hedge is winning so keep it
                        else:
                            log(
                                f"   💥 [{symbol}] Emergency selling {entry_side} position ({final_entry_filled_size:.2f} shares)"
                            )
                            emergency_sell_position(
                                symbol=symbol,
                                token_id=entry_token_id,
                                size=final_entry_filled_size,
                                reason=f"holding winning {hedge_side}, liquidating {entry_side}",
                                entry_order_id=entry_order_id,
                                entry_price=entry_price,
                                window_end=window_end,
                            )
                    else:
                        # Neither side is clearly winning OR both equally profitable
                        # SPECIAL CASE: If hedge filled 0.0 shares (complete timeout) and we have high confidence,
                        # HOLD the entry position - our signal says it's likely to win
                        from src.config.settings import (
                            EMERGENCY_SELL_CONFIDENCE_HOLD_THRESHOLD,
                        )

                        if (
                            final_hedge_filled_size <= 0.01
                            and confidence is not None
                            and confidence >= EMERGENCY_SELL_CONFIDENCE_HOLD_THRESHOLD
                        ):
                            # High confidence trade with complete hedge failure
                            # Trust the signal and HOLD entry position
                            log(
                                f"   🎯 [{symbol}] HIGH CONFIDENCE ({confidence:.1%}) - HOLDING {entry_side} despite hedge timeout"
                            )
                            log(
                                f"   💎 [{symbol}] Trusting signal: {entry_side} @ ${entry_price:.2f} expected to win"
                            )
                            # Don't emergency sell - keep position through resolution
                            # This is a calculated risk based on strong signal confidence
                        else:
                            # Low/medium confidence or partial hedge fill - liquidate to minimize risk
                            if confidence is not None:
                                log(
                                    f"   💥 [{symbol}] Confidence too low ({confidence:.1%}) - emergency selling ALL positions"
                                )
                            else:
                                log(
                                    f"   💥 [{symbol}] No clear winner - emergency selling ALL positions"
                                )
                            log(
                                f"   💥 [{symbol}] Emergency selling entry position ({final_entry_filled_size:.2f} shares)"
                            )
                            emergency_sell_position(
                                symbol=symbol,
                                token_id=entry_token_id,
                                size=final_entry_filled_size,
                                reason="hedge timeout after entry fill",
                                entry_order_id=entry_order_id,
                                entry_price=entry_price,
                                window_end=window_end,
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
                                    entry_price=hedge_price,
                                    window_end=window_end,
                                )

                    # Note: Hedge order already cancelled at the top of this block

                elif final_hedge_filled and not final_entry_filled:
                    # Hedge filled but entry didn't (or partially filled)
                    log(
                        f"   🚨 [{symbol}] CRITICAL: {hedge_side} filled ({final_hedge_filled_size:.2f}) but {entry_side} timed out (filled {final_entry_filled_size:.2f}/{entry_size:.1f})"
                    )

                    # CRITICAL: Cancel unfilled entry order IMMEDIATELY to prevent race condition
                    # The entry order might be partially filled (e.g., 2/6 shares) or completely unfilled (0/6 shares)
                    # Either way, we MUST cancel the remaining unfilled portion NOW before starting emergency sell
                    # If we wait until after emergency sell (can take 60+ seconds), the entry could fill during that time!
                    try:
                        cancel_order(entry_order_id)
                        log(
                            f"   🚫 [{symbol}] {entry_side} order cancelled IMMEDIATELY (prevent race condition)"
                        )
                    except Exception as e:
                        log_error(f"[{symbol}] Error cancelling {entry_side}: {e}")

                    # SMART DECISION: Check which position is more profitable
                    # CRITICAL: Can only hold ONE side (holding both = 100% loss on losing side)
                    from src.utils.websocket_manager import ws_manager
                    from src.config.settings import (
                        EMERGENCY_SELL_HOLD_IF_WINNING,
                        EMERGENCY_SELL_MIN_PROFIT_CENTS,
                    )

                    hedge_profit = 0.0
                    entry_profit = 0.0
                    hedge_mid = 0.0
                    entry_mid = 0.0

                    if EMERGENCY_SELL_HOLD_IF_WINNING:
                        # Check hedge position profit
                        hedge_bid, hedge_ask = ws_manager.get_bid_ask(hedge_token_id)
                        if hedge_bid and hedge_ask and hedge_bid > 0.01:
                            hedge_mid = (hedge_bid + hedge_ask) / 2
                            hedge_profit = hedge_mid - hedge_price
                            log(
                                f"   📊 [{symbol}] {hedge_side}: Filled ${hedge_price:.2f}, now ${hedge_mid:.2f} ({hedge_profit:+.2f})"
                            )

                        # Check entry position profit (if partially filled)
                        if final_entry_filled_size > 0.01:
                            entry_bid, entry_ask = ws_manager.get_bid_ask(
                                entry_token_id
                            )
                            if entry_bid and entry_ask and entry_bid > 0.01:
                                entry_mid = (entry_bid + entry_ask) / 2
                                entry_profit = entry_mid - entry_price
                                log(
                                    f"   📊 [{symbol}] {entry_side}: Filled ${entry_price:.2f}, now ${entry_mid:.2f} ({entry_profit:+.2f})"
                                )

                    # CRITICAL LOGIC: Only hold the MORE profitable side (can't hold both!)
                    # If hedge is more profitable → hold hedge, sell entry
                    # If entry is more profitable → hold entry, sell hedge
                    # If neither profitable → sell both
                    min_profit = EMERGENCY_SELL_MIN_PROFIT_CENTS / 100

                    # FALLBACK: If orderbook unavailable, infer winner from entry prices
                    # Lower entry price = cheaper = more profit potential
                    if (
                        hedge_mid == 0.0
                        and entry_mid == 0.0
                        and final_entry_filled_size > 0.01
                    ):
                        # Use entry prices as proxy for profitability
                        # Cheaper entry = higher profit potential in binary market
                        if hedge_price < entry_price:
                            # Hedge cheaper → hedge is winning
                            hedge_profit = 0.10  # Fake profit to trigger hold logic
                            log(
                                f"   🔮 [{symbol}] Orderbook unavailable - inferring from entry prices: {hedge_side} ${hedge_price:.2f} < {entry_side} ${entry_price:.2f} → {hedge_side} is winning"
                            )
                        else:
                            # Entry cheaper → entry is winning
                            entry_profit = 0.10  # Fake profit to trigger hold logic
                            log(
                                f"   🔮 [{symbol}] Orderbook unavailable - inferring from entry prices: {entry_side} ${entry_price:.2f} < {hedge_side} ${hedge_price:.2f} → {entry_side} is winning"
                            )

                    if (
                        EMERGENCY_SELL_HOLD_IF_WINNING
                        and hedge_profit > min_profit
                        and hedge_profit > entry_profit
                    ):
                        # Hedge is clearly winning - HOLD IT
                        log(
                            f"   🎯 [{symbol}] {hedge_side} is MORE PROFITABLE (+${hedge_profit:.2f}) - HOLDING {hedge_side}, selling {entry_side}"
                        )
                        # Keep hedge, sell entry
                        if final_entry_filled_size > 0.01:
                            if final_entry_filled_size < MIN_ORDER_SIZE:
                                log_error(
                                    f"   ⚠️  [{symbol}] Partial {entry_side} fill ({final_entry_filled_size:.2f} shares) is below minimum order size ({MIN_ORDER_SIZE})"
                                )
                                log_error(
                                    f"   🔒 [{symbol}] Cannot emergency sell - HOLDING {hedge_side}, keeping orphaned {entry_side}"
                                )
                                # Partial entry too small to sell, but hedge is winning so keep it
                            else:
                                log(
                                    f"   💥 [{symbol}] Emergency selling partial {entry_side} position ({final_entry_filled_size:.2f} shares)"
                                )
                                emergency_sell_position(
                                    symbol=symbol,
                                    token_id=entry_token_id,
                                    size=final_entry_filled_size,
                                    reason=f"holding winning {hedge_side}, liquidating {entry_side}",
                                    entry_order_id=entry_order_id,
                                    entry_price=entry_price,
                                    window_end=window_end,
                                )
                    elif (
                        EMERGENCY_SELL_HOLD_IF_WINNING
                        and final_entry_filled_size > 0.01
                        and entry_profit > min_profit
                        and entry_profit > hedge_profit
                    ):
                        # Entry is clearly winning - HOLD IT
                        log(
                            f"   🎯 [{symbol}] {entry_side} is MORE PROFITABLE (+${entry_profit:.2f}) - HOLDING {entry_side}, selling {hedge_side}"
                        )
                        # Keep entry, sell hedge
                        log(
                            f"   💥 [{symbol}] Emergency selling {hedge_side} position ({final_hedge_filled_size:.2f} shares)"
                        )
                        emergency_sell_position(
                            symbol=symbol,
                            token_id=hedge_token_id,
                            size=final_hedge_filled_size,
                            reason=f"holding winning {entry_side}, liquidating {hedge_side}",
                            entry_order_id=hedge_order_id,
                            entry_price=hedge_price,
                            window_end=window_end,
                        )
                    else:
                        # Neither is clearly winning, or both similar - SELL BOTH
                        log(
                            f"   ⚠️  [{symbol}] Neither side clearly winning (hedge: {hedge_profit:+.2f}, entry: {entry_profit:+.2f}) - selling both"
                        )
                        # Sell hedge
                        log(
                            f"   💥 [{symbol}] Emergency selling {hedge_side} position ({final_hedge_filled_size:.2f} shares)"
                        )
                        emergency_sell_position(
                            symbol=symbol,
                            token_id=hedge_token_id,
                            size=final_hedge_filled_size,
                            reason=f"{entry_side} timeout after {hedge_side} fill",
                            entry_order_id=hedge_order_id,
                            entry_price=hedge_price,
                            window_end=window_end,
                        )
                        # Sell entry if partially filled
                        if final_entry_filled_size > 0.01:
                            if final_entry_filled_size < MIN_ORDER_SIZE:
                                log_error(
                                    f"   ⚠️  [{symbol}] Partial {entry_side} fill ({final_entry_filled_size:.2f} shares) is below minimum order size ({MIN_ORDER_SIZE})"
                                )
                                log_error(
                                    f"   🔒 [{symbol}] Cannot emergency sell - position ORPHANED (too small to sell)"
                                )
                                # Note: Orphaned positions (<5 shares) are held through resolution
                                # Future enhancement: Add database tracking for orphaned positions
                            else:
                                log(
                                    f"   💥 [{symbol}] Emergency selling partial {entry_side} position ({final_entry_filled_size:.2f} shares)"
                                )
                                emergency_sell_position(
                                    symbol=symbol,
                                    token_id=entry_token_id,
                                    size=final_entry_filled_size,
                                    reason=f"partial {entry_side} fill cleanup",
                                    entry_order_id=entry_order_id,
                                    entry_price=entry_price,
                                    window_end=window_end,
                                )
                        # Note: Entry order already cancelled at the top of this block

                else:
                    # Neither filled or both partially filled - cancel both and check what to keep
                    log(
                        f"   🚨 [{symbol}] TIMEOUT: Neither order fully filled - cleaning up"
                    )

                    # SMART DECISION: Check which position is more profitable
                    # CRITICAL: Can only hold ONE side (holding both = 100% loss on losing side)
                    from src.utils.websocket_manager import ws_manager
                    from src.config.settings import (
                        EMERGENCY_SELL_HOLD_IF_WINNING,
                        EMERGENCY_SELL_MIN_PROFIT_CENTS,
                    )

                    entry_profit = 0.0
                    hedge_profit = 0.0

                    if EMERGENCY_SELL_HOLD_IF_WINNING:
                        # Check entry position profit (if partially filled)
                        if final_entry_filled_size > 0.01:
                            entry_bid, entry_ask = ws_manager.get_bid_ask(
                                entry_token_id
                            )
                            if entry_bid and entry_ask and entry_bid > 0.01:
                                entry_mid = (entry_bid + entry_ask) / 2
                                entry_profit = entry_mid - entry_price
                                log(
                                    f"   📊 [{symbol}] {entry_side}: Filled ${entry_price:.2f}, now ${entry_mid:.2f} ({entry_profit:+.2f})"
                                )

                        # Check hedge position profit (if partially filled)
                        if final_hedge_filled_size > 0.01:
                            hedge_bid, hedge_ask = ws_manager.get_bid_ask(
                                hedge_token_id
                            )
                            if hedge_bid and hedge_ask and hedge_bid > 0.01:
                                hedge_mid = (hedge_bid + hedge_ask) / 2
                                hedge_profit = hedge_mid - hedge_price
                                log(
                                    f"   📊 [{symbol}] {hedge_side}: Filled ${hedge_price:.2f}, now ${hedge_mid:.2f} ({hedge_profit:+.2f})"
                                )

                    # CRITICAL LOGIC: Only hold the MORE profitable side (can't hold both!)
                    min_profit = EMERGENCY_SELL_MIN_PROFIT_CENTS / 100

                    if (
                        EMERGENCY_SELL_HOLD_IF_WINNING
                        and final_entry_filled_size > 0.01
                        and entry_profit > min_profit
                        and entry_profit > hedge_profit
                    ):
                        # Entry is more profitable - HOLD IT, sell hedge
                        log(
                            f"   🎯 [{symbol}] {entry_side} is MORE PROFITABLE (+${entry_profit:.2f}) - HOLDING {entry_side}, selling {hedge_side}"
                        )
                        if final_hedge_filled_size > 0.01:
                            if final_hedge_filled_size < MIN_ORDER_SIZE:
                                log_error(
                                    f"   ⚠️  [{symbol}] Partial {hedge_side} fill ({final_hedge_filled_size:.2f} shares) is below minimum order size ({MIN_ORDER_SIZE})"
                                )
                                log_error(
                                    f"   🔒 [{symbol}] Cannot emergency sell - HOLDING {entry_side}, keeping orphaned {hedge_side}"
                                )
                            else:
                                log(
                                    f"   💥 [{symbol}] Emergency selling partial {hedge_side} position ({final_hedge_filled_size:.2f} shares)"
                                )
                                emergency_sell_position(
                                    symbol=symbol,
                                    token_id=hedge_token_id,
                                    size=final_hedge_filled_size,
                                    reason=f"holding winning {entry_side}, liquidating {hedge_side}",
                                    entry_order_id=hedge_order_id,
                                    entry_price=hedge_price,
                                )
                    elif (
                        EMERGENCY_SELL_HOLD_IF_WINNING
                        and final_hedge_filled_size > 0.01
                        and hedge_profit > min_profit
                        and hedge_profit > entry_profit
                    ):
                        # Hedge is more profitable - HOLD IT, sell entry
                        log(
                            f"   🎯 [{symbol}] {hedge_side} is MORE PROFITABLE (+${hedge_profit:.2f}) - HOLDING {hedge_side}, selling {entry_side}"
                        )
                        if final_entry_filled_size > 0.01:
                            if final_entry_filled_size < MIN_ORDER_SIZE:
                                log_error(
                                    f"   ⚠️  [{symbol}] Partial {entry_side} fill ({final_entry_filled_size:.2f} shares) is below minimum order size ({MIN_ORDER_SIZE})"
                                )
                                log_error(
                                    f"   🔒 [{symbol}] Cannot emergency sell - HOLDING {hedge_side}, keeping orphaned {entry_side}"
                                )
                            else:
                                log(
                                    f"   💥 [{symbol}] Emergency selling partial {entry_side} position ({final_entry_filled_size:.2f} shares)"
                                )
                                emergency_sell_position(
                                    symbol=symbol,
                                    token_id=entry_token_id,
                                    size=final_entry_filled_size,
                                    reason=f"holding winning {hedge_side}, liquidating {entry_side}",
                                    entry_order_id=entry_order_id,
                                    entry_price=entry_price,
                                    window_end=window_end,
                                )
                    else:
                        # Neither is clearly winning, or both similar - SELL BOTH
                        log(
                            f"   ⚠️  [{symbol}] Neither side clearly winning (entry: {entry_profit:+.2f}, hedge: {hedge_profit:+.2f}) - selling both"
                        )
                        # Sell any partial entry fills
                        if final_entry_filled_size > 0.01:
                            if final_entry_filled_size < MIN_ORDER_SIZE:
                                log_error(
                                    f"   ⚠️  [{symbol}] Partial {entry_side} fill ({final_entry_filled_size:.2f} shares) is below minimum order size ({MIN_ORDER_SIZE})"
                                )
                                log_error(
                                    f"   🔒 [{symbol}] Cannot emergency sell {entry_side} - position ORPHANED"
                                )
                            else:
                                log(
                                    f"   💥 [{symbol}] Emergency selling partial {entry_side} position ({final_entry_filled_size:.2f} shares)"
                                )
                                emergency_sell_position(
                                    symbol=symbol,
                                    token_id=entry_token_id,
                                    size=final_entry_filled_size,
                                    reason=f"partial {entry_side} fill cleanup",
                                    entry_order_id=entry_order_id,
                                    entry_price=entry_price,
                                    window_end=window_end,
                                )

                        # Sell any partial hedge fills
                        if final_hedge_filled_size > 0.01:
                            if final_hedge_filled_size < MIN_ORDER_SIZE:
                                log_error(
                                    f"   ⚠️  [{symbol}] Partial {hedge_side} fill ({final_hedge_filled_size:.2f} shares) is below minimum order size ({MIN_ORDER_SIZE})"
                                )
                                log_error(
                                    f"   🔒 [{symbol}] Cannot emergency sell {hedge_side} - position ORPHANED"
                                )
                            else:
                                log(
                                    f"   💥 [{symbol}] Emergency selling partial {hedge_side} position ({final_hedge_filled_size:.2f} shares)"
                                )
                                emergency_sell_position(
                                    symbol=symbol,
                                    token_id=hedge_token_id,
                                    size=final_hedge_filled_size,
                                    reason=f"partial {hedge_side} fill cleanup",
                                    entry_order_id=hedge_order_id,
                                    entry_price=hedge_price,
                                )

                        # Cancel both orders since we're selling both sides
                        try:
                            cancel_order(entry_order_id)
                            log(f"   ✅ [{symbol}] {entry_side} order cancelled")
                        except Exception as e:
                            log_error(f"[{symbol}] Error cancelling {entry_side}: {e}")

                        try:
                            cancel_order(hedge_order_id)
                            log(f"   ✅ [{symbol}] {hedge_side} order cancelled")
                        except Exception as e:
                            log_error(f"[{symbol}] Error cancelling {hedge_side}: {e}")

                    # If holding winning entry, keep its order open for more fills
                    if (
                        EMERGENCY_SELL_HOLD_IF_WINNING
                        and final_entry_filled_size > 0.01
                        and entry_profit > min_profit
                        and entry_profit > hedge_profit
                    ):
                        log(
                            f"   📋 [{symbol}] Keeping {entry_side} order open for potential additional fills at ${entry_price:.2f}"
                        )
                    # If holding winning hedge, keep its order open for more fills
                    elif (
                        EMERGENCY_SELL_HOLD_IF_WINNING
                        and final_hedge_filled_size > 0.01
                        and hedge_profit > min_profit
                        and hedge_profit > entry_profit
                    ):
                        log(
                            f"   📋 [{symbol}] Keeping {hedge_side} order open for potential additional fills at ${hedge_price:.2f}"
                        )
                        # Cancel the losing side's order
                        try:
                            cancel_order(entry_order_id)
                            log(
                                f"   ✅ [{symbol}] {entry_side} order cancelled (losing side)"
                            )
                        except Exception as e:
                            log_error(f"[{symbol}] Error cancelling {entry_side}: {e}")

            except Exception as timeout_err:
                log_error(
                    f"[{symbol}] Error handling timeout with partial fills: {timeout_err}"
                )

            log(f"   🚫 [{symbol}] Trade skipped - atomic pair failed")
            return None, None, None

        return entry_result, hedge_result, hedge_price

    except Exception as e:
        log_error(f"[{symbol}] Error in atomic entry+hedge placement: {e}")
        return None, None, None


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
    hedge_price = None
    actual_size = size
    actual_price = price

    if not is_reversal:
        entry_result, hedge_result, hedge_price = place_entry_and_hedge_atomic(
            symbol,
            token_id,
            side,
            price,
            size,
            trade_params.get("window_end"),
            trade_params.get("confidence"),
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
                            "UPDATE trades SET hedge_order_id = ?, hedge_order_price = ?, is_hedged = 1, order_status = 'HEDGED' WHERE id = ?",
                            (hedge_order_id, hedge_price, trade_id),
                        )
                        log(
                            f"   ✅ [{symbol}] #{trade_id} Hedge verified filled - position fully hedged"
                        )
                    else:
                        from src.data.db_connection import db_connection

                        with db_connection() as conn:
                            c = conn.cursor()
                            c.execute(
                                "UPDATE trades SET hedge_order_id = ?, hedge_order_price = ?, is_hedged = 1, order_status = 'HEDGED' WHERE id = ?",
                                (hedge_order_id, hedge_price, trade_id),
                            )
                            log(
                                f"   ✅ [{symbol}] #{trade_id} Hedge verified filled - position fully hedged"
                            )
                else:
                    # At least one order is not filled/cancelled - save hedge_order_id and hedge_price but don't mark as hedged
                    if cursor:
                        cursor.execute(
                            "UPDATE trades SET hedge_order_id = ?, hedge_order_price = ? WHERE id = ?",
                            (hedge_order_id, hedge_price, trade_id),
                        )
                    else:
                        from src.data.db_connection import db_connection

                        with db_connection() as conn:
                            c = conn.cursor()
                            c.execute(
                                "UPDATE trades SET hedge_order_id = ?, hedge_order_price = ? WHERE id = ?",
                                (hedge_order_id, hedge_price, trade_id),
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
                # Still save hedge_order_id and hedge_price even if verification failed
                if cursor:
                    cursor.execute(
                        "UPDATE trades SET hedge_order_id = ?, hedge_order_price = ? WHERE id = ?",
                        (hedge_order_id, hedge_price, trade_id),
                    )
                else:
                    from src.data.db_connection import db_connection

                    with db_connection() as conn:
                        c = conn.cursor()
                        c.execute(
                            "UPDATE trades SET hedge_order_id = ?, hedge_order_price = ? WHERE id = ?",
                            (hedge_order_id, hedge_price, trade_id),
                        )

        emoji = trade_params.get("emoji", "🚀")
        entry_type = trade_params.get("entry_type", "Trade")
        log(
            f"{emoji} [{symbol}] {entry_type}: {trade_params.get('core_summary', '')} | #{trade_id} {side} ${trade_params['bet_usd']:.2f} @ {actual_price:.4f} | ID: {order_id[:10] if order_id else 'N/A'}"
        )

        # Subscribe to both entry and hedge tokens for real-time price updates
        # This ensures pre-settlement exit has fresh prices available
        try:
            from src.utils.websocket_manager import ws_manager
            from src.data.market_data import get_token_ids

            up_id, down_id = get_token_ids(symbol)
            tokens_to_subscribe = []
            if up_id:
                tokens_to_subscribe.append(up_id)
            if down_id:
                tokens_to_subscribe.append(down_id)

            if tokens_to_subscribe:
                ws_manager.subscribe_to_prices(
                    tokens_to_subscribe,
                    symbol_map={token_id: symbol for token_id in tokens_to_subscribe},
                )
        except Exception as sub_err:
            log_error(f"[{symbol}] Error subscribing to WebSocket prices: {sub_err}")

        return trade_id
    except Exception as e:
        log_error(f"[{symbol}] Trade completion error: {e}")
        return None
