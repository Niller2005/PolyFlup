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

## Schema Overview (v0.6.0+)

### Normalized Schema (Migration 012+)

**NEW**: As of migration 012, the database uses a normalized schema with three main tables:

#### 1. `windows` table
Trading window metadata (one per 15-minute window per symbol):
- Window identification: `id`, `symbol`, `window_start`, `window_end`
- Market metadata: `slug`, `token_id`, `condition_id`
- Market data: `p_yes`, `best_bid`, `best_ask`, `imbalance`, `funding_bias`, `market_prior_p_up`
- Signal scores: `up_total`, `down_total`, `momentum_score`, `flow_score`, `divergence_score`, etc.
- Settlement: `final_outcome`

#### 2. `positions` table
Trading positions (can have multiple per window for reversals/hedges):
- Position ID: `id`, `window_id`, `created_at`
- Trade details: `side`, `entry_price`, `size`, `bet_usd`, `edge`
- Confidence: `additive_confidence`, `additive_bias`, `bayesian_confidence`, `bayesian_bias`
- Position type: `is_reversal`, `is_hedged`, `target_price`
- Scale-in: `scaled_in`, `last_scale_in_at`
- Settlement: `settled`, `settled_at`, `exited_early`, `exit_price`, `pnl_usd`, `roi_pct`
- Hedge tracking: `hedge_exit_price`, `hedge_exited_early`
- CTF: `merge_tx_hash`, `redeem_tx_hash`
- Reversals: `reversal_triggered`, `reversal_triggered_at`

#### 3. `orders` table
Exchange orders (multiple per position):
- Order ID: `id`, `position_id`, `created_at`
- Order details: `order_id` (exchange ID), `order_type`, `order_status`, `price`, `size`
- Tracking: `filled_at`, `cancelled_at`
- Order types: `ENTRY`, `LIMIT_SELL`, `SCALE_IN`, `HEDGE`

### Relationships
- `windows` (1) → (many) `positions`
- `positions` (1) → (many) `orders`
- A reversal creates a NEW position in the same window with opposite side
- A hedge creates a NEW order linked to the same position

### Using the Normalized Schema

**For new code, use `src.data.normalized_db` functions:**

```python
from src.data.normalized_db import (
    get_or_create_window,
    create_position,
    create_order,
    get_open_positions,
    get_position_with_window,
    settle_position,
)

# Create a window and position
with db_connection() as conn:
    c = conn.cursor()
    
    # Get or create window
    window_id = get_or_create_window(
        c, symbol="ETH", window_start="2026-01-19T12:00:00Z",
        window_end="2026-01-19T12:15:00Z", slug="eth-...", token_id="0x..."
    )
    
    # Create position
    position_id = create_position(
        c, window_id=window_id, side="UP", 
        entry_price=0.52, size=10.0, bet_usd=5.2
    )
    
    # Create entry order
    create_order(
        c, position_id=position_id, order_type="ENTRY",
        order_id="0xabc...", order_status="FILLED", price=0.52, size=10.0
    )
    
    # Query open positions (with window data joined)
    positions = get_open_positions(c, symbol="ETH")
```

### Common Queries

#### Get open positions with window data
```python
c.execute("""
    SELECT 
        p.id, p.side, p.size, p.bet_usd, p.entry_price,
        w.symbol, w.window_start, w.window_end, w.token_id
    FROM positions p
    JOIN windows w ON p.window_id = w.id
    WHERE p.settled = 0 AND p.exited_early = 0
""")
```

#### Get all orders for a position
```python
c.execute("""
    SELECT order_type, order_id, order_status, price, size
    FROM orders
    WHERE position_id = ?
""", (position_id,))
```

#### Find position by exchange order ID
```python
c.execute("""
    SELECT p.*, w.symbol, w.token_id
    FROM orders o
    JOIN positions p ON o.position_id = p.id
    JOIN windows w ON p.window_id = w.id
    WHERE o.order_id = ?
""", (exchange_order_id,))
```

### Legacy `trades` Table

The legacy `trades` table is retained for backward compatibility but will be deprecated.
- All new writes to `trades` also populate the normalized tables
- Existing queries continue to work
- Gradually migrate queries to use normalized schema

### WAL Mode
- Database uses Write-Ahead Logging (WAL) mode for better concurrency
- Enabled on initialization: `PRAGMA journal_mode=WAL`
- Allows concurrent readers while writes are in progress

### Migration History
- **Migration 012** (v0.6.0): Normalized schema into `windows`, `positions`, and `orders` tables
- **Migration 011**: Added hedge exit tracking columns (`hedge_exit_price`, `hedge_exited_early`)
- **Migration 010**: Added `redeem_tx_hash` for post-resolution redemption tracking
- **Migration 009**: Added CTF merge tracking columns (`condition_id`, `merge_tx_hash`)
- **Migration 008**: Added hedge order tracking columns (`hedge_order_id`, `hedge_order_price`, `is_hedged`)
- **Migration 007** (v0.5.0): Added Bayesian confidence comparison columns for A/B testing
- **Migration 006** (v0.4.x): Added raw signal score columns for confidence formula calibration
- **Migration 005**: Added `last_scale_in_at` column for tracking scale-in timing
- **Migration 004**: Added `reversal_triggered_at` column for timing reversals
- **Migration 003**: Added `reversal_triggered` column for reversal tracking
- **Migration 002**: Added timestamp verification
- **Migration 001**: Added `scale_in_order_id` column for tracking pending scale-in orders

