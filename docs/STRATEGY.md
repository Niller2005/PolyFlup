# Enhanced Trading Strategy: Polymarket + Binance Integration

## Overview

The enhanced strategy combines **Polymarket order book data** with **real-time Binance market data** to make more informed trading decisions. Instead of relying solely on Polymarket's internal pricing (which can be circular), we now use external market signals to identify true mispricings.

## Confidence Calculation Methods

PolyFlup supports two confidence calculation methods for A/B testing:

### Additive Method (Default)

The additive method uses a directional voting system where signals vote for UP or DOWN with weighted confidence:

```
confidence = (dominant_total - (weaker_total × 0.2)) × lead_lag_bonus
```

**Characteristics:**
- Simple and intuitive
- Penalizes conflicting signals (reduces confidence when signals disagree)
- Applies lead/lag bonus (1.2x when exchanges agree, 0.8x when they disagree)
- Works well when signals are relatively independent

### Bayesian Method (New, Optional)

The Bayesian method uses proper probability theory with log-likelihood accumulation:

```
# Log-likelihood from a signal
evidence = (score - 0.5) × 2  # Map 0-1 score to -1 to +1
log_LR = evidence × 3.0 × quality_factor  # Calibration with quality

# Accumulate evidence starting from market prior
log_odds = ln(p_up / (1 - p_up)) + Σ(log_LR × weight)

# Convert to probability
confidence = 1 / (1 + exp(-log_odds))
```

