# Exit Plan Self-Healing Analysis & Fix

## Issues Identified

### 1. Balance Validation Logic Inconsistency
**Location:** Lines 168-185 in `src/trading/position_manager/exit.py`

**Problem:** When the grace period logic triggers (`actual_bal < 0.1` and `age < 300`), it uses DB size for `sell_size`, but the MIN_SIZE validation still uses `actual_bal`.

**Current Code:**
```python
if size >= MIN_SIZE and actual_bal < 0.1 and age < 300:
    sell_size = truncate_float(size, 2)  # Uses DB size

if sell_size < MIN_SIZE:
    if actual_bal >= MIN_SIZE:  # Still uses actual_bal!
        sell_size = MIN_SIZE
    else:
        return  # Exits without healing
```

**Fix:**
```python
if sell_size < MIN_SIZE:
    # Use the same size logic that was applied above for consistency
    effective_balance = actual_bal
    if size >= MIN_SIZE and actual_bal < 0.1 and age < 300:
        # If we're in grace period, use DB size for consistency
        effective_balance = size
    
    if effective_balance >= MIN_SIZE:
        sell_size = MIN_SIZE
        log(f"   📈 [{symbol}] #{trade_id} Bumping sell size to {MIN_SIZE} shares")
    else:
        log(f"   ⏭️  [{symbol}] #{trade_id} size {sell_size} < {MIN_SIZE}. Skipping, trying again next window.")
        return
```

### 2. Grace Period Too Short
**Problem:** 5-minute grace period (300 seconds) is too aggressive for API sync issues.
**Solution:** Increase to 10 minutes (600 seconds).

### 3. Ghost Trade Threshold Too Aggressive
**Problem:** 36+ trades settled as `GHOST_TRADE_ZERO_BAL` suggests the balance validation is too sensitive.
**Solution:** 
- Increase threshold from 0.1 to 0.5 shares
- Add retry logic before settling as ghost trades
- Better logging to distinguish real vs timing issues

### 4. Monitor.py Import Issue
**Problem:** `UnboundLocalError` for `get_order_status` is blocking position monitoring.
**Solution:** Fix the import scoping issue in the function.

### 5. 0.999 Pricing Impact
**Problem:** Aggressive pricing might cause orders to sit unfilled longer.
**Solution:** Consider using 0.995 instead of 0.999 for better fill rates while maintaining profitability.

## Root Cause
The main issue is that the balance validation logic (added in recent commits) is:
1. Too sensitive: Triggers on near-zero balances that might be API timing issues
2. Inconsistent: Uses different size logic for different validations
3. Blocking healing: Prevents proper self-healing when needed most

## Recommended Implementation Order
1. Fix monitor.py import issue (critical - blocking all monitoring)
2. Fix balance validation consistency in exit.py
3. Adjust grace period and thresholds
4. Add better logging and retry logic
5. Consider 0.999 pricing adjustment
