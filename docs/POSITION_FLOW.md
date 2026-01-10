# Position Flow Lifecycle

This document explains the complete lifecycle of a trading position in PolyFlup, from initial entry to final exit. It covers all position management modules, their triggers, decision flows, and the hedged reversal strategy.

---

## Table of Contents

1. [Overview](#overview)
2. [Position Lifecycle Stages](#position-lifecycle-stages)
3. [Module Breakdown](#module-breakdown)
4. [Decision Flow Diagram](#decision-flow-diagram)
5. [Hedged Reversal Strategy](#hedged-reversal-strategy)
6. [Balance Syncing & Repair](#balance-syncing--repair)
7. [Exit Outcomes](#exit-outcomes)

---

## Overview

PolyFlup uses a modular position management system that runs on a **1-second monitoring cycle**. Each position progresses through a series of stages with automated checks for:

- **Entry**: Initial trade placement when conditions are met
- **Monitoring**: Continuous evaluation of position health
- **Adjustments**: Scale-in (add to position) or reversal (opposite side hedge)
- **Exit**: Profitable exit via limit orders or stop loss

### Architecture

The position manager is organized into specialized modules:

```
src/trading/position_manager/
├── entry.py       # First entry logic
├── monitor.py     # Main monitoring loop (1s cycle)
├── stop_loss.py   # Stop loss enforcement
├── reversal.py    # Opposite side hedging
├── scale.py       # Scale-in management
└── exit.py        # Exit plan placement & repair
```

---

## Position Lifecycle Stages

### Stage 1: Entry (`execute_first_entry()`)

**File**: `src/trading/position_manager/entry.py`

**Purpose**: Places the initial position when trading conditions are favorable.

**Flow**:
1. Called from `bot.py` during entry evaluation cycles
2. Calls `_prepare_trade_params()` to check all filters:
   - Signal confidence threshold (`MIN_EDGE`)
   - Position spacing rules
   - Available balance
   - Market conditions
3. If valid parameters returned, executes trade via `execute_trade()`
4. Trade is recorded in database with initial state:
   - `order_status = "PENDING"` or `"OPEN"`
   - `settled = 0`
   - `exited_early = 0`

**Example**:
```
🚀 [BTC] Entry triggered: UP @ $0.52, confidence=0.65, size=100.00
✅ [BTC] Trade #1234 BUY UP @ $0.52 filled (100 shares)
```

---

### Stage 2: Monitoring (`check_open_positions()`)

**File**: `src/trading/position_manager/monitor.py`

**Purpose**: Runs every 1 second to evaluate all open positions and trigger appropriate actions.

**Frequency**: 1-second cycle (real-time monitoring with WebSocket price updates)

**Sequence of Checks** (per position, in order):
1. **Stop Loss Check** (`_check_stop_loss()`) → May trigger reversal
2. **Scale-In Check** (`_check_scale_in()`) → Add to winning position
3. **Exit Plan Check** (`_check_exit_plan()`) → Place/manage limit sell

**Optimizations**:
- **WebSocket price feeds**: Real-time midpoint prices via Market Channel subscriptions
- **Batch price fetching**: All token prices fetched in single API call when WebSocket unavailable
- **Batch scoring**: Checks order scoring in batch for multiple positions
- **Lock mechanism**: Prevents concurrent monitoring cycles
- **Notification batching**: WebSocket updates for fills, cancels, and P&L changes

---

## Module Breakdown

### Entry Management (`entry.py`)

**Function**: `execute_first_entry(symbol, balance, verbose)`

**When Called**:
- From `bot.py` during entry evaluation cycles (every 30-60 seconds)

**Key Logic**:
```python
trade_params = _prepare_trade_params(symbol, balance, verbose)
if trade_params:
    return execute_trade(trade_params, is_reversal=False)
```

**Output**:
- Returns `trade_id` if trade executed
- Returns `None` if conditions not met

---

### Stop Loss Management (`stop_loss.py`)

**Function**: `_check_stop_loss(...)`

**When Called**:
- Every monitoring cycle (1s) for each open position
- **Priority 1** in the monitoring sequence

**Trigger Condition**:
```python
dynamic_trigger = min(STOP_LOSS_PRICE, entry_price - 0.10)
if current_price <= dynamic_trigger:
    # Stop loss check triggered
```

**Default Config**:
- `STOP_LOSS_PRICE = 0.30` ($0.30 floor)
- Minimum headroom: $0.10 below entry price
- Example: Entry at $0.60 → trigger at $0.30 (min of 0.30, 0.60-0.10=0.50)

**Triple Check Logic** (for hedged reversals):

1. **Immediate Floor** (no cooldown):
   - If `current_price <= $0.15` → execute stop loss immediately

2. **Time Cooldown** (after reversal):
   - Must wait **120 seconds** after reversal before stopping out
   - Logs every 60s to show progress

3. **Strategy Confirmation**:
   - Checks if strategy now favors opposite side with `confidence > 30%`
   - Only stops loss if strategy flip is confirmed

**Special Cases**:
- **Spot price check**: If midpoint is low but spot price is winning side, holds position
- **Age check**: Positions must be at least 30s old to avoid noise
- **Enhanced balance sync**: Uses cross-validation between balance API and position data (symbol-specific tolerance)
- **XRP handling**: Special validation for XRP positions with lower API trust factor

**Example**:
```
🛑 [BTC] #1234 CRITICAL FLOOR hit ($0.14). Executing immediate stop loss.
```

---

### Reversal Management (`reversal.py`)

**Functions**:
- `check_and_trigger_reversal()` - Main entry point
- `_trigger_price_based_reversal()` - Executes reversal trade

**When Called**:
- From `_check_stop_loss()` when price drops below trigger
- Only if `ENABLE_REVERSAL = YES`

**Trigger Condition**:
```python
if current_price <= min(STOP_LOSS_PRICE, entry_price - 0.10):
    if not reversal_triggered:
        _trigger_price_based_reversal()
```

**Reversal Logic**:
1. **Determine opposite side**:
   - Original: UP → Reversal: DOWN
   - Original: DOWN → Reversal: UP

2. **Check for existing position**:
   - If already have opposite side in this window, link as hedge instead

3. **Calculate reversal price**:
   - Buying DOWN: `1.0 - UP_best_ask`
   - Buying UP: `UP_best_bid`

4. **Position sizing**:
   - If strategy agrees with reversal: uses strategy's confidence
   - If strategy disagrees: uses default 40% confidence

5. **Execute trade** as reversal (`is_reversal=True`)

6. **Mark original trade**:
   - `reversal_triggered = 1`
   - `reversal_triggered_at = current_time`

**Example**:
```
🔄 [BTC] #1234 UP midpoint $0.28 <= $0.30 trigger. INITIATING REVERSAL.
⚔️ Reversal trade #1235 opened for BTC DOWN
```

---

### Scale-In Management (`scale.py`)

**Function**: `_check_scale_in(...)`

**When Called**:
- Every monitoring cycle (1s) after stop loss check
- **Priority 2** in the monitoring sequence

**Conditions Required**:
1. `ENABLE_SCALE_IN = YES`
2. Position is already filled (`buy_status in ["FILLED", "MATCHED"]`)
3. Not already scaled in (`scaled_in = 0`)
4. Price in valid range (`SCALE_IN_MIN_PRICE` to `SCALE_IN_MAX_PRICE`)
5. **Winning side** on spot price
6. **Time window** (dynamic based on confidence)

**Default Config**:
- `SCALE_IN_MIN_PRICE = 0.50`
- `SCALE_IN_MAX_PRICE = 0.75`
- `SCALE_IN_TIME_LEFT = 450` (7.5 minutes)
- `SCALE_IN_MULTIPLIER = 1.5` (adds 50% of original size)

**Dynamic Timing**:
| Confidence | Price | Scale-in Window |
|------------|-------|-----------------|
| ≥ 90%      | ≥ $0.80 | Up to 12 min (720s) |
| ≥ 80%      | ≥ $0.70 | Up to 9 min (540s) |
| ≥ 70%      | ≥ $0.65 | Up to 7 min (420s) |
| Default    | ≥ $0.60 | 7.5 min (450s) |

**Order Type**: **MAKER** (limit) order
- Joins the bid to avoid taker fees
- Earns rebates if filled as maker
- Uses WebSocket bid price for best pricing

**Winner Check**:
- UP position: spot price ≥ target price
- DOWN position: spot price ≤ target price

**Example**:
```
📈 [BTC] Trade #1234 UP | 📈 SCALE IN triggered (Maker Order): size=150.00, price=$0.75, 420s left
📈 [BTC] Trade #1234 UP | ✅ SCALE IN order filled: 150.00 shares @ $0.7500
```

---

### Exit Plan Management (`exit.py`)

**Function**: `_check_exit_plan(...)`

**When Called**:
- Every monitoring cycle (1s) for each position
- **Priority 3** in the monitoring sequence (always runs)

**Purpose**:
- Place limit sell orders at `EXIT_PRICE_TARGET` (default $0.95)
- Manage existing exit orders
- Repair size mismatches after scale-in
- Adopt orphaned orders from exchange

**Key Features**:

#### 1. Order Placement
- Places when no existing order in database
- No minimum position age (places immediately after fill)
- Cooldown: 10s between placement attempts
- **Reward optimization**: Automatically adjusts price via `check_scoring` API to earn liquidity rewards (if enabled)

#### 2. Balance Syncing (Bi-Directional Healing with Enhanced Validation)
- **Sync if balance > size + 0.0001**: Always sync immediately (we bought more)
- **Periodic sync**: If `age > 60s` and `scale_in_age > 60s`, sync any discrepancies
- **Cross-validation**: Validates balance API against position data from Data API
- **Symbol-specific tolerance**: XRP and other unreliable symbols use position data as fallback
- **Grace period**: Waits 15 minutes (configurable) before settling zero-balance positions

#### 3. Order Adoption
- Checks exchange for existing SELL orders before placing new one
- If found, adopts order into database (prevents duplicates)

#### 4. Size Repair
- Compares exit plan size to current position size
- If mismatch detected:
  1. Cancels existing order
  2. Places new order with correct size
- Accounts for scale-in changes automatically

#### 5. Minimum Size Handling
- Enforces `MIN_SIZE = 5.0` shares
- If size < MIN_SIZE but balance ≥ MIN_SIZE, bumps to MIN_SIZE
- If both size and balance < MIN_SIZE, skips and retries next cycle

**Example**:
```
🔧 [BTC] #1234 Syncing size to match balance: 100.00 -> 250.00
🔧 [BTC] #1234 Exit plan size repaired: 100.00 -> 250.00
```

---

## Decision Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                     Position Entry                              │
│              execute_first_entry()                              │
│                   ↓                                             │
│              Trade Placed                                        │
│                   ↓                                             │
│         [1-Second Monitoring Cycle]                             │
│                   ↓                                             │
┌─────────────────────────────────────────────────────────────────┐
│  1. STOP LOSS CHECK                                              │
│     current_price <= min(0.30, entry_price - 0.10)?            │
│        │                                                          │
│        ├─ YES ──→ Reversal triggered?                            │
│        │            │                                            │
│        │            ├─ NO → Trigger reversal trade              │
│        │            │        (opposite side, is_reversal=True)   │
│        │            │        Mark: reversal_triggered=1         │
│        │            │        Return (wait for next cycle)       │
│        │            │                                            │
│        │            └─ YES → Triple check:                       │
│        │                     ├─ Price <= $0.15? → Stop loss     │
│        │                     ├─ < 120s since reversal? → Wait    │
│        │                     └─ Strategy favors opposite? → SL   │
│        │                     Otherwise: Continue (hold hedge)   │
│        │                                                         │
│        └─ NO ──→ Continue to scale-in check                     │
│                   ↓                                             │
│  2. SCALE-IN CHECK                                               │
│     Conditions met?                                              │
│        ├─ Price in range ($0.50-$0.75)?                          │
│        ├─ Winning side on spot?                                  │
│        ├─ Time window met? (dynamic based on confidence)         │
│        └─ Already scaled in?                                     │
│                   ↓                                             │
│        ├─ YES ──→ Place MAKER limit order                       │
│        │            (adds SCALE_IN_MULTIPLIER × original size)   │
│        │            Update: scaled_in=1, size increased          │
│        │            Continue to exit plan (size may change)     │
│        │                                                         │
│        └─ NO ──→ Continue to exit plan check                    │
│                   ↓                                             │
│  3. EXIT PLAN CHECK                                              │
│     (Always runs for every position)                            │
│        ├─ Has limit_sell_order_id?                              │
│        │   ├─ YES → Check status, repair size if needed        │
│        │   └─ NO → Place limit sell at EXIT_PRICE_TARGET        │
│        │                                                           │
│        └─ Exit order fills?                                      │
│            → Mark settled=1, exited_early=1                      │
│            → Return (position closed)                            │
│                   ↓                                             │
│  Return to top of monitoring loop (next position or next cycle) │
└─────────────────────────────────────────────────────────────────┘
```

---

## Hedged Reversal Strategy

### Concept

The hedged reversal strategy is a risk management technique that opens a position on the **opposite side** before stopping out a losing trade. This provides two benefits:

1. **Cap losses**: If market continues in losing direction, reversal position profits
2. **Market timing**: Allows waiting for confirmation before closing losing position

### Sequence

```
Original Trade: BUY UP @ $0.60
    ↓ (Price drops to $0.28)
Reversal Triggered: BUY DOWN @ $0.72 (1.0 - UP_ask)
    ↓ (Both positions open)
Hedge Period: Wait 120s
    ↓
Check Strategy:
    ├─ Strategy favors DOWN @ 45% → Stop loss UP position
    │   (Keep DOWN position, let it profit)
    └─ Strategy still favors UP @ 35% → Hold both (wait)
```

### Database State

**After Reversal**:

| Trade ID | Side | reversal_triggered | reversal_triggered_at | Status |
|----------|------|-------------------|----------------------|--------|
| 1234     | UP   | 1                 | 2026-01-08 10:05:30   | Hedged |
| 1235     | DOWN | 0                 | NULL                  | Open   |

### Key Points

1. **Original trade marked** (`reversal_triggered=1`) to prevent duplicate reversals
2. **Reversal is a new trade** (`is_reversal=True`, `reversal_triggered=0`)
3. **120s cooldown** prevents immediate stop loss after reversal
4. **Strategy confirmation** ensures reversal aligns with market signals
5. **Stop loss on original** occurs only after confirmation, keeping hedge if strategy disagrees

---

## Balance Syncing & Repair

### Why It's Needed

The Polymarket API may not immediately reflect balance changes after orders fill, causing discrepancies between:
- **Database size** (what we think we have)
- **Exchange balance** (what we actually have)

### Syncing Triggers

**Exit Plan Module** (`exit.py`):

```python
# Always sync if balance is higher (we bought more)
if actual_bal > size + 0.0001:
    needs_sync = True

# Periodic sync for other cases
elif age > 60 and scale_in_age > 60 and abs(actual_bal - size) > 0.0001:
    needs_sync = True
```

### Repair Mechanisms

#### 1. Entry/Scale-in Follow-up
After scale-in order fills:
```python
# Update position size with new shares
size = size + scale_in_shares
bet_usd = bet_usd + (scale_in_shares × scale_in_price)
entry_price = new_bet_usd / new_size
scaled_in = 1
```

#### 2. Exit Plan Size Repair
When size mismatches detected:
1. Cancel existing exit order
2. Sync database size to actual balance
3. Place new exit order with corrected size

#### 3. Stop Loss Sync
Before stop loss sell:
```python
balance_info = get_balance_allowance(token_id)
actual_balance = balance_info.get("balance", 0)

# Update size if mismatch detected
if abs(actual_balance - size) > 0.0001:
    size = actual_balance
    c.execute("UPDATE trades SET size = ? WHERE id = ?", (size, trade_id))
```

### Precision Thresholds

- **Standard precision**: `0.0001` (supports 6-decimal token pricing)
- **Conservative**: Larger gaps trigger immediate sync
- **Periodic**: Small gaps synced after 60s grace period

---

## Exit Outcomes

### 1. Profitable Exit (Exit Plan Filled)

**Trigger**: Limit sell order at `EXIT_PRICE_TARGET` fills

**Outcome**:
- `order_status = "EXIT_PLAN_FILLED"`
- `settled = 1`
- `exited_early = 1`
- Records actual fill price, PnL, ROI

**Example**:
```
💰 [BTC] #1234 UP EXIT SUCCESS: MATCHED at 0.95! (size: 250.00) | +107.50$ (+42.8%)
```

---

### 2. Stop Loss Exit (Loss)

**Trigger**: Price drops below stop loss threshold (after reversal cooldown and strategy confirmation)

**Outcome**:
- `order_status = "STOP_LOSS"` or `"REVERSAL_STOP_LOSS"` (if reversed)
- `settled = 1`
- `exited_early = 1`
- Records actual fill price, PnL, ROI
- Cancels any pending scale-in orders

**Example**:
```
🛡️ [BTC] #1234 HEDGE CONFIRMED: Strategy favors DOWN @ 45%. Clearing losing side.
🛑 [BTC] #1234 STOP_LOSS: Midpoint $0.25 <= $0.30 trigger
```

---

### 3. Settlement (Window Expiry)

**Trigger**: 15-minute prediction window expires, Polymarket settles markets

**Outcome**:
- `settled = 1`
- `exited_early = 0` (ran to expiry)
- Final outcome based on actual market result

**Example**:
```
⏰ [BTC] Window expired. Market settled: UP wins
✅ [BTC] #1234 Settlement: UP position won (size: 250.00 @ $1.00) | +125.00$ (+50.0%)
```

---

### 4. Ghost Trades (Edge Cases)

**Trigger**: Position has 0 balance after fill or scale-in

**Outcome**:
- `settled = 1`
- `final_outcome = "GHOST_TRADE_ZERO_BAL"` or `"STOP_LOSS_GHOST_FILL"`
- `pnl_usd = 0.0`, `roi_pct = 0.0`

**Handling**:
- If balance is 0 but position age > 5 minutes, settles as ghost trade
- If age < 5 minutes, waits for API sync

---

## Configuration

Key environment variables controlling position flow:

```bash
# Entry
MIN_EDGE=0.565              # Minimum confidence to enter trade
MAX_POSITIONS=5             # Maximum concurrent positions

# Scale-In
ENABLE_SCALE_IN=YES
SCALE_IN_MIN_PRICE=0.50
SCALE_IN_MAX_PRICE=0.75
SCALE_IN_TIME_LEFT=450
SCALE_IN_MULTIPLIER=1.5

# Stop Loss
ENABLE_STOP_LOSS=YES
STOP_LOSS_PERCENT=0.50      # 50% loss threshold
STOP_LOSS_PRICE=0.30

# Reversal
ENABLE_REVERSAL=YES
ENABLE_HEDGED_REVERSAL=YES

# Exit Plan
ENABLE_EXIT_PLAN=YES
EXIT_PRICE_TARGET=0.95
EXIT_MIN_POSITION_AGE=0
```

---

## Summary

The PolyFlup position flow lifecycle is a sophisticated multi-stage system:

1. **Entry**: Smart initial position placement based on signal confidence
2. **Monitoring**: 1-second cycle evaluating position health
3. **Stop Loss**: Triple-checked protection with reversal option
4. **Reversal**: Hedged strategy to cap losses while waiting for confirmation
5. **Scale-In**: Adding to winning positions at optimal times
6. **Exit Plan**: Continuous management of limit sell orders with auto-repair

**Key Design Principles**:
- **Modularity**: Each function has a single responsibility
- **Robustness**: Balance sync and repair prevent ghost positions
- **Flexibility**: Dynamic thresholds adapt to market conditions
- **Safety**: Cooldowns and triple checks prevent premature exits
- **Efficiency**: Batch operations and WebSocket caching minimize API calls

This architecture allows the bot to respond quickly to market movements while maintaining position safety and maximizing profitable exits.
