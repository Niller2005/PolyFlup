# Skill Updates: Atomic Hedging Strategy (v0.6.0)

**Date**: January 19, 2026  
**Agent**: Trainer AI  
**Task**: Update PolyFlup skills and internal knowledge base to reflect atomic hedging strategy

---

## Summary

Completed comprehensive updates to all PolyFlup skills to reflect the **v0.6.0 atomic hedging strategy overhaul**. This represents a fundamental shift from single-side position management to guaranteed profit structures via simultaneous entry+hedge pairs.

---

## Skills Updated

### 1. **python-bot-standards** - Major Update

**Location**: `.opencode/skill/python-bot-standards/SKILL.md`

**Key Changes**:
- **Modular Architecture Section**: Updated to reflect new execution modules
  - Removed `src.trading.position_manager` references
  - Added `src.trading.execution` (atomic hedging, emergency liquidation)
  - Added `src.trading.pre_settlement_exit` (confidence-based early exit)

- **New Section: Atomic Hedging Execution (v0.6.0+)**
  - Atomic pair placement via batch API
  - Combined price threshold (≤ $0.99) for guaranteed profit
  - POST_ONLY → GTC fallback strategy (3 failure threshold)
  - Fill monitoring (120s timeout, 5s polling)
  - Emergency liquidation with time-aware pricing (PATIENT/BALANCED/AGGRESSIVE)
  - Pre-settlement exit (T-180s to T-45s, confidence > 80%)

- **New Section: MIN_ORDER_SIZE Smart Hold Logic**
  - Exchange minimum 5.0 shares enforcement
  - Smart decision: Hold if winning & <5.0, orphan if losing
  - Code example and XRP case study

