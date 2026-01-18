"""
Pre-Settlement Exit Strategy for Both Hedged and Unhedged Positions

Sells the losing side before window close to extract additional value:
- **Hedged positions**: ALWAYS sell losing side (we know one side loses)
- **Unhedged positions**: Sell losing side if winning side has high confidence

Strategy:
1. Monitor positions approaching window close (45s before settlement)
2. Identify which side is winning based on current price
3. Sell losing side using progressive pricing
4. Ride winning side to full $1.00 settlement payout

Example (Hedged):
  Entry: UP @ $0.48 (6.0 shares, $2.88 cost)
  Hedge: DOWN @ $0.52 (6.0 shares, $3.12 cost)
  Total: $6.00 perfectly hedged

  45s before close: UP @ $0.75, DOWN @ $0.25
  Action: Sell losing DOWN @ $0.25 → Recover $1.50
  Settlement: UP wins → $6.00 payout
  Net: $6.00 + $1.50 - $6.00 = +$1.50 profit! 🚀

Example (Unhedged):
  Timeout: UP @ $0.48 (winning), DOWN sold immediately
  45s before close: UP @ $0.75 (75% confidence - very safe)
  Settlement: UP wins → Full $1.00 payout
"""

import time
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, Tuple
from src.data.db_connection import db_connection
from src.utils.logger import log, log_error
from src.utils.websocket_manager import ws_manager
from src.config.settings import (
    ENABLE_PRE_SETTLEMENT_EXIT,
    PRE_SETTLEMENT_EXIT_SECONDS,
    PRE_SETTLEMENT_MIN_CONFIDENCE,
    PRE_SETTLEMENT_CHECK_INTERVAL,
    PRE_SETTLEMENT_PRICE_MAX_AGE,
    PRE_SETTLEMENT_CONFIDENCE_SCHEDULE,
)
from src.trading.execution import emergency_sell_position

# Track already processed trades to avoid duplicate exits
_processed_trade_ids = set()


def _get_token_price_from_api(token_id: str, symbol: str) -> Optional[float]:
    """
    Fetch current midpoint price from CLOB API as fallback when WebSocket unavailable.
    Uses the CLOB client's getMidpoint method.

    Args:
        token_id: Token ID to fetch price for
        symbol: Trading symbol (for logging)

    Returns:
        Midpoint price as float, or None if request fails
    """
    try:
        from src.trading.orders import client

        # Call client.get_midpoint(token_id) which returns {"mid": "0.75"}
        response = client.get_midpoint(token_id)

        if response and "mid" in response:
            mid_price = float(response["mid"])
            if mid_price > 0:
                return mid_price

        return None

    except Exception as e:
        log_error(
            f"[{symbol}] Error fetching midpoint from CLOB for token {token_id[:16]}...: {e}"
        )
        return None

    except Exception as e:
        log_error(
            f"[{symbol}] Error fetching price from API for token {token_id[:16]}...: {e}"
        )
        return None


def _get_losing_side_token_id(symbol: str, winning_side: str, slug: str):
    """
    Get the token_id for the losing (opposite) side of an unhedged position.

    Args:
        symbol: Trading symbol (e.g. "BTC")
        winning_side: The side we're holding ("UP" or "DOWN")
        slug: Market slug to look up tokens

    Returns:
        token_id of losing side, or None if not found
    """
    try:
        from src.data.market_data import get_token_ids

        up_token_id, down_token_id = get_token_ids(symbol)
        if not up_token_id or not down_token_id:
            log_error(f"[{symbol}] Could not find token IDs for market {slug}")
            return None

        # Return opposite side's token
        if winning_side == "UP":
            return down_token_id
        else:
            return up_token_id

    except Exception as e:
        log_error(f"[{symbol}] Error getting losing side token_id: {e}")
        return None


