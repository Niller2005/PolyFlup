# Enhanced Trading Strategy: Polymarket + Binance Integration

## Overview

The enhanced strategy combines **Polymarket order book data** with **real-time Binance market data** to make more informed trading decisions. Instead of relying solely on Polymarket's internal pricing (which can be circular), we now use external market signals to identify true mispricings.

## Problem with "Buying the Undervalued Side"

The original strategy had a fundamental issue:
- It only used Polymarket's order book to determine "fair value"
- This is **circular reasoning** - you're using market prices to determine if market prices are wrong
- There was **no external reference point** to validate whether a side is truly undervalued

## Solution: Multi-Source Signal Integration

The new strategy integrates **4 major Binance data sources** with Polymarket pricing:

### Edge Calculation Breakdown (Adaptive Weights)

The strategy uses **directional scoring** where signals vote for UP or DOWN with weighted confidence:

```
Signal Weights (ADX Disabled, default):
├── Price Momentum (30%) - Velocity, acceleration, RSI
├── Polymarket Momentum (20%) - Internal price confirmation
├── Order Flow (20%) - Buy/sell pressure from volume
├── Cross-Exchange Divergence (20%) - Polymarket vs Binance trend mismatch
└── Volume-Weighted Momentum (10%) - VWAP distance & quality

Signal Weights (ADX Enabled):
├── Price Momentum (25%) - Velocity, acceleration, RSI
├── Order Flow (15%) - Buy/sell pressure from volume
├── Cross-Exchange Divergence (20%) - Polymarket vs Binance trend mismatch
├── Volume-Weighted Momentum (10%) - VWAP distance & quality
├── ADX Trend Strength (15%) - Trend confirmation
└── Polymarket Momentum (15%) - Internal price confirmation
```

**Note**: The original documentation's "Base Signals (60%)" approach was replaced with a directional voting system where each signal contributes its strength weighted by confidence.

---

## 1. Price Momentum Analysis (30% weight, or 25% with ADX)

**Source:** Binance 1-minute candles (configurable lookback, default 15 minutes)

**Metrics:**
- **Velocity**: % price change over lookback period
- **Acceleration**: Change in velocity (trending faster or slower?)
- **RSI (14-period)**: Overbought/oversold indicator
- **Direction**: UP, DOWN, or NEUTRAL
- **Strength**: 0.0 to 1.0 confidence score

**Logic:**
```
IF Binance shows momentum (direction != NEUTRAL):
  score = momentum.strength (0-1)
  
  IF accelerating in same direction:
    score *= 1.2  (20% bonus)
  
  Signal contributes: score × weight × direction
```

**Example:**
- BTC up 1.5% in last 15 minutes with positive acceleration
- Momentum strength: 0.85, direction: UP
- Accelerating bonus: 0.85 × 1.2 = 1.02 (capped at 1.0)
- Contribution: 1.0 × 0.30 = 0.30 toward UP side
- **Signal:** Strong bullish momentum → Vote for UP

---

## 2. Order Flow Analysis (20% weight, or 15% with ADX)

**Source:** Binance 1-minute candles (recent volume data)

**Metrics:**
- **Buy Pressure**: Taker buy volume / Total volume
- **Volume Ratio**: 0-1 scale (0.5 = balanced)
- **Direction**: UP if > 0.5, DOWN if < 0.5

**Logic:**
```
score = abs(buy_pressure - 0.5) × 2.0  # Scale to 0-1
direction = "UP" if buy_pressure > 0.5 else "DOWN"

Signal contributes: score × weight × direction
```

**Example:**
- BTC shows 62% buy pressure (strong buying)
- Score: abs(0.62 - 0.5) × 2.0 = 0.24
- Direction: UP
- Contribution: 0.24 × 0.20 = 0.048 toward UP side
- **Signal:** Moderate bullish order flow → Vote for UP

---

## 3. Cross-Exchange Divergence (20% weight, constant)

**Source:** Binance price movement vs Polymarket implied probability

**Metrics:**
- **Binance Implied Probability**: Expected outcome based on recent price action
- **Polymarket Price**: Current UP token mid-price
- **Divergence**: Difference between the two (negative = Poly underpricing UP)
- **Opportunity**: BUY_UP, BUY_DOWN, or NEUTRAL

**Logic:**
```
score = min(abs(divergence) × 5.0, 1.0)  # 20% divergence = 1.0 score

direction = "UP" if opportunity == "BUY_UP"
           "DOWN" if opportunity == "BUY_DOWN"
           else "NEUTRAL"

Signal contributes: score × weight × direction
```

**Example:**
- Binance: BTC up 2% → Implies 70% chance of UP
- Polymarket: Pricing UP at 52%
- Divergence: -18% (Polymarket underpricing)
- Score: min(0.18 × 5.0, 1.0) = 0.90
- Direction: UP
- Contribution: 0.90 × 0.20 = 0.18 toward UP side
- **Signal:** Strong divergence → Vote for UP

---

## 4. Volume-Weighted Momentum (10% weight, constant)

**Source:** Binance VWAP analysis

