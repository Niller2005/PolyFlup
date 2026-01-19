# Codebase Migration Plan - Normalized Schema

## Query Analysis

### Files with most queries (Priority order):
1. **src/utils/notifications.py** - 28 queries (14 SELECT + 14 UPDATE)
2. **src/trading/execution.py** - 11 queries (2 SELECT + 9 UPDATE)
3. **src/trading/settlement.py** - 12 queries (8 SELECT + 4 UPDATE)
4. **src/data/database.py** - 6 SELECT queries
5. **src/trading/position_manager/sync.py** - 12 queries (6 SELECT + 6 UPDATE)
6. **src/bot.py** - 2 queries (1 SELECT + 1 UPDATE)
7. **src/trading/position_manager/reversal.py** - 2 UPDATE queries
8. **src/trading/background_redemption.py** - 2 UPDATE queries

## Migration Strategy

### Phase 1: Core Infrastructure (Start Here)
These files provide foundational functions used by others.

#### 1.1 src/data/database.py ✓ ALREADY DUAL-WRITE
- `save_trade()` already writes to both schemas
- Migrate helper functions:
  - `has_side_for_window()` → Query positions table
  - `has_trade_for_window()` → Query positions table
  - `generate_statistics()` → Query positions table
  - `get_total_exposure()` → Already has normalized version

#### 1.2 Add missing normalized_db helpers
Need to add helpers that match current usage patterns:
- `get_position_by_id()` - Simple position lookup
- `update_position_order_fields()` - Update order IDs on position
- `get_positions_by_window()` - Get all positions for a window

### Phase 2: Core Trading Operations
Critical path for trading logic.

#### 2.1 src/trading/execution.py
**Queries to migrate:**
- Line 45: Find position by order_id or hedge_order_id
- Line 220: Find position by order_id for limit sell
- Multiple UPDATE queries for order status

**Migration approach:**
- Use `get_position_by_order_id(cursor, order_id, order_type='ENTRY')`
- Use `update_order_status()` for status changes
- Keep position updates but also update orders table

#### 2.2 src/trading/settlement.py
**Queries to migrate:**
- Line 67: Check last settled position for token
- Line 87: Get position details by ID
- Line 148, 277, 348: Check merge status and hedge info
- Line 196: Get expired positions
- Line 428-435: Window settlement stats

**Migration approach:**
- Use `get_position_with_window()` for full position+window data
- Use `settle_position()` for settlement
- Query both positions and orders for complete picture

#### 2.3 src/bot.py
**Queries to migrate:**
- Line 99: Check if position is reversal
  
**Migration approach:**
- Simple query, use `get_position_with_window()`

### Phase 3: Position Management
Position sync and reversal logic.

#### 3.1 src/trading/position_manager/sync.py
**Queries to migrate:**
- Line 41: Get open positions with orders
- Line 99: Get all open positions
- Line 146: Get hedge info
- Line 197: Get position timestamp
- Line 261: Find hedged position by slug/side
- Line 342: Get open positions

**Migration approach:**
- Use `get_open_positions()` with joins
- Use `get_orders_for_position()` for order data
- Most queries benefit from normalized schema

#### 3.2 src/trading/position_manager/reversal.py
**Queries to migrate:**
- UPDATE queries for reversal tracking

**Migration approach:**
- Use `trigger_reversal()` helper
- Query positions table directly

### Phase 4: Notifications
Discord notification system - can be done last.

#### 4.1 src/utils/notifications.py (28 queries!)
**Pattern:** Most queries look up position by order_id, then format message

**Migration approach:**
- Replace all order_id lookups with `get_position_by_order_id()`
- This one function replaces ~10 different SELECT queries
- Update operations can use position_id directly

### Phase 5: Background Jobs
Lower priority utility scripts.

#### 5.1 src/trading/background_redemption.py
- UPDATE queries for redemption tracking

#### 5.2 redeem_old_trades.py / redeem_old_trades_simple.py
- One-off scripts, can keep as-is or update if needed

## Implementation Order

### Step 1: Add Missing Helpers (TODAY)
Add to `src/data/normalized_db.py`:
```python
def get_position_by_id(cursor, position_id: int) -> Optional[Dict]
def update_position_order(cursor, position_id: int, order_type: str, order_id: str)
def get_positions_by_window(cursor, window_id: int) -> List[Dict]
```

### Step 2: Migrate database.py (TODAY)
- `has_side_for_window()`
- `has_trade_for_window()`
- `generate_statistics()`

### Step 3: Migrate execution.py (TODAY/TOMORROW)
- Order tracking queries
- Status updates
- Test with paper trading

### Step 4: Migrate settlement.py (TOMORROW)
- Position settlement
- Window settlement
- Test thoroughly

### Step 5: Migrate bot.py (TOMORROW)
- Simple reversal check
- Quick win

### Step 6: Migrate sync.py (DAY 3)
- Position sync logic
- Order sync logic
- Critical for exchange sync

### Step 7: Migrate notifications.py (DAY 3-4)
- Bulk replace order_id lookups
- Test all notification types

### Step 8: Clean up (DAY 4+)
- Reversal.py
- Background jobs
- Remove legacy code markers

## Testing Strategy

After each file migration:
1. Run `uv run polyflup.py` in dry-run mode
2. Watch logs for errors
3. Verify queries return expected data
4. Run test_migration_012.py to check data consistency

## Rollback Plan

If issues arise:
- Legacy `trades` table continues to receive writes
- Normalized tables are additive only
- Can revert code changes without data loss
- Migration 012 is idempotent and safe

## Success Metrics

- [ ] All 38+ queries migrated to normalized schema
- [ ] No degradation in performance
- [ ] All tests passing
- [ ] Bot runs successfully with normalized queries
- [ ] Legacy trades table only used for backward compat

---

**Let's start with Phase 1: Add missing helpers and migrate database.py**
