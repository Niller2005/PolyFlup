"""Trade execution utilities"""

import time
from typing import Optional, Dict, Any
from src.utils.logger import log, log_error, send_discord
from src.data.database import save_trade
from src.trading.orders import (
    place_order,
    get_order,
    get_balance_allowance,
    get_clob_client,
)
from src.data.market_data import get_token_ids


def place_hedge_order(
    trade_id: int,
    symbol: str,
    entry_side: str,
    entry_price: float,
    entry_size: float,
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
        entry_size: Size of entry trade
        cursor: Optional database cursor (if called within existing transaction)

    Returns:
        Order ID if successful, None otherwise
    """
    try:
        from src.config.settings import ENABLE_CTF_MERGE
        from src.data.db_connection import db_connection

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
        if ENABLE_CTF_MERGE:
            # Target $1.00 combined (merge returns full $1.00)
            target_combined = 1.00
        else:
            # Target $0.99 combined (guarantee profit on settlement)
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

        # Place hedge order
        result = place_order(hedge_token_id, hedge_price, entry_size)

        if not result["success"]:
            log(f"   ❌ [{symbol}] Hedge order failed: {result.get('error')}")
            return None

        order_id = result["order_id"]
        merge_status = " [MERGE]" if ENABLE_CTF_MERGE else ""
        log(
            f"   🛡️  [{symbol}] Hedge order placed{merge_status}: {hedge_side} {entry_size:.1f} @ ${hedge_price:.2f} (ID: {order_id[:10]}) | Combined: ${entry_price:.2f} + ${hedge_price:.2f} = ${(entry_price + hedge_price):.2f}"
        )

        # Check remaining capital after hedge - warn if insufficient for scale-ins
        try:
            from src.trading.orders import get_balance_allowance

            bal_info = get_balance_allowance()
            remaining_usdc = float(bal_info.get("balance", 0)) if bal_info else 0.0

            # Scale-in typically needs ~$6-8 (size * price for similar position)
            scale_in_reserve = entry_size * entry_price

            if remaining_usdc < scale_in_reserve:
                log(
                    f"   ⚠️  [{symbol}] Low capital after hedge: ${remaining_usdc:.2f} remaining (scale-in needs ~${scale_in_reserve:.2f})"
                )
                log(
                    f"   💡 [{symbol}] Consider disabling scale-ins or reducing BET_SIZE to preserve capital for hedges"
                )
        except Exception as cap_check_err:
            pass  # Don't fail hedge placement if balance check errors

        # Store hedge price in database
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
                if hedge_filled_size >= entry_size - 0.0001:
                    log(
                        f"   ✅ [{symbol}] Hedge immediately verified: {hedge_filled_size:.2f}/{entry_size:.2f} shares filled"
                    )

                    # Mark as hedged in database
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
                        f"   ⏳ [{symbol}] Hedge partially filled: {hedge_filled_size:.2f}/{entry_size:.2f} shares (will continue monitoring)"
                    )
        except Exception as verify_err:
            log(
                f"   ⚠️  [{symbol}] Could not verify hedge fill immediately: {verify_err}"
            )

        return order_id
    except Exception as e:
        log_error(f"[{symbol}] Error placing hedge order: {e}")
        return None


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
    entry_cost = size * price
    hedge_cost = size * (1.0 - price)  # Approximate hedge cost (opposite side)
    total_cost_needed = entry_cost + hedge_cost

    bal_info = get_balance_allowance()
    if bal_info:
        usdc_balance = bal_info.get("balance", 0)
        if usdc_balance < total_cost_needed:
            log(
                f"[{symbol}] ❌ Insufficient funds for entry + hedge (Need ${total_cost_needed:.2f} [entry ${entry_cost:.2f} + hedge ${hedge_cost:.2f}], Have ${usdc_balance:.2f})"
            )
            return None

    # Place order
    result = place_order(token_id, price, size)

    if not result["success"]:
        log(f"[{symbol}] ❌ Order failed: {result.get('error')}")
        return None

    actual_size = size
    actual_price = price
    actual_status = result["status"]
    order_id = result["order_id"]

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
