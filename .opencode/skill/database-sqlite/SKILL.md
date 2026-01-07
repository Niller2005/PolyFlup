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
- Fields: `id`, `timestamp`, `symbol`, `side`, `entry_price`, `size`, `settled`, `final_outcome`, `exit_price`, `pnl_usd`, `roi_pct`, `order_id`, `order_status`, `target_price`, `is_reversal`, `edge`.
