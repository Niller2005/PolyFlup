# Database Migrations Guide

This document explains how to add new database migrations to PolyAstra.

## How It Works

The migration system tracks schema versions in a `schema_version` table. On bot startup, `init_database()` automatically runs all pending migrations.

## Migration Registry

Migrations are defined in `src/data/migrations.py` in the `MIGRATIONS` list:

```python
MIGRATIONS: List[tuple[int, str, Callable]] = [
    (1, "Add scale_in_order_id column", migration_001_add_scale_in_order_id),
    (2, "Verify timestamp column", migration_002_add_created_at_column),
    (3, "Add reversal_triggered column", migration_003_add_reversal_triggered_column),
    (4, "Add reversal_triggered_at column", migration_004_add_reversal_triggered_at_column),
]
```

## Adding a New Migration

### Step 1: Create Migration Function

Add a new function to `src/data/migrations.py`:

```python
def migration_003_add_my_new_column(conn: sqlite3.Connection) -> None:
    """Description of what this migration does"""
    c = conn.cursor()
    
    # Check if column already exists (for safety)
    c.execute("PRAGMA table_info(trades)")
    columns = [row[1] for row in c.fetchall()]
    
    if "my_new_column" not in columns:
        log("  - Adding my_new_column...")
        c.execute("ALTER TABLE trades ADD COLUMN my_new_column TEXT")
        log("    ✓ Column added")
    else:
        log("    ✓ my_new_column already exists")
```

### Step 2: Register Migration

Add to the `MIGRATIONS` list:

```python
MIGRATIONS: List[tuple[int, str, Callable]] = [
    (1, "Add scale_in_order_id column", migration_001_add_scale_in_order_id),
    (2, "Verify timestamp column", migration_002_add_created_at_column),
    (3, "Add my new column", migration_003_add_my_new_column),  # NEW
]
```

### Step 3: Update Schema in database.py

Update the `CREATE TABLE IF NOT EXISTS trades` statement to include the new column:

```python
c.execute("""
    CREATE TABLE IF NOT EXISTS trades (
        ...existing columns...
        my_new_column TEXT,  -- NEW
        ...rest of columns...
    )
""")
```

This ensures fresh databases have the column from the start.

### Step 4: Test

Run the bot or test with:

```python
from src.data.database import init_database
init_database()
```

The migration will run automatically and be tracked.

## Migration Best Practices

### ✅ DO:
- Always check if column/index exists before creating
- Use descriptive migration names
- Log progress during migration
- Include the migration in both `MIGRATIONS` list AND base schema
- Test migrations on a copy of production database first
- Make migrations idempotent (safe to run multiple times)

### ❌ DON'T:
- Don't delete or modify existing migrations
- Don't skip version numbers
- Don't make destructive changes (DROP TABLE, DROP COLUMN)
- Don't assume data state - check before migrating
- Don't call `conn.commit()` manually - it is handled by the context manager

## Common Migration Patterns

### Add Column
```python
def migration_00X_add_column(conn: Any) -> None:
    c = conn.cursor()
    c.execute("PRAGMA table_info(trades)")
    columns = [row[1] for row in c.fetchall()]
    
    if "new_column" not in columns:
        c.execute("ALTER TABLE trades ADD COLUMN new_column TEXT DEFAULT NULL")
        # Automatic commit by context manager
```

### Add Index
```python
def migration_00X_add_index(conn: Any) -> None:
    c = conn.cursor()
    c.execute("CREATE INDEX IF NOT EXISTS idx_new_column ON trades(new_column)")
```

### Data Migration
```python
def migration_00X_update_data(conn: Any) -> None:
    c = conn.cursor()
    c.execute("UPDATE trades SET new_column = 'default_value' WHERE new_column IS NULL")
```

### Rename Column (Complex)
SQLite doesn't support RENAME COLUMN in older versions. Use:
```python
def migration_00X_rename_column(conn: sqlite3.Connection) -> None:
    c = conn.cursor()
    # 1. Add new column
    # 2. Copy data: UPDATE trades SET new_name = old_name
    # 3. Drop old column (requires table rebuild - avoid if possible)
```

## Current Schema Version

Check current version:
```bash
uv run python -c "import sqlite3; conn = sqlite3.connect('trades.db'); c = conn.cursor(); c.execute('SELECT MAX(version) FROM schema_version'); print(f'Schema version: {c.fetchone()[0]}'); conn.close()"
```

## Applied Migrations

| Version | Description | Applied |
|---------|-------------|---------|
| 1 | Add scale_in_order_id column | ✅ |
| 2 | Verify timestamp column | ✅ |
| 3 | Add reversal_triggered column | ✅ |
| 4 | Add reversal_triggered_at column | ✅ |

## Rollback

Migrations don't support automatic rollback. To rollback:

1. **Backup database first**: `cp trades.db trades.db.backup`
2. Manually reverse the migration with SQL
3. Delete version from schema_version table

Example:
```python
import sqlite3
conn = sqlite3.connect('trades.db')
c = conn.cursor()

# Remove the column (requires table rebuild in SQLite)
# OR set version back
with db_connection() as conn:
    c = conn.cursor()
    c.execute("DELETE FROM schema_version WHERE version = 3")
```

## Testing Migrations

Always test on a copy of production database:

```bash
# Copy production database
cp trades.db trades_test.db

# Edit DB_FILE in settings temporarily
# Run bot or init_database()

# Check if migration worked
uv run check_db.py
```
