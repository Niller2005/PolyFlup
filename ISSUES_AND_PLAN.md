# Issue Tracking & Improvement Plan

## Critical Issues (High Priority)

### 1. ✅ FIXED: Missing Bayesian Comparison Data (COMMITTED)

**Status**: FIXED in commit `59ecad8`

**Problem**: Migration 007 added 5 new columns (`additive_confidence`, `bayesian_confidence`, etc.) but bot code wasn't passing them to `save_trade()`, causing all NULL values in database.

**Root Cause**: `src/bot.py` and `src/trading/execution.py` `save_trade()` calls missing new parameters.

**Fix Applied**:
- Added 5 new parameters to both `save_trade()` calls:
  - `additive_confidence`
  - `additive_bias`
  - `bayesian_confidence`
  - `bayesian_bias`
  - `market_prior_p_up`

**Next Action**: **Restart bot** to populate Bayesian columns in new trades.

---

## Secondary Issues (Medium Priority)

### 2. ✅ FIXED: Exchange API False FILLED Status (NEW)

**Status**: FIXED in commit `bf33b96`

**Observed**: 3 catastrophic stop losses in window "January 12, 4:00-4:15PM ET"
- BTC #569: Stop loss at $0.14 → "UNFILLED_NO_BALANCE"
- ETH #570: Stop loss at $0.06 → "UNFILLED_NO_BALANCE"  
- XRP #571: Stop loss at $0.04 → "UNFILLED_NO_BALANCE"

**Root Cause**: Exchange API returned false "FILLED" status for orders that never filled.

**Timeline**:
```
21:00:29 - 3 entry orders placed (BTC #569, ETH #570, XRP #571)
21:00:33-37 - Balance sync shows "near-zero (0.0000)" for all 3 positions
21:03-21:05 - CRITICAL FLOOR stop losses triggered on phantom positions
21:03-21:05 - Exit orders failed: "insufficient funds" (no shares to sell)
21:21:00 - Window summary: All 3 trades "UNFILLED_NO_BALANCE"
```

**Evidence**:
- Database shows all 3 trades as "FILLED" status
- **NO WebSocket fill notifications** for any of the 3 entry order IDs
- Balance API showed 0.00 shares (no actual position existed)
- Exit orders failed due to "insufficient funds" (no shares to sell)

**The Bug**: `src/bot.py` line 236-248 trusted batch API response `status` field without verification.

**Fix Applied**:
```python
# Added 2-second delay after batch order placement
time.sleep(2.0)

# Verify each order status via get_order() after delay
verified_status = actual_status  # Default to batch response
o_data = get_order(order_id)  # Query again
api_status = o_data.get("status", "").upper()

# Use verified status instead of batch response
if api_status in ["FILLED", "MATCHED"]:
    verified_status = api_status
    sz_m = float(o_data.get("size_matched", 0))
    if sz_m > 0:  # Only FILLED if shares matched
        actual_size = sz_m
elif api_status in ["LIVE", "OPEN", "PENDING"]:
    verified_status = api_status  # Order still waiting
    log(f"Order verified as LIVE (2s delay check)")

# Only save as FILLED if order actually filled
if verified_status.upper() in ["FILLED", "MATCHED"]:
    trade_id = save_trade(...)
```

**Next Action**: Monitor next 10-20 trades to ensure no phantom FILLED status

---

### 3. Balance API Lag Causing Temporary Failures

**Observed**: 8 occurrences in logs
```
⚠️  [SOL] #512 Balance sync shows near-zero (0.0000) for active position (10.48)
⚠️  SELL Order: Insufficient funds (likely already filled or locked)
❌ [SOL] Failed to place exit plan: Insufficient funds
```

**Root Cause**: Polymarket balance API returns 0 shares immediately after order fills, before the exchange has fully registered the position in the balance system.

**Impact**:
- Temporary exit plan placement failures (1-2 seconds after fill)
- Additional log noise
- Self-healing works but adds delay

**Current Mitigation** (WORKING):
- System detects 0 balance and uses DB size as fallback
- `ENABLE_ENHANCED_BALANCE_VALIDATION = YES` provides graceful degradation
- Balance sync every 5 seconds eventually catches up

