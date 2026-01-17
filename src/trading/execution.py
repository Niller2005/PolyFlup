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


def place_hedge_order(
    trade_id: int,
    symbol: str,
    entry_side: str,
    entry_price: float,
    entry_size: float,
    entry_order_id: str = None,
    cursor=None,
) -> Optional[str]:
    """
    Place a limit order on the opposite side to hedge the position.

    Hedge price is calculated based on CTF_MERGE setting:
    - If CTF_MERGE enabled: Combined price = $1.00 (merge returns full $1.00 per share)
    - If CTF_MERGE disabled: Combined price = $0.99 (wait for settlement, guarantee profit)

    Example (CTF_MERGE=YES): Enter UP @ $0.52, hedge DOWN @ $0.48 = $1.00 total
    Example (CTF_MERGE=NO):  Enter UP @ $0.52, hedge DOWN @ $0.47 = $0.99 total

    Args:
        trade_id: ID of the entry trade
        symbol: Trading symbol
        entry_side: Side of entry trade (UP or DOWN)
        entry_price: Price of entry trade
        entry_size: Size of entry trade (will be verified against actual fill)
        entry_order_id: Order ID of entry trade (for fill verification)
        cursor: Optional database cursor (if called within existing transaction)

    Returns:
        Order ID if successful, None otherwise
    """
    try:
        from src.config.settings import ENABLE_CTF_MERGE
        from src.data.db_connection import db_connection

        # CRITICAL: Verify actual entry fill size before placing hedge
        # This prevents hedge size mismatch when entry order partially fills
        actual_entry_size = entry_size
        if entry_order_id:
            try:
                entry_order = get_order(entry_order_id)
                if entry_order:
                    filled_size = float(entry_order.get("size_matched", 0))
                    original_size = float(entry_order.get("original_size", entry_size))

                    if filled_size > 0:
                        actual_entry_size = filled_size

                        # Log if there's a significant difference (>0.1%)
                        size_diff_pct = abs(filled_size - entry_size) / entry_size * 100
                        if size_diff_pct > 0.1:
                            log(
                                f"   📊 [{symbol}] Entry partially filled: {filled_size:.4f}/{entry_size:.1f} ({size_diff_pct:.1f}% diff) - adjusting hedge size"
                            )
            except Exception as verify_err:
                log(
                    f"   ⚠️  [{symbol}] Could not verify entry fill size, using requested size: {verify_err}"
                )

        up_id, down_id = get_token_ids(symbol)
        if not up_id or not down_id:
            return None

        # Calculate opposite side and token
        if entry_side == "UP":
            hedge_side = "DOWN"
            hedge_token_id = down_id
        else:
            hedge_side = "UP"
            hedge_token_id = up_id

        # Calculate hedge price based on CTF merge setting
        # CRITICAL: Always target $0.99 combined to guarantee profit
        # Even with CTF merge enabled, $0.99 cost → $1.00 merge = $0.01 profit
        # Using $1.00 combined would be break-even (loses money on fees)
        target_combined = 0.99

        target_hedge_price = round(target_combined - entry_price, 2)
        target_hedge_price = max(0.01, min(0.99, target_hedge_price))

        # CRITICAL: Check orderbook to ensure hedge will fill
        # If best ask is higher than our target, we need to pay more to fill immediately
        hedge_price = target_hedge_price
        try:
            clob_client = get_clob_client()
            orderbook = clob_client.get_order_book(hedge_token_id)

            if orderbook and isinstance(orderbook, dict):
                asks = orderbook.get("asks", [])
                if asks and len(asks) > 0:
                    # Get best ask (lowest sell price)
                    best_ask = float(asks[0].get("price", 0))

                    if best_ask > target_hedge_price:
                        # Market is more expensive than our target
                        # Check if using best ask would still guarantee profit
                        combined_price = entry_price + best_ask

                        if combined_price < 1.00:
                            # Still profitable at market price, use it
                            hedge_price = best_ask
                            log(
                                f"   📊 [{symbol}] Hedge adjusted: target ${target_hedge_price:.2f} → ${hedge_price:.2f} (market best ask) | Combined: ${combined_price:.2f}"
                            )
                        else:
                            # Market price would create guaranteed loss
                            log(
                                f"   ⚠️  [{symbol}] Hedge skipped: market ${best_ask:.2f} + entry ${entry_price:.2f} = ${combined_price:.2f} > $1.00 (guaranteed loss)"
                            )
                            return None
                    else:
                        log(
                            f"   💰 [{symbol}] Hedge pricing favorable: target ${target_hedge_price:.2f}, market ${best_ask:.2f}"
                        )
        except Exception as e:
            log(f"   ⚠️  [{symbol}] Could not check orderbook, using target price: {e}")

        # Ensure final price is within valid range
        hedge_price = max(0.01, min(0.99, hedge_price))

        # Pre-flight balance check to ensure we can afford the hedge
        from src.trading.orders import get_balance_allowance
        from src.trading.logic import MIN_SIZE

        try:
            bal_check = get_balance_allowance()
            current_balance = float(bal_check.get("balance", 0)) if bal_check else 0.0
            hedge_cost = actual_entry_size * hedge_price

            if current_balance < hedge_cost:
                log(
                    f"   ❌ [{symbol}] Cannot place hedge: Insufficient balance (${current_balance:.2f} < ${hedge_cost:.2f} needed)"
                )
                return None

            # Also warn if balance would drop below $5 minimum after hedge
            remaining_after_hedge = current_balance - hedge_cost
            if remaining_after_hedge < (MIN_SIZE * 1.0):
                log(
                    f"   ⚠️  [{symbol}] Placing hedge will leave ${remaining_after_hedge:.2f} (below ${MIN_SIZE * 1.0:.2f} minimum for future trades)"
                )
        except Exception as bal_err:
            log(f"   ⚠️  [{symbol}] Could not verify balance before hedge: {bal_err}")
            # Continue anyway - better to attempt hedge than leave position unhedged

        # Place hedge order with actual filled entry size
        result = place_order(hedge_token_id, hedge_price, actual_entry_size)

        if not result["success"]:
            log(f"   ❌ [{symbol}] Hedge order failed: {result.get('error')}")
            return None

        order_id = result["order_id"]
        merge_status = " [MERGE]" if ENABLE_CTF_MERGE else ""
        log(
            f"   🛡️  [{symbol}] Hedge order placed{merge_status}: {hedge_side} {actual_entry_size:.1f} @ ${hedge_price:.2f} (ID: {order_id[:10]}) | Combined: ${entry_price:.2f} + ${hedge_price:.2f} = ${(entry_price + hedge_price):.2f}"
        )

        # Check remaining capital after hedge - warn if insufficient for scale-ins
        try:
            from src.trading.orders import get_balance_allowance

            bal_info = get_balance_allowance()
            remaining_usdc = float(bal_info.get("balance", 0)) if bal_info else 0.0

            # Scale-in typically needs ~$6-8 (size * price for similar position)
            scale_in_reserve = actual_entry_size * entry_price

            if remaining_usdc < scale_in_reserve:
                log(
                    f"   ⚠️  [{symbol}] Low capital after hedge: ${remaining_usdc:.2f} remaining (scale-in needs ~${scale_in_reserve:.2f})"
                )
                log(
                    f"   💡 [{symbol}] Consider disabling scale-ins or reducing BET_SIZE to preserve capital for hedges"
                )
        except Exception as cap_check_err:
            pass  # Don't fail hedge placement if balance check errors

        # Store hedge price in database (only if trade_id exists)
        if trade_id > 0:
            if cursor:
                # Use existing transaction cursor
                cursor.execute(
                    "UPDATE trades SET hedge_order_price = ? WHERE id = ?",
                    (hedge_price, trade_id),
                )
            else:
                # Open new connection
                with db_connection() as conn:
                    c = conn.cursor()
                    c.execute(
                        "UPDATE trades SET hedge_order_price = ? WHERE id = ?",
                        (hedge_price, trade_id),
                    )

        # IMMEDIATE VERIFICATION: Check if hedge filled within 2 seconds
        # This provides a fallback when WebSocket notifications fail
        time.sleep(2)
        try:
            hedge_status = get_order(order_id)
            if hedge_status:
                hedge_filled_size = float(hedge_status.get("size_matched", 0))
                # Use 0.01 tolerance (99%+ filled = fully hedged)
                if hedge_filled_size >= actual_entry_size - 0.01:
                    log(
                        f"   ✅ [{symbol}] Hedge immediately verified: {hedge_filled_size:.2f}/{actual_entry_size:.2f} shares filled"
                    )

                    # Mark as hedged in database (only if trade_id exists)
                    # When called from execute_trade with trade_id=0, this will be updated later
                    if trade_id > 0:
                        if cursor:
                            cursor.execute(
                                "UPDATE trades SET is_hedged = 1, order_status = 'HEDGED' WHERE id = ?",
                                (trade_id,),
                            )
                        else:
                            with db_connection() as conn:
                                c = conn.cursor()
                                c.execute(
                                    "UPDATE trades SET is_hedged = 1, order_status = 'HEDGED' WHERE id = ?",
                                    (trade_id,),
                                )

                        # Cancel exit plan if present (hedge is already filled)
                        try:
                            if cursor:
                                cursor.execute(
                                    "SELECT limit_sell_order_id FROM trades WHERE id = ?",
                                    (trade_id,),
                                )
                                exit_row = cursor.fetchone()
                            else:
                                with db_connection() as conn:
                                    c = conn.cursor()
                                    c.execute(
                                        "SELECT limit_sell_order_id FROM trades WHERE id = ?",
                                        (trade_id,),
                                    )
                                    exit_row = c.fetchone()

                            if exit_row and exit_row[0]:
                                from src.trading.orders import cancel_order

                                cancel_result = cancel_order(exit_row[0])
                                if cancel_result:
                                    log(
                                        f"   🚫 [{symbol}] Exit plan cancelled (hedge verified filled)"
                                    )
                                    if cursor:
                                        cursor.execute(
                                            "UPDATE trades SET limit_sell_order_id = NULL WHERE id = ?",
                                            (trade_id,),
                                        )
                                    else:
                                        with db_connection() as conn:
                                            c = conn.cursor()
                                            c.execute(
                                                "UPDATE trades SET limit_sell_order_id = NULL WHERE id = ?",
                                                (trade_id,),
                                            )
                        except Exception as cancel_err:
                            log_error(
                                f"[{symbol}] Error cancelling exit plan after hedge verification: {cancel_err}"
                            )
                else:
                    log(
                        f"   ⏳ [{symbol}] Hedge partially filled: {hedge_filled_size:.2f}/{actual_entry_size:.2f} shares (will continue monitoring)"
                    )
        except Exception as verify_err:
            log(
                f"   ⚠️  [{symbol}] Could not verify hedge fill immediately: {verify_err}"
            )

        return order_id
    except Exception as e:
        log_error(f"[{symbol}] Error placing hedge order: {e}")
        return None