**Characteristics:**
- Properly combines independent evidence using probability theory
- Starts with market prior (Polymarket's implied probability) as anchor
- Conflicting signals naturally cancel each other out
- Quality factors (0.8-1.5x) adjust log-likelihood based on signal reliability
- Mathematically grounded - confidence always bounded 0-1

**Quality Factors Applied to Log-Likelihood:**

| Signal | Quality Factor Conditions |
|--------|--------------------------|
| **Momentum** | RSI < 30 (oversold) = 1.3x, RSI > 70 (overbought) = 0.8x, Strength > 0.8 = 1.1x |
| **Order Flow** | Buy pressure > 70% or < 30% = 1.3x, Large trades consistent = 1.1x |
| **Divergence** | Larger divergence magnitude = 1.0-1.3x multiplier |
| **VWM** | Based on momentum_quality (0.8-1.3x) |
| **ADX** | ADX > 40 = 1.3x, ADX > 30 = 1.15x, ADX < 15 = 0.8x |
| **PM Momentum** | Default 1.0 (no built-in quality metrics) |

### A/B Testing Framework

Both methods are **always calculated and stored** in the database for comparison. This allows you to:

1. **Run with additive** (default) to collect baseline data
2. **After 100+ trades**, analyze which method performs better
3. **Switch to Bayesian** if it shows superior win rate

**Configuration:**
```env
BAYESIAN_CONFIDENCE=NO   # Use additive (default)
BAYESIAN_CONFIDENCE=YES  # Use Bayesian
```

**Comparing Methods:**
```sql
SELECT 
    CASE WHEN bayesian_confidence > additive_confidence THEN 'Bayesian higher'
         WHEN additive_confidence > bayesian_confidence THEN 'Additive higher'
         ELSE 'Equal' END as method,
    AVG(edge) as avg_edge,
    COUNT(*) as total,
    SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) as wins,
    CAST(SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) AS REAL) / COUNT(*) as win_rate
FROM trades
WHERE settled = 1
GROUP BY method;
```

**Database Columns (Migration 007):**
- `additive_confidence`: Additive method confidence value
- `additive_bias`: Additive method directional bias (UP/DOWN/NEUTRAL)
- `bayesian_confidence`: Bayesian method confidence value
- `bayesian_bias`: Bayesian method directional bias (UP/DOWN/NEUTRAL)
- `market_prior_p_up`: Polymarket orderbook probability (market prior)

### Key Differences

| Aspect | Additive | Bayesian |
|--------|----------|----------|
| **Mathematical Foundation** | Heuristic voting | Probability theory |
| **Starting Point** | Zero baseline | Market prior (p_up) |
| **Conflict Handling** | Penalizes (reduces confidence) | Cancels (log-odds sum) |
| **Quality Adjustment** | Multiplier on weighted scores | Multiplier on log-likelihood |
| **Bounded Confidence** | Requires manual clamping | Naturally 0-1 |
| **Signal Independence** | Assumes independence | Properly combines |

---

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

### 🧠 Lead/Lag Indicator
The bot tracks the "Lead/Lag" relationship between exchanges using Polymarket's native momentum data. When Binance and Polymarket momentum signals are consistent (both pointing in the same direction), a **1.2x multiplier** is applied to the final confidence. When they diverge sharply (pointing in opposite directions), a **0.8x penalty** reduces confidence to filter out conflicting signals. This feature is automatically active when `ENABLE_MOMENTUM_FILTER=YES`.

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

## Scale-In (removed)

The dynamic scale-in mechanism was removed with the move to atomic hedging: adding to one side would create unhedged exposure. The `SCALE_IN_*`, `EXIT_*` and reward-optimisation settings no longer exist.

---

## Position Sizing

```
available  = wallet cash − cash reserved by entries still being placed
exposure   = cost of open positions (entry + hedge for hedged pairs)
equity     = wallet cash + exposure
headroom   = MAX_EXPOSURE_PERCENT% of equity − exposure − reserved
allocation = min(BET_PERCENT% of available, headroom)      (≤ MAX_SIZE shares)
```

Each new entry is smaller as cash is used and no entry can push exposure over the cap (`src/trading/exposure.py`). With `KELLY_SIZING=YES` the fraction comes from Kelly instead:
- **Pairs**: gain `MIN_NET_EDGE` with probability `1 − q`, lose `KELLY_LEG_LOSS` with probability `q` (leg-fail rate `KELLY_LEG_FAIL_RATE`): `f* = (1−q)/loss − q/gain`. With a 1¢ edge and a 4¢ loss this is zero above about 17% leg failures, so **Kelly refuses pairs until the leg-fail rate is measured below that**.
- **Late-window bets**: `f* = (p − cost)/(1 − cost)` with `cost = ask + fee` and `p` reduced by `KELLY_PROB_HAIRCUT`.
Both use `KELLY_FRACTION` of full Kelly (default ¼), capped at `KELLY_MAX_FRACTION`. A Kelly stake below the 5-share minimum is skipped, never rounded up. Risk profiles scale all of this ([RISK_PROFILES.md](RISK_PROFILES.md)).

---

## Late-Window Conviction Mode

In the last `LATE_WINDOW_SECONDS` of a window the side ahead of the strike usually wins:

```
z      = ln(spot/strike) / (σ₁ₘ · √(seconds_left/60))
p_use  = min(Φ(|z|), measured hold rate for that |z| bucket)
EV/sh  = p_use − ask − 0.07·ask·(1 − ask)
```

Strike and spot come from the Chainlink feed Polymarket resolves on (PolyBolt `price.crypto.twap`/`price.crypto`, authenticated with the CLOB API credentials); without it Binance is used with a doubled distance filter because of basis risk. A 7-day Binance study of the T-60s leader gave hold rates of about 65–73% for |z| < 0.5, 85% for 0.5–1, 92–94% for 1–1.5, 97% for 1.5–2 and ≥ 99% above 2. Every evaluation is written to `shadow_signals` and resolved against the real outcome; orders are placed only with `LATE_WINDOW_LIVE=YES`. Judge it with `uv run analyze_strategy.py` (≥ 100 resolved ENTER signals with positive realized EV before going live).

---

## Atomic Hedging Execution

### Overview

PolyFlup uses an **atomic hedging strategy** where every trade consists of a simultaneous entry+hedge pair. This eliminates unhedged positions and ensures a guaranteed profit structure when both orders fill.

### Why Atomic Hedging?

Traditional single-side entries expose the bot to directional risk. By placing both sides simultaneously:
- **Guaranteed profit structure**: Entry + Hedge ≤ $0.99 ensures profit regardless of outcome
- **No unhedged positions**: Eliminates the risk of holding a single-side position
- **Immediate hedging**: No delay between entry and hedge placement

### Fee Model (CLOB V2)

Taker fee per share: `0.07 × p × (1 − p)` in pUSD, for BUY and SELL. Makers pay nothing and share a 20% rebate pool. At p=0.50 that is 1.75¢ per share per taker leg (3.5% of notional); it falls toward the extremes (0.7% at p=0.90). Live order books show best asks summing to 1.00–1.02, so a taker pair below 0.97 almost never exists and, near 50/50, two taker legs lose money even at 0.97.

### Execution Flow

1. **Signal Calculation**: confidence and bias pick the entry side.
2. **Pricing** (`place_entry_and_hedge_atomic`):
   - **Taker pair** from the ask side, only if `1 − a − b − fee(a) − fee(b) ≥ MIN_NET_EDGE`.
   - Otherwise a **maker pair**: POST_ONLY bids on both legs from a fresh order book, only while both bids lie in `[MAKER_PAIR_PRICE_BAND, 1 − band]` (a lopsided book means the window is decided).
   - If neither has the required net edge the trade is skipped.
3. **Batch Order Placement**: both orders go out together; maker orders never cross the book.
4. **Fill Monitoring**: polls every 5s for up to `HEDGE_FILL_TIMEOUT_SECONDS` (scaled to the window: 90s on 15m, 30s on 5m).
5. **Trailing**: once one leg has filled, the other is cancelled and re-quoted at the best bid (plus up to `TRAIL_MAX_TICKS` as time passes). The price never exceeds `1 − filled + TRAIL_MAX_LOCK_LOSS` (default 3¢ above break-even): completing a pair as a maker at a small certain loss beats the ~14¢/share a leg-out costs, and completing passively saves the taker fee plus spread that flattening pays.
6. **Leg risk**: if the cap makes trailing impossible, a taker hedge is bought only when its net result beats both selling the filled leg and holding it (market-implied value); otherwise the resting order stays until the timeout.
7. **Outcome**: both filled → trade saved with the final (trailed) prices; one filled → emergency liquidation (below); neither → both cancelled.

### POST_ONLY and failures

Maker orders are priced at or below the best bid so they rest instead of crossing. A crossing rejection (market moved between quote and post) triggers a retry capped one tick below the ask. The retry's price is what gets saved with the trade.

### Combined Price Threshold

The bot enforces a strict profitability check:
```
entry_price + hedge_price ≤ COMBINED_PRICE_THRESHOLD (default $0.99)
```

Example:
- Entry: BUY UP @ $0.52
- Hedge: BUY DOWN @ $0.46
- Combined: $0.98 ≤ $0.99 ✅
- Guaranteed profit: $0.01 per share (1¢ edge)

If the combined price exceeds the threshold, the trade is rejected.

---

## Pre-Settlement Exit Strategy

### Overview

Between T-180s and T-45s before market resolution, the bot evaluates positions and may exit the **losing side early** while keeping the winning side for full resolution profit.

### Trigger Conditions

1. **Time window**: 180-45 seconds before resolution
2. **Confidence threshold**: Strategy signals show > 80% confidence on one side
3. **Position exists**: Have both sides of a hedged pair

### Decision Logic

```
IF confidence_up > 80%:
    SELL DOWN position (losing side)
    HOLD UP position (winning side)
ELIF confidence_down > 80%:
    SELL UP position (losing side)
    HOLD DOWN position (winning side)
ELSE:
    HOLD both sides (wait for resolution)
```

### Benefits

- **Maximize profit**: Winning side resolves at $1.00
- **Lock in gains**: Losing side sold before resolution (recovers some value)
- **Confidence-driven**: Only exits when strategy strongly favors one side

### Configuration

```env
ENABLE_PRE_SETTLEMENT_EXIT=YES       # Enable/disable feature
PRE_SETTLEMENT_MIN_CONFIDENCE=0.80   # Min confidence to trigger
PRE_SETTLEMENT_EXIT_SECONDS=180      # Start checking at T-180s
PRE_SETTLEMENT_CHECK_INTERVAL=30     # Check every 30s
```

---

## Time-Aware Emergency Liquidation

### Overview

When atomic pairs partially fill or timeout, the bot must liquidate the filled side. The liquidation strategy adapts based on **time remaining** in the 15-minute window.

### Liquidation Modes

#### PATIENT Mode (>600s remaining)
- **Goal**: Maximize recovery
- **Pricing**: Small price drops (1¢ steps)
- **Wait times**: Long waits (10-20s) between attempts
- **Use case**: Plenty of time to find a buyer

#### BALANCED Mode (300-600s remaining)
- **Goal**: Balance recovery and urgency
- **Pricing**: Moderate drops (2-5¢ steps)
- **Wait times**: Balanced waits (6-10s)
- **Use case**: Moderate time pressure

#### AGGRESSIVE Mode (<300s remaining)
- **Goal**: Ensure liquidation
- **Pricing**: Rapid drops (5-10¢ steps)
- **Wait times**: Short waits (5-10s)
- **Use case**: Race against expiry

### Progressive Pricing Example

**PATIENT** (>600s):
```
1. bid - $0.01 (wait 10s)
2. bid - $0.02 (wait 10s)
3. bid - $0.05 (wait 10s)
4. bid - $0.10 (wait 10s)
...
```

**BALANCED** (300-600s):
```
1. bid - $0.02 (wait 8s)
2. bid - $0.05 (wait 8s)
3. bid - $0.10 (wait 8s)
...
```

**AGGRESSIVE** (<300s):
```
1. bid - $0.05 (wait 5s)
2. bid - $0.10 (wait 5s)
3. $0.30 (wait 5s)
4. $0.20 (wait 5s)
...
```

### Configuration

```env
EMERGENCY_SELL_ENABLE_PROGRESSIVE=YES # Enable time-aware pricing
EMERGENCY_SELL_WAIT_SHORT=5          # Wait time for aggressive mode
EMERGENCY_SELL_WAIT_MEDIUM=8         # Wait time for balanced mode
EMERGENCY_SELL_WAIT_LONG=10          # Wait time for patient mode
EMERGENCY_SELL_FALLBACK_PRICE=0.10   # Final fallback price
```

---

## MIN_ORDER_SIZE Smart Hold Logic

### The Problem

Polymarket enforces a minimum order size of **5.0 shares**. Positions smaller than this cannot be sold on the exchange.

### The Solution

The bot checks if small positions are **winning or losing**:

#### If Winning (current_price > entry_price)
- **Action**: HOLD through resolution
- **Rationale**: Let it resolve at $1.00 for profit
- **Status**: Marked as "HOLD_MIN_SIZE_WINNING"

#### If Losing (current_price ≤ entry_price)
- **Action**: ORPHAN the position
- **Rationale**: Can't sell, accept small loss
- **Status**: Marked as "ORPHANED_MIN_SIZE"

### Example

```
Position: 4.5 shares UP @ $0.60 entry
Current price: $0.75

Since 4.5 < 5.0 (MIN_ORDER_SIZE):
  Check if winning: $0.75 > $0.60 ✅
  Action: HOLD through resolution
  Expected profit: 4.5 × ($1.00 - $0.60) = $1.80
```

---

## Configuration

All features can be toggled in `.env`:

```bash
# Confidence Calculation Method
BAYESIAN_CONFIDENCE=NO         # Use Bayesian method (YES) or Additive method (NO)
                               # Both always calculated for A/B testing

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
| **Confidence Method** | Single additive formula | Dual calculation (Additive + Bayesian) for A/B testing |
| **Momentum** | Not considered | Real-time price velocity & acceleration (35%) |
| **Order Flow** | Ignored | Buy/sell pressure from actual trades (25%) |
| **Divergence** | N/A | Detects cross-exchange mispricings (25%) |
| **Volume** | Not weighted | Volume-weighted signals with quality filter (15%) |
| **Conflict Handling** | None | Conflicting signals penalize confidence |
| **Quality Factors** | N/A | 0.8-1.5x multipliers based on signal reliability |
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

**Bayesian Confidence (experimental):**
```bash
BAYESIAN_CONFIDENCE=YES  # Use Bayesian method
ENABLE_MOMENTUM_FILTER=YES
ENABLE_ORDER_FLOW=YES
ENABLE_DIVERGENCE=YES
ENABLE_VWM=YES
MIN_EDGE=0.40            # Lower threshold for Bayesian (better calibration)
```

**Legacy (Original strategy):**
```bash
ENABLE_MOMENTUM_FILTER=NO
ENABLE_ORDER_FLOW=NO
ENABLE_DIVERGENCE=NO
ENABLE_VWM=NO
MIN_EDGE=0.565
```

## Multi-Confirmation System

After confidence is calculated (by either additive or Bayesian method), the bot applies a graduated multi-confirmation filter for high-confidence trades:

### How It Works

1. **Trigger**: Runs when confidence > 60%
2. **Key Indicators**: Analyzes the 5 primary signals:
   - Price Momentum (35%)
   - Polymarket Momentum (20%)
   - Order Flow (15%)
   - Cross-Exchange Divergence (15%)
   - ADX Trend Strength (15%)

3. **Graduated Reduction**:
   - **60-70% confidence**: Requires 3/5 key signals agreeing
   - **70-80% confidence**: Requires 2/5 key signals agreeing
   - **80-85% confidence**: No reduction (all signals agree)
   - **85%+ confidence**: No reduction (very high confidence)

### Why This Matters

The multi-confirmation system acts as a sanity check for very high confidence values (>85%). It prevents situations where a single strong signal (e.g., divergence) creates unrealistic confidence without broader signal agreement.

**Note**: This system runs **after** the Bayesian calculation, so quality factors and log-likelihood accumulation are applied first. The multi-confirmation filter then validates the final result.

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

When `BAYESIAN_CONFIDENCE=YES`, the bot also logs:

```
[BTC] 🔬 Using Bayesian confidence (p_up=0.520): 68.3% | Additive would be: 51.0%
```

This shows:
- Active method's confidence
- Market prior (p_up) used by Bayesian
- What additive method would have calculated (for comparison)

Watch for:
- **Strong alignment**: When all signals agree → High confidence
- **Divergence signals**: Often indicate the best opportunities
- **Momentum strength**: >0.7 = very strong trend
- **Buy pressure**: >0.60 or <0.40 = extreme imbalance
- **Confidence method difference**: Large gaps between Bayesian and additive may indicate which method is better calibrated

---

## Next Steps

1. **Backtest**: Compare new vs old strategy performance
2. **Optimize Weights**: Adjust signal weights based on historical accuracy
3. **Add More Signals**: Consider order book depth, whale trades, etc.
4. **Machine Learning**: Train ML model to optimize signal combination

---

## Questions?

This strategy fundamentally changes how we identify edges by using **external market data** (Binance) as the "truth" to validate **Polymarket pricing**. This is far more robust than circular internal pricing logic.
