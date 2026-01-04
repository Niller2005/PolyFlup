# Database Best Practices

## Using the Database Connection

### Context Manager Pattern

**ALWAYS use the `db_connection()` context manager:**

```python
from src.data.db_connection import db_connection

# ✅ CORRECT: Use context manager
with db_connection() as conn:
    c = conn.cursor()
    c.execute("SELECT * FROM trades")
    results = c.fetchall()
    # Commit happens automatically on success
```

```python
# ❌ WRONG: Don't call commit manually
with db_connection() as conn:
    c = conn.cursor()
    c.execute("INSERT INTO trades (...) VALUES (...)")
    conn.commit()  # ❌ DON'T DO THIS - context manager handles it
```

### Important Rules

1. **NEVER call `conn.commit()` manually**
   - The context manager handles commits automatically
   - Calling `conn.commit()` will cause Rust panics with libsql
   
2. **Automatic Transaction Management**
   - On success: `conn.commit()` is called automatically
   - On exception: `conn.rollback()` is called automatically
   - Connection is always closed properly

3. **Embedded Replica Syncing**
   - Reads: Sync every 30 seconds (first connection after interval)
   - Writes: Sync immediately after commit
   - No manual sync needed

## Database Connection Modes

### Local SQLite (Development)
```env
# .env
USE_TURSO=NO
USE_EMBEDDED_REPLICA=NO
```

- Uses `trades.db` local file
- No network calls
- Simple and fast for development

### Embedded Replica (RECOMMENDED for Production)
```env
# .env
USE_TURSO=NO
USE_EMBEDDED_REPLICA=YES
TURSO_DATABASE_URL=libsql://your-db.turso.io
TURSO_AUTH_TOKEN=your_token_here
EMBEDDED_REPLICA_FILE=trades_replica.db
```

- Local SQLite file with remote sync
- Fast reads (microsecond latency)
- Automatic sync every 30 seconds
- Immediate sync after writes
- Best of both worlds

### Direct Turso (Remote Only)
```env
# .env
USE_TURSO=YES
USE_EMBEDDED_REPLICA=NO
TURSO_DATABASE_URL=libsql://your-db.turso.io
TURSO_AUTH_TOKEN=your_token_here
```

- Direct connection to Turso
- All operations go through network
- Higher latency
- Not recommended for high-frequency operations

## Common Operations

### Reading Data
```python
# Simple read
with db_connection() as conn:
    c = conn.cursor()
    c.execute("SELECT * FROM trades WHERE settled = 0")
    open_trades = c.fetchall()
    # No commit needed for reads
```

### Writing Data
```python
# Insert
with db_connection() as conn:
    c = conn.cursor()
    c.execute(
        "INSERT INTO trades (symbol, side, entry_price) VALUES (?, ?, ?)",
        ("BTC", "UP", 0.52)
    )
    # Automatic commit on exit
```

### Update with Multiple Operations
```python
# Multiple operations in one transaction
with db_connection() as conn:
    c = conn.cursor()
    
    # Update trade
    c.execute("UPDATE trades SET order_status = ? WHERE id = ?", ("FILLED", 1))
    
    # Insert new record
    c.execute("INSERT INTO trades (...) VALUES (...)", (...))
    
    # All commits together on exit
```

### Error Handling
```python
try:
    with db_connection() as conn:
        c = conn.cursor()
        c.execute("INSERT INTO trades (...) VALUES (...)", (...))
        # Automatic commit
except Exception as e:
    # Automatic rollback already happened
    log(f"❌ Database error: {e}")
```

## Performance Tips

### For High-Frequency Reads (Position Monitoring)

With embedded replica mode, you get:
- **Instant local reads** (no network latency)
- **Automatic syncing** every 30 seconds
- **No manual optimization needed**

The bot checks positions every 1 second, which is handled efficiently:
```python
def check_open_positions():
    with db_connection() as conn:  # Opens connection
        c = conn.cursor()
        # First call within 30s: syncs with remote
        # Subsequent calls: uses local replica (instant)
        c.execute("SELECT * FROM trades WHERE settled = 0")
        positions = c.fetchall()
        
        for position in positions:
            # Process each position
            c.execute("UPDATE trades SET order_status = ? WHERE id = ?", (...))
            # Updates written to local file
            
        # On exit: commits and syncs to remote
```

