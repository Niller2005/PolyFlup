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

### Execution & Logic (`src.trading`)
- `execution.py`: Centralized trade execution (`execute_trade`).
- `logic.py`: Parameter preparation and side determination.

---

## Order Placement

### Standard Trade Execution
```python
from src.trading import execute_trade

# Execute a trade with full tracking and notifications
trade_id = execute_trade(trade_params, is_reversal=False)
```

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

## Reversal & Stop Loss Logic

### Reversal-First Stop Loss
The bot prioritizes flipping the position before exiting.
1. **Trigger:** Midpoint price hits `$0.30` (or `STOP_LOSS_PRICE`).
2. **Action 1 (Reversal):** If `reversal_triggered` is 0, buy the opposite side and mark original as reversed.
3. **Action 2 (Stop Loss):** If `reversal_triggered` is 1, execute market sell to clear the losing position.

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

---

## Monitoring

### Check Intervals
The bot operates on a multi-tiered monitoring schedule:
- **1s (Passive)**: Position monitoring, P&L updates, and **Exit Plan size healing**.
- **10s (Active)**: Order status synchronization with exchange and deep self-healing.
- **20s**: Market evaluation and new trade entry logic.
- **60s (Verbose)**: Summary logging and settlement audit.

---

## Database

### Applied Migrations

| Version | Description | Date |
|---------|-------------|------|
| 1 | Add scale_in_order_id column | Jan 2026 |
| 2 | Verify timestamp column | Jan 2026 |
| 3 | Add reversal_triggered column | Jan 2026 |
| 4 | Add reversal_triggered_at column | Jan 2026 |

---

## Precision & Limits

### Share Precision Syncing
The bot uses a tight threshold of **0.0001** to compare database size vs actual wallet balance.
- If discrepancy > 0.0001: Database is synced to actual balance.
- If actual balance < 0.1: Position is settled as a "ghost trade".

### Minimum Order Size
Polymarket enforces a **5.0 share minimum** for all limit orders.
- The bot performs a pre-flight check for `size >= 5.0` before placing exit plans.
- If size < 5.0, the bot skips placement and logs a warning to prevent API errors.

---

**For detailed information, see `SESSION_IMPROVEMENTS.md`**
