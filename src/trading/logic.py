"""Trade logic and parameter preparation"""

from typing import Optional, Tuple, Dict, Any
from datetime import datetime
from zoneinfo import ZoneInfo
import time

# Track A/B test logs per window to limit to once per symbol
_ab_test_logged: set[tuple[str, str]] = set()  # (symbol, window_start)


def _cleanup_old_ab_test_logs():
    """Clean up old A/B test log entries (>15 minutes old)"""
    global _ab_test_logged
    now = time.time()
    cutoff = now - 900  # 15 minutes in seconds

    # Convert window timestamps to time and remove old entries
    to_remove = []
    for symbol, window_iso in _ab_test_logged:
        try:
            window_dt = datetime.fromisoformat(window_iso)
            window_ts = window_dt.timestamp()
            if window_ts < cutoff:
                to_remove.append((symbol, window_iso))
        except Exception:
            to_remove.append((symbol, window_iso))

    for entry in to_remove:
        _ab_test_logged.discard(entry)


from src.config.settings import (
    MIN_EDGE,
    BET_PERCENT,
    CONFIDENCE_SCALING_FACTOR,
    MAX_SIZE,
    MAX_SIZE_MODE,
    MAX_ENTRY_LATENESS_SEC,
    ENABLE_BFXD,
    BAYESIAN_CONFIDENCE,
    ENABLE_AB_TESTING,
)
from src.utils.logger import log
from src.data.database import has_trade_for_window, has_side_for_window
from src.data.market_data import (
    get_token_ids,
    get_current_slug,
    get_window_times,
    get_funding_bias,
    get_window_start_price,
)
from src.trading.strategy import calculate_confidence, bfxd_allows_trade
from src.data.market_data import validate_price_movement_for_trade
from src.trading.orders import get_clob_client

MIN_SIZE = 5.0


def _determine_trade_side(
    symbol: str,
    bias: str,
    confidence: float,
    raw_scores: Optional[Dict[str, Any]] = None,
    verbose: bool = True,
) -> tuple[str, float]:
    """
    Determine actual trading side and confidence for sizing.
    Enforces MIN_EDGE threshold for trend following entries.
    Uses evidence-based reversal instead of fixed contrarian threshold.
    """
    opposite = "DOWN" if bias == "UP" else "UP"

    # Evidence-based reversal: Check if opposite-side signals strongly favor reversal
    if raw_scores and bias != "NEUTRAL":
        signals_list = [
            (
                "momentum",
                raw_scores.get("momentum_score", 0.0),
                raw_scores.get("momentum_dir", "NEUTRAL"),
            ),
            (
                "pm_momentum",
                raw_scores.get("pm_mom_score", 0.0),
                raw_scores.get("pm_mom_dir", "NEUTRAL"),
            ),
            (
                "flow",
                raw_scores.get("flow_score", 0.0),
                raw_scores.get("flow_dir", "NEUTRAL"),
            ),
            (
                "divergence",
                raw_scores.get("divergence_score", 0.0),
                raw_scores.get("divergence_dir", "NEUTRAL"),
            ),
            (
                "vwm",
                raw_scores.get("vwm_score", 0.0),
                raw_scores.get("vwm_dir", "NEUTRAL"),
            ),
            (
                "adx",
                raw_scores.get("adx_score", 0.0),
                raw_scores.get("adx_dir", "NEUTRAL"),
            ),
        ]

        # Count opposite-side signals with strong scores (> 0.6)
        opposite_aligned = sum(
            1
            for _, score, direction in signals_list
            if score > 0.6 and direction == opposite
        )

        # Check score totals for strength
        up_total = raw_scores.get("up_total", 0.0)
        down_total = raw_scores.get("down_total", 0.0)

        # Evidence-based reversal criteria:
        # 1. 4+ opposite signals strongly aligned (> 0.6 score)
        # 2. Opposite score is at least 1.5x current bias score
        if opposite_aligned >= 4:
            if bias == "UP" and down_total > up_total * 1.5:
                actual_side = opposite
                sizing_confidence = min(0.40, down_total * 0.5)
                return actual_side, sizing_confidence
            elif bias == "DOWN" and up_total > down_total * 1.5:
                actual_side = opposite
                sizing_confidence = min(0.40, up_total * 0.5)
                return actual_side, sizing_confidence

    # Trend following with graduated sizing based on confidence level
    if confidence >= MIN_EDGE:
        # Full confidence: use full sizing
        actual_side = bias
        sizing_confidence = confidence
    elif confidence >= 0.15:
        # Partial confidence zone: 15% - 35%
        # Use discounted sizing to capture moderate signal strength
        actual_side = bias
        sizing_confidence = confidence * 0.7
    else:
        # Very low confidence: skip trade entirely
        actual_side = "NEUTRAL"
        sizing_confidence = 0.0

    # Log partial sizing for transparency
    if actual_side == bias and confidence < MIN_EDGE and verbose:
        log(
            f"[{symbol}] 📊 PARTIAL CONFIDENCE: {confidence:.1%} → {sizing_confidence:.1%} sizing (70% of confidence)"
        )

    return actual_side, sizing_confidence


