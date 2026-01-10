---
name: database-sqlite
description: SQLite best practices, connection management, and migration system for PolyFlup.
---

## Database Best Practices

### Connection Management
**ALWAYS use the `db_connection()` context manager:**

```python
from src.data.db_connection import db_connection

with db_connection() as conn:
    c = conn.cursor()
    c.execute("SELECT * FROM trades")
    # Commit happens automatically on success
```

- **NEVER** call `conn.commit()` manually.
- **Deadlock Prevention**: When calling write functions (like `execute_trade`) from within an existing transaction, **MUST pass the active cursor**.

### Migration System
The migration system tracks versions in the `schema_version` table.

#### Adding a New Migration:
1. Create a migration function in `src/data/migrations.py`.
2. Register it in the `MIGRATIONS` list.
3. Update the base schema in `src/data/database.py`.

#### Rules:
- Check if columns/indices exist before creating.
- Migrations must be idempotent.
- Never delete or modify existing migrations.

### Schema Overview
Main table: `trades`
- Core Fields: `id`, `timestamp`, `symbol`, `side`, `entry_price`, `size`, `bet_usd`, `edge`
- Order Tracking: `order_id`, `order_status`, `limit_sell_order_id`, `scale_in_order_id`
- Position Management: `scaled_in`, `is_reversal`, `target_price`, `reversal_triggered`, `reversal_triggered_at`
- Settlement: `settled`, `settled_at`, `exited_early`, `final_outcome`, `exit_price`, `pnl_usd`, `roi_pct`
- Timing: `window_start`, `window_end`, `last_scale_in_at`
- Market Data: `slug`, `token_id`, `p_yes`, `best_bid`, `best_ask`, `imbalance`, `funding_bias`

### Key Database Patterns

#### Position Queries
```python
# Get open positions with all relevant data
c.execute("""
    SELECT id, symbol, token_id, side, entry_price, size, bet_usd, 
           limit_sell_order_id, scale_in_order_id, scaled_in, edge, 
           last_scale_in_at, window_end
    FROM trades 
    WHERE settled = 0 AND exited_early = 0 
    AND datetime(window_end) > datetime(?)
""", (now.isoformat(),))
```

#### Trade Updates
```python
# Update position size after scale-in
c.execute("""
    UPDATE trades 
    SET size = ?, bet_usd = ? * entry_price, 
        scaled_in = 1, last_scale_in_at = ?
    WHERE id = ?
""", (new_size, new_size, now.isoformat(), trade_id))
```

#### Settlement
```python
# Settle position with exit data
c.execute("""
    UPDATE trades 
    SET settled = 1, exited_early = 1, exit_price = ?, 
        pnl_usd = ?, roi_pct = ?, settled_at = ?
    WHERE id = ?
""", (exit_price, pnl_usd, roi_pct, now.isoformat(), trade_id))
```

### WAL Mode
- Database uses Write-Ahead Logging (WAL) mode for better concurrency
- Enabled on initialization: `PRAGMA journal_mode=WAL`
- Allows concurrent readers while writes are in progress
