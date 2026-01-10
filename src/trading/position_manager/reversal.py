"""Reversal logic for switching to opposite side positions"""

from src.config.settings import (
    STOP_LOSS_PRICE,
    ENABLE_REVERSAL,
)
from src.utils.logger import log, log_error
from src.data.market_data import (
    get_window_times,
    get_token_ids,
    get_window_start_price,
    get_current_slug,
)
from src.trading import (
    execute_trade,
    calculate_confidence,
    _calculate_bet_size,
    _determine_trade_side,
    get_clob_client,
)


def _trigger_price_based_reversal(
    symbol, original_trade_id, original_side, c, conn
) -> bool:
    """Trigger a reversal trade for the opposite side"""
    try:
        up_id, down_id = get_token_ids(symbol)
        if not up_id or not down_id:
            return False

        client = get_clob_client()
        confidence, bias, p_up, best_bid, best_ask, _ = calculate_confidence(
            symbol, up_id, client
        )

        # Opposite side
        rev_side = "DOWN" if original_side == "UP" else "UP"
        rev_token_id = down_id if original_side == "UP" else up_id

        # NEW: Check if we already have a trade for THIS SIDE in this window
        window_start, window_end = get_window_times(symbol)
        from src.data.database import has_side_for_window

        if has_side_for_window(symbol, window_start.isoformat(), rev_side):
            log(
                f"   ℹ️  [{symbol}] Already have an open {rev_side} position for this window. Linking as hedge."
            )
            return True

        if original_side == "UP":
            # Buying DOWN tokens: Join the bid (1 - UP best ask)
            rev_price = 1.0 - float(best_ask) if best_ask is not None else (1.0 - p_up)
        else:
            # Buying UP tokens: Join the bid
            rev_price = float(best_bid) if best_bid is not None else p_up

        # Clamp and round
        rev_price = max(0.01, min(0.99, round(rev_price, 2)))

        # Prepare parameters similar to bot.py
        actual_side, sizing_confidence = _determine_trade_side(bias, confidence)

        # If strategy strongly agrees with the reversal, use that sizing.
        # Otherwise use a default sizing for the price-trigger reversal.
        if actual_side != rev_side:
            sizing_confidence = 0.40  # Default for price-triggered reversal

        from src.utils.web3_utils import get_balance
        from src.config.settings import PROXY_PK, FUNDER_PROXY
        from eth_account import Account

        addr = (
            FUNDER_PROXY
            if (FUNDER_PROXY and FUNDER_PROXY.startswith("0x"))
            else Account.from_key(PROXY_PK).address
        )
        balance = get_balance(addr)

        size, bet_usd = _calculate_bet_size(balance, rev_price, sizing_confidence)

        # Pre-flight balance check
        if balance < bet_usd:
            log(
                f"   ⏳ [{symbol}] Reversal skipped: Insufficient funds (Need ${bet_usd:.2f}, Have ${balance:.2f})"
            )
            return False

        window_start, window_end = get_window_times(symbol)

        trade_params = {
            "symbol": symbol,
            "token_id": rev_token_id,
            "side": rev_side,
            "price": rev_price,
            "size": size,
            "bet_usd": bet_usd,
            "confidence": sizing_confidence,
            "core_summary": f"Price-Based Reversal (Trigger: ${STOP_LOSS_PRICE:.2f})",
            "p_up": p_up,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "imbalance": 0.5,
            "funding_bias": 0.0,
            "target_price": float(get_window_start_price(symbol)),
            "window_start": window_start,
            "window_end": window_end,
            "slug": get_current_slug(symbol),
        }

        rev_id = execute_trade(trade_params, is_reversal=True, cursor=c)
        if rev_id:
            log(f"⚔️ Reversal trade #{rev_id} opened for {symbol} {rev_side}")
            return True
        return False

    except Exception as e:
        log_error(f"Error triggering price-based reversal for {symbol}: {e}")
        return False


def check_and_trigger_reversal(
    symbol,
    trade_id,
    side,
    current_price,
    entry_price,
    c,
    conn,
    now,
    reversal_triggered=False,
):
    """
    Check if reversal should be triggered based on price.

    Args:
        symbol: Trading symbol
        trade_id: Original trade ID
        side: Original trade side
        current_price: Current midpoint price
        entry_price: Entry price
        c: Database cursor
        conn: Database connection
        now: Current datetime
        reversal_triggered: Whether reversal was already triggered

    Returns:
        True if reversal was triggered in this call, False otherwise
    """
    if reversal_triggered:
        return False

    # Dynamic trigger based on entry price with minimum headroom
    dynamic_trigger = min(STOP_LOSS_PRICE, entry_price - 0.10)

    # If price is above trigger, no action needed
    if current_price > dynamic_trigger:
        return False

    # Trigger reversal
    if ENABLE_REVERSAL:
        log(
            f"🔄 [{symbol}] #{trade_id} {side} midpoint ${current_price:.2f} <= ${dynamic_trigger:.2f} trigger. INITIATING REVERSAL."
        )
        if _trigger_price_based_reversal(symbol, trade_id, side, c, conn):
            # Mark as reversal triggered
            c.execute(
                "UPDATE trades SET reversal_triggered = 1, reversal_triggered_at = ? WHERE id = ?",
                (now.isoformat(), trade_id),
            )
            return True
    else:
        # Reversals disabled, mark as triggered anyway to allow SL path
        c.execute(
            "UPDATE trades SET reversal_triggered = 1, reversal_triggered_at = ? WHERE id = ?",
            (now.isoformat(), trade_id),
        )
        return True

    return False