def _check_target_price_alignment(
    symbol: str,
    side: str,
    confidence: float,
    current_spot: float,
    target_price: float,
    current_price: float,
    verbose: bool = True,
) -> bool:
    """Check if target price alignment allows trading"""

    # 1. Winning Side Only Filter: Skip underdog positions (price < $0.50) unless confidence is high
    # This allows underdog trades only when the signal is strong (confidence >= 60%)
    is_underdog = current_price < 0.50

    if is_underdog and confidence < 0.60:
        if verbose:
            log(
                f"[{symbol}] ⚠️  {side} is UNDERDOG (${current_price:.2f}) with LOW confidence ({confidence:.1%}). WAITING."
            )
        return False

    if is_underdog and confidence >= 0.60:
        if verbose:
            log(
                f"[{symbol}] ✅ {side} is UNDERDOG (${current_price:.2f}) but HIGH confidence ({confidence:.1%}). ALLOWING."
            )

    # 2. Spot-vs-Target Alignment (Safety Layer)
    if target_price > 0 and current_spot > 0:
        from src.config.settings import WINDOW_START_PRICE_BUFFER_PCT

        buffer = target_price * (WINDOW_START_PRICE_BUFFER_PCT / 100.0)

        is_winning_side_on_spot = False
        if side == "UP":
            is_winning_side_on_spot = current_spot >= (target_price - buffer)
        elif side == "DOWN":
            is_winning_side_on_spot = current_spot <= (target_price + buffer)

        if not is_winning_side_on_spot:
            if verbose:
                log(
                    f"[{symbol}] ⚠️  {side} is losing on SPOT (${current_spot:,.2f} vs Target ${target_price:,.2f}). SKIPPING."
                )
            return False

    return True


def _calculate_bet_size(
    balance: float, price: float, sizing_confidence: float
) -> tuple[float, float]:
    """Calculate position size and effective bet amount"""
    base_bet = balance * (BET_PERCENT / 100.0)
    # Scaled bet based on confidence
    confidence_multiplier = sizing_confidence * CONFIDENCE_SCALING_FACTOR
    target_bet = base_bet * confidence_multiplier

    # Ensure at least some minimum multiplier if confidence is very low but valid
    if target_bet < base_bet * 0.5:
        target_bet = base_bet * 0.5

    size = round(target_bet / price, 4)

    # Bump to MIN_SIZE if calculated size is below minimum and we can afford it
    if size < MIN_SIZE:
        min_size_cost = MIN_SIZE * price
        if balance >= min_size_cost:
            size = MIN_SIZE
            bet_usd_effective = min_size_cost
        else:
            bet_usd_effective = target_bet
    else:
        bet_usd_effective = target_bet

    # Use MAX_SIZE strategy
    if MAX_SIZE:
        max_size_bet = MAX_SIZE * price

        if MAX_SIZE_MODE == "MAXIMIZE":
            # Use higher of balance % or MAX_SIZE
            if max_size_bet > bet_usd_effective:
                bet_usd_effective = max_size_bet
                size = MAX_SIZE
        else:  # CAP mode (default)
            # Use smaller of balance % or MAX_SIZE
            if max_size_bet < bet_usd_effective:
                bet_usd_effective = max_size_bet
                size = MAX_SIZE

    return size, bet_usd_effective


