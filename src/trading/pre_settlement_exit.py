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
from src.data.db_connection import db_connection
from src.utils.logger import log, log_error
from src.utils.websocket_manager import ws_manager
from src.config.settings import (
    ENABLE_PRE_SETTLEMENT_EXIT,
    PRE_SETTLEMENT_EXIT_SECONDS,
    PRE_SETTLEMENT_MIN_CONFIDENCE,
    PRE_SETTLEMENT_CHECK_INTERVAL,
)
from src.trading.execution import emergency_sell_position

# Track already processed trades to avoid duplicate exits
_processed_trade_ids = set()


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
) -> tuple[bool, str]:
    """
    Check if it's safe to sell losing side by verifying winning side is secure.

    Args:
        symbol: Trading symbol
        winning_side: Side we're holding ("UP" or "DOWN")
        confidence: Current confidence level for this market
        current_price: Current price of winning side
        entry_price: Entry price of winning side

    Returns:
        (is_safe, reason) tuple
    """
    # Check 1: Confidence threshold
    if confidence < PRE_SETTLEMENT_MIN_CONFIDENCE:
        return (
            False,
            f"confidence too low ({confidence:.1%} < {PRE_SETTLEMENT_MIN_CONFIDENCE:.1%})",
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
    Background task to check for pre-settlement exit opportunities.

    Runs periodically (every 5s) to find unhedged positions near window close
    that can safely exit their losing side for additional profit recovery.
    """
    if not ENABLE_PRE_SETTLEMENT_EXIT:
        return

    try:
        now = datetime.now(tz=ZoneInfo("UTC"))
        exit_window_start = now + timedelta(seconds=PRE_SETTLEMENT_EXIT_SECONDS)
        exit_window_end = now + timedelta(seconds=PRE_SETTLEMENT_EXIT_SECONDS + 10)

        with db_connection() as conn:
            c = conn.cursor()

            # Find both hedged AND unhedged positions within exit window
            # For unhedged: sell losing side if winning side has high confidence
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
                (exit_window_start.isoformat(), exit_window_end.isoformat()),
            )

            positions = c.fetchall()

            if not positions:
                return

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

                # Mark as processed to avoid duplicate attempts
                _processed_trade_ids.add(trade_id)

                # Use bayesian confidence (more reliable for near-settlement predictions)
                confidence = bayesian_conf if bayesian_conf else additive_conf

                # For hedged positions, we don't need confidence check
                # We KNOW one side will lose, so always try to exit the losing side
                if not is_hedged:
                    if not confidence:
                        log(
                            f"   ⚠️  [{symbol}] #{trade_id} No confidence data, skipping pre-settlement exit"
                        )
                        continue

                    # Get current price of winning side
                    winning_price = ws_manager.get_price(winning_token_id)
                    if not winning_price or winning_price < 0.01:
                        log(
                            f"   ⚠️  [{symbol}] #{trade_id} Could not get current price for winning side"
                        )
                        continue

                    # Check if it's safe to exit losing side (UNHEDGED only)
                    is_safe, reason = _check_position_safety(
                        symbol, winning_side, confidence, winning_price, entry_price
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
                    winning_price = ws_manager.get_price(winning_token_id)
                    if not winning_price or winning_price < 0.01:
                        log(f"   ⚠️  [{symbol}] #{trade_id} Could not get current price")
                        continue

                    # For hedged positions, check which side is actually winning
                    # If entry side price > 0.50, entry side is winning
                    # If entry side price < 0.50, hedge side is winning
                    if winning_price > 0.50:
                        # Entry side winning, winning_side and winning_token_id are correct
                        reason = f"Hedged position, {winning_side} @ ${winning_price:.2f} > $0.50"
                    else:
                        # Hedge side winning! Need to swap which side we consider "winning"
                        # Get hedge token_id
                        hedge_token_id = _get_losing_side_token_id(
                            symbol, winning_side, slug
                        )
                        if not hedge_token_id:
                            continue

                        hedge_current_price = ws_manager.get_price(hedge_token_id)
                        if not hedge_current_price or hedge_current_price < 0.01:
                            continue

                        # Swap: hedge is now winning side
                        winning_token_id = hedge_token_id
                        winning_side = "DOWN" if winning_side == "UP" else "UP"
                        winning_price = hedge_current_price
                        entry_price = hedge_price if hedge_price else 0.50
                        reason = f"Hedged position, {winning_side} @ ${winning_price:.2f} > $0.50"

                    hedge_status = "🛡️ HEDGED"

                # Get losing side token_id
                losing_token_id = _get_losing_side_token_id(symbol, winning_side, slug)
                if not losing_token_id:
                    continue

                # Get current price of losing side
                losing_price = ws_manager.get_price(losing_token_id)
                if not losing_price:
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

                # Calculate time remaining
                window_end_dt = (
                    datetime.fromisoformat(window_end)
                    if isinstance(window_end, str)
                    else window_end
                )
                seconds_left = (window_end_dt - now).total_seconds()

                log(f"   💰 [{symbol}] #{trade_id} PRE-SETTLEMENT EXIT OPPORTUNITY")

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
        f"🎯 [Startup] Pre-settlement exit task started (check every {PRE_SETTLEMENT_CHECK_INTERVAL}s, "
        f"exit {PRE_SETTLEMENT_EXIT_SECONDS}s before close)"
    )
    log(
        f"   💡 Strategy: Sell losing side of ALL positions (hedged + unhedged) for profit recovery"
    )
    log(
        f"   📊 Unhedged positions require {PRE_SETTLEMENT_MIN_CONFIDENCE:.0%} confidence | Hedged positions always exit"
    )

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
