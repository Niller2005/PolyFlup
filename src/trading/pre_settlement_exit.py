"""
Pre-Settlement Exit Strategy for Unhedged Positions

When holding an unhedged position (one side winning), this module checks
if we can safely exit the losing side before window close to extract
additional value, while riding the winning side to full settlement.

Strategy:
1. Monitor unhedged positions approaching window close
2. Check if winning side has high confidence (e.g. 70%+)
3. If safe, sell losing side using progressive pricing
4. Ride winning side to $1.00 settlement payout

Example:
  Timeout: UP @ $0.48 (winning), DOWN @ $0.51 (partial, sold immediately)
  45s before close: UP @ $0.75 (75% confidence - very safe)
  Action: Sell losing DOWN side for ~$0.25 recovery
  Settlement: UP wins → Full $1.00 payout on UP position
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

            # Find unhedged positions within exit window
            c.execute(
                """
                SELECT id, symbol, slug, token_id, side, entry_price, size, 
                       window_end, bayesian_confidence, additive_confidence
                FROM trades
                WHERE settled = 0 
                  AND exited_early = 0
                  AND is_hedged = 0
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
                ) = pos

                # Skip if already processed
                if trade_id in _processed_trade_ids:
                    continue

                # Mark as processed to avoid duplicate attempts
                _processed_trade_ids.add(trade_id)

                # Use bayesian confidence (more reliable for near-settlement predictions)
                confidence = bayesian_conf if bayesian_conf else additive_conf
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

                # Check if it's safe to exit losing side
                is_safe, reason = _check_position_safety(
                    symbol, winning_side, confidence, winning_price, entry_price
                )

                if not is_safe:
                    log(
                        f"   🛡️  [{symbol}] #{trade_id} Not safe to exit losing side: {reason}"
                    )
                    continue

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

                # Calculate time remaining
                window_end_dt = (
                    datetime.fromisoformat(window_end)
                    if isinstance(window_end, str)
                    else window_end
                )
                seconds_left = (window_end_dt - now).total_seconds()

                log(f"   💰 [{symbol}] #{trade_id} PRE-SETTLEMENT EXIT OPPORTUNITY")
                log(
                    f"   📊 [{symbol}] Winning {winning_side} @ ${winning_price:.2f} ({confidence:.1%} conf) | "
                    f"Losing {losing_side} @ ${losing_price:.2f} | {int(seconds_left)}s left"
                )
                log(
                    f"   🎯 [{symbol}] {reason} - Selling losing {losing_side} side for profit recovery"
                )

                # Check if we actually have shares on losing side to sell
                # (We might have already sold it during timeout handling)
                from src.trading.orders import get_balance_allowance

                balance_info = get_balance_allowance(losing_token_id)
                losing_balance = balance_info["balance"] if balance_info else 0.0

                if not losing_balance or losing_balance < 0.01:
                    log(
                        f"   ✅ [{symbol}] #{trade_id} No {losing_side} shares to sell (already liquidated)"
                    )
                    continue

                # Execute progressive pricing exit
                success = emergency_sell_position(
                    symbol=symbol,
                    token_id=losing_token_id,
                    size=losing_balance,
                    reason=f"pre-settlement exit ({confidence:.1%} confidence, {int(seconds_left)}s left)",
                    entry_price=losing_price,  # Use current price as reference
                )

                if success:
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
        f"exit {PRE_SETTLEMENT_EXIT_SECONDS}s before close, min confidence {PRE_SETTLEMENT_MIN_CONFIDENCE:.0%})"
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
    log(
        f"   ✅ Pre-settlement exit monitoring enabled (exit {PRE_SETTLEMENT_EXIT_SECONDS}s before close)"
    )
