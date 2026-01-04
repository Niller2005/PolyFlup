# Quick Reference - New Features

Quick reference for all new features added in the 2026-01-04 session.

---

## Order Placement

### Batch Orders
```python
from src.trading.orders import place_batch_orders

orders = [
    {"token_id": "123...", "price": 0.50, "size": 10.0, "side": "BUY"},
    {"token_id": "456...", "price": 0.60, "size": 15.0, "side": "BUY"},
]
results = place_batch_orders(orders)
```

### Market Orders
```python
from src.trading.orders import place_market_order

# Sell at market price
result = place_market_order(
    token_id="123...",
    amount=10.0,  # 10 shares
    side="SELL",
    order_type="FAK"  # Fill partial and kill rest
)
```

### Advanced Order Types
```python
from src.trading.orders import place_limit_order
import time

# GTD order (expires in 5 minutes)
expiration = int(time.time()) + 300
result = place_limit_order(
    token_id="123...",
    price=0.50,
    size=10.0,
    side="BUY",
    order_type="GTD",
    expiration=expiration
)
```

---

## Order Management

### Get Active Orders
```python
from src.trading.orders import get_orders

# All active orders
orders = get_orders()

# Orders for specific market
btc_orders = get_orders(market="0xbd31...")

# Orders for specific token
token_orders = get_orders(asset_id="123...")
```

### Bulk Cancel
```python
from src.trading.orders import cancel_orders, cancel_market_orders, cancel_all

# Cancel specific orders
result = cancel_orders(["0xabc...", "0xdef..."])

# Cancel all orders for a market
result = cancel_market_orders(market="0xbd31...")

# Emergency: cancel everything
result = cancel_all()
```

---

## Market Data

### Get Prices
```python
from src.trading.orders import get_midpoint

# Get accurate midpoint price
price = get_midpoint(token_id)
```

### Check Liquidity
```python
from src.trading.orders import get_spread, check_liquidity

# Get spread
spread = get_spread(token_id)
print(f"Spread: {spread*100:.1f}%")

# Check if liquidity is good
if check_liquidity(token_id, size=100, warn_threshold=0.05):
    # Safe to trade
    place_order(...)
```

### Get Tick Size
```python
from src.trading.orders import get_tick_size

tick = get_tick_size(token_id)
# Returns: 0.01, 0.001, 0.0001, etc.
```

---

## Balance Checking

### Check Balance Before Trading
```python
from src.trading.orders import get_balance_allowance

# Check USDC balance
balance_info = get_balance_allowance()
if balance_info["balance"] >= bet_amount:
    place_order(...)

# Check conditional token balance
token_balance = get_balance_allowance(token_id="123...")
if token_balance["balance"] >= size:
    sell_position(...)
```

---

## Monitoring

### Real-Time Notifications
```python
from src.utils.notifications import process_notifications

# Process notifications (runs automatically in bot)
process_notifications()

# Logs:
# 🔔 Order filled: BUY 10.0 @ $0.52
#   ✅ Buy order for trade #53 filled
```

### Trade History
```python
from src.trading.orders import get_trades

# Get recent trades
trades = get_trades(asset_id=token_id, limit=10)

for trade in trades:
    print(f"Filled: {trade['size']} @ ${trade['price']}")
```

### Server Time
```python
from src.trading.orders import get_server_time

server_time = get_server_time()
# Returns Unix timestamp
```

---

## Database Migrations

### Check Migration Status
```bash
python check_migration_status.py
```

### Add New Migration
1. Create function in `src/data/migrations.py`:
```python
def migration_003_add_my_column(conn: sqlite3.Connection) -> None:
    c = conn.cursor()
    c.execute("ALTER TABLE trades ADD COLUMN my_column TEXT")
    conn.commit()
```

2. Add to `MIGRATIONS` list:
```python
MIGRATIONS = [
    (1, "Add scale_in_order_id", migration_001_add_scale_in_order_id),
    (2, "Verify timestamp", migration_002_add_created_at_column),
    (3, "Add my column", migration_003_add_my_column),  # NEW
]
```

3. Restart bot - migration runs automatically!

---

## Configuration

### New Settings (.env)
```env
# Unfilled Order Management
UNFILLED_TIMEOUT_SECONDS=300           # Cancel after 5 minutes (default: 300)
UNFILLED_RETRY_ON_WINNING_SIDE=YES     # Retry at market if winning (default: YES)
```

---

## Common Use Cases

### Scale-In with Exit Plan
```python
# Exit plan automatically updates when scale-in fills
# No manual intervention needed!

# 1. Exit plan places order at 300s for initial size
# 2. Scale-in triggers and fills
# 3. Exit plan order automatically updated for new total size
# 4. Full position covered ✅
```

### Pre-Flight Checks
```python
# Check balance before trading
balance = get_balance_allowance()
if balance["balance"] < bet_amount:
    return  # Skip trade

# Check liquidity
if not check_liquidity(token_id, size):
    return  # Skip trade - low liquidity
```

### Verify Execution
```python
# Place order
result = place_order(token_id, price, size)

# Verify it filled
trades = get_trades(asset_id=token_id, limit=10)
my_trade = next((t for t in trades if t['id'] == result['order_id']), None)
if my_trade:
    actual_price = my_trade['price']
```

### Emergency Stop
```python
# Cancel all orders immediately
result = cancel_all()
print(f"Cancelled {len(result['canceled'])} orders")
```

---

## Troubleshooting

### "Trades not saving"
**Cause:** Database auto-commit bug (fixed)  
**Solution:** Update code, restart bot

### "FOK order couldn't be filled"
**Cause:** Not enough liquidity for full fill  
**Solution:** Market orders now use FAK (partial fills allowed)

### "Unfilled order spam in logs"
**Cause:** Timeout logic retrying every second  
**Solution:** Anti-spam fix checks order_status before retry

### "Exit plan monitoring spam"
**Cause:** Logging every position check (every 1-2 seconds)  
**Solution:** Monitoring logs only on verbose cycles (every 60 seconds)

### "Notification spam with None values"
**Cause:** Logging all order fills including untracked orders  
**Solution:** Only log fills for orders tracked in database, skip others silently

### "Price validation failed"
**Cause:** Floating point precision (0.485 instead of 0.48)  
**Solution:** All prices now rounded to tick size

### "Exit plan only sold half position"
**Cause:** Scale-in increased position size but exit plan order wasn't updated  
**Solution:** Exit plan order automatically updated when scale-in fills

**Example:**
```
Initial: 5.62 shares, Exit plan: 5.62 @ $0.99
Scale-in: +5.62 shares → Total: 11.24
Auto-update: Cancel old, place new exit plan for 11.24 shares
Result: Full position covered ✅
```

---

## Function Reference

### Most Useful New Functions

| Function | Purpose | Example |
|----------|---------|---------|
| `place_batch_orders()` | Place multiple orders | 4 markets in 1 call |
| `place_market_order()` | Market sell/buy | Stop loss execution |
| `get_midpoint()` | Get accurate price | P&L calculation |
| `get_balance_allowance()` | Check balance | Pre-flight validation |
| `check_liquidity()` | Check spread | Avoid illiquid markets |
| `get_notifications()` | Real-time fills | Automatic monitoring |
| `cancel_all()` | Emergency stop | Cancel everything |
| `run_migrations()` | Schema updates | No data loss |
| `process_notifications()` | Monitor fills | Real-time updates |
| `_update_exit_plan_after_scale_in()` | Update exit after scale | Cover full position |

---

**For detailed information, see `SESSION_IMPROVEMENTS.md`**
