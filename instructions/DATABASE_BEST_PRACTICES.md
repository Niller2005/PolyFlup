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
   - The context manager handles commits automatically on successful exit of the `with` block.
    
2. **Automatic Transaction Management**
   - On success: `conn.commit()` is called automatically
   - On exception: `conn.rollback()` is called automatically
    - Connection is always closed properly

## Avoiding "Database is Locked" Deadlocks

When calling functions that write to the database (like `execute_trade` or `save_trade`) from within an existing database transaction, you **MUST** pass the active cursor.

Opening a second connection (`with db_connection()`) while a first connection has an uncommitted transaction will cause a `database is locked` error in SQLite.

**✅ CORRECT: Passing the cursor**
```python
def check_positions():
    with db_connection() as conn:
        c = conn.cursor()
        # ... logic ...
        if needs_reversal:
            # Pass the cursor to use the existing transaction
            execute_trade(params, cursor=c) 
```

**❌ WRONG: Nested connections**
```python
def check_positions():
    with db_connection() as conn:
        c = conn.cursor()
        # ... logic ...
        if needs_reversal:
            # This will fail with "database is locked" 
            # because execute_trade internally tries to open a new connection
            execute_trade(params) 
```

## Database Connection Mode


The bot uses a local SQLite file (`trades.db`) located in the project root.

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

### WAL Mode
The bot automatically enables Write-Ahead Logging (WAL) mode for better concurrency during initialization in `src/data/database.py`.

### Batch Operations

For multiple writes, group them in one transaction:
```python
# ✅ Good: One transaction
with db_connection() as conn:
    c = conn.cursor()
    for trade_id in trade_ids:
        c.execute("UPDATE trades SET settled = 1 WHERE id = ?", (trade_id,))
    # One commit
```

## Troubleshooting

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

---

**See Also:**
- [MIGRATIONS.md](./MIGRATIONS.md) - Database schema migrations