def _prepare_trade_params(
    symbol: str, balance: float, add_spacing: bool = True, verbose: bool = True
) -> Optional[dict]:
    """
    Prepare trade parameters without executing order
    """
    _cleanup_old_ab_test_logs()

    up_id, down_id = get_token_ids(symbol)
    if not up_id or not down_id:
        if verbose:
            log(f"[{symbol}] ❌ Market not found")
            if add_spacing:
                log("")
        return

    client = get_clob_client()
    confidence, bias, p_up, best_bid, best_ask, signals, raw_scores = (
        calculate_confidence(symbol, up_id, client, verbose=verbose)
    )

    # A/B TESTING: Randomly select between Additive and Bayesian (50/50)
    # Or use configured method if A/B testing is disabled
    import random

    if ENABLE_AB_TESTING:
        use_bayesian = random.random() < 0.5  # 50% chance

        if use_bayesian:
            confidence = raw_scores.get("bayesian_confidence", confidence)
            bias = raw_scores.get("bayesian_bias", bias)
            method_used = "BAYESIAN"
        else:
            confidence = raw_scores.get("additive_confidence", confidence)
            bias = raw_scores.get("additive_bias", bias)
            method_used = "ADDITIVE"
    else:
        # Use configured method from BAYESIAN_CONFIDENCE setting
        if BAYESIAN_CONFIDENCE:
            confidence = raw_scores.get("bayesian_confidence", confidence)
            bias = raw_scores.get("bayesian_bias", bias)
            method_used = "BAYESIAN (CONFIGURED)"
        else:
            confidence = raw_scores.get("additive_confidence", confidence)
            bias = raw_scores.get("additive_bias", bias)
            method_used = "ADDITIVE (CONFIGURED)"

    # Get window start for tracking
    window_start, window_end = get_window_times(symbol)
    window_key = (symbol, window_start.isoformat())

    # Log which method was selected (limit to once per symbol per window)
    if bias != "NEUTRAL" and window_key not in _ab_test_logged:
        if ENABLE_AB_TESTING:
            log(
                f"[{symbol}] 🧪 A/B TEST: Using {method_used} confidence (p_up={p_up:.3f}): "
                f"{confidence:.1%} | BAYESIAN: {raw_scores.get('bayesian_confidence', 0):.1%}, ADDITIVE: {raw_scores.get('additive_confidence', 0):.1%}"
            )
        else:
            log(
                f"[{symbol}] ✅ Using {method_used} confidence (p_up={p_up:.3f}): "
                f"{confidence:.1%} | BAYESIAN: {raw_scores.get('bayesian_confidence', 0):.1%}, ADDITIVE: {raw_scores.get('additive_confidence', 0):.1%}"
            )
        _ab_test_logged.add(window_key)

    if bias == "NEUTRAL" or best_bid is None or best_ask is None:
        if verbose:
            if bias == "NEUTRAL":
                log(f"[{symbol}] ⚪ Confidence: {confidence:.1%} ({bias}) - NO TRADE")
            else:
                log(f"[{symbol}] ⚪ Liquidity not ready (Bid/Ask missing)")
            if add_spacing:
                log("")
        return

    # Extract current_spot from signals for validation
    current_spot = 0.0
    if isinstance(signals, dict):
        current_spot = float(signals.get("current_spot", 0))

    # Price Movement Validation for High Confidence Trades
    if confidence >= 0.75:  # Only validate high confidence trades
        validation_result = validate_price_movement_for_trade(
            symbol=symbol,
            confidence=confidence,
            current_spot=current_spot,
            max_movement_threshold=20.0,
            min_confidence_threshold=0.75,
        )

        if not validation_result["valid"]:
            if verbose:
                reason = validation_result["reduction_reason"]
                log(f"[{symbol}] 🚫 Price validation BLOCKED trade: {reason}")
                if add_spacing:
                    log("")
            return

        # Use adjusted confidence if it was reduced
        if validation_result["adjusted_confidence"] < confidence:
            original_confidence = confidence
            confidence = validation_result["adjusted_confidence"]

            # Recalculate sizing confidence with reduced confidence
            actual_side, sizing_confidence = _determine_trade_side(
                symbol, bias, confidence, raw_scores, verbose=verbose
            )

            if verbose:
                log(
                    f"[{symbol}] 📉 Price validation reduced confidence: {original_confidence:.1%} → {confidence:.1%}"
                )
                if validation_result["reduction_reason"]:
                    reason = validation_result["reduction_reason"]
                    log(f"   Reason: {reason}")

    actual_side, sizing_confidence = _determine_trade_side(
        symbol, bias, confidence, raw_scores, verbose=verbose
    )

    if actual_side == "NEUTRAL":
        if verbose:
            if confidence == 0 and bias == "NEUTRAL":
                log(f"[{symbol}] ⚪ Neutral / No Signal")
            else:
                log(
                    f"[{symbol}] ⏳ WAIT ZONE: {bias} ({confidence:.1%}) | < {MIN_EDGE:.1%} (evidence-based reversal not met)"
                )

            if add_spacing:
                log("")
        return

    if actual_side == "UP":
        # MAKER PRICING: Join the best bid for UP tokens
        token_id, side, price = up_id, "UP", float(best_bid)
    else:
        # MAKER PRICING: Join the best bid for DOWN tokens (which is 1 - UP best ask)
        token_id, side, price = down_id, "DOWN", 1.0 - float(best_ask)

    # NEW: Check if we already have a trade for THIS SIDE in this window
    window_start, window_end = get_window_times(symbol)
    window_start_str = window_start.isoformat()

    # Check if ANY trade exists for this window
    other_side_exists = False
    if has_trade_for_window(symbol, window_start_str):
        other_side_exists = True

    if has_side_for_window(symbol, window_start_str, side):
        if verbose:
            log(
                f"   ⏭️  [{symbol}] Already have {side} position for this window. Skipping."
            )
        return None

    if actual_side == bias:
        entry_type = "Trend Following"
        emoji = "🚀"
    else:
        entry_type = "Contrarian Entry"
        emoji = "🔄"

    if other_side_exists:
        # Construct Hedged Reversal label
        # Example: ⚔️ 🚀 Hedged Reversal (Trend)
        short_type = "Trend" if actual_side == bias else "Contrarian"
        entry_type = f"Hedged Reversal ({short_type})"
        emoji = f"⚔️ {emoji}"

    if verbose:
        # This log is for evaluation, not execution
        pass

    # Check lateness
    now_et = datetime.now(tz=ZoneInfo("America/New_York"))
    lateness = (now_et - window_start).total_seconds()
    time_left = (window_end - now_et).total_seconds()

    if lateness > MAX_ENTRY_LATENESS_SEC:
        if verbose:
            log(
                f"[{symbol}] ⚠️   Cycle is TOO LATE ({lateness:.0f}s into window, {time_left:.0f}s left). SKIPPING."
            )
            if add_spacing:
                log("")
        return
    elif lateness > 60:
        if verbose:
            log(
                f"[{symbol}] ⏳ Cycle is LATE ({lateness:.0f}s into window, {time_left:.0f}s left)"
            )

    target_price = float(get_window_start_price(symbol))

    if not _check_target_price_alignment(
        symbol, side, confidence, current_spot, target_price, price, verbose=verbose
    ):
        if add_spacing and verbose:
            log("")
        return

    # Check filters
    bfxd_ok, bfxd_trend = bfxd_allows_trade(symbol, side)

    # Signal details
    rsi = 50.0
    imbalance_val = 0.5
    adx_val = 0.0
    if isinstance(signals, dict):
        if "momentum" in signals and isinstance(signals["momentum"], dict):
            rsi = signals["momentum"].get("rsi", 50.0)

        if "order_flow" in signals and isinstance(signals["order_flow"], dict):
            imbalance_val = signals["order_flow"].get("buy_pressure", 0.5)

        if "adx" in signals and isinstance(signals["adx"], dict):
            adx_val = signals["adx"].get("value", 0.0)

    filter_text = f"ADX: {adx_val:.1f}"

    if ENABLE_BFXD and symbol == "BTC":
        filter_text += f" | BFXD: {bfxd_trend} {'✅' if bfxd_ok else '❌'}"

    core_summary = f"Confidence: {confidence:.1%} ({bias})"

    if not bfxd_ok:
        log(f"[{symbol}] ⛔ {core_summary} | status: BLOCKED")
        if add_spacing:
            log("")
        return

    if price <= 0:
        log(f"[{symbol}] ERROR: Invalid price {price}")
        if add_spacing:
            log("")
        return

    # Clamp and round to minimum tick size (0.01)
    price = max(0.01, min(0.99, price))
    price = round(price, 2)

    size, bet_usd_effective = _calculate_bet_size(balance, price, sizing_confidence)

    if size < MIN_SIZE:
        min_size_cost = MIN_SIZE * price
        log(
            f"   💸 [{symbol}] Cannot afford {MIN_SIZE} shares (${min_size_cost:.2f}). Balance: ${balance:.2f}. Skipping."
        )
        if add_spacing:
            log("")
        return None

    if size == MIN_SIZE:
        log(
            f"   📈 [{symbol}] Bumping size to {MIN_SIZE} shares (${bet_usd_effective:.2f})"
        )

    return {
        "symbol": symbol,
        "token_id": token_id,
        "side": side,
        "price": price,
        "size": size,
        "bet_usd": bet_usd_effective,
        "confidence": confidence,
        "core_summary": core_summary,
        "entry_type": entry_type,
        "emoji": emoji,
        "p_up": p_up,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "imbalance": imbalance_val,
        "funding_bias": get_funding_bias(symbol),
        "target_price": target_price if target_price > 0 else None,
        "window_start": window_start,
        "window_end": window_end,
        "slug": get_current_slug(symbol),
        "raw_scores": raw_scores,
    }
