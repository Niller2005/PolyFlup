#!/usr/bin/env python3
"""Generate patch to add signal quality weights to strategy.py"""

# This shows the changes needed in src/trading/strategy.py
# Run this to generate the patch, then manually review/apply

PATCH_CONTENT = '''--- a/src/trading/strategy.py
+++ b/src/trading/strategy.py
@@ -163,13 +163,16 @@
         pm_mom_score, pm_mom_dir, pm_mom_weight, pm_mom_dir,
         flow_score, flow_dir, flow_weight, flow_dir,
         divergence_score, divergence_dir, div_weight, divergence_dir,
         vwm_score, vwm_dir, vwm_weight, vwm_dir,
         adx_score, adx_dir, adx_weight, adx_dir,
         lead_lag_bonus,
     ]
 
+    # Calculate quality factors for each signal (0.8 - 1.5 range)
+    mom_quality = _calculate_momentum_quality(momentum_score, momentum_dir, momentum, rsi=momentum.get("rsi", 50.0))
+    pm_mom_quality = 1.0  # PM momentum has no quality metrics
+    flow_quality = _calculate_flow_quality(order_flow)
+    divergence_quality = _calculate_divergence_quality(divergence)
+    vwm_quality = _calculate_vwm_quality(vwm)
+    adx_quality = _calculate_adx_quality(adx_score, adx_value=adx_val)
+
     # Aggregate Scores for each direction
     up_total = 0.0
     down_total = 0.0
@@ -180,8 +188,11 @@
     for score, direction, weight in [
-        (momentum_score, momentum_dir, mom_weight),
-        (pm_mom_score, pm_mom_dir, pm_mom_weight),
-        (flow_score, flow_dir, flow_weight),
-        (divergence_score, divergence_dir, div_weight),
-        (vwm_score, vwm_dir, vwm_weight),
-        (adx_score, adx_dir, adx_weight),
+        (momentum_score * mom_quality, momentum_dir, mom_weight),
+        (pm_mom_score * pm_mom_quality, pm_mom_dir, pm_mom_weight),
+        (flow_score * flow_quality, flow_dir, flow_weight),
+        (divergence_score * divergence_quality, divergence_dir, div_weight),
+        (vwm_score * vwm_quality, vwm_dir, vwm_weight),
+        (adx_score * adx_quality, adx_dir, adx_weight),
     ]:
@@ -352,6 +357,41 @@
         "adx_score": adx_score,
         "adx_dir": adx_dir,
         "lead_lag_bonus": lead_lag_bonus,
     }
+
+def _calculate_momentum_quality(score: float, direction: str, momentum: dict, rsi: float = 50.0) -> float:
+    """Calculate momentum quality based on RSI extremes and strength"""
+    if direction == "NEUTRAL":
+        return 1.0
+
+    strength = momentum.get("strength", 0.0)
+    quality = 1.0
+
+    # RSI extremes indicate high confidence in trend direction
+    if direction == "UP":
+        if rsi < 30:
+            # Strong uptrend with oversold RSI - very high quality
+            quality = 1.4
+        elif rsi > 70:
+            # Potential exhaustion at high RSI - lower quality
+            quality = 0.7
+    elif direction == "DOWN":
+        if rsi < 30:
+            # Strong downtrend with oversold RSI - high quality
+            quality = 1.2
+        elif rsi > 70:
+            # Strong downtrend with overbought RSI - very high quality
+            quality = 1.3
+
+    # Strength boost: very strong momentum gets bonus
+    if strength > 0.8:
+        quality *= 1.1
+
+    return quality
+
+
+def _calculate_flow_quality(flow: dict) -> float:
+    """Calculate flow quality based on buy pressure extremes and trade intensity"""
+    if not flow:
+        return 1.0
+
+    buy_pressure = flow.get("buy_pressure", 0.5)
+    large_trade_direction = flow.get("large_trade_direction", "NEUTRAL")
+    trade_intensity = flow.get("trade_intensity", 0.0)
+
+    quality = 1.0
+
+    # Buy pressure extremes indicate conviction
+    if buy_pressure > 0.70:
+        # Very strong buying pressure - high quality
+        quality = 1.3
+    elif buy_pressure < 0.30:
+        # Strong selling pressure - high quality
+        quality = 1.2
+
+    # Large trades in consistent direction boost quality
+    if large_trade_direction != "NEUTRAL" and buy_pressure > 0.6:
+        quality *= 1.1
+
+    # Higher trade intensity = better quality
+    if trade_intensity > 0.5:
+        quality *= 1.05
+
+    return quality
+
+
+def _calculate_divergence_quality(divergence: dict) -> float:
+    """Calculate divergence quality based on magnitude and opportunity"""
+    if not divergence:
+        return 1.0
+
+    divergence_val = divergence.get("divergence", 0.0)
+    opportunity = divergence.get("opportunity", "NEUTRAL")
+
+    quality = 1.0
+
+    # Larger divergence = stronger signal (up to a point)
+    quality = 1.0 + min(abs(divergence_val), 0.3)
+
+    # Check opportunity quality
+    if opportunity != "NEUTRAL":
+        # Clear opportunity direction = better quality
+        quality *= 1.15
+    elif abs(divergence_val) < 0.05:
+        # Very small divergence - weak signal
+        quality *= 0.8
+
+    return quality
+
+
+def _calculate_vwm_quality(vwm: dict) -> float:
+    """Calculate VWM quality factor"""
+    if not vwm:
+        return 1.0
+
+    # VWM already has momentum_quality calculated
+    momentum_quality = vwm.get("momentum_quality", 0.0)
+
+    # Convert 0-1 scale to 0.8-1.3 multiplier
+    # momentum_quality: 0.0 = 0.8, 1.0 = 1.0, > 1.0 = 1.3
+    return 0.8 + (momentum_quality * 0.5)
+
+
+def _calculate_adx_quality(score: float, adx_value: float = 0.0) -> float:
+    """Calculate ADX quality based on trend strength"""
+    quality = 1.0
+
+    # Higher ADX = stronger trend = higher quality
+    if adx_value > 40:
+        # Very strong trend
+        quality = 1.3
+    elif adx_value > 30:
+        # Strong trend
+        quality = 1.15
+    elif adx_value > 25:
+        # Moderate trend
+        quality = 1.05
+    elif adx_value > 20:
+        # Weak trend
+        quality = 0.9
+    elif adx_value > 15:
+        # Very weak trend
+        quality = 0.8
+    else:
+        # No trend
+        quality = 0.7
+
+    return quality
'''

if __name__ == "__main__":
    print(PATCH_CONTENT)