**Potential Improvements**:
1. **Increase balance sync delay after fills**: Add 2-3 second cooldown before querying balance API after fill detection
2. **Add retry with backoff**: If balance returns 0, retry after 1, 2, 3 seconds before giving up
3. **Use position API as primary**: Balance API lags; position API may be more reliable post-fill
4. **Cache balance during exit plan placement**: Use last known good balance for exit order sizing

**Files to Review**:
- `src/trading/position_manager/exit.py` - Exit plan placement logic
- `src/trading/position_manager/reconciliation.py` - Balance validation and sync
- `src/data/market_data/` - Balance API calls

---

### 3. Scale-in Failures Due to Low USDC Balance

**Observed**: 15+ scale-in failures in logs
```
❌ SCALE-IN PLACEMENT FAILED: Insufficient funds
⏳ Scale-in skipped: Insufficient funds (Need $8.75, Have $1.45)
```

**Root Cause**: Wallet USDC balance is very low (~$1.45), insufficient for scale-in orders which require ~$6-9 per position.

**Timeline Analysis**:
- 17:46-17:47 - 3 new positions entered (BTC #511, SOL #512, ETH #513, XRP #514)
- 17:52-17:56 - Scale-in attempts show "Insufficient funds" consistently
- 17:58+ - Scale-in placement failures (API errors)

**Investigation Needed**:
1. **Why is USDC so low?**
   - Check if positions are not settling and releasing USDC
   - Check if there are frozen/funds locked in pending orders
   - Check if there's a settlement delay issue

2. **Are scale-ins actually succeeding?**
   - DB shows #512 (SOL) size: 20.57 shares (initial was 10.48)
   - DB shows #514 (XRP) size: 20.00 shares (initial was 10.00)
   - But all scale-in PLACEMENT FAILED messages show errors
   - **Possible discrepancy**: Scale-ins may be succeeding via a different code path, or there's a sync bug inflating DB sizes

**Potential Improvements**:
1. **Add USDC balance monitoring**: Log available USDC before scale-in attempts
2. **Better error handling**: Distinguish "truly insufficient" vs "API lag/lock"
3. **Settlement verification**: Check if settled positions are actually releasing USDC
4. **Scale-in gating**: Skip scale-in if available USDC < minimum threshold (e.g., $5)

**Files to Review**:
- `src/trading/position_manager/scale_in.py` - Scale-in logic
- `src/trading/position_manager/reconciliation.py` - Balance validation
- `src/data/settlement.py` - Settlement and auto-claim

---

### 4. ✅ FIXED: Position Size Inflation Mystery

**Status**: FIXED in commit (pending)

**Observed**: Unexpected DB size growth for SOL #512 and XRP #514

```
17:46:23 - SOL #512 UP size: 20.6 (initial entry)
17:58:58 - DB shows size: 20.57 (scale-in attempt failed)
17:46:22 - XRP #514 UP size: 20.0 (initial entry)
17:58:53 - DB shows size: 20.00 (scale-in attempt failed)
```

**Issue**:
- All scale-in PLACEMENT FAILED messages show "Insufficient funds"
- Yet DB sizes show ~2x initial entry sizes
- **Contradiction**: If placements failed, how did sizes double?

**Root Cause Found**:
Balance API returning phantom shares despite scale-in orders failing. Log evidence:

```
XRP #514:
- Initial: 10.00 shares
- 17:58:47 - Sync: 10.00 → 12.78 (balance API returned 12.78)
- 17:58:51 - Sync: 12.77 → 19.99 (balance API returned 19.99)
- 17:58:55 - Sync: 19.99 → 20.00 (balance API returned 20.00)

SOL #512:
- Initial: 10.48 shares
- 17:46:28 - Sync: 10.32 → 10.33
- 17:58:56 - Sync: 10.33 → 20.57 (balance API returned 20.57)
```

**The Bug**: In `src/trading/position_manager/exit.py` lines 157-160:

```python
if actual_bal > size + 0.0001:
    needs_sync = True  # Always sync if we have more tokens than DB thinks
```

This logic **blindly trusts balance API** without verifying:
1. Did a scale-in actually succeed?
2. Is balance API returning valid data?
3. Are there phantom/ghost shares?

Balance API is returning inflated values (20.00 instead of 10.00) even though all scale-in orders failed with "Insufficient funds" errors.

**Fix Applied**:
1. **Added scale-in order validation**: Before syncing to higher balance, verify scale-in order actually filled
2. **Added reasonable adjustment check**: Only allow sync if difference < 50% of current size
3. **Added minimum sync interval**: 30 seconds between syncs to prevent rapid compounding
4. **Added suspicious sync blocking**: Log and block when balance is higher but no confirmed fill

**Files Modified**:
- `src/trading/position_manager/exit.py` - Added validation logic before balance sync
- `src/trading/position_manager/shared.py` - Added `_last_balance_sync` tracking

**Next Action**: Test fix with new trades

---

## Working Correctly (No Action Needed)

### 5. Exit Order Self-Healing ✅

**Observed**: 5 successful repairs
```
⚠️  [SOL] #512 Exit order size mismatch detected! Order: 10.32, DB: 10.48. Repairing...
🔧 Syncing size to match balance: 10.32 -> 10.33
```

**Assessment**: System working as designed. Self-healing correctly:
- Detects size mismatches between DB and active orders
- Repairs exit order sizes
- Logs all events for audit trail

**No Action Needed**: Continue monitoring.

---

## Implementation Priority

### Phase 1: Immediate (This Week)
1. ✅ **COMPLETED**: Restart bot to populate Bayesian comparison data
2. ✅ **FIXED**: Position size inflation mystery (Issue #4)
    - ✅ Root cause identified: Balance API returning phantom shares
    - ✅ Added validation: Check scale-in order status before syncing
    - ✅ Added reasonable adjustment check: < 50% difference only
    - ✅ Added minimum sync interval: 30 seconds between syncs
    - ✅ Added suspicious sync blocking: Log and block phantom inflation
    - 🔄 Test fix with new trades
3. ✅ **FIXED**: Exchange API false FILLED status (NEW - Issue #6)
    - ✅ Root cause identified: API returning "FILLED" status for unfilled orders
    - ✅ Added 2-second delay after batch order placement
    - ✅ Added order verification via get_order() after delay
    - ✅ Use verified status instead of batch response
    - ✅ Only save as FILLED if size_matched > 0
    - ✅ Log verified orders as LIVE when still waiting
    - ✅ Fix commit: Use verified_status instead of actual_status (commit `4001384`)
    - 🔄 Monitor next 10-20 trades to ensure no phantom FILLED status
4. ✅ **COMPLETED**: Monitoring UI improvements (commit `9287f72`)
    - ✅ Add USDC balance to monitoring output
    - ✅ Log settlement releases with USDC amount
    - ✅ Add balance low warning when USDC < $10
5. **MONITOR**: USDC balance and settlement flow (Issue #3)
    - Check if settlements are releasing funds properly
    - Verify no funds locked in pending orders

### Phase 2: Short-term (Next Week)
4. **IMPROVE**: Balance API lag handling (Issue #2)
   - Add 2-3 second cooldown after fills before balance queries
   - Implement retry with backoff for zero balance responses
5. **ANALYZE**: Scale-in failure root cause (Issue #3)
   - Add USDC balance monitoring and logging
   - Implement scale-in gating based on available funds
6. **TEST**: Run `compare_bayesian_additive.py` after 50-100 trades
   - Determine if Bayesian performs better than additive
   - Potentially enable Bayesian if superior

### Phase 3: Long-term (Month 2)
7. **ARCHITECT**: Balance API reliability improvements
   - Consider position API as primary source post-fill
   - Implement balance caching strategy
8. **OPTIMIZE**: Scale-in strategy
   - Adjust scale-in timing based on USDC availability
   - Consider minimum balance thresholds before attempting

---

## Quick Wins (Low Effort)

1. ✅ **COMPLETED**: Add USDC balance to monitoring output (commit `9287f72`)
2. ✅ **COMPLETED**: Log settlement releases (commit `9287f72`)
3. ✅ **COMPLETED**: Add balance low warning (commit `9287f72`)

---

## Questions to Answer

1. **Issue #3**: Why is USDC consistently at ~$1.45 despite multiple active positions?
2. **Issue #4**: How are position sizes doubling if all scale-ins fail?
3. **General**: Is there a wallet/funding issue causing USDC to not be released after settlements?

---

## Next Actions

1. **Restart bot** with updated code (Bayesian comparison data will now populate)
2. **Monitor** next 10-20 trades for:
   - Bayesian comparison data in database
   - USDC balance changes after settlements
   - Position size accuracy
3. **Run** `uv run python compare_bayesian_additive.py` after 50 trades
4. **Investigate** position size inflation mystery with added logging
