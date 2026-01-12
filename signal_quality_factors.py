#!/usr/bin/env python3
"""Calculate signal quality factors for confidence weighting

This script defines quality factors that multiply signal weights
to reflect signal reliability based on various conditions.
"""


def calculate_momentum_quality(momentum_data: dict) -> float:
    """Calculate quality factor for momentum signal based on RSI extremes and strength"""
    if not momentum_data:
        return 1.0

    rsi = momentum_data.get("rsi", 50.0)
    strength = momentum_data.get("strength", 0.0)
    direction = momentum_data.get("direction", "NEUTRAL")

    # Base quality starts at 1.0
    quality = 1.0

    # RSI extremes indicate high confidence in trend direction
    if direction != "NEUTRAL":
        if direction == "UP" and rsi < 30:
            # Strong uptrend with oversold RSI - very high quality
            quality = 1.3
        elif direction == "UP" and rsi > 70:
            # Potential exhaustion at high RSI - lower quality
            quality = 0.8
        elif direction == "DOWN" and rsi > 70:
            # Strong downtrend with overbought RSI - high quality
            quality = 1.2
        elif direction == "DOWN" and rsi < 30:
            # Potential bounce at low RSI - moderate quality
            quality = 0.9

    # Strength boost: very strong momentum gets bonus
    if strength > 0.8:
        quality *= 1.1

    return quality


def calculate_flow_quality(flow_data: dict) -> float:
    """Calculate quality factor for order flow signal"""
    if not flow_data:
        return 1.0

    buy_pressure = flow_data.get("buy_pressure", 0.5)
    large_trade_direction = flow_data.get("large_trade_direction", "NEUTRAL")
    trade_intensity = flow_data.get("trade_intensity", 0.0)

    quality = 1.0

    # Buy pressure extremes indicate conviction
    if buy_pressure > 0.70:
        # Very strong buying pressure - high quality
        quality = 1.3
    elif buy_pressure < 0.30:
        # Strong selling pressure - high quality
        quality = 1.2

    # Large trades in consistent direction boost quality
    if large_trade_direction != "NEUTRAL" and buy_pressure > 0.6:
        quality *= 1.1

    # Higher trade intensity = better quality
    if trade_intensity > 0.5:
        quality *= 1.05

    return quality


def calculate_divergence_quality(divergence_data: dict) -> float:
    """Calculate quality factor for divergence signal"""
    if not divergence_data:
        return 1.0

    divergence = divergence_data.get("divergence", 0.0)
    opportunity = divergence_data.get("opportunity", "NEUTRAL")

    quality = 1.0

    # Larger divergence = stronger signal (up to a point)
    quality = 1.0 + min(abs(divergence), 0.3)

    # Check opportunity quality
    if opportunity != "NEUTRAL":
        # Clear opportunity direction = better quality
        quality *= 1.15
    elif abs(divergence) < 0.05:
        # Very small divergence - weak signal
        quality *= 0.8

    return quality


def calculate_vwm_quality(vwm_data: dict) -> float:
    """Calculate quality factor for VWM signal"""
    if not vwm_data:
        return 1.0

    # VWM already has momentum_quality
    momentum_quality = vwm_data.get("momentum_quality", 0.0)

    # Convert 0-1 scale to 0.8-1.3 multiplier
    # momentum_quality: 0.0 = 0.8, 1.0 = 1.0, 1.0 = 1.3 (already ranges)
    quality = 0.8 + (momentum_quality * 0.5)

    return quality


def calculate_adx_quality(adx_data: dict) -> float:
    """Calculate quality factor for ADX signal"""
    if not adx_data:
        return 1.0

    adx_score = adx_data.get("score", 0.0)
    adx_value = adx_data.get("value", 0.0)

    quality = 1.0

    # Higher ADX = stronger trend = higher quality
    if adx_value > 40:
        # Very strong trend
        quality = 1.3
    elif adx_value > 30:
        # Strong trend
        quality = 1.15
    elif adx_value > 25:
        # Moderate trend
        quality = 1.0
    elif adx_value > 20:
        # Weak trend
        quality = 0.85
    else:
        # No trend
        quality = 0.7

    return quality


def calculate_pm_momentum_quality(pm_mom_data: dict) -> float:
    """Calculate quality factor for Polymarket momentum"""
    if not pm_mom_data:
        return 1.0

    # PM momentum doesn't have built-in quality metrics
    # Use default moderate quality factor
    return 1.0


# Test the quality factors
if __name__ == "__main__":
    print("Signal Quality Factor Tests")
    print("=" * 60)

    # Test momentum quality
    print("\nMomentum Quality:")
    for rsi in [15, 25, 50, 75, 80]:
        for direction in ["UP", "DOWN", "NEUTRAL"]:
            for strength in [0.4, 0.6, 1.0]:
                data = {"rsi": rsi, "direction": direction, "strength": strength}
                q = calculate_momentum_quality(data)
                print(
                    f"  RSI={rsi:2d}, {direction:6s}, Strength={strength:.1f} → Quality={q:.2f}"
                )

    # Test flow quality
    print("\nOrder Flow Quality:")
    for bp in [0.25, 0.5, 0.75, 0.85]:
        data = {
            "buy_pressure": bp,
            "large_trade_direction": "BUY",
            "trade_intensity": 0.6,
        }
        q = calculate_flow_quality(data)
        print(f"  Buy Pressure={bp:.2f} → Quality={q:.2f}")

    # Test ADX quality
    print("\nADX Quality:")
    for adx_val in [10, 20, 30, 40, 50]:
        data = {"score": 0.8, "value": adx_val}
        q = calculate_adx_quality(data)
        print(f"  ADX={adx_val:.1f} → Quality={q:.2f}")

    print("\n" + "=" * 60)