### Batch Operations

For multiple writes, group them in one transaction:
```python
# ✅ Good: One transaction
with db_connection() as conn:
    c = conn.cursor()
    for trade_id in trade_ids:
        c.execute("UPDATE trades SET settled = 1 WHERE id = ?", (trade_id,))
    # One commit + one sync

# ❌ Bad: Multiple transactions
for trade_id in trade_ids:
    with db_connection() as conn:
        c = conn.cursor()
        c.execute("UPDATE trades SET settled = 1 WHERE id = ?", (trade_id,))
    # Multiple commits + multiple syncs
```

## Troubleshooting

### Rust Panic: `called Option::unwrap() on a None value`

**Cause:** Calling `conn.commit()` manually

**Solution:** Remove all manual `conn.commit()` calls
```python
# ❌ Wrong
with db_connection() as conn:
    c.execute(...)
    conn.commit()  # ← Remove this

# ✅ Correct
with db_connection() as conn:
    c.execute(...)
    # commit happens automatically
```

### Connection Outside Context Manager

**Cause:** Using `conn` or `c` outside the `with` block

```python
# ❌ Wrong
with db_connection() as conn:
    c = conn.cursor()
    c.execute("SELECT ...")
    data = c.fetchall()

# Connection closed here
for row in data:
    c.execute("UPDATE ...")  # ← Error: connection closed
```

**Solution:** Keep all operations inside the context
```python
# ✅ Correct
with db_connection() as conn:
    c = conn.cursor()
    c.execute("SELECT ...")
    data = c.fetchall()
    
    # Process inside the context
    for row in data:
        c.execute("UPDATE ...")
```

### Sync Issues

If data isn't appearing in Turso dashboard immediately:
- Embedded replica syncs after writes automatically
- Check sync interval: 30 seconds for reads
- Force manual sync: `python sync_replica.py` (if needed)

### PRAGMA Errors with Turso

**Error:** `SQL not allowed statement: PRAGMA journal_mode=WAL`

**Cause:** PRAGMA statements don't work with remote Turso

**Solution:** Already handled in `database.py`:
```python
# PRAGMA only for local SQLite
if not USE_TURSO and not USE_EMBEDDED_REPLICA:
    c.execute("PRAGMA journal_mode=WAL")
```

## Migration from Old Code

If you have code with manual commits:

**Before:**
```python
conn = sqlite3.connect('trades.db')
c = conn.cursor()
c.execute("INSERT ...")
conn.commit()  # Manual commit
conn.close()   # Manual close
```

**After:**
```python
with db_connection() as conn:
    c = conn.cursor()
    c.execute("INSERT ...")
    # Automatic commit and close
```

## Testing Database Code

```python
def test_database_operation():
    """Test database operation"""
    # Setup test data
    with db_connection() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM trades WHERE symbol = 'TEST'")
        
    # Test operation
    with db_connection() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO trades (symbol, side) VALUES (?, ?)",
            ("TEST", "UP")
        )
    
    # Verify
    with db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM trades WHERE symbol = 'TEST'")
        result = c.fetchone()
        assert result is not None
        
    # Cleanup
    with db_connection() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM trades WHERE symbol = 'TEST'")

if __name__ == "__main__":
    test_database_operation()
    print("✓ Test passed")
```

## Summary

### Do's ✅
- Always use `with db_connection() as conn:`
- Let context manager handle commits
- Use embedded replica for production
- Group multiple operations in one transaction
- Handle exceptions appropriately

### Don'ts ❌
- Never call `conn.commit()` manually
- Don't use `conn` or `c` outside the `with` block
- Don't create connections without context manager
- Don't use PRAGMA statements with Turso
- Don't call `conn.close()` manually

---

**See Also:**
- [TURSO_MIGRATION.md](./TURSO_MIGRATION.md) - Turso setup and migration
- [EMBEDDED_REPLICAS.md](./EMBEDDED_REPLICAS.md) - Embedded replica details
- [MIGRATIONS.md](./MIGRATIONS.md) - Database schema migrations