def _check_position_safety(
    symbol: str,
    winning_side: str,
    confidence: float,
    current_price: float,
    entry_price: float,
    min_confidence: float = None,
) -> tuple[bool, str]:
    """
    Check if it's safe to sell losing side by verifying winning side is secure.

    Args:
        symbol: Trading symbol
        winning_side: Side we're holding ("UP" or "DOWN")
        confidence: Current confidence level for this market
        current_price: Current price of winning side
        entry_price: Entry price of winning side
        min_confidence: Minimum required confidence (defaults to PRE_SETTLEMENT_MIN_CONFIDENCE)

    Returns:
        (is_safe, reason) tuple
    """
    if min_confidence is None:
        min_confidence = PRE_SETTLEMENT_MIN_CONFIDENCE

    # Check 1: Confidence threshold
    if confidence < min_confidence:
        return (
            False,
            f"confidence too low ({confidence:.1%} < {min_confidence:.1%})",
        )

    # Check 2: Price should still be favorable
    # For UP side: current price should be significantly above entry
    # For DOWN side: current price should be significantly below entry
    price_change = current_price - entry_price

    if winning_side == "UP":
        # UP position should have maintained or increased value
        if price_change < -0.05:  # More than 5¢ worse
            return False, f"UP price dropped ${abs(price_change):.2f} from entry"
    else:  # DOWN
        # DOWN position should have maintained or increased value (price went down)
        if price_change > 0.05:  # More than 5¢ worse
            return False, f"DOWN price rose ${price_change:.2f} from entry"

    # All checks passed
    return True, f"Safe: {confidence:.1%} confidence, price favorable"