def emergency_sell_position(
    symbol: str, token_id: str, size: float, reason: str = "hedge timeout"
) -> bool:
    """
    Emergency market sell of a filled position when hedge fails.
    Uses best bid price to immediately exit the position.
    Falls back to simple limit order at 0.05 if orderbook unavailable.

    Returns True if sell order placed successfully, False otherwise.
    """
    try:
        # Get current orderbook to find best bid
        clob_client = get_clob_client()
        orderbook = clob_client.get_order_book(token_id)

        best_bid = None
        if orderbook and isinstance(orderbook, dict):
            bids = orderbook.get("bids", [])
            if bids and len(bids) > 0:
                best_bid = float(bids[0].get("price", 0))

        # If we have a valid best bid, use it
        if best_bid and best_bid > 0.01:
            log(
                f"   🚨 [{symbol}] EMERGENCY SELL: Exiting {size:.2f} shares at ${best_bid:.2f} (best bid) due to {reason}"
            )

            # Place SELL limit order at best bid (should fill immediately as taker)
            result = place_limit_order(
                token_id=token_id,
                price=best_bid,
                size=size,
                side=SELL,
                order_type="FOK",  # Fill-or-Kill: fill immediately or cancel
            )

            if result.get("success"):
                order_id = result.get("order_id", "unknown")
                log(
                    f"   ✅ [{symbol}] Emergency sell order placed: {size:.2f} @ ${best_bid:.2f} (ID: {order_id[:10] if order_id != 'unknown' else order_id})"
                )
                return True
            else:
                log_error(
                    f"[{symbol}] Emergency sell at best bid failed: {result.get('error', 'unknown error')}"
                )
        else:
            log(
                f"   ⚠️  [{symbol}] Orderbook unavailable or no bids - using fallback price"
            )

        # FALLBACK: If orderbook unavailable or FOK failed, place simple limit order at low price
        # Use $0.05 to ensure quick fill while recovering some value
        fallback_price = 0.05
        log(
            f"   🚨 [{symbol}] EMERGENCY SELL (FALLBACK): Exiting {size:.2f} shares at ${fallback_price:.2f} due to {reason}"
        )

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
                f"   ✅ [{symbol}] Emergency sell order placed (fallback): {size:.2f} @ ${fallback_price:.2f} (ID: {order_id[:10] if order_id != 'unknown' else order_id})"
            )
            return True
        else:
            log_error(
                f"[{symbol}] Emergency sell failed (both attempts): {result.get('error', 'unknown error')}"
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

        # Calculate hedge price (always target $0.99 combined)
        target_hedge_price = round(0.99 - entry_price, 2)
        hedge_price = max(0.01, min(0.99, target_hedge_price))

        # Check orderbook to adjust hedge price for immediate fill
        try:
            clob_client = get_clob_client()
            orderbook = clob_client.get_order_book(hedge_token_id)

            if orderbook and isinstance(orderbook, dict):
                asks = orderbook.get("asks", [])
                if asks and len(asks) > 0:
                    best_ask = float(asks[0].get("price", 0))

                    if best_ask > hedge_price:
                        # Market wants more, check if still profitable
                        combined_price = entry_price + best_ask

                        if combined_price < 1.00:
                            # Still profitable, use market price
                            hedge_price = best_ask
                            log(
                                f"   📊 [{symbol}] Hedge adjusted for immediate fill: ${target_hedge_price:.2f} → ${hedge_price:.2f} (market best ask)"
                            )
                        else:
                            # Would create loss, skip hedge
                            log(
                                f"   ⚠️  [{symbol}] Cannot hedge atomically: market ${best_ask:.2f} + entry ${entry_price:.2f} = ${combined_price:.2f} > $1.00"
                            )
                            return None, None
        except Exception as book_err:
            log(
                f"   ⚠️  [{symbol}] Could not check orderbook, using target hedge price: {book_err}"
            )

        # Create batch order
        orders = [
            {
                "token_id": entry_token_id,
                "price": entry_price,
                "size": entry_size,
                "side": BUY,
            },
            {
                "token_id": hedge_token_id,
                "price": hedge_price,
                "size": entry_size,
                "side": BUY,
            },
        ]

        log(
            f"   🔄 [{symbol}] Placing ATOMIC entry+hedge: {entry_side} {entry_size:.1f} @ ${entry_price:.2f} + {hedge_side} {entry_size:.1f} @ ${hedge_price:.2f}"
        )

        # Submit both orders simultaneously
        results = place_batch_orders(orders)

        if len(results) < 2:
            log_error(f"[{symbol}] Batch order returned insufficient results")
            return None, None

        entry_result = results[0]
        hedge_result = results[1]

        # CRITICAL: If hedge fails, cancel entry to prevent unhedged position
        if entry_result.get("success") and not hedge_result.get("success"):
            entry_order_id = entry_result.get("order_id")
            log(
                f"   ⚠️  [{symbol}] Hedge order failed, cancelling entry order {entry_order_id[:10]} to prevent unhedged position"
            )
            try:
                from src.trading.orders import cancel_order

                cancel_order(entry_order_id)
                log(f"   ✅ [{symbol}] Entry order cancelled successfully")
                return None, None
            except Exception as cancel_err:
                log_error(
                    f"[{symbol}] CRITICAL: Failed to cancel entry order {entry_order_id[:10]}: {cancel_err}"
                )
                # Continue with original flow - entry might already be filled

        # Log results
        if entry_result.get("success"):
            log(
                f"   ✅ [{symbol}] Entry order placed: {entry_side} {entry_size:.1f} @ ${entry_price:.2f} (ID: {entry_result.get('order_id', 'unknown')[:10]})"
            )
        else:
            log(f"   ❌ [{symbol}] Entry order failed: {entry_result.get('error')}")

        if hedge_result.get("success"):
            log(
                f"   ✅ [{symbol}] Hedge order placed: {hedge_side} {entry_size:.1f} @ ${hedge_price:.2f} (ID: {hedge_result.get('order_id', 'unknown')[:10]})"
            )
        else:
            log(f"   ❌ [{symbol}] Hedge order failed: {hedge_result.get('error')}")

        # MONITOR: Poll hedge fill status with early exit
        # If hedge doesn't fill within timeout, cancel entry to prevent unhedged position
        if entry_result.get("success") and hedge_result.get("success"):
            from src.config.settings import (
                HEDGE_FILL_TIMEOUT_SECONDS,
                HEDGE_POLL_INTERVAL_SECONDS,
            )

            entry_order_id = entry_result.get("order_id")
            hedge_order_id = hedge_result.get("order_id")

            if not entry_order_id or not hedge_order_id:
                log(f"   ⚠️  [{symbol}] Missing order IDs, skipping hedge monitoring")
            else:
                log(
                    f"   ⏱️  [{symbol}] Monitoring hedge fill for {HEDGE_FILL_TIMEOUT_SECONDS}s (polling every {HEDGE_POLL_INTERVAL_SECONDS}s)..."
                )

                elapsed = 0
                hedge_filled = False
                entry_filled = False

                while elapsed < HEDGE_FILL_TIMEOUT_SECONDS:
                    time.sleep(HEDGE_POLL_INTERVAL_SECONDS)
                    elapsed += HEDGE_POLL_INTERVAL_SECONDS

                    try:
                        # Check hedge status
                        hedge_status = get_order(hedge_order_id)
                        if hedge_status:
                            hedge_filled_size = float(
                                hedge_status.get("size_matched", 0)
                            )
                            hedge_filled = hedge_filled_size >= (entry_size - 0.01)

                            if hedge_filled:
                                log(
                                    f"   ✅ [{symbol}] Hedge filled after {elapsed}s: {hedge_filled_size:.2f}/{entry_size:.1f} shares"
                                )
                                break
                            elif hedge_filled_size > 0:
                                log(
                                    f"   ⏳ [{symbol}] Hedge partially filled ({elapsed}s): {hedge_filled_size:.2f}/{entry_size:.1f} shares"
                                )

                        # Check entry status
                        entry_status = get_order(entry_order_id)
                        if entry_status:
                            entry_filled_size = float(
                                entry_status.get("size_matched", 0)
                            )
                            entry_filled = entry_filled_size >= (entry_size - 0.01)

                            if entry_filled and not hedge_filled:
                                log(
                                    f"   ⚠️  [{symbol}] Entry filled but hedge not filled after {elapsed}s"
                                )

                                # EARLY EXIT: If entry filled after 10+ seconds and hedge still unfilled,
                                # immediately exit the position to minimize loss
                                if elapsed >= 10:
                                    log(
                                        f"   🚨 [{symbol}] EARLY EXIT TRIGGER: Entry filled but hedge unfilled after {elapsed}s - exiting now"
                                    )

                                    sell_success = emergency_sell_position(
                                        symbol=symbol,
                                        token_id=entry_token_id,
                                        size=entry_filled_size,
                                        reason=f"hedge unfilled after {elapsed}s",
                                    )

                                    if sell_success:
                                        log(
                                            f"   ✅ [{symbol}] Early exit successful - position closed"
                                        )
                                        # Cancel the hedge order
                                        try:
                                            cancel_order(hedge_order_id)
                                            log(
                                                f"   ✅ [{symbol}] Hedge order cancelled"
                                            )
                                        except Exception:
                                            pass
                                        return None, None
                                    else:
                                        log_error(
                                            f"[{symbol}] Early exit FAILED - will retry at timeout"
                                        )

                    except Exception as poll_err:
                        log(f"   ⚠️  [{symbol}] Error polling order status: {poll_err}")

                # TIMEOUT: If hedge didn't fill and entry did, cancel entry to prevent unhedged position
                if not hedge_filled:
                    try:
                        # Check final status
                        entry_status = get_order(entry_order_id)
                        if entry_status:
                            entry_filled_size = float(
                                entry_status.get("size_matched", 0)
                            )
                            entry_filled = entry_filled_size >= (entry_size - 0.01)

                            if entry_filled:
                                log(
                                    f"   ❌ [{symbol}] HEDGE TIMEOUT: Entry filled ({entry_filled_size:.2f}) but hedge unfilled after {HEDGE_FILL_TIMEOUT_SECONDS}s"
                                )
                                log(
                                    f"   ⚠️  [{symbol}] CRITICAL: Position is UNHEDGED - initiating emergency exit"
                                )

                                # EMERGENCY EXIT: Market sell the filled entry position immediately
                                # This prevents holding an unhedged position which could result in 50%+ losses
                                sell_success = emergency_sell_position(
                                    symbol=symbol,
                                    token_id=entry_token_id,
                                    size=entry_filled_size,
                                    reason="hedge timeout",
                                )

                                if sell_success:
                                    log(
                                        f"   ✅ [{symbol}] Emergency exit successful - position closed"
                                    )
                                    # Cancel the hedge order since we no longer need it
                                    try:
                                        cancel_result = cancel_order(hedge_order_id)
                                        if cancel_result:
                                            log(
                                                f"   ✅ [{symbol}] Hedge order cancelled (no longer needed)"
                                            )
                                    except Exception as cancel_err:
                                        log(
                                            f"   ⚠️  [{symbol}] Could not cancel hedge order: {cancel_err}"
                                        )

                                    # Return None to prevent trade from being saved to database
                                    return None, None
                                else:
                                    log_error(
                                        f"[{symbol}] Emergency exit FAILED - position remains unhedged!"
                                    )
                                    # Continue with original flow - position will be tracked as unhedged
                                    # The position manager will attempt corrective hedges
                            else:
                                # Entry not filled yet, cancel it
                                log(
                                    f"   ⏱️  [{symbol}] HEDGE TIMEOUT: Hedge unfilled after {HEDGE_FILL_TIMEOUT_SECONDS}s, cancelling entry order"
                                )
                                from src.trading.orders import cancel_order

                                cancel_result = cancel_order(entry_order_id)
                                if cancel_result:
                                    log(
                                        f"   ✅ [{symbol}] Entry order cancelled to prevent unhedged position"
                                    )
                                    log(
                                        f"   📌 [{symbol}] Hedge order left live (may still fill later)"
                                    )
                                    return None, None
                                else:
                                    log(
                                        f"   ⚠️  [{symbol}] Failed to cancel entry order - check position manually"
                                    )

                        # NOTE: Do NOT cancel the hedge order - leave it live so it can still fill later
                        # The hedge provides protection even if entry was cancelled

                    except Exception as timeout_err:
                        log_error(
                            f"[{symbol}] Error handling hedge timeout: {timeout_err}"
                        )

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
        if usdc_balance < total_cost_needed:
            log(
                f"[{symbol}] ❌ Insufficient funds for entry + hedge (Need ${total_cost_needed:.2f} [entry ${entry_cost:.2f} + hedge ${hedge_cost:.2f}], Have ${usdc_balance:.2f})"
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
