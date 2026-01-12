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

### 2. Balance API Lag Causing Temporary Failures

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

### 4. Position Size Inflation Mystery

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

**Possible Causes**:
1. **Sync bug**: Balance sync is incorrectly inflating DB sizes when it sees larger balance
2. **Ghost shares**: Exchange actually filled scale-ins but reported error
3. **Rounding compounding**: Multiple small sync operations adding up
4. **Wrong position tracked**: Syncing wrong position ID to balance

**Investigation Needed**:
1. **Add detailed logging**: Log all DB size changes with reasons (scale-in vs sync)
2. **Track order fills**: Check if scale-in order fills are being received via WebSocket
3. **Compare with exchange**: Query `closed-positions` API to verify actual position sizes

**Files to Review**:
- `src/trading/position_manager/reconciliation.py` - All size sync logic
- `src/utils/websocket.py` - Order fill notifications
- `src/trading/orders/` - Order status tracking

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
1. ✅ **COMMITTED**: Restart bot to populate Bayesian comparison data
2. **INVESTIGATE**: Position size inflation mystery (Issue #4)
   - Add detailed logging to reconciliation.py
   - Compare with exchange closed-positions API
3. **MONITOR**: USDC balance and settlement flow (Issue #3)
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

1. **Add USDC balance to monitoring output**: Show available funds in position reports
2. **Log settlement releases**: When a position settles, log "Released $X.XX USDC to wallet"
3. **Add balance low warning**: Alert when USDC < $10 to avoid scale-in failures

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