**Metrics:**
- **VWAP Distance**: % distance from Volume-Weighted Average Price
- **Volume Trend**: Volume quality indicator
- **Momentum Quality**: 0-1 scale (high volume confirming trend = high quality)
- **Direction**: UP if above VWAP, DOWN if below

**Logic:**
```
score = momentum_quality (0-1)
direction = "UP" if vwap_distance > 0 else "DOWN"

Signal contributes: score × weight × direction
```

---

## 5. Polymarket Momentum (20% weight, or 15% with ADX)

**Source:** Polymarket CLOB 1-minute price history

**Metrics:**
- **Internal Velocity**: % price change on Polymarket over last few minutes
- **Internal Strength**: Confidence based on internal price action consistency
- **Direction**: UP or DOWN based on token price movement

**Logic:**
```
score = pm_momentum.strength (0-1)
direction = pm_momentum.direction

Signal contributes: score × weight × direction
```

### 🧠 Trend Agreement Bonus
If Binance momentum and Polymarket momentum agree on the direction, the strategy applies a **1.1x multiplier** to the final confidence. This reinforces high-conviction entries where external and internal markets are aligned.

### 🧠 Lead/Lag Indicator (Experimental)
The bot also tracks the "Lead/Lag" relationship between exchanges. If a signal shows strong cross-exchange consistency, a **1.2x multiplier** may be applied to the final confidence. If they diverge sharply, a **0.8x penalty** is applied to filter out noise.

---

## 6. ADX Trend Strength (15% weight, optional)


**Source:** Binance ADX indicator (configurable period and interval)

**Metrics:**
- **ADX Value**: Trend strength (0-100, typically 0-50 useful range)
- **Score**: Normalized ADX / 50.0 (capped at 1.0)
- **Direction**: Follows the strongest directional signal (momentum or divergence)

**Logic:**
```
IF ADX_ENABLED:
  score = min(adx_value / 50.0, 1.0)
  direction = momentum_dir if momentum != NEUTRAL else divergence_dir
  
  Signal contributes: score × weight × direction
```

**Example:**
- ADX: 35 (strong trend)
- Score: 35 / 50 = 0.70
- Momentum says UP
- Contribution: 0.70 × 0.15 = 0.105 toward UP side
- **Signal:** Strong trend confirmation → Vote for UP

---

## Final Decision Logic

After all signals vote, the strategy aggregates scores:

```python
up_total = sum of all signals voting UP (weighted)
down_total = sum of all signals voting DOWN (weighted)

IF up_total > down_total:
  bias = "UP"
  confidence = up_total - (down_total × 0.5)  # Penalty for conflicting signals
ELIF down_total > up_total:
  bias = "DOWN"
  confidence = down_total - (up_total × 0.5)
ELSE:
  bias = "NEUTRAL"
  confidence = 0.0

# Normalize confidence to 0-1
confidence = clamp(confidence, 0.0, 1.0)
```

**Trade Execution:**
- Only enter if `confidence >= MIN_EDGE` (default 0.565)
- Position size scales with confidence via `CONFIDENCE_SCALING_FACTOR`
- Direction determined by `bias` (UP or DOWN)

---

## Combined Signal Example

### Scenario: BTC Market Analysis

**Polymarket Data:**
- UP token: bid=0.51, ask=0.53 → p_up = 0.52
- Order book: Slight imbalance toward UP

**Binance Data:**
1. **Momentum**: +1.2% velocity, accelerating, RSI=58 → +0.08 edge
2. **Order Flow**: 58% buy pressure → +0.016 edge
3. **Divergence**: Binance implies 65% UP, Poly at 52% → +0.065 edge
4. **VWM**: 0.2% above VWAP with volume → +0.03 edge

**Signal Aggregation:**
```
Momentum (35%): strength=0.85, dir=UP, accelerating → 0.85 × 1.2 × 0.35 = 0.357
Order Flow (25%): buy_pressure=0.58 → (0.58-0.5)×2.0 × 0.25 = 0.040
Divergence (25%): div=-0.13 → 0.13×5.0 × 0.25 = 0.163 (capped)
VWM (15%): quality=0.60, dir=UP → 0.60 × 0.15 = 0.090

UP total = 0.357 + 0.040 + 0.163 + 0.090 = 0.650
DOWN total = 0.0

Confidence = 0.650 - (0.0 × 0.5) = 0.650 (65%)
Bias = UP
```

**Decision:**
- Confidence = 0.650 > MIN_EDGE (0.565) ✅
- Polymarket pricing UP at 52%
- **Trade:** BUY UP token
- **Position Size:** Base × (1 + confidence × CONFIDENCE_SCALING_FACTOR)
- **Reason:** All Binance signals strongly bullish (65% confidence), Polymarket underpricing at 52%

---

## Dynamic Scale-In Mechanism

The bot includes an intelligent scale-in mechanism that adds to winning positions as they approach expiry. This allows the bot to maximize profit on high-conviction trades while minimizing risk early in the window.

### Tiered Entry Timing

Instead of a fixed timer, the scale-in window is dynamically determined by the trade's confidence and current price:

