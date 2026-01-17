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
    reason: str = "hedge timeout",
    entry_order_id: str = None,
) -> bool:
    """
    Emergency market sell of a filled position when hedge fails.
    Uses best bid price to immediately exit the position.
    Falls back to simple limit order at 0.05 if orderbook unavailable.

    Args:
        symbol: Trading symbol
        token_id: Token ID to sell
        size: Number of shares to sell
        reason: Reason for emergency exit
        entry_order_id: Entry order ID to look up any existing exit orders (optional)

    Returns True if sell order placed successfully, False otherwise.
    """
    try:
        # CRITICAL: Wait for balance API to sync after order fill
        # The entry order just filled, but balance API has 1-3s lag before shares are available
        log(f"   ⏳ [{symbol}] Waiting 3s for balance API to sync after fill...")
        time.sleep(3)

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
        # Get current orderbook to find best bid
        clob_client = get_clob_client()

        best_bid = None
        orderbook_available = False

        try:
            orderbook = clob_client.get_order_book(token_id)

            if orderbook and isinstance(orderbook, dict):
                bids = orderbook.get("bids", [])
                if bids and len(bids) > 0:
                    best_bid = float(bids[0].get("price", 0))
                    if best_bid > 0.01:
                        orderbook_available = True
                        log(
                            f"   📊 [{symbol}] Orderbook available: best bid = ${best_bid:.2f}"
                        )
                    else:
                        log(
                            f"   ⚠️  [{symbol}] Orderbook has invalid best bid: ${best_bid:.2f}"
                        )
                else:
                    log(f"   ⚠️  [{symbol}] Orderbook has no bids (empty book)")
            else:
                log(
                    f"   ⚠️  [{symbol}] Orderbook returned invalid data: {type(orderbook)}"
                )
        except Exception as book_err:
            log(f"   ⚠️  [{symbol}] Error fetching orderbook: {book_err}")

        # PROGRESSIVE PRICING: Try multiple prices before falling back to low price
        # This maximizes recovery value while ensuring quick fill
        if orderbook_available and best_bid:
            # Strategy: Try best bid, then progressively lower prices with FOK
            # FOK ensures immediate fill or rejection, preventing stuck orders
            attempts = [
                ("best bid", best_bid),
                ("best bid - $0.01", max(0.01, best_bid - 0.01)),
                ("best bid - $0.02", max(0.01, best_bid - 0.02)),
                ("best bid - $0.03", max(0.01, best_bid - 0.03)),
            ]

            for attempt_name, attempt_price in attempts:
                log(
                    f"   🚨 [{symbol}] EMERGENCY SELL: Trying {size:.2f} shares at ${attempt_price:.2f} ({attempt_name}) due to {reason}"
                )

                result = place_limit_order(
                    token_id=token_id,
                    price=attempt_price,
                    size=size,
                    side=SELL,
                    order_type="FOK",  # Fill-or-Kill: fill immediately or cancel
                )

                if result.get("success"):
                    order_id = result.get("order_id", "unknown")
                    log(
                        f"   ✅ [{symbol}] Emergency sell filled: {size:.2f} @ ${attempt_price:.2f} ({attempt_name}) (ID: {order_id[:10] if order_id != 'unknown' else order_id})"
                    )
                    return True
                else:
                    log(
                        f"   ⚠️  [{symbol}] Attempt at ${attempt_price:.2f} failed: {result.get('error', 'no fill')}"
                    )
        else:
            log(
                f"   ⚠️  [{symbol}] Orderbook unavailable - skipping progressive pricing"
            )

        # FINAL FALLBACK: If all FOK attempts failed, place GTC order at conservative price
        # Use $0.10 instead of $0.05 - better recovery value, still fills quickly
        fallback_price = 0.10
        log(
            f"   🚨 [{symbol}] EMERGENCY SELL (FINAL FALLBACK): Placing GTC at ${fallback_price:.2f} due to {reason}"
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

        # CRITICAL: Use TAKER pricing for hedge to ensure immediate fill
        # Query orderbook to get best ask (market's sell price)
        clob_client = get_clob_client()
        orderbook = clob_client.get_order_book(hedge_token_id)

        hedge_price = None
        best_ask = None

        if orderbook and isinstance(orderbook, dict):
            asks = orderbook.get("asks", [])
            if asks and len(asks) > 0:
                best_ask = float(asks[0].get("price", 0))
                if best_ask > 0:
                    hedge_price = best_ask
                    log(
                        f"   📊 [{symbol}] Hedge using TAKER pricing: ${hedge_price:.2f} (best ask)"
                    )

        # FALLBACK: If orderbook unavailable, calculate target price
        if hedge_price is None:
            target_hedge_price = round(0.99 - entry_price, 2)
            hedge_price = max(0.01, min(0.99, target_hedge_price))
            log(
                f"   ⚠️  [{symbol}] Orderbook unavailable for hedge, using calculated price: ${hedge_price:.2f}"
            )

        # PROFIT SAFETY CHECK: Verify combined price <= $0.98 for guaranteed profit
        # Use $0.98 threshold (2 cent buffer) to ensure profitability after fees
        final_combined = entry_price + hedge_price
        if final_combined > 0.98:
            log(
                f"   ❌ [{symbol}] REJECTED: Combined ${final_combined:.2f} > $0.98 (entry ${entry_price:.2f} + hedge ${hedge_price:.2f})"
            )
            log(
                f"   💡 [{symbol}] Need hedge <= ${0.98 - entry_price:.2f} for profit, market wants ${hedge_price:.2f}"
            )
            return None, None

        log(
            f"   ✅ [{symbol}] Hedge pricing approved: ${hedge_price:.2f} (combined ${final_combined:.2f} <= $0.98)"
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
            f"   🔄 [{symbol}] Placing ATOMIC entry+hedge: {entry_side} {entry_size:.1f} @ ${entry_price:.2f} + {hedge_side} {entry_size:.1f} @ ${hedge_price:.2f} (combined ${final_combined:.2f})"
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

        # MONITOR: Poll both orders for fill status
        # If BOTH fill within timeout, save trade. Otherwise cancel BOTH.
        if entry_result.get("success") and hedge_result.get("success"):
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
                    if final_entry_filled and not final_hedge_filled:
                        # Entry filled but hedge didn't - emergency sell the entry position
                        log(
                            f"   🚨 [{symbol}] CRITICAL: Entry filled ({final_entry_filled_size:.2f}) but hedge timed out - emergency selling entry"
                        )
                        emergency_sell_position(
                            symbol=symbol,
                            token_id=entry_token_id,
                            size=final_entry_filled_size,
                            reason="hedge timeout after entry fill",
                            entry_order_id=entry_order_id,
                        )
                        # Cancel unfilled hedge
                        try:
                            cancel_order(hedge_order_id)
                            log(f"   ✅ [{symbol}] Hedge order cancelled")
                        except Exception as e:
                            log_error(f"[{symbol}] Error cancelling hedge: {e}")

                    elif final_hedge_filled and not final_entry_filled:
                        # Hedge filled but entry didn't - emergency sell the hedge position
                        log(
                            f"   🚨 [{symbol}] CRITICAL: Hedge filled ({final_hedge_filled_size:.2f}) but entry timed out - emergency selling hedge"
                        )
                        emergency_sell_position(
                            symbol=symbol,
                            token_id=hedge_token_id,
                            size=final_hedge_filled_size,
                            reason="entry timeout after hedge fill",
                            entry_order_id=hedge_order_id,
                        )
                        # Cancel unfilled entry
                        try:
                            cancel_order(entry_order_id)
                            log(f"   ✅ [{symbol}] Entry order cancelled")
                        except Exception as e:
                            log_error(f"[{symbol}] Error cancelling entry: {e}")

                    else:
                        # Neither filled or both partially filled - just cancel both
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