def check_pre_settlement_exits():
    """
    Background task to check for pre-settlement exit opportunities with progressive timing.

    Progressive Strategy:
    - T-180s (3min): Exit if 80%+ confidence (markets still active)
    - T-120s (2min): Exit if 70%+ confidence (markets closing soon)
    - T-60s (1min): Exit if 60%+ confidence (markets likely closed)
    - T-45s: Exit regardless, use cached prices (last resort)

    Uses price caching since markets close ~2-3 minutes before settlement.
    """
    if not ENABLE_PRE_SETTLEMENT_EXIT:
        return

    try:
        now = datetime.now(tz=ZoneInfo("UTC"))

        # Check positions in ALL progressive exit windows
        # Find the earliest window (e.g., 180s) and latest (e.g., 45s)
        earliest_window = max(PRE_SETTLEMENT_CONFIDENCE_SCHEDULE.keys())
        latest_window = min(PRE_SETTLEMENT_CONFIDENCE_SCHEDULE.keys())

        exit_window_start = now + timedelta(seconds=earliest_window)
        exit_window_end = now + timedelta(
            seconds=latest_window - 10
        )  # Buffer to avoid missing

        with db_connection() as conn:
            c = conn.cursor()

            # Find both hedged AND unhedged positions within ANY exit window
            # For unhedged: sell losing side if winning side has sufficient confidence (progressive)
            # For hedged: ALWAYS sell losing side (we know one side will lose)
            c.execute(
                """
                SELECT id, symbol, slug, token_id, side, entry_price, size, 
                       window_end, bayesian_confidence, additive_confidence, is_hedged,
                       hedge_order_price
                FROM trades
                WHERE settled = 0 
                  AND exited_early = 0
                  AND datetime(window_end) BETWEEN datetime(?) AND datetime(?)
                ORDER BY window_end ASC
                """,
                (exit_window_end.isoformat(), exit_window_start.isoformat()),
            )

            positions = c.fetchall()

            if not positions:
                return

            # Log summary of positions being checked
            log(
                f"🔍 Pre-settlement check: {len(positions)} position(s) in exit window ({int(earliest_window)}s-{int(latest_window)}s before close)"
            )

            for pos in positions:
                (
                    trade_id,
                    symbol,
                    slug,
                    winning_token_id,
                    winning_side,
                    entry_price,
                    size,
                    window_end,
                    bayesian_conf,
                    additive_conf,
                    is_hedged,
                    hedge_price,
                ) = pos

                # Skip if already processed
                if trade_id in _processed_trade_ids:
                    continue

                # Calculate time remaining until window close
                window_end_dt = (
                    datetime.fromisoformat(window_end)
                    if isinstance(window_end, str)
                    else window_end
                )
                seconds_left = (window_end_dt - now).total_seconds()

                # Determine required confidence based on time remaining
                required_confidence = None
                for time_threshold, conf_threshold in sorted(
                    PRE_SETTLEMENT_CONFIDENCE_SCHEDULE.items(), reverse=True
                ):
                    if seconds_left <= time_threshold:
                        required_confidence = conf_threshold
                        break

                # If we haven't reached any exit window yet, skip
                if required_confidence is None:
                    continue

                # Use bayesian confidence (more reliable for near-settlement predictions)
                confidence = bayesian_conf if bayesian_conf else additive_conf

                # IMPORTANT: Recalculate confidence based on CURRENT market conditions
                # The stored confidence is from trade entry (10-14 min ago)
                # Market conditions change - we need fresh confidence for exit decision
                try:
                    from src.trading.strategy import calculate_confidence
                    from src.trading.orders import get_clob_client

                    client = get_clob_client()
                    fresh_confidence, bias, p_up, _, _, _, _ = calculate_confidence(
                        symbol, winning_token_id, client
                    )

                    if fresh_confidence and fresh_confidence > 0:
                        # Use fresh confidence if available
                        old_conf = confidence
                        confidence = fresh_confidence
                        log(
                            f"   🔄 [{symbol}] #{trade_id} Updated confidence: {old_conf:.1%} (entry) → {confidence:.1%} (current)"
                        )
                except Exception as e:
                    # Fall back to stored confidence if calculation fails
                    log(
                        f"   ⚠️  [{symbol}] #{trade_id} Could not recalculate confidence: {e}, using stored {confidence:.1%}"
                    )

                # Log position details
                hedge_label = "🛡️ HEDGED" if is_hedged else "⚠️  UNHEDGED"
                conf_label = f"{confidence:.1%}" if confidence else "N/A"
                log(
                    f"   📊 [{symbol}] #{trade_id} {hedge_label} | {winning_side} @ ${entry_price:.2f} | Confidence: {conf_label} | T-{int(seconds_left)}s"
                )

                # For hedged positions, we don't need confidence check
                # We KNOW one side will lose, so always try to exit the losing side
                if not is_hedged:
                    if not confidence:
                        log(
                            f"   ⚠️  [{symbol}] #{trade_id} No confidence data, skipping pre-settlement exit"
                        )
                        continue

                    # Get current price of winning side (with fallback to cached, then API)
                    winning_price, price_age = ws_manager.get_price_with_age(
                        winning_token_id, max_age_seconds=PRE_SETTLEMENT_PRICE_MAX_AGE
                    )

                    # If WebSocket/cache unavailable, try API fallback
                    if not winning_price or winning_price < 0.01:
                        log(
                            f"   🔄 [{symbol}] #{trade_id} No cached price, fetching from API..."
                        )
                        winning_price = _get_token_price_from_api(
                            winning_token_id, symbol
                        )
                        if winning_price:
                            price_age = 0  # Fresh from API
                            log(
                                f"   ✅ [{symbol}] #{trade_id} Got fresh price from API: ${winning_price:.2f}"
                            )
                        else:
                            log(
                                f"   ⚠️  [{symbol}] #{trade_id} Could not get price from cache or API"
                            )
                            continue

                    # Log if using cached price
                    if price_age and price_age > 60:
                        log(
                            f"   💾 [{symbol}] #{trade_id} Using cached price (age: {price_age:.0f}s)"
                        )

                    # Check if it's safe to exit losing side (UNHEDGED only)
                    is_safe, reason = _check_position_safety(
                        symbol,
                        winning_side,
                        confidence,
                        winning_price,
                        entry_price,
                        min_confidence=required_confidence,
                    )

                    if not is_safe:
                        log(
                            f"   🛡️  [{symbol}] #{trade_id} Not safe to exit losing side: {reason}"
                        )
                        continue

                    hedge_status = "⚠️  UNHEDGED"
                else:
                    # HEDGED position - always safe to exit losing side
                    # Determine which side is winning based on current prices
                    winning_price, price_age = ws_manager.get_price_with_age(
                        winning_token_id, max_age_seconds=PRE_SETTLEMENT_PRICE_MAX_AGE
                    )

                    # If WebSocket/cache unavailable, try API fallback
                    if not winning_price or winning_price < 0.01:
                        log(
                            f"   🔄 [{symbol}] #{trade_id} No cached price, fetching from API..."
                        )
                        winning_price = _get_token_price_from_api(
                            winning_token_id, symbol
                        )
                        if winning_price:
                            price_age = 0  # Fresh from API
                            log(
                                f"   ✅ [{symbol}] #{trade_id} Got fresh price from API: ${winning_price:.2f}"
                            )
                        else:
                            age_str = f"{price_age:.0f}s" if price_age else "N/A"
                            log(
                                f"   ⚠️  [{symbol}] #{trade_id} Could not get price from cache or API (age: {age_str})"
                            )
                            continue

                    # Log if using cached price
                    if price_age and price_age > 60:
                        log(
                            f"   💾 [{symbol}] #{trade_id} Using cached price (age: {price_age:.0f}s)"
                        )

                    # For hedged positions, check which side is actually winning
                    # If entry side price > 0.50, entry side is winning
                    # If entry side price < 0.50, hedge side is winning
                    if winning_price > 0.50:
                        # Entry side winning, winning_side and winning_token_id are correct
                        reason = f"Hedged position, {winning_side} @ ${winning_price:.2f} > $0.50"
                        log(
                            f"      ✅ [{symbol}] Entry side {winning_side} winning @ ${winning_price:.2f}"
                        )
                    else:
                        # Hedge side winning! Need to swap which side we consider "winning"
                        # Get hedge token_id
                        hedge_token_id = _get_losing_side_token_id(
                            symbol, winning_side, slug
                        )
                        if not hedge_token_id:
                            continue

                        hedge_current_price, hedge_price_age = (
                            ws_manager.get_price_with_age(
                                hedge_token_id,
                                max_age_seconds=PRE_SETTLEMENT_PRICE_MAX_AGE,
                            )
                        )

                        # If WebSocket/cache unavailable, try API fallback
                        if not hedge_current_price or hedge_current_price < 0.01:
                            log(
                                f"   🔄 [{symbol}] #{trade_id} No cached hedge price, fetching from API..."
                            )
                            hedge_current_price = _get_token_price_from_api(
                                hedge_token_id, symbol
                            )
                            if hedge_current_price:
                                hedge_price_age = 0  # Fresh from API
                                log(
                                    f"   ✅ [{symbol}] #{trade_id} Got fresh hedge price from API: ${hedge_current_price:.2f}"
                                )
                            else:
                                log(
                                    f"   ⚠️  [{symbol}] #{trade_id} Could not get hedge price from cache or API"
                                )
                                continue

                        # Swap: hedge is now winning side
                        winning_token_id = hedge_token_id
                        winning_side = "DOWN" if winning_side == "UP" else "UP"
                        winning_price = hedge_current_price
                        entry_price = hedge_price if hedge_price else 0.50

                        # IMPORTANT: Since hedge side is winning, flip the confidence
                        # If we bet UP with 60% confidence, DOWN has 40% confidence
                        if confidence:
                            confidence = 1.0 - confidence

                        reason = f"Hedged position, {winning_side} @ ${winning_price:.2f} > $0.50"
                        log(
                            f"      ✅ [{symbol}] Hedge side {winning_side} winning @ ${hedge_current_price:.2f}"
                        )

                    # For hedged positions, still check confidence before exiting losing side
                    # Even though it's hedged, we need high confidence to avoid selling the wrong side
                    if not confidence:
                        log(
                            f"   ⚠️  [{symbol}] #{trade_id} No confidence data, skipping pre-settlement exit"
                        )
                        continue

                    # Check if confidence is sufficient for this time window
                    is_safe, reason = _check_position_safety(
                        symbol,
                        winning_side,
                        confidence,
                        winning_price,
                        entry_price,
                        min_confidence=required_confidence,
                    )

                    if not is_safe:
                        log(
                            f"   🛡️  [{symbol}] #{trade_id} Confidence too low for hedged exit: {reason} (need {required_confidence:.0%}, have {confidence:.1%})"
                        )
                        continue

                    hedge_status = "🛡️ HEDGED"

                # Get losing side token_id
                losing_token_id = _get_losing_side_token_id(symbol, winning_side, slug)
                if not losing_token_id:
                    continue

                # Get current price of losing side (with fallback to cached)
                losing_price, losing_price_age = ws_manager.get_price_with_age(
                    losing_token_id, max_age_seconds=PRE_SETTLEMENT_PRICE_MAX_AGE
                )

                # If WebSocket/cache unavailable, try API fallback
                if not losing_price or losing_price < 0.01:
                    log(
                        f"   🔄 [{symbol}] #{trade_id} No cached losing side price, fetching from API..."
                    )
                    losing_price = _get_token_price_from_api(losing_token_id, symbol)
                    if losing_price:
                        losing_price_age = 0  # Fresh from API
                        log(
                            f"   ✅ [{symbol}] #{trade_id} Got fresh losing side price from API: ${losing_price:.2f}"
                        )
                    else:
                        log(
                            f"   ⚠️  [{symbol}] #{trade_id} Could not get losing side price from cache or API"
                        )
                        continue
                elif not losing_price:
                    log(
                        f"   ⚠️  [{symbol}] #{trade_id} Could not get price for losing side"
                    )
                    continue

                losing_side = "DOWN" if winning_side == "UP" else "UP"

                # Check if we actually have shares on losing side to sell FIRST
                # (We might have already sold it during timeout handling)
                from src.trading.orders import get_balance_allowance

                balance_info = get_balance_allowance(losing_token_id)
                losing_balance = balance_info["balance"] if balance_info else 0.0

                if not losing_balance or losing_balance < 0.01:
                    log(
                        f"   ⏭️  [{symbol}] #{trade_id} No {losing_side} shares to sell (already liquidated)"
                    )
                    continue

                # Log combined price for hedged positions
                if is_hedged:
                    combined_price = winning_price + losing_price
                    log(
                        f"      📊 [{symbol}] Combined: ${combined_price:.2f} | {winning_side} @ ${winning_price:.2f} | {losing_side} @ ${losing_price:.2f}"
                    )

                # Calculate time remaining
                window_end_dt = (
                    datetime.fromisoformat(window_end)
                    if isinstance(window_end, str)
                    else window_end
                )
                seconds_left = (window_end_dt - now).total_seconds()

                # Log with progressive timing info
                timing_info = f"T-{int(seconds_left)}s"
                confidence_info = (
                    f"req: {required_confidence:.0%}" if not is_hedged else "HEDGED"
                )
                log(
                    f"   💰 [{symbol}] #{trade_id} PRE-SETTLEMENT EXIT OPPORTUNITY ({timing_info}, {confidence_info})"
                )

                # Initialize profit tracking variables
                expected_profit = 0.0
                expected_roi = 0.0

                if is_hedged:
                    # Validate and log combined hedge price
                    # entry_price = price of entry side, hedge_price = price of hedge side
                    if not hedge_price or hedge_price < 0.01:
                        log(
                            f"   ⚠️  [{symbol}] #{trade_id} Missing hedge price data, skipping validation"
                        )
                        combined_entry = entry_price  # Fallback
                    else:
                        combined_entry = entry_price + hedge_price

                    # Calculate expected profit from losing side exit
                    losing_side_recovery = losing_balance * losing_price
                    total_cost = size * combined_entry
                    winning_payout = size * 1.00
                    expected_profit = winning_payout + losing_side_recovery - total_cost
                    expected_roi = (
                        (expected_profit / total_cost * 100) if total_cost > 0 else 0
                    )

                    log(
                        f"   📊 [{symbol}] {hedge_status} | Combined entry: ${combined_entry:.2f}/pair | "
                        f"Winning {winning_side} @ ${winning_price:.2f} | Losing {losing_side} @ ${losing_price:.2f} | {int(seconds_left)}s left"
                    )
                    log(
                        f"   💵 [{symbol}] Expected: ${total_cost:.2f} cost + ${losing_side_recovery:.2f} recovery + ${winning_payout:.2f} payout = ${expected_profit:+.2f} profit ({expected_roi:+.1f}%)"
                    )

                    # Warn if combined entry was > $0.99 (shouldn't happen, but safety check)
                    if combined_entry > 0.99:
                        log(
                            f"   ⚠️  [{symbol}] #{trade_id} WARNING: Combined entry ${combined_entry:.2f} > $0.99 (not a good hedge)"
                        )
                else:
                    log(
                        f"   📊 [{symbol}] {hedge_status} | Winning {winning_side} @ ${winning_price:.2f} ({confidence:.1%} conf) | "
                        f"Losing {losing_side} @ ${losing_price:.2f} | {int(seconds_left)}s left"
                    )

                log(
                    f"   🎯 [{symbol}] {reason} - Selling losing {losing_side} side for profit recovery"
                )

                # Execute progressive pricing exit
                exit_reason = (
                    f"pre-settlement exit (hedged, {int(seconds_left)}s left)"
                    if is_hedged
                    else f"pre-settlement exit ({confidence:.1%} confidence, {int(seconds_left)}s left)"
                )

                success = emergency_sell_position(
                    symbol=symbol,
                    token_id=losing_token_id,
                    size=losing_balance,
                    reason=exit_reason,
                    entry_price=losing_price,  # Use current price as reference
                )

                if success:
                    # Mark as processed to avoid duplicate exit attempts
                    _processed_trade_ids.add(trade_id)

                    if is_hedged:
                        log(
                            f"   ✅ [{symbol}] #{trade_id} Pre-settlement exit complete | "
                            f"Expected profit: ${expected_profit:+.2f} ({expected_roi:+.1f}%) after settlement"
                        )
                    else:
                        log(
                            f"   ✅ [{symbol}] #{trade_id} Pre-settlement exit complete | "
                            f"Riding winning {winning_side} to settlement"
                        )
                else:
                    log_error(
                        f"[{symbol}] #{trade_id} Pre-settlement exit failed | "
                        f"Will ride both sides to settlement"
                    )

    except Exception as e:
        log_error(f"Error in pre-settlement exit check: {e}")