- **Aggressive (High Confidence)**: If confidence $\ge 90\%$ and price $\ge \$0.80$, the bot can scale in as early as **12 minutes** before expiry.
- **Moderate**: If confidence $\ge 80\%$ and price $\ge \$0.70$, scale-in begins at **10 minutes**.
- **Default**: The standard scale-in window is **7.5 minutes** (450s) for any winning position meeting the minimum price threshold (default \$0.60).

### Scale-In Logic

1. **Verification**: The bot ensures the position is already in profit (Price > `SCALE_IN_MIN_PRICE`).
2. **Timing**: Checks if the remaining time in the 15-minute window is within the dynamically calculated threshold.
3. **Multiplier**: Applies `SCALE_IN_MULTIPLIER` (default 1.5x) to the base bet size for the additional entry.
4. **Safety**: Only scales in if no other active orders are pending for that market.

---

## Configuration

All features can be toggled in `.env`:

```bash
# Enable/disable individual signals
ENABLE_MOMENTUM_FILTER=YES    # Price velocity, acceleration, RSI (30% weight)
ENABLE_ORDER_FLOW=YES          # Buy/sell pressure analysis (20% weight)
ENABLE_DIVERGENCE=YES          # Cross-exchange mismatch detection (20% weight)
ENABLE_VWM=YES                 # Volume-weighted momentum (10% weight)
ADX=NO                         # Optional ADX trend filter (15% weight, adjusts others)

# Tuning parameters
MOMENTUM_LOOKBACK_MINUTES=15   # Momentum analysis window (default: 15)
MIN_EDGE=0.35                  # Decision threshold (35% confidence required)
CONFIDENCE_SCALING_FACTOR=5.0  # Position sizing multiplier (higher = more aggressive)
```

---

## Key Improvements Over Original Strategy

| Aspect | Old Strategy | Current Strategy |
|--------|-------------|------------------|
| **Data Sources** | Polymarket only | Polymarket + Binance (4-5 signals) |
| **Decision Model** | Price vs implied probability | Directional voting with weighted confidence |
| **Validation** | Circular (internal only) | External price action validates Polymarket prices |
| **Momentum** | Not considered | Real-time price velocity & acceleration (35%) |
| **Order Flow** | Ignored | Buy/sell pressure from actual trades (25%) |
| **Divergence** | N/A | Detects cross-exchange mispricings (25%) |
| **Volume** | Not weighted | Volume-weighted signals with quality filter (15%) |
| **Conflict Handling** | None | Conflicting signals penalize confidence |
| **Position Sizing** | Fixed percentage | Dynamic scaling based on signal confidence |
| **Predictive Power** | Low | High (multi-source forward-looking) |

---

## Risk Considerations

1. **API Rate Limits**: Binance has rate limits - excessive calls may cause throttling
2. **Latency**: Adding Binance calls increases execution time (managed with caching)
3. **False Signals**: Short-term Binance volatility might not reflect 15-minute outcome
4. **Market Conditions**: Works best in trending markets, less effective in choppy ranges

---

## Recommended Settings

**Aggressive (All signals enabled):**
```bash
ENABLE_MOMENTUM_FILTER=YES
ENABLE_ORDER_FLOW=YES
ENABLE_DIVERGENCE=YES
ENABLE_VWM=YES
MIN_EDGE=0.55
```

**Conservative (Core signals only):**
```bash
ENABLE_MOMENTUM_FILTER=YES
ENABLE_ORDER_FLOW=NO
ENABLE_DIVERGENCE=YES
ENABLE_VWM=NO
MIN_EDGE=0.57
```

**Legacy (Original strategy):**
```bash
ENABLE_MOMENTUM_FILTER=NO
ENABLE_ORDER_FLOW=NO
ENABLE_DIVERGENCE=NO
ENABLE_VWM=NO
MIN_EDGE=0.565
```

---

## Monitoring

The bot logs all signal components:

```
[BTC] Edge calculation:
  Base: p_up=0.5200 bid=0.5100 ask=0.5300 imb=0.5500
  Momentum: dir=UP str=0.650 adj=+0.0800
  OrderFlow: buy_pressure=0.580 adj=+0.0160
  Divergence: opp=BUY_UP div=-0.130 adj=+0.0650
  VWM: dist=0.200% quality=0.600 adj=+0.0300
  Final edge=0.5100
```

Watch for:
- **Strong alignment**: When all signals agree → High confidence
- **Divergence signals**: Often indicate the best opportunities
- **Momentum strength**: >0.7 = very strong trend
- **Buy pressure**: >0.60 or <0.40 = extreme imbalance

---

## Next Steps

1. **Backtest**: Compare new vs old strategy performance
2. **Optimize Weights**: Adjust signal weights based on historical accuracy
3. **Add More Signals**: Consider order book depth, whale trades, etc.
4. **Machine Learning**: Train ML model to optimize signal combination

---

## Questions?

This strategy fundamentally changes how we identify edges by using **external market data** (Binance) as the "truth" to validate **Polymarket pricing**. This is far more robust than circular internal pricing logic.
