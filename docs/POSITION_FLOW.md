# Position Flow Lifecycle

This document explains the complete lifecycle of a trading position in PolyFlup, from initial entry to final exit. With the **v0.6.0 atomic hedging strategy**, all trades are now simultaneous entry+hedge pairs.

---

## Table of Contents

1. [Overview](#overview)
2. [Position Lifecycle Stages](#position-lifecycle-stages)
3. [Module Breakdown](#module-breakdown)
4. [Decision Flow Diagram](#decision-flow-diagram)
5. [Atomic Hedging Strategy](#atomic-hedging-strategy)
6. [Emergency Liquidation](#emergency-liquidation)
7. [Pre-Settlement Exit](#pre-settlement-exit)
8. [Exit Outcomes](#exit-outcomes)

---

## Overview

PolyFlup uses an **atomic hedging strategy** where every trade consists of a simultaneous entry+hedge pair. This eliminates unhedged positions and ensures a guaranteed profit structure.

### Architecture

The execution system is organized into specialized modules:

```
src/trading/
├── execution.py           # Atomic pair placement & emergency liquidation
├── pre_settlement_exit.py # Pre-settlement exit logic (T-180s to T-45s)
└── strategy.py            # Signal calculation & confidence scoring
```

---

## Position Lifecycle Stages

### Stage 1: Signal Generation (`calculate_confidence_and_bias()`)

**File**: `src/trading/strategy.py`

**Purpose**: Analyzes market conditions to determine trade direction and confidence.

**Flow**:
1. Fetches Polymarket order book data
2. Fetches Binance market data (momentum, order flow, divergence, VWM)
3. Calculates confidence using additive or Bayesian method
4. Determines bias (UP or DOWN)

**Output**:
- `confidence`: 0.0 to 1.0 score
- `bias`: "UP", "DOWN", or "NEUTRAL"
- `signal_components`: Breakdown of all signals

---

### Stage 2: Atomic Pair Placement (`place_atomic_entry_and_hedge()`)

**File**: `src/trading/execution.py`

**Purpose**: Places simultaneous entry+hedge orders using batch API.

**Flow**:
1. **Price Calculation**:
   - Entry side: Uses best bid (MAKER pricing)
   - Hedge side: Calculated to ensure combined ≤ $0.99
   - Example: Entry UP @ $0.52 + Hedge DOWN @ $0.46 = $0.98 (1¢ edge)

2. **Order Type Selection**:
   - **First 3 attempts**: POST_ONLY (maker rebates)
   - **Taker pair only if** its net edge after fees is ≥ `MIN_NET_EDGE`; otherwise a maker pair at the bids

3. **Batch Submission**:
   - Both orders submitted simultaneously via batch API
   - Prevents race conditions and guarantees atomic placement

4. **Fill Monitoring** (`HEDGE_FILL_TIMEOUT_SECONDS`, 90s on 15m / 30s on 5m windows; the second leg trails the market once the first fills):
   - Polls every 5 seconds
   - Tracks fill status for both orders

5. **Outcome Handling**:
   - **Both fill**: Trade recorded, profit structure locked in ✅
   - **One fills**: Emergency liquidation triggered ⚠️
   - **Neither fills**: Both orders cancelled immediately ❌

**Example**:
```
🎯 [BTC] Placing atomic pair: UP @ $0.52 + DOWN @ $0.46 (combined $0.98, 1.0¢ edge)
   📊 [BTC] Both orders using MAKER (POST_ONLY) pricing
   ⏱️  [BTC] Monitoring fills for 120s (polling every 5s)...
   ✅ [BTC] Both orders filled! Atomic pair complete.
```

---

### Stage 3: Fill Monitoring & Outcome Handling

**File**: `src/trading/execution.py`

**Purpose**: Continuously monitors order fills and handles partial fills or timeouts.

**Possible Outcomes**:

#### Outcome A: Both Orders Fill (Success)
- Trade recorded in database
- Profit structure locked in: Combined price ≤ $0.99
- No further action needed (position held through resolution)

#### Outcome B: Entry Fills, Hedge Unfilled (Emergency)
- Entry position exposed
- **Immediate action**: Cancel hedge order
- **Emergency liquidation**: Sell entry side (see Stage 4)

#### Outcome C: Hedge Fills, Entry Unfilled (Emergency)
- Hedge position exposed
- **Immediate action**: Cancel entry order
- **Emergency liquidation**: Sell hedge side (see Stage 4)

#### Outcome D: Neither Order Fills (Timeout)
- **Immediate action**: Cancel both orders
- No positions held, no risk
- POST_ONLY crossing rejection: retry one tick below the ask

---

### Stage 4: Emergency Liquidation (If Needed)

**File**: `src/trading/execution.py`
**Function**: `emergency_sell_position()`

**Purpose**: Liquidates filled side when atomic pair fails to complete.

**Time-Aware Pricing Strategy**:

1. **PATIENT Mode** (>600s remaining):
   - Small price drops (1¢ steps)
   - Long waits (10-20s)
   - Goal: Maximize recovery

2. **BALANCED Mode** (300-600s remaining):
   - Moderate drops (2-5¢ steps)
   - Balanced waits (6-10s)
   - Goal: Balance recovery and urgency

3. **AGGRESSIVE Mode** (<300s remaining):
   - Rapid drops (5-10¢ steps)
   - Short waits (5-10s)
   - Goal: Ensure liquidation before expiry

**Special Cases**:
- **MIN_ORDER_SIZE (5.0 shares)**: If position < 5.0 shares
  - Check if winning (current_price > entry_price)
  - If winning: HOLD through resolution
  - If losing: ORPHAN (accept small loss)

**Example**:
```
⚠️  [BTC] Emergency sell triggered: UP position filled but DOWN hedge unfilled
🕒 [BTC] 450s remaining → BALANCED mode
💰 [BTC] Progressive pricing: bid - $0.02 (wait 8s)
✅ [BTC] Emergency sell complete @ $0.48 (recovered 92% of entry cost)
```

---

### Stage 5: Pre-Settlement Exit (Optional)

**File**: `src/trading/pre_settlement_exit.py`
**Function**: `pre_settlement_monitor()`

**Purpose**: Evaluates positions T-180s to T-45s before resolution and exits losing side early.

**Trigger Conditions**:
1. **Time window**: 180-45 seconds before resolution
2. **Confidence threshold**: Strategy signals > 80% confidence on one side
3. **Position exists**: Have both sides of a hedged pair

**Decision Logic**:
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

**Example**:
```
🎯 [BTC] Pre-settlement check (T-120s): Confidence UP 85%
💰 [BTC] Selling losing side (DOWN) @ $0.25
✅ [BTC] Keeping winning side (UP) for resolution @ $1.00
```

---

## Module Breakdown

### Signal Calculation (`strategy.py`)

**Function**: `calculate_confidence_and_bias(symbol, balance, verbose)`

**Purpose**: Analyzes market conditions to generate trading signals.

**Inputs**:
- `symbol`: Market symbol (BTC, ETH, XRP, SOL)
- `balance`: Available pUSD balance
- `verbose`: Logging level

**Outputs**:
- `confidence`: 0.0 to 1.0 score
- `bias`: "UP", "DOWN", or "NEUTRAL"
- `signal_components`: Breakdown of all signals

**Signal Sources**:
1. **Price Momentum** (30%): Binance velocity, acceleration, RSI
2. **Polymarket Momentum** (20%): Internal price action
3. **Order Flow** (20%): Buy/sell pressure from Binance
4. **Cross-Exchange Divergence** (20%): Polymarket vs Binance mismatch
5. **Volume-Weighted Momentum** (10%): VWAP analysis

---

### Atomic Pair Placement (`execution.py`)

**Function**: `place_atomic_entry_and_hedge(...)`

**Key Parameters**:
- `symbol`: Market symbol
- `side`: Entry side ("UP" or "DOWN")
- `confidence`: Signal confidence (0.0-1.0)
- `size`: Position size in shares
- `verbose`: Logging level

**Key Logic**:

```python
# 1. Calculate prices
entry_price = best_bid  # MAKER pricing
max_hedge_price = COMBINED_PRICE_THRESHOLD - entry_price
hedge_price = min(best_hedge_bid, max_hedge_price)

# 2. Check profitability
combined_price = entry_price + hedge_price
if combined_price > COMBINED_PRICE_THRESHOLD:
    reject_trade()  # Not profitable

# 3. Select order type
use_post_only = failures[symbol] < MAX_POST_ONLY_ATTEMPTS

# 4. Submit batch order
batch_order = [
    {"side": entry_side, "price": entry_price, "post_only": use_post_only},
    {"side": hedge_side, "price": hedge_price, "post_only": use_post_only}
]

# 5. Monitor fills
for t in range(0, HEDGE_FILL_TIMEOUT_SECONDS, HEDGE_POLL_INTERVAL_SECONDS):
    check_fill_status()
    if both_filled:
        return SUCCESS
    
# 6. Handle outcome
if timeout:
    cancel_both_orders()
elif one_filled:
    emergency_sell_position()
```

---

### Emergency Liquidation (`execution.py`)

**Function**: `emergency_sell_position(...)`

**Purpose**: Liquidates filled side when atomic pair fails to complete.

**Time-Aware Pricing**:

| Time Remaining | Mode | Price Drops | Wait Times | Goal |
|----------------|------|-------------|------------|------|
| >600s | PATIENT | 1¢ steps | 10-20s | Maximize recovery |
| 300-600s | BALANCED | 2-5¢ steps | 6-10s | Balance recovery/urgency |
| <300s | AGGRESSIVE | 5-10¢ steps | 5-10s | Ensure liquidation |

**Progressive Pricing Example** (PATIENT mode):
1. Sell at bid - $0.01 (wait 10s)
2. Sell at bid - $0.02 (wait 10s)
3. Sell at bid - $0.05 (wait 10s)
4. Sell at bid - $0.10 (wait 10s)
5. Continue until filled or timeout

**MIN_ORDER_SIZE Handling**:
```python
if size < MIN_ORDER_SIZE:  # 5.0 shares
    if current_price > entry_price:
        # Winning: HOLD through resolution
        mark_as_hold_min_size()
    else:
        # Losing: ORPHAN position
        mark_as_orphaned()
```

---

### Pre-Settlement Exit (`pre_settlement_exit.py`)

**Function**: `pre_settlement_monitor()`

**Purpose**: Continuously monitors positions and exits losing side early when confident.

**Execution Flow**:
1. Runs in background thread (starts on bot startup)
2. Checks every `PRE_SETTLEMENT_CHECK_INTERVAL` seconds (default 30s)
3. For each position in time window (T-180s to T-45s):
   - Fetches latest strategy signals
   - Calculates confidence for both sides
   - If confidence > threshold on one side:
     - Sells losing side
     - Keeps winning side for resolution

**Configuration**:
```env
ENABLE_PRE_SETTLEMENT_EXIT=YES
PRE_SETTLEMENT_MIN_CONFIDENCE=0.80
PRE_SETTLEMENT_EXIT_SECONDS=180
PRE_SETTLEMENT_CHECK_INTERVAL=30
```

---

## Decision Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                   ATOMIC HEDGING FLOW                           │
└─────────────────────────────────────────────────────────────────┘

1. SIGNAL GENERATION (strategy.py)
   ↓
   Calculate confidence and bias from multi-source signals
   - Binance momentum, order flow, divergence
   - Polymarket momentum
   - Volume-weighted signals
   ↓
   confidence >= MIN_EDGE?
   ├─ NO → Skip trade
   └─ YES → Continue

2. ATOMIC PAIR PLACEMENT (execution.py)
   ↓
   Calculate prices:
   - Entry: best_bid (MAKER pricing)
   - Hedge: calculated to ensure combined ≤ $0.99
   ↓
   combined_price <= COMBINED_PRICE_THRESHOLD?
   ├─ NO → Reject trade (not profitable)
   └─ YES → Continue
   ↓
   Select order type:
   - POST_ONLY (first 3 attempts) → maker rebates
   - GTC taker pair only when profitable after `0.07·p·(1−p)` fees
   ↓
   Submit batch order (both orders simultaneously)
   ↓
   Monitor fills for 120s (poll every 5s)
   ↓
   ┌─────────────────────────────────────┐
   │ OUTCOME HANDLING                    │
   └─────────────────────────────────────┘
   ├─ BOTH FILL → Trade complete ✅
   │               Record in database
   │               Profit structure locked in
   │               → Continue to Stage 3
   │
   ├─ ONE FILLS → Emergency liquidation ⚠️
   │              Cancel unfilled order
   │              → Emergency sell (Stage 4)
   │
   └─ NEITHER FILLS → Cancel both ❌
                       Increment POST_ONLY counter
                       No risk, try again

3. PRE-SETTLEMENT EXIT (pre_settlement_exit.py)
   ↓
   Runs in background thread
   Checks every 30s
   ↓
   For each position in time window (T-180s to T-45s):
   ├─ Calculate strategy confidence
   ├─ confidence_one_side > 80%?
   │  ├─ YES → Sell losing side early
   │  │         Keep winning side for resolution
   │  └─ NO → Hold both sides (wait)
   └─ Continue monitoring

4. EMERGENCY LIQUIDATION (if needed)
   ↓
   Determine time remaining:
   ├─ >600s → PATIENT mode
   │          Small drops (1¢), long waits (10-20s)
   │          Goal: Maximize recovery
   │
   ├─ 300-600s → BALANCED mode
   │             Moderate drops (2-5¢), balanced waits (6-10s)
   │             Goal: Balance recovery/urgency
   │
   └─ <300s → AGGRESSIVE mode
              Rapid drops (5-10¢), short waits (5-10s)
              Goal: Ensure liquidation
   ↓
   Progressive pricing:
   1. bid - $0.01 (wait)
   2. bid - $0.02 (wait)
   3. bid - $0.05 (wait)
   4. bid - $0.10 (wait)
   ...continue until filled
   ↓
   Special case: size < MIN_ORDER_SIZE (5.0 shares)?
   ├─ Winning → HOLD through resolution
   └─ Losing → ORPHAN position

5. RESOLUTION
   ↓
   Market settles at T=0
   ├─ Complete atomic pair → Full profit
   ├─ Pre-settlement exit → Partial profit
   └─ Emergency liquidation → Recovery
```

---

## Atomic Hedging Strategy

### Concept

The atomic hedging strategy places **simultaneous entry+hedge pairs** using batch API. Every trade consists of two orders:
1. **Entry order**: The side favored by strategy signals (UP or DOWN)
2. **Hedge order**: The opposite side to cap risk and ensure profitability

This eliminates unhedged positions and guarantees a profit structure when both orders fill.

### Benefits

1. **Guaranteed Profit Structure**: Entry + Hedge ≤ $0.99 ensures profit regardless of outcome
   - Example: UP @ $0.52 + DOWN @ $0.46 = $0.98 (1¢ edge per share)
   - If UP wins: Profit = $1.00 - $0.52 = $0.48, Loss = $0.46, Net = $0.02
   - If DOWN wins: Profit = $1.00 - $0.46 = $0.54, Loss = $0.52, Net = $0.02

2. **No Unhedged Positions**: Eliminates directional risk
3. **Maker Rebates**: POST_ONLY orders pay no fee and share a 20% rebate pool of taker fees
4. **Immediate Hedging**: No delay between entry and hedge placement

### Taker vs maker pairs

| Mode | When | Fees | Notes |
|------|------|------|-------|
| Taker pair (asks) | net edge after `0.07·p·(1−p)` per leg ≥ `MIN_NET_EDGE` | pays both legs | rare: asks usually sum above 1.00 |
| Maker pair (bids, POST_ONLY) | taker pair not profitable, both bids in the price band | none (+ rebate share) | the second leg trails the market after the first fills |

If one leg fills first the other is re-quoted every poll (best bid, capped at `1 − filled − MIN_NET_EDGE`). A taker hedge is used only when it beats flattening or holding.

### Combined Price Threshold

The bot enforces strict profitability:
```
entry_price + hedge_price ≤ COMBINED_PRICE_THRESHOLD
```

**Default**: $0.99 (1¢ edge minimum)

If combined price exceeds threshold, trade is rejected (not profitable).

### Database State

**After Successful Atomic Placement**:

| Trade ID | Side | Entry Price | Hedge Trade ID | Status |
|----------|------|-------------|----------------|--------|
| 1234 | UP | $0.52 | 1235 | FILLED |
| 1235 | DOWN | $0.46 | 1234 | FILLED |

Both trades linked via `hedge_trade_id` field.

---

## Emergency Liquidation

### When It's Triggered

Emergency liquidation occurs when an atomic pair **partially fills**:
- Entry order fills, hedge order unfilled
- Hedge order fills, entry order unfilled

### Time-Aware Pricing Strategy

The bot adapts its liquidation strategy based on **time remaining** in the 15-minute window:

#### PATIENT Mode (>600s remaining)
- **Pricing**: Small drops (1¢ steps from bid)
- **Wait Times**: Long waits (10-20s between attempts)
- **Goal**: Maximize recovery (plenty of time)

**Example Sequence**:
```
1. Sell at bid - $0.01 (wait 10s)
2. Sell at bid - $0.02 (wait 10s)
3. Sell at bid - $0.05 (wait 10s)
4. Sell at bid - $0.10 (wait 10s)
5. Continue...
```

#### BALANCED Mode (300-600s remaining)
- **Pricing**: Moderate drops (2-5¢ steps)
- **Wait Times**: Balanced waits (6-10s)
- **Goal**: Balance recovery and urgency

**Example Sequence**:
```
1. Sell at bid - $0.02 (wait 8s)
2. Sell at bid - $0.05 (wait 8s)
3. Sell at bid - $0.10 (wait 8s)
4. Continue...
```

#### AGGRESSIVE Mode (<300s remaining)
- **Pricing**: Rapid drops (5-10¢ steps)
- **Wait Times**: Short waits (5-10s)
- **Goal**: Ensure liquidation before expiry

**Example Sequence**:
```
1. Sell at bid - $0.05 (wait 5s)
2. Sell at bid - $0.10 (wait 5s)
3. Sell at $0.30 (wait 5s)
4. Sell at $0.20 (wait 5s)
5. Continue...
```

### MIN_ORDER_SIZE Handling

Polymarket enforces a **5.0 share minimum** for all orders. The bot handles small positions intelligently:

```python
if size < MIN_ORDER_SIZE:
    if current_price > entry_price:
        # Position is winning
        HOLD through resolution (let it profit at $1.00)
        mark_as_hold_min_size()
    else:
        # Position is losing
        ORPHAN position (accept small loss)
        mark_as_orphaned()
```

**Example**:
```
Position: 4.5 shares UP @ $0.60 entry
Current price: $0.75

Since 4.5 < 5.0:
  Check: $0.75 > $0.60 ✅ (winning)
  Action: HOLD through resolution
  Expected profit: 4.5 × ($1.00 - $0.60) = $1.80
```

---

## Pre-Settlement Exit

### Overview

Between T-180s and T-45s before market resolution, the bot evaluates positions and may **exit the losing side early** while keeping the winning side for full resolution profit.

### Why It's Useful

- **Maximize profit**: Winning side resolves at $1.00 (full profit)
- **Lock in gains**: Losing side sold before resolution (recovers some value)
- **Confidence-driven**: Only exits when strategy strongly favors one side

### Trigger Conditions

1. **Time window**: 180-45 seconds before resolution
2. **Confidence threshold**: Strategy signals > 80% (default) on one side
3. **Position exists**: Have both sides of a hedged pair

### Decision Logic

```python
# Fetch latest strategy signals
confidence_up, confidence_down = get_current_confidence(symbol)

if confidence_up > PRE_SETTLEMENT_MIN_CONFIDENCE:
    # Strong confidence UP is winning
    SELL DOWN position (losing side)
    HOLD UP position (winning side for $1.00)
    
elif confidence_down > PRE_SETTLEMENT_MIN_CONFIDENCE:
    # Strong confidence DOWN is winning
    SELL UP position (losing side)
    HOLD DOWN position (winning side for $1.00)
    
else:
    # Not confident enough
    HOLD both sides (wait for resolution)
```

### Example Scenario

```
Position: UP @ $0.52 + DOWN @ $0.46 (atomic pair)
Time: T-120s (2 minutes before resolution)

Strategy signals:
  - Confidence UP: 85%
  - Confidence DOWN: 15%

Decision: Confidence UP (85%) > threshold (80%)
Action:
  1. Sell DOWN @ $0.15 (losing side, recover some value)
  2. Hold UP (winning side, resolves at $1.00)

Result:
  - DOWN sold: -$0.46 + $0.15 = -$0.31 loss
  - UP resolves: -$0.52 + $1.00 = +$0.48 profit
  - Net profit: $0.48 - $0.31 = $0.17 per share
```

### Configuration

```env
ENABLE_PRE_SETTLEMENT_EXIT=YES       # Enable feature
PRE_SETTLEMENT_MIN_CONFIDENCE=0.80   # Min confidence to trigger (80%)
PRE_SETTLEMENT_EXIT_SECONDS=180      # Start checking at T-180s
PRE_SETTLEMENT_CHECK_INTERVAL=30     # Check every 30s
```

---

## Exit Outcomes

### 1. Complete Atomic Pair (Full Profit)

**Trigger**: Both entry and hedge orders fill

**Outcome**:
- Profit structure locked in: Combined price ≤ $0.99
- Both positions held through resolution
- Guaranteed profit regardless of outcome

**Example**:
```
Entry: UP @ $0.52 (100 shares)
Hedge: DOWN @ $0.46 (100 shares)
Combined: $0.98 (1¢ edge)

Resolution: UP wins
  - UP profit: 100 × ($1.00 - $0.52) = +$48.00
  - DOWN loss: 100 × ($0.46 - $0.00) = -$46.00
  - Net profit: $48.00 - $46.00 = +$2.00
```

---

### 2. Pre-Settlement Exit (Optimized Profit)

**Trigger**: Strategy confidence > 80% on one side (T-180s to T-45s)

**Outcome**:
- Losing side sold early (recovers some value)
- Winning side held for full resolution at $1.00
- Higher profit than complete atomic pair

**Example**:
```
Atomic pair: UP @ $0.52 + DOWN @ $0.46
Time: T-120s

Strategy: 85% confidence UP wins
Action: Sell DOWN @ $0.15

Resolution: UP wins
  - UP profit: $1.00 - $0.52 = +$0.48
  - DOWN early exit: $0.15 - $0.46 = -$0.31
  - Net profit: $0.48 - $0.31 = +$0.17 per share (17¢ vs 1¢ edge)
```

---

### 3. Emergency Liquidation (Partial Fill Recovery)

**Trigger**: One order fills, other unfilled (timeout or partial fill)

**Outcome**:
- Unfilled order cancelled immediately
- Filled position liquidated using time-aware pricing
- Recovery depends on mode (PATIENT/BALANCED/AGGRESSIVE)

**Example**:
```
Intended: UP @ $0.52 + DOWN @ $0.46
Actual: UP filled @ $0.52, DOWN unfilled

Emergency liquidation (BALANCED mode, 450s remaining):
  1. Sell UP @ bid - $0.02 = $0.50 (wait 8s) - filled
  2. Recovery: $0.50 - $0.52 = -$0.02 per share (small loss)
```

---

### 4. MIN_ORDER_SIZE Hold (Winning Small Position)

**Trigger**: Position < 5.0 shares and winning (current_price > entry_price)

**Outcome**:
- Position held through resolution (can't sell due to minimum)
- Resolves at $1.00 for profit

**Example**:
```
Position: 4.5 shares UP @ $0.60
Current price: $0.75 (winning)

Action: HOLD through resolution
Resolution: UP wins
  - Profit: 4.5 × ($1.00 - $0.60) = +$1.80
```

---

### 5. MIN_ORDER_SIZE Orphan (Losing Small Position)

**Trigger**: Position < 5.0 shares and losing (current_price ≤ entry_price)

**Outcome**:
- Position orphaned (can't sell, accept loss)
- Marked as "ORPHANED_MIN_SIZE"

**Example**:
```
Position: 4.2 shares DOWN @ $0.55
Current price: $0.25 (losing)

Action: ORPHAN position
Resolution: DOWN loses
  - Loss: 4.2 × ($0.55 - $0.00) = -$2.31 (small loss accepted)
```

---

### 6. Timeout (No Fills)

**Trigger**: Neither entry nor hedge fills after 120s

**Outcome**:
- Both orders cancelled immediately
- No positions held, no risk
- POST_ONLY crossing rejection: retry one tick below the ask

**Example**:
```
Intended: UP @ $0.52 + DOWN @ $0.46
Actual: Neither order filled after 120s

Action: Cancel both orders
Result: No trade, no risk, try again next window
```

---

## Configuration

Key environment variables controlling position flow:

```bash
# Entry
MIN_EDGE=0.35              # Minimum confidence to enter trade (35%)

# Atomic Hedging
COMBINED_PRICE_THRESHOLD=0.99        # Max combined price (default: 0.99)
HEDGE_FILL_TIMEOUT_SECONDS=120       # Fill timeout (default: 120s)
HEDGE_POLL_INTERVAL_SECONDS=5        # Poll interval (default: 5s)

# Pre-Settlement Exit
ENABLE_PRE_SETTLEMENT_EXIT=YES       # Enable early exit
PRE_SETTLEMENT_MIN_CONFIDENCE=0.80   # Min confidence (80%)
PRE_SETTLEMENT_EXIT_SECONDS=180      # Start at T-180s
PRE_SETTLEMENT_CHECK_INTERVAL=30     # Check every 30s

# Emergency Liquidation
EMERGENCY_SELL_ENABLE_PROGRESSIVE=YES # Time-aware pricing
EMERGENCY_SELL_WAIT_SHORT=5          # Aggressive mode wait (<300s)
EMERGENCY_SELL_WAIT_MEDIUM=8         # Balanced mode wait (300-600s)
EMERGENCY_SELL_WAIT_LONG=10          # Patient mode wait (>600s)
EMERGENCY_SELL_FALLBACK_PRICE=0.10   # Final fallback
```

---

## Summary

The PolyFlup position flow lifecycle with atomic hedging is a sophisticated risk-managed system:

1. **Signal Generation**: Multi-source confidence calculation (Binance + Polymarket)
2. **Atomic Placement**: Simultaneous entry+hedge pairs with guaranteed profit structure
3. **Fill Monitoring**: window-scaled timeout with immediate cancellation
4. **Pre-Settlement Exit**: Confidence-driven early exit of losing side (T-180s to T-45s)
5. **Emergency Liquidation**: Time-aware pricing adapts to remaining time
6. **Resolution**: Complete pairs maximize profit, partial fills recover capital

**Key Design Principles**:
- **No Unhedged Positions**: Every trade is an atomic pair
- **Guaranteed Profitability**: Combined price ≤ $0.99 ensures edge
- **Adaptive Execution**: fee-aware taker/maker pairs, trailing second leg, EV-based leg-risk handling, time-aware liquidation
- **Smart Recovery**: MIN_ORDER_SIZE handling, pre-settlement optimization
- **Risk Minimization**: Immediate cancellation, progressive pricing

This architecture eliminates directional risk while maintaining profitability through maker rebates and optimized exit strategies.
