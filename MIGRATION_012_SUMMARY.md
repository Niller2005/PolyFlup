# Database Schema Normalization - Migration 012

## Summary

Successfully implemented database schema normalization, splitting the monolithic `trades` table into three normalized tables:

1. **`windows`** - Trading window metadata (1053 records migrated)
2. **`positions`** - Position tracking (1260 records migrated)
3. **`orders`** - Order tracking (2052 records migrated)

## Migration Results

```
✓ Migrated 1053 windows
✓ Migrated 1260 positions  
✓ Migrated 2052 orders
  - 1111 entry orders
  - 686 limit_sell orders
  - 255 hedge orders
  - 0 scale_in orders
```

All data integrity checks passed. Position count matches legacy trade count perfectly.

## Benefits

1. **Clearer separation of concerns**: Windows track trading periods, positions track holdings, orders track exchange activity
2. **Better reversal support**: A window can have multiple positions (initial + reversal)
3. **Better hedge support**: Each position can have multiple orders (entry + hedge + limit sell + scale-in)
4. **Easier queries**: Join tables as needed instead of cramming everything into one row
5. **Less data duplication**: Window-level data stored once per window instead of per trade
6. **Simpler updates**: Update order status without touching position or window data

## Database Structure

### `windows` table
- One record per unique (symbol, window_start) combination
- Stores market data and signal scores at window opening
- Stores final_outcome after settlement
- 1053 unique windows from 1260 positions (some windows had reversals)

### `positions` table  
- One record per position (including reversals and hedges)
- Links to window via `window_id` foreign key
- Stores position-specific data: side, entry_price, size, bet_usd, etc.
- Tracks settlement status and P&L

### `orders` table
- One record per exchange order
- Links to position via `position_id` foreign key
- Tracks order type (ENTRY, LIMIT_SELL, SCALE_IN, HEDGE)
- Stores exchange order ID and status

## Helper Functions

New module `src/data/normalized_db.py` provides functions for working with the normalized schema:

**Window operations:**
- `get_or_create_window()` - Get existing or create new window
- `update_window_outcome()` - Set final outcome after settlement
- `get_window_by_symbol_and_time()` - Query window by symbol/time

**Position operations:**
- `create_position()` - Create new position
- `get_open_positions()` - Get all unsettled positions (with window data joined)
- `get_position_with_window()` - Get position by ID with window data
- `update_position_size()` - Update size after scale-in
- `settle_position()` - Settle position with exit data
- `trigger_reversal()` - Mark reversal triggered
- `has_position_for_window()` - Check if position exists

**Order operations:**
- `create_order()` - Create new order
- `update_order_status()` - Update order status
- `get_orders_for_position()` - Get all orders for a position
- `get_order_by_exchange_id()` - Find order by exchange ID
- `get_position_by_order_id()` - Find position by exchange order ID

**Statistics:**
- `get_total_exposure()` - Total USD in open positions
- `get_performance_stats()` - Overall performance metrics

## Backward Compatibility

The legacy `trades` table is retained for backward compatibility:
- New writes to `trades` via `save_trade()` also populate normalized tables
- All existing queries continue to work
- Migration is non-breaking

## Next Steps

The codebase has 38 files with direct SQL queries against the `trades` table. These should be gradually migrated to use the normalized schema:

### Priority 1: Core trading operations
- `src/trading/execution.py` - Order execution and tracking
- `src/trading/settlement.py` - Position settlement  
- `src/bot.py` - Main trading loop

### Priority 2: Position management
- `src/trading/position_manager/sync.py` - Exchange sync operations

### Priority 3: Notifications and reporting
- `src/utils/notifications.py` - Discord notifications
- `src/data/database.py` - Statistics generation

### Migration Strategy

For each file:
1. Identify all SQL queries against `trades` table
2. Refactor to use normalized tables or helper functions from `normalized_db.py`
3. Test thoroughly to ensure behavior unchanged
4. Update gradually, one module at a time

Example refactor:
```python
# OLD - Direct trades table query
c.execute("SELECT * FROM trades WHERE order_id = ? AND settled = 0", (order_id,))

# NEW - Use helper function
from src.data.normalized_db import get_position_by_order_id
position = get_position_by_order_id(c, order_id)
```

## Testing

Run `test_migration_012.py` to verify migration:
```bash
uv run test_migration_012.py
```

## Documentation

Updated `.opencode/skill/database-sqlite/SKILL.md` with:
- Normalized schema overview
- Table relationships
- Common query patterns
- Migration from legacy schema

---

**Status**: Migration complete and tested successfully. Legacy compatibility maintained. Ready for gradual query migration.