def _pre_settlement_task():
    """Background thread that periodically checks for pre-settlement exit opportunities"""
    log(
        f"🎯 [Startup] Pre-settlement exit task started (check every {PRE_SETTLEMENT_CHECK_INTERVAL}s)"
    )
    log(
        f"   💡 Strategy: Progressive exit with cached prices (markets close ~2-3 min before settlement)"
    )
    log(f"   ⏰ Exit windows: T-180s (80%), T-120s (70%), T-60s (60%), T-45s (50%)")
    log(f"   ✅ Pre-settlement exit monitoring enabled (hedged + unhedged positions)")

    while True:
        try:
            check_pre_settlement_exits()
        except Exception as e:
            log_error(f"Pre-settlement task error: {e}")

        time.sleep(PRE_SETTLEMENT_CHECK_INTERVAL)


def start_pre_settlement_monitor():
    """Launch pre-settlement exit monitoring in a separate thread"""
    if not ENABLE_PRE_SETTLEMENT_EXIT:
        log("   ⏭️  Pre-settlement exit monitoring disabled")
        return

    thread = threading.Thread(
        target=_pre_settlement_task, name="PreSettlementExit", daemon=True
    )
    thread.start()
    log(f"   ✅ Pre-settlement exit monitoring enabled (hedged + unhedged positions)")
