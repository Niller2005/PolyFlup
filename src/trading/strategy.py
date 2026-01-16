"""Trading strategy logic"""

from py_clob_client.client import ClobClient
from src.config.settings import (
    MAX_SPREAD,
    ADX_ENABLED,
    BFXD_URL,
    MIN_EDGE,
    MOMENTUM_LOOKBACK_MINUTES,
    ENABLE_MOMENTUM_FILTER,
    ENABLE_ORDER_FLOW,
    ENABLE_DIVERGENCE,
    ENABLE_VWM,
    ENABLE_BFXD,
    ENABLE_PRICE_VALIDATION,
    PRICE_VALIDATION_MAX_MOVEMENT,
    PRICE_VALIDATION_MIN_CONFIDENCE,
    BAYESIAN_CONFIDENCE,
)
from src.utils.logger import log, log_error
from src.trading.orders.utils import is_404_error
from src.data.market_data import (
    get_funding_bias,
    get_fear_greed,
    get_adx_from_binance,
    get_price_momentum,
    get_order_flow_analysis,
    get_cross_exchange_divergence,
    get_volume_weighted_momentum,
    get_current_spot_price,
    get_polymarket_momentum,
    validate_price_movement_for_trade,
)
import requests
import numpy as np


def calculate_confidence(symbol: str, up_token: str, client: ClobClient):
    """
    Calculate confidence score and directional bias combining Polymarket and Binance data.
    Goal: Higher quality entries, fewer stop losses.

    Returns:
        tuple: (confidence, bias, p_up, best_bid, best_ask, signals, raw_scores)
        - confidence: 0.0 to 1.0 (sizing)
        - bias: "UP", "DOWN", or "NEUTRAL"
        - signals: Dictionary of detailed signal information
        - raw_scores: Dictionary of raw signal scores for backtesting
    """
    try:
        book = client.get_order_book(up_token)
        if isinstance(book, dict):
            bids = book.get("bids", []) or []
            asks = book.get("asks", []) or []
        else:
            bids = getattr(book, "bids", []) or []
            asks = getattr(book, "asks", []) or []
    except Exception as e:
        if is_404_error(e):
            # Log the token ID occasionally or during 404 to help debug "wrong ID" vs "not ready"
            log(f"[{symbol}] Order book not ready for token {up_token[:10]}... (404)")
        else:
            log_error(f"[{symbol}] Order book error for {up_token}: {e}")
        return 0.0, "NEUTRAL", 0.5, None, None, {}, {}

    if not bids or not asks:
        return 0.0, "NEUTRAL", 0.5, None, None, {}, {}

    best_bid = float(
        bids[-1].price if hasattr(bids[-1], "price") else bids[-1].get("price", 0)
    )
    best_ask = float(
        asks[-1].price if hasattr(asks[-1], "price") else asks[-1].get("price", 0)
    )

    if not best_bid or not best_ask:
        return 0.0, "NEUTRAL", 0.5, best_bid, best_ask, {}, {}

    spread = best_ask - best_bid
    if spread > MAX_SPREAD:
        return (
            0.0,
            "NEUTRAL",
            0.5,
            best_bid,
            best_ask,
            {"reason": "spread_too_wide"},
            {},
        )

    # Base Polymarket probability
    p_up = (best_bid + best_ask) / 2.0

    # 1. Price Momentum (Binance) - Weight: 0.35
    momentum_score = 0.0
    momentum_dir = "NEUTRAL"
    momentum = get_price_momentum(symbol, lookback_minutes=MOMENTUM_LOOKBACK_MINUTES)
    if momentum["direction"] != "NEUTRAL":
        momentum_score = momentum["strength"]
        momentum_dir = momentum["direction"]
        if momentum["acceleration"] > 0 and momentum_dir == "UP":
            momentum_score *= 1.2
        if momentum["acceleration"] < 0 and momentum_dir == "DOWN":
            momentum_score *= 1.2

    # 2. Order Flow (Binance) - Weight: 0.15
    flow_score = 0.0
    flow_dir = "NEUTRAL"
    order_flow = get_order_flow_analysis(symbol)
    # Scale: 0.55 or 0.45 = 1.0 strength (aggressive flow signal)
    flow_score = min(abs(order_flow["buy_pressure"] - 0.5) * 10.0, 1.0)
    flow_dir = "UP" if order_flow["buy_pressure"] > 0.5 else "DOWN"

    # 3. Divergence (Poly vs Binance) - Weight: 0.15
    divergence_score = 0.0
    divergence_dir = "NEUTRAL"
    divergence = get_cross_exchange_divergence(symbol, p_up)
    # Scale: 0.1 divergence = 1.0 score
    divergence_score = min(abs(divergence["divergence"]) * 10.0, 1.0)
    divergence_dir = (
        "UP"
        if divergence["opportunity"] == "BUY_UP"
        else "DOWN"
        if divergence["opportunity"] == "BUY_DOWN"
        else "NEUTRAL"
    )

    # 4. VWAP / VWM - Weight: 0.10
    vwm_score = 0.0
    vwm_dir = "NEUTRAL"
    vwm = get_volume_weighted_momentum(symbol)
    vwm_score = vwm["momentum_quality"]
    vwm_dir = "UP" if vwm["vwap_distance"] > 0 else "DOWN"

    # 5. Polymarket Native Momentum - Weight: 0.20 (New confirmed source)
    pm_mom_score = 0.0
    pm_mom_dir = "NEUTRAL"
    pm_momentum = get_polymarket_momentum(up_token)
    if pm_momentum["direction"] != "NEUTRAL":
        pm_mom_score = pm_momentum["strength"]
        pm_mom_dir = pm_momentum["direction"]

    # 5.1 Lead/Lag Indicator (Experimental)
    lead_lag_bonus = 1.0
    if momentum_dir != "NEUTRAL" and pm_mom_dir != "NEUTRAL":
        if momentum_dir == pm_mom_dir:
            # Both agree - strong signal
            lead_lag_bonus = 1.2
        else:
            # Divergence in momentum - cautionary
            lead_lag_bonus = 0.8

    # 6. ADX (Trend Strength) - Weight: 0.15
    adx_score = 0.0
    adx_dir = "NEUTRAL"
    adx_val = 0.0
    if ADX_ENABLED:
        adx_val = get_adx_from_binance(symbol)
        if adx_val > 0:
            # Normalize ADX (25-50 range maps to 0.5-1.0 score)
            adx_score = min(adx_val / 50.0, 1.0)
            # ADX follows the strongest current directional signal
            adx_dir = (
                momentum_dir
                if momentum_dir != "NEUTRAL"
                else pm_mom_dir
                if pm_mom_dir != "NEUTRAL"
                else divergence_dir
            )

    # Aggregate Scores for each direction
    up_total = 0.0
    down_total = 0.0

    # Adjust weights to include PM momentum
    # Capped momentum at 25-30%, redistributed to flow and divergence
    adx_weight = 0.15 if ADX_ENABLED else 0.0
    mom_weight = 0.25 if ADX_ENABLED else 0.30
    pm_mom_weight = 0.20 if ADX_ENABLED else 0.25
    flow_weight = 0.15 if ADX_ENABLED else 0.15
    div_weight = 0.20 if ADX_ENABLED else 0.20
    vwm_weight = 0.05 if ADX_ENABLED else 0.10

    # Calculate quality factors for each signal (0.8 - 1.5 range)
    # Momentum quality: based on RSI extremes and strength
    if momentum_dir != "NEUTRAL":
        momentum_strength = momentum.get("strength", 0.0)
        momentum_rsi = momentum.get("rsi", 50.0)
        if momentum_rsi < 30:
            # Oversold in uptrend = very high quality
            mom_quality = 1.3
        elif momentum_rsi > 70:
            # Overbought in uptrend = potential exhaustion = lower quality
            mom_quality = 0.8
        elif momentum_dir == "UP" and momentum_rsi < 30:
            # Strong downtrend with oversold = bounce potential = high quality
            mom_quality = 1.2
        elif momentum_dir == "DOWN" and momentum_rsi > 70:
            # Strong uptrend with overbought = exhaustion = high quality
            mom_quality = 1.3
        else:
            mom_quality = 1.0
        # Strength boost: very strong momentum gets bonus
        if momentum_strength > 0.8:
            mom_quality *= 1.1
    else:
        mom_quality = 1.0

    # PM momentum: no built-in quality metrics, use moderate factor
    pm_mom_quality = 1.0

    # Flow quality: based on buy pressure extremes and trade intensity
    if flow_dir != "NEUTRAL":
        buy_pressure = order_flow.get("buy_pressure", 0.5)
        large_trade_dir = order_flow.get("large_trade_direction", "NEUTRAL")
        trade_intensity = order_flow.get("trade_intensity", 0.0)

        flow_quality = 1.0

        if buy_pressure > 0.70:
            # Very strong buying pressure - high quality
            flow_quality = 1.3
        elif buy_pressure < 0.30:
            # Strong selling pressure - high quality
            flow_quality = 1.2
        elif large_trade_dir != "NEUTRAL" and buy_pressure > 0.6:
            # Large trades in consistent direction = better quality
            flow_quality *= 1.1
        # Higher trade intensity = better quality
        if trade_intensity > 0.5:
            flow_quality *= 1.05
    else:
        flow_quality = 1.0

    # Divergence quality: based on magnitude and opportunity
    if divergence_dir != "NEUTRAL":
        divergence_val = divergence.get("divergence", 0.0)
        opportunity = divergence.get("opportunity", "NEUTRAL")

        quality = 1.0
        # Larger divergence = stronger signal
        quality = 1.0 + min(abs(divergence_val), 0.3)

        # Check opportunity quality
        if opportunity != "NEUTRAL":
            # Clear opportunity direction = better quality
            quality *= 1.15
        elif abs(divergence_val) < 0.05:
            # Very small divergence = weak signal
            quality *= 0.8
        divergence_quality = quality
    else:
        divergence_quality = 1.0

    # VWM quality: already has momentum_quality from VWM calculation
    # Convert 0-1 scale to 0.8-1.3 multiplier
    vwm_mom_quality = vwm.get("momentum_quality", 0.0)
    vwm_quality = 0.8 + (vwm_mom_quality * 0.5)

    # ADX quality: based on trend strength
    if adx_score > 0:
        if adx_val > 40:
            # Very strong trend
            adx_quality = 1.3
        elif adx_val > 30:
            # Strong trend
            adx_quality = 1.15
        elif adx_val > 25:
            # Moderate trend
            adx_quality = 1.05
        elif adx_val > 20:
            # Weak trend
            adx_quality = 0.9
        elif adx_val > 15:
            # Very weak trend
            adx_quality = 0.8
        else:
            # No trend
            adx_quality = 0.7
    else:
        adx_quality = 1.0

    # Log-likelihood helper for Bayesian approach
    def log_likelihood(score: float, direction: str, quality: float) -> float:
        evidence = (score - 0.5) * 2  # -1 to +1
        log_LR = evidence * 3.0 * quality  # Calibration factor with quality
        if direction == "DOWN":
            log_LR = -log_LR
        return log_LR

    # Apply weights with quality factors
    for score, direction, weight, quality in [
        (momentum_score, momentum_dir, mom_weight, mom_quality),
        (pm_mom_score, pm_mom_dir, pm_mom_weight, pm_mom_quality),
        (flow_score, flow_dir, flow_weight, flow_quality),
        (divergence_score, divergence_dir, div_weight, divergence_quality),
        (vwm_score, vwm_dir, vwm_weight, vwm_quality),
        (adx_score, adx_dir, adx_weight, adx_quality),
    ]:
        if direction == "UP":
            up_total += score * weight * quality
        elif direction == "DOWN":
            down_total += score * weight * quality

    # Calculate additive confidence (for A/B testing)
    if up_total > down_total:
        additive_bias = "UP"
        if momentum_dir == pm_mom_dir and momentum_dir == "UP":
            additive_up_total = up_total * 1.1
        else:
            additive_up_total = up_total
        additive_confidence = (additive_up_total - (down_total * 0.2)) * lead_lag_bonus
    elif down_total > up_total:
        additive_bias = "DOWN"
        if momentum_dir == pm_mom_dir and momentum_dir == "DOWN":
            additive_down_total = down_total * 1.1
        else:
            additive_down_total = down_total
        additive_confidence = (additive_down_total - (up_total * 0.2)) * lead_lag_bonus
    else:
        additive_bias = "NEUTRAL"
        additive_confidence = 0.0

    # Normalize additive confidence
    additive_confidence = max(0.0, min(1.0, additive_confidence))

    # Calculate Bayesian confidence (for A/B testing)
    # Start with market prior from Polymarket orderbook
    prior_odds = p_up / (1 - p_up) if p_up != 1.0 else 10.0
    bayesian_log_odds = np.log(prior_odds) if prior_odds > 0 else 0.0

    # Accumulate evidence from all signals
    for score, direction, weight, quality in [
        (momentum_score, momentum_dir, mom_weight, mom_quality),
        (pm_mom_score, pm_mom_dir, pm_mom_weight, pm_mom_quality),
        (flow_score, flow_dir, flow_weight, flow_quality),
        (divergence_score, divergence_dir, div_weight, divergence_quality),
        (vwm_score, vwm_dir, vwm_weight, vwm_quality),
        (adx_score, adx_dir, adx_weight, adx_quality),
    ]:
        bayesian_log_odds += log_likelihood(score, direction, quality) * weight

    # Convert log-odds back to probability (P(UP))
    bayesian_prob_up = 1 / (1 + np.exp(-bayesian_log_odds))

    # Convert to symmetric confidence (distance from 50% neutral)
    # This makes confidence symmetric for UP and DOWN sides
    # Example: 60% UP → 20% confidence, 40% UP → 20% confidence (same conviction)
    if bayesian_prob_up > 0.5:
        bayesian_confidence = (bayesian_prob_up - 0.5) * 2
    elif bayesian_prob_up < 0.5:
        bayesian_confidence = (0.5 - bayesian_prob_up) * 2
    else:
        bayesian_confidence = 0.0

    # Apply lead-lag bonus as multiplier on final confidence
    bayesian_confidence *= lead_lag_bonus

    # Determine bias from sign of log-odds
    if bayesian_log_odds > 0:
        bayesian_bias = "UP"
    elif bayesian_log_odds < 0:
        bayesian_bias = "DOWN"
    else:
        bayesian_bias = "NEUTRAL"

    # Select active confidence based on configuration
    if BAYESIAN_CONFIDENCE:
        confidence = bayesian_confidence
        bias = bayesian_bias
    else:
        confidence = additive_confidence
        bias = additive_bias

    # Multi-confirmation system with graduated reduction (starting at 60%)
    if confidence > 0.60:
        # Define the 5 key indicators with their weights
        key_signals = [
            {
                "name": "price_momentum",
                "score": momentum_score,
                "dir": momentum_dir,
                "weight": 0.35,
            },
            {
                "name": "polymarket_momentum",
                "score": pm_mom_score,
                "dir": pm_mom_dir,
                "weight": 0.20,
            },
            {
                "name": "order_flow",
                "score": flow_score,
                "dir": flow_dir,
                "weight": 0.15,
            },
            {
                "name": "cross_divergence",
                "score": divergence_score,
                "dir": divergence_dir,
                "weight": 0.15,
            },
            {
                "name": "adx_strength",
                "score": adx_score,
                "dir": adx_dir,
                "weight": 0.15,
            },
        ]

        # Count strongly aligned signals (score > 0.5 and same direction as bias)
        strongly_aligned = 0
        confirmation_score = 0.0

        for signal in key_signals:
            if (
                signal["score"] > 0.5
                and signal["dir"] == bias
                and signal["dir"] != "NEUTRAL"
            ):
                strongly_aligned += 1
                confirmation_score += signal["score"] * signal["weight"]

        # Require at least 3 out of 5 signals to be strongly aligned
        if strongly_aligned < 3:
            # Graduated reduction: scales with confidence level
            # Higher confidence = more penalty for missing confirmation
            confidence_factor = (confidence - 0.60) / 0.25  # 0.0 at 60%, 1.0 at 85%
            confidence_reduction = (3 - strongly_aligned) * 0.10 * confidence_factor
            confidence = max(0.60, confidence - confidence_reduction)

    # Additional validation: cap maximum confidence at 85% for extreme signals
    if confidence > 0.85:
        confidence = 0.85

    # Final normalization
    confidence = max(0.0, min(1.0, confidence))

    # Get current spot price for entry logic
    current_spot = divergence.get("binance_price", 0)
    if current_spot == 0:
        current_spot = get_current_spot_price(symbol)

    signals = {
        "momentum": momentum,
        "pm_momentum": pm_momentum,
        "order_flow": order_flow,
        "divergence": divergence,
        "vwm": vwm,
        "adx": {"value": adx_val, "score": adx_score},
        "scores": {"up": up_total, "down": down_total},
        "current_spot": current_spot,
    }
    # Price Movement Validation for High Confidence Trades
    if ENABLE_PRICE_VALIDATION and confidence >= PRICE_VALIDATION_MIN_CONFIDENCE:
        validation_result = validate_price_movement_for_trade(
            symbol=symbol,
            confidence=confidence,
            current_spot=current_spot,
            max_movement_threshold=PRICE_VALIDATION_MAX_MOVEMENT,
            min_confidence_threshold=PRICE_VALIDATION_MIN_CONFIDENCE,
        )

        if validation_result["adjusted_confidence"] < confidence:
            original_confidence = confidence
            confidence = validation_result["adjusted_confidence"]

            # Add validation data to signals for logging
            signals["price_validation"] = validation_result["price_data"]

            if validation_result["reduction_reason"]:
                reason = validation_result["reduction_reason"]
                log(
                    f"[{symbol}] 📉 Price validation reduced confidence: {original_confidence:.1%} → {confidence:.1%} | {reason}"
                )

            # If confidence dropped significantly, might want to make it neutral
            if confidence < MIN_EDGE:
                bias = "NEUTRAL"
                confidence = 0.0
                log(
                    f"[{symbol}] ⚠️  Price validation blocked trade (confidence too low after reduction)"
                )

    signals = {
        "momentum": momentum,
        "pm_momentum": pm_momentum,
        "order_flow": order_flow,
        "divergence": divergence,
        "vwm": vwm,
        "adx": {"value": adx_val, "score": adx_score},
        "scores": {"up": up_total, "down": down_total},
        "current_spot": current_spot,
    }

    # Raw signal scores for database storage and backtesting
    raw_scores = {
        "up_total": up_total,
        "down_total": down_total,
        "momentum_score": momentum_score,
        "momentum_dir": momentum_dir,
        "flow_score": flow_score,
        "flow_dir": flow_dir,
        "divergence_score": divergence_score,
        "divergence_dir": divergence_dir,
        "vwm_score": vwm_score,
        "vwm_dir": vwm_dir,
        "pm_mom_score": pm_mom_score,
        "pm_mom_dir": pm_mom_dir,
        "adx_score": adx_score,
        "adx_dir": adx_dir,
        "lead_lag_bonus": lead_lag_bonus,
        # A/B Testing: store both methods for comparison
        "additive_confidence": additive_confidence,
        "additive_bias": additive_bias,
        "bayesian_confidence": bayesian_confidence,
        "bayesian_bias": bayesian_bias,
        "market_prior_p_up": p_up,
    }

    # Price Movement Validation for High Confidence Trades
    if ENABLE_PRICE_VALIDATION and confidence >= PRICE_VALIDATION_MIN_CONFIDENCE:
        validation_result = validate_price_movement_for_trade(
            symbol=symbol,
            confidence=confidence,
            current_spot=current_spot,
            max_movement_threshold=PRICE_VALIDATION_MAX_MOVEMENT,
            min_confidence_threshold=PRICE_VALIDATION_MIN_CONFIDENCE,
        )

        if validation_result["adjusted_confidence"] < confidence:
            original_confidence = confidence
            confidence = validation_result["adjusted_confidence"]

            # Add validation data to signals for logging
            signals["price_validation"] = validation_result["price_data"]

            if validation_result["reduction_reason"]:
                reason = validation_result["reduction_reason"]
                log(
                    f"[{symbol}] 📉 Price validation reduced confidence: {original_confidence:.1%} → {confidence:.1%} | {reason}"
                )

            # If confidence dropped significantly, might want to make it neutral
            if confidence < MIN_EDGE:
                bias = "NEUTRAL"
                confidence = 0.0
                log(
                    f"[{symbol}] ⚠️  Price validation blocked trade (confidence too low after reduction)"
                )

    return confidence, bias, p_up, best_bid, best_ask, signals, raw_scores


def bfxd_allows_trade(symbol: str, direction: str) -> tuple[bool, str]:
    """
    External BTC trend filter
    """
    if not ENABLE_BFXD:
        return True, "DISABLED"

    symbol_u = symbol.upper()
    direction_u = direction.upper()

    if not BFXD_URL:
        return True, "NO_URL"

    if symbol_u != "BTC":
        return True, "N/A"

    try:
        r = requests.get(BFXD_URL, timeout=5)
        r.raise_for_status()
        data = r.json()

        if not isinstance(data, dict):
            return True, "INVALID"

        trend = str(data.get("BTC/USDT", "")).upper()
        if not trend:
            return True, "NONE"

        if trend not in ("UP", "DOWN"):
            return True, f"UNK({trend})"

        match = trend == direction_u
        return match, trend

    except Exception as e:
        return True, f"ERR({str(e)[:10]})"