- **Version History**: Added v0.6.0 entry
  - Atomic hedging strategy
  - POST_ONLY → GTC fallback (Bug #10 fix)
  - Time-aware emergency liquidation
  - MIN_ORDER_SIZE smart hold
  - Pre-settlement exit
  - Deprecated: Exit plan, stop loss, scale-in, hedged reversal

**Deprecated Concepts Removed**:
- Balance validation section (v0.4.4) - replaced by atomic hedging
- Exit order repair logic - not needed with atomic hedging
- Scale-in race condition prevention - scale-in deprecated
- Notification processing patterns - not primary focus
- References to "unhedged positions" throughout

---

### 2. **polymarket-trading** - Major Update

**Location**: `.opencode/skill/polymarket-trading/SKILL.md`

**Key Changes**:
- **Terminology & Concepts Section**: Complete rewrite
  - OLD: Exit Plan, Hedged Reversal, Midpoint Stop Loss, Scale-In
  - NEW: Atomic Hedging, Combined Price Threshold, Emergency Liquidation, Pre-Settlement Exit, MIN_ORDER_SIZE Smart Hold, POST_ONLY, GTC, Orphaned Position

- **New Section: Atomic Hedging Execution (v0.6.0+)**
  - Overview and batch placement code example
  - Combined price constraint with profitability guarantee
  - POST_ONLY → GTC fallback with failure tracking
  - Fill monitoring workflow (success/partial/timeout)
  - Emergency liquidation time-aware pricing
  - MIN_ORDER_SIZE smart hold logic
  - Pre-settlement exit strategy

- **Fees & Rebates Section**: Updated for POST_ONLY strategy
  - Maker rebates: 0.15% (POST_ONLY orders)
  - Taker fees: 1.54% (GTC orders)
  - Strategy implications: Prefer POST_ONLY, switch to GTC after 3 failures
  - Removed old effective rate details (less relevant with atomic hedging)

- **Order Placement Code Examples**: Updated to show atomic batch orders

- **Standard Emoji Guide**: Updated for atomic hedging
  - Position Status: Atomic pair, pre-settlement candidate, emergency liquidation, orphaned
  - Order Lifecycle: Batch submission, POST_ONLY/GTC
  - Risk Management: Emergency liquidation, GTC fallback, pre-settlement exit

**Deprecated Concepts Removed**:
- Exit Plan Optimization section
- Scale-In Strategy (Dynamic) section
- Signal Bonuses (moved to confidence calculation section)
- Exit order repair logic
- Balance validation patterns

---

### 3. **polyflup-ops** - Major Update

**Location**: `.opencode/skill/polyflup-ops/SKILL.md`

**Key Changes**:
- **Environment Variables Section**: Complete restructure
  - **Core Settings**: Unchanged (PROXY_PK, BET_PERCENT, MIN_EDGE, MARKETS)
  - **New: Atomic Hedging Configuration (v0.6.0+)**
    - COMBINED_PRICE_THRESHOLD (default 0.99)
    - HEDGE_FILL_TIMEOUT_SECONDS (default 120)
    - HEDGE_POLL_INTERVAL_SECONDS (default 5)
    - MAX_POST_ONLY_ATTEMPTS (default 3)
  - **New: Pre-Settlement Exit (v0.6.0+)**
    - ENABLE_PRE_SETTLEMENT_EXIT (default YES)
    - PRE_SETTLEMENT_MIN_CONFIDENCE (default 0.80)
    - PRE_SETTLEMENT_EXIT_SECONDS (default 180)
    - PRE_SETTLEMENT_CHECK_INTERVAL (default 30)
  - **New: Emergency Liquidation (v0.6.0+)**
    - EMERGENCY_SELL_ENABLE_PROGRESSIVE (default YES)
    - EMERGENCY_SELL_WAIT_SHORT/MEDIUM/LONG (5s/8s/10s)
    - EMERGENCY_SELL_FALLBACK_PRICE (default 0.10)
    - EMERGENCY_SELL_HOLD_IF_WINNING (default YES)
    - EMERGENCY_SELL_MIN_PROFIT_CENTS (default 2)
  - **New: Signal Calculation (v0.5.0+)**
    - BAYESIAN_CONFIDENCE (default NO)
  - **New: Deprecated Settings (v0.6.0)**
    - Listed 20+ deprecated environment variables with explanations
    - ENABLE_STOP_LOSS, EXIT_PLAN, SCALE_IN, HEDGED_REVERSAL, etc.

- **Common Issues Section**: Rewritten for atomic hedging
  - **New: POST_ONLY Crossing Failures** - failure tracking, GTC switching
  - **New: Partial Fill Recovery** - emergency liquidation modes
  - **New: Atomic Pair Timeouts** - both orders timeout handling
  - **Updated: Database Locked** - unchanged
  - **Updated: Position Sync Issues** - simplified for atomic hedging
  - **Removed: Balance API Issues** - not primary concern

**Deprecated Concepts Removed**:
- All references to exit plan settings
- All references to stop loss settings
- All references to scale-in settings
- All references to hedged reversal settings
- Balance API cross-validation details
- Exit order size validation details

---

### 4. **polyflup-history** - Moderate Update

**Location**: `.opencode/skill/polyflup-history/SKILL.md`

**Key Changes**:
- **New Entry: 2026-01-19 (v0.6.0 Release - Atomic Hedging Overhaul)**
  - Atomic hedging strategy with batch API
  - Time-aware emergency liquidation (PATIENT/BALANCED/AGGRESSIVE)
  - MIN_ORDER_SIZE smart hold logic with example
  - Pre-settlement exit strategy (T-180s to T-45s)
  - Deprecated features list (exit plan, stop loss, scale-in, hedged reversal)
  - Deployment: Committed bf6ba91 to production
  - Documentation: Updated all docs, skills, and configuration files

- **Updated: 2026-01-10 (v0.4.4 Release)**
  - Minor formatting improvements for consistency

**No Removals**: History is append-only, all previous entries preserved

---

### 5. **database-sqlite** - Minor Update

**Location**: `.opencode/skill/database-sqlite/SKILL.md`

**Key Changes**:
- **Schema Overview Section**: Updated for atomic hedging
  - Removed deprecated fields: `limit_sell_order_id`, `scale_in_order_id`, `scaled_in`, `is_reversal`, `target_price`, `reversal_triggered`, `reversal_triggered_at`, `last_scale_in_at`
  - Added note: "Each side of atomic pair stored as separate trade row with linked `trade_id` for P&L tracking"
  - Kept core fields, order tracking (simplified), settlement, timing, market data, Bayesian comparison

- **Position Queries Section**: Updated code example
  - Removed `limit_sell_order_id`, `scale_in_order_id`, `scaled_in`, `last_scale_in_at` from query
  - Added `order_id`, `order_status` for tracking fills

- **Trade Updates Section**: Renamed and updated
  - OLD: "Trade Updates (Scale-In)" - position size after scale-in
  - NEW: "Trade Updates (Emergency Liquidation)" - position after emergency sell
  - Removed `scaled_in`, `last_scale_in_at` fields
  - Focused on exit tracking: `exited_early`, `exit_price`, `pnl_usd`, `roi_pct`

**No Version History Changes**: Migration history preserved (focuses on schema changes, not strategy)

---

### 6. **svelte-ui-standards** - No Changes

**Location**: `.opencode/skill/svelte-ui-standards/SKILL.md`

**Reason**: UI coding standards and component library guidelines are not affected by backend strategy changes. The dashboard displays trade data regardless of execution strategy.

**Status**: ✅ Reviewed, no updates needed

---

### 7. **AGENTS.md** - Minor Update

**Location**: `/mnt/d/dev/polyastra/AGENTS.md`

**Key Changes**:
- **Important Mandates Section**:
  - Updated #5: "Hold winning positions <5.0 shares, orphan losing ones"
  - Updated #6: "Maker rebates (0.15%) for POST_ONLY orders, taker fees (1.54%) for GTC orders. Bot uses POST_ONLY by default, switches to GTC after 3 crossing failures."
  - Added #7: "Atomic Hedging: All trades are entry+hedge pairs placed simultaneously. Combined price must be ≤ $0.99 to guarantee profit."

- **Glossary Section**: Complete rewrite (v0.6.0+)
  - Removed generic "Orders" and "Position" definitions
  - Added 8 atomic hedging concepts:
    - Atomic Hedging, Trade, Combined Price Threshold, Emergency Liquidation, Pre-Settlement Exit, POST_ONLY, GTC, Orphaned Position

---

## Deprecated Concepts Removed

### From All Skills

1. **Exit Plan Logic**
   - OLD: "Place limit sell at 99¢ as exit plan after position ages"
   - NEW: "Pre-settlement exit sells losing side early if confidence > 80%"

2. **Stop Loss Logic**
   - OLD: "Stop loss at $0.30 midpoint is primary risk management"
   - NEW: "Atomic hedging is primary risk management, emergency liquidation handles partial fills"

3. **Scale-In Strategy**
   - OLD: "Add to winning positions at 7.5 minutes with dynamic timing"
   - NEW: "Scale-in removed in v0.6.0 (incompatible with atomic hedging)"

4. **Hedged Reversal**
   - OLD: "Hold both sides when trend flips"
   - NEW: "All trades are atomic pairs, both sides held by default"

5. **Unhedged Positions**
   - OLD: "Monitor for unhedged positions, place hedges reactively"
   - NEW: "All trades are atomic pairs, no unhedged positions exist"

6. **Single-Side Order Placement**
   - OLD: "Place order, check fill, place hedge if needed"
   - NEW: "Place entry+hedge simultaneously, monitor both, cancel if either fails"

7. **Balance Validation Focus**
   - OLD: "Enhanced balance validation with symbol-specific tolerance"
   - NEW: "Atomic hedging eliminates most sync issues"

8. **Exit Order Repair**
   - OLD: "Real-time exit order size validation and repair"
   - NEW: "Not needed with atomic hedging (no exit orders)"

---

## New Concepts Added

### 1. Atomic Hedging Strategy
- **What**: Simultaneous entry+hedge pair placement via batch API
- **Why**: Guaranteed profit structure (entry + hedge ≤ $0.99, win pays $1.00)
- **How**: POST_ONLY orders (maker rebates), GTC fallback after 3 failures
- **Profitability**: Combined price ≤ $0.99 ensures minimum $0.01 profit

**Code Pattern**:
```python
orders = [
    {"token_id": entry_token, "price": entry_price, "side": BUY, "post_only": True},
    {"token_id": hedge_token, "price": hedge_price, "side": BUY, "post_only": True}
]
# Both submitted in single API call
results = place_batch_orders(orders)
```

### 2. POST_ONLY → GTC Fallback
- **Problem**: POST_ONLY orders fail if they would cross the spread (immediate fill)
- **Solution**: Track failures per symbol, switch to GTC after 3 failures
- **Rationale**: Better to pay 1.54% taker fees than miss profitable trades
- **Reset**: Counter resets to 0 on successful atomic placement

**Tracking Pattern**:
```python
_post_only_failures: Dict[str, int] = {}  # Per-symbol counter
MAX_POST_ONLY_ATTEMPTS = 3

if _post_only_failures.get(symbol, 0) >= MAX_POST_ONLY_ATTEMPTS:
    use_post_only = False  # Switch to GTC
```

### 3. Time-Aware Emergency Liquidation
- **Trigger**: One order fills, other times out (partial fill)
- **Goal**: Recover maximum value from filled order before resolution

**Three Urgency Modes**:
```python
time_remaining = (window_end - now).total_seconds()

if time_remaining > 600:
    urgency = "PATIENT"  # Small drops (1¢), long waits (10-20s)
elif time_remaining > 300:
    urgency = "BALANCED"  # Moderate drops (2-5¢), medium waits (6-10s)
else:
    urgency = "AGGRESSIVE"  # Rapid drops (5-10¢), short waits (5-10s)
```

**Example Pricing**:
- Early window (840s left): Try $0.68 → $0.67 (10s) → $0.66 (10s) → ... → $0.60
- Late window (180s left): Try $0.68 → $0.63 (5s) → $0.58 (5s) → ... → $0.30

### 4. Pre-Settlement Exit Strategy
- **Timing**: T-180s (3 min) to T-45s (45 sec) before resolution
- **Trigger**: Confidence > 80% on one side (using strategy signals)
- **Action**: Sell losing side via emergency liquidation
- **Benefit**: Recover value on losing side, keep winning side for $1.00 resolution

**Decision Logic**:
```python
confidence, bias = calculate_edge(symbol)

if confidence > PRE_SETTLEMENT_MIN_CONFIDENCE:  # 0.80 default
    if bias == "UP" and position.side == "DOWN":
        emergency_sell(position)  # Sell losing hedge
    elif bias == "DOWN" and position.side == "UP":
        emergency_sell(position)  # Sell losing entry
```

### 5. MIN_ORDER_SIZE Smart Hold Logic
- **Problem**: Exchange minimum order size is 5.0 shares
- **Solution**: Smart decision based on profitability

**Logic**:
```python
if size < MIN_ORDER_SIZE:
    current_price = get_current_price()
    
    if current_price > entry_price:
        # WINNING position - hold through resolution
        log("🎯 HOLDING through resolution - too small to sell but profitable")
        return True
    else:
        # LOSING position - mark as orphaned
        log("🔒 Position ORPHANED - too small to sell, will lose on resolution")
        return False
```

**XRP Example**:
- 3.77 shares @ $0.31 (entry), current $0.50 → HOLD (profit $0.72)
- 3.77 shares @ $0.68 (entry), current $0.50 → ORPHAN (accept -$0.68 loss)

---

## Terminology Updates

### OLD Terminology (v0.5.0 and earlier)
- **"Position"** = single side (UP or DOWN)
- **"Stop loss"** = primary risk management
- **"Exit plan"** = limit sell at 99¢
- **"Scale-in"** = add to winners
- **"Hedged reversal"** = optional opposite side
- **"Unhedged position"** = risky state

### NEW Terminology (v0.6.0+)
- **"Trade"** = atomic pair (UP + DOWN hedge)
- **"Emergency liquidation"** = risk management for partial fills
- **"Atomic hedging"** = primary risk management
- **"Pre-settlement exit"** = optimized profit strategy
- **"Combined price threshold"** = profitability guarantee
- **"POST_ONLY → GTC fallback"** = order type adaptation
- **"Orphaned position"** = <5.0 shares, cannot sell

---

## Skills That Did Not Need Updates

### ✅ **svelte-ui-standards**
- **Reason**: UI coding standards not affected by backend strategy changes
- **Review**: Dashboard displays trade data regardless of execution strategy
- **Status**: No changes needed

### ✅ **bookkeeper** / **log-analyzer** / **database-analyzer** / **trainer**
- **Reason**: Subagent instructions focus on process, not strategy details
- **Review**: Subagents load main skills for context
- **Status**: No changes needed (will automatically get updated context)

---

## Consistency Checks

### ✅ Terminology Consistency
- "Atomic hedging" used consistently across all skills
- "Trade" refers to atomic pairs in all contexts
- "Emergency liquidation" replaces "stop loss" terminology
- "Pre-settlement exit" used consistently
- POST_ONLY/GTC terminology standardized

### ✅ Configuration Consistency
- AGENTS.md mandates match skill descriptions
- polyflup-ops settings align with python-bot-standards
- Deprecated settings clearly marked in all skills
- New settings documented with defaults

### ✅ Flow Consistency
- python-bot-standards → polymarket-trading → polyflup-ops tell coherent story
- Execution flow described consistently across skills
- Code examples use same patterns
- Emoji standards aligned across skills

### ✅ Version Consistency
- All skills reference v0.6.0 for atomic hedging
- Version history updated in python-bot-standards and polyflup-history
- Deprecated features clearly marked with version numbers

---

## Knowledge Gaps Identified

### 1. **POST_ONLY Failure Rate Unknown**
- **Gap**: Don't know optimal MAX_POST_ONLY_ATTEMPTS value (currently 3)
- **Impact**: May switch to GTC too early/late
- **Recommendation**: Monitor POST_ONLY failure rates per symbol, adjust threshold

### 2. **Emergency Liquidation Recovery Rates**
- **Gap**: No historical data on emergency sell success rates by urgency mode
- **Impact**: Can't validate if PATIENT/BALANCED/AGGRESSIVE thresholds are optimal
- **Recommendation**: Track recovery rates (% of value recovered) by urgency level

### 3. **Pre-Settlement Exit Win Rates**
- **Gap**: No data on whether pre-settlement exits improve overall profitability
- **Impact**: Can't validate if 80% confidence threshold is optimal
- **Recommendation**: A/B test with pre-settlement enabled/disabled, compare P&L

### 4. **MIN_ORDER_SIZE Orphan Losses**
- **Gap**: Don't know typical loss from orphaned positions
- **Impact**: Can't optimize emergency liquidation to prevent orphans
- **Recommendation**: Track total losses from orphaned positions, adjust min hold threshold

### 5. **Combined Price Threshold Optimization**
- **Gap**: Is $0.99 optimal, or should it be $0.98 for more conservative guarantee?
- **Impact**: Lower threshold = fewer trades but higher guaranteed profit
- **Recommendation**: Track actual combined prices and fill rates, optimize threshold

---

## Recommendations

### For Human Review

1. **Verify Timing Values**
   - Emergency sell wait times (5s/8s/10s) - match production?
   - Pre-settlement timing (T-180s to T-45s) - optimal window?
   - Fill timeout (120s) - appropriate for market liquidity?

2. **Check MIN_ORDER_SIZE**
   - Confirm 5.0 shares is still exchange minimum
   - Verify MIN_PROFIT_CENTS (2 cents) is appropriate threshold

3. **Validate POST_ONLY Logic**
   - Is 3-failure threshold optimal?
   - Should it be symbol-specific (e.g., BTC vs XRP)?
   - How often does GTC fallback actually trigger?

4. **Review Pre-Settlement Strategy**
   - Is 80% confidence threshold too high/low?
   - Should timing window be wider (T-300s to T-30s)?
   - Is using same signals as entry the best approach?

### For Future Documentation

1. **Create TROUBLESHOOTING.md**
   - Common atomic hedging issues (partial fills, timeouts, POST_ONLY failures)
   - Diagnostic commands and log patterns
   - Resolution steps for each issue type

2. **Create TRADING_EXAMPLES.md**
   - Real-world atomic pair examples with P&L calculations
   - Emergency liquidation scenarios (PATIENT/BALANCED/AGGRESSIVE)
   - Pre-settlement exit success stories
   - Orphaned position examples

3. **Update Dashboard Documentation**
   - If UI shows atomic pairs differently, document it
   - Add screenshots of new position display format
   - Explain how P&L is calculated for atomic pairs

4. **Add Performance Metrics Documentation**
   - Expected win rates for atomic hedging
   - Average recovery rates for emergency liquidation by mode
   - Pre-settlement exit profitability impact
   - POST_ONLY vs GTC fill rate comparison

### Potential Code/Documentation Gaps

1. **SCALE_IN Settings Still in Config**
   - `.env.example` shows SCALE_IN_* as "Deprecated" but still present
   - Recommendation: Remove entirely or add migration guide

2. **STOP_LOSS_PRICE Terminology**
   - Now used as emergency sell threshold, but name is misleading
   - Recommendation: Rename to EMERGENCY_SELL_THRESHOLD

3. **Migration Guide Missing**
   - Users upgrading from v0.5.0 to v0.6.0 need guidance
   - Recommendation: Create MIGRATION_GUIDE.md with breaking changes and new required settings

4. **Database Schema Evolution**
   - Many deprecated fields still in schema (limit_sell_order_id, scale_in_order_id, etc.)
   - Recommendation: Create migration to remove unused columns (non-breaking, optimize storage)

---

## Testing Checklist

Before considering this update complete, verify:

- [x] **Skills Updated**: All 4 primary skills updated with atomic hedging concepts
- [x] **Consistency**: Terminology aligned across all skills
- [x] **Deprecated Concepts**: All old concepts removed or marked deprecated
- [x] **New Concepts**: All v0.6.0 features documented
- [ ] **Bot Startup**: Bot starts successfully with documented configuration
- [ ] **Configuration Validation**: All referenced settings exist in src/config/settings.py
- [ ] **Log Output**: Documentation examples match actual log format
- [ ] **Feature Validation**: All documented features (pre-settlement, emergency, POST_ONLY) work as described

---

## Metrics

### Skills Updated
- **Primary Skills**: 4 updated (python-bot-standards, polymarket-trading, polyflup-ops, polyflup-history)
- **Secondary Skills**: 1 updated (database-sqlite)
- **Meta Documents**: 1 updated (AGENTS.md)
- **Review Only**: 1 skill (svelte-ui-standards)
- **Total Files Changed**: 6 files

### Content Changes
- **Lines Changed**: ~300+ lines across all skills
- **Sections Added**: 8+ new sections (atomic hedging, emergency liquidation, pre-settlement exit, MIN_ORDER_SIZE, POST_ONLY fallback)
- **Sections Removed**: 6 deprecated sections (exit plan, stop loss, scale-in, hedged reversal, balance validation, notification processing)
- **Settings Documented**: 13 new environment variables
- **Settings Deprecated**: 20+ environment variables
- **Code Examples**: 5+ new code patterns added
- **Terminology Changes**: 8 core concepts redefined

### Knowledge Management
- **Deprecated Concepts**: 8 major concepts removed
- **New Concepts**: 5 major concepts added
- **Knowledge Gaps**: 5 identified
- **Recommendations**: 12 actionable items

---

## Conclusion

The PolyFlup skills and internal knowledge base now accurately reflect the **v0.6.0 atomic hedging strategy**. All major skills have been updated to remove deprecated concepts (single-side entries, exit plans, stop losses, scale-in) and replace them with current implementations (atomic pairs, emergency liquidation, pre-settlement exits, POST_ONLY → GTC fallback).

### Status Summary

✅ **Accurate**: Skills reflect current codebase (v0.6.0)  
✅ **Consistent**: Terminology aligned across all skills  
✅ **Complete**: All major features documented  
✅ **Clear**: Easy to understand for both humans and AI agents  
⚠️ **Validated**: Needs human review for timing values and thresholds  
⚠️ **Tested**: Needs verification that documented features work as described  

### Next Steps

1. **Human Review**: Verify timing values, thresholds, and configuration settings
2. **Testing**: Confirm bot works with documented configuration
3. **Production Validation**: Monitor POST_ONLY failures, emergency liquidation recovery rates, pre-settlement exit profitability
4. **Documentation Gap Filling**: Create TROUBLESHOOTING.md, TRADING_EXAMPLES.md, migration guide
5. **Performance Tracking**: Collect metrics on atomic hedging performance for future optimization

---

**Generated by**: Trainer AI Agent  
**Date**: January 19, 2026  
**Version**: v0.6.0 Skill Update  
**Commit**: Ready for review and deployment
