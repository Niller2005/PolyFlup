# Quick Reference - New Features

Quick reference for all new features and modular architecture added in Jan 2026.

---

## Modular Architecture (New)

The backend has been fully modularized into packages. You can still import from the main package, or directly from submodules for clarity:

### Order Management (`src.trading.orders`)
- `client.py`: CLOB Client & API Creds
- `limit.py`: Limit & Batch Orders
- `market.py`: Market Orders
- `management.py`: Order status, retrieval, and cancellation.
- `positions.py`: Balance & Positions
- `market_info.py`: Pricing (midpoints, spreads), tick sizes, and server time.
- `notifications.py`: Legacy notification polling support.
- `scoring.py`: Liquidity reward scoring checks.
- `constants.py`: BUY/SELL constants and API constraints.
- `utils.py`: Validation helpers, retry logic, and truncation.

### Market Data (`src.data.market_data`)
- `polymarket.py`: CLOB-specific data (token IDs, slugs, internal momentum).
- `binance.py`: Spot price fetching and window start tracking.
- `indicators.py`: Technical analysis (ADX, RSI, VWM).
- `analysis.py`: Order flow and cross-exchange divergence.
- `external.py`: Funding rates and Fear & Greed index.

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

### Batch Prices
```python
from src.trading.orders import get_multiple_market_prices

# Fetch prices for all open positions in 1 call
prices = get_multiple_market_prices(["token_id1", "token_id2"])
```

### Get Accurate Fill Details
```python
from src.trading.orders import get_trades_for_user

# Get exact fill prices and fees for current user
trades = get_trades_for_user(user_address, asset_id="123...")
```

### Audit Closed Positions
```python
from src.trading.orders import get_closed_positions

# Verify settled trades against exchange history
closed = get_closed_positions(user_address, limit=20)
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

### Check Intervals
The bot operates on a multi-tiered monitoring schedule:
- **1s (Passive)**: Position monitoring and P&L updates.
- **10s (Active)**: Order status synchronization with exchange and deep self-healing.
- **20s**: Market evaluation and new trade entry logic.
- **60s (Verbose)**: Summary logging and settlement audit.
- **30s (Legacy)**: Notification polling (now largely superseded by real-time WebSocket).

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

## Database

### Local SQLite
The bot uses a local `trades.db` file for all operations.

### Database Migrations


#### Check Migration Status
```bash
uv run check_migration_status.py
```

#### Add New Migration
1. Create function in `src/data/migrations.py`:
```python
def migration_003_add_my_column(conn: Any) -> None:
    c = conn.cursor()
    c.execute("ALTER TABLE trades ADD COLUMN my_column TEXT")
    # Automatic commit by context manager
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
# Exit Plan Management
EXIT_MIN_POSITION_AGE=60               # Wait 1 minute before exit plan (default: 60)

# Unfilled Order Management
UNFILLED_TIMEOUT_SECONDS=300           # Cancel after 5 minutes (default: 300)
UNFILLED_RETRY_ON_WINNING_SIDE=YES     # Retry at market if winning (default: YES)

# Reversal & Stop Loss (NEW)
ENABLE_HEDGED_REVERSAL=YES             # Hold both sides during trend flip
STOP_LOSS_PRICE=0.30                   # Stop out if midpoint <= $0.30
LOSING_SIDE_MIN_CONFIDENCE=0.40        # Min 40% confidence for underdog entries
```

---

## Common Use Cases

### Scale-In with Exit Plan
```python
# Exit plan automatically updates when scale-in fills
# No manual intervention needed!

# 1. Exit plan places order at 60s for initial size
# 2. Scale-in triggers and fills (using Market Order)
# 3. Exit plan order automatically updated for new total size
# 4. Full position covered ✅
```

### Hedged Reversal
```python
# Bot holds UP position.
# Signal flips to DOWN with high confidence.
# Bot opens DOWN position WITHOUT closing UP.
# Both positions managed independently.
# Losing side clears via $0.30 Stop Loss.
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
**Solution:** Exit plan order automatically updated when scale-in fills (robust status sync handles MATCHED/FILLED)

### "Evaluating 4 markets ... Spread too wide (UP: 1.000, DOWN: 1.000). SKIPPING."
**Cause:** New window just started, order book is empty (warm-up phase)  
**Solution:** Bot now retries evaluation every 10s up to 3 times when it detects zero liquidity.

### "Zombie position stuck in loop (Price Unavailable)"
**Cause:** Market is closed or illiquid, API cannot return midpoint price.  
**Solution:** Bot now automatically force-settles trades after 3 consecutive failed price checks to clear the queue.

### "Insufficient funds loop on Stop Loss"
**Cause:** Stop loss triggered while target fill was also happening.
**Solution:** Bot now verifies if exit target has already filled before attempting stop loss.

---

## Function Reference


### Most Useful New Functions

| Function | Purpose | Example |
|----------|---------|---------|
| `place_batch_orders()` | Place multiple orders | 4 markets in 1 call |
| `place_market_order()` | Market sell/buy | Stop loss execution |
| `get_midpoint()` | Get accurate price | P&L calculation |
| `get_multiple_market_prices()` | Batch price fetch | Efficient P&L monitoring |
| `get_balance_allowance()` | Check balance | Pre-flight validation |
| `check_liquidity()` | Check spread | Avoid illiquid markets |
| `get_notifications()` | Real-time fills | Automatic monitoring |
| `get_trades_for_user()` | Exchange fill history | Accurate execution audit |
| `get_closed_positions()` | Closed trade history | Settlement verification |
| `cancel_all()` | Emergency stop | Cancel everything |
| `run_migrations()` | Schema updates | No data loss |
| `process_notifications()` | Monitor fills | Real-time updates |
| `_update_exit_plan_after_scale_in()` | Update exit after scale | Cover full position |
| `check_order_scoring()` | Reward verification | Earn liquidity rewards |
| `truncate_float()` | Precision safety | Prevent balance errors |
| `sync_positions_with_exchange()` | Startup sync | Adopts untracked positions |
| `get_polymarket_momentum()` | Native trend | Better entry accuracy |
| `get_bulk_spreads()` | Bulk liquidity check | Efficient market filtering |

---

**For detailed information, see `SESSION_IMPROVEMENTS.md`**
