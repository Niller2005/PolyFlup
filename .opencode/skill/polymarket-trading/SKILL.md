---
name: polymarket-trading
description: Polymarket-specific terminology, trading strategies, and API reference.
---

## Terminology & Concepts

- **Exit Plan**: Automated limit sell at $0.99 placed as soon as position opens.
- **Hedged Reversal**: Holding both UP and DOWN positions simultaneously when trend flips.
- **Midpoint Stop Loss**: Market sell triggered by midpoint price dropping to/below $0.30.
- **Scale-In**: Adding capital to winning positions via Market Orders (FAK).
- **Underdog**: Side trading below $0.50. Requires >40% confidence for entry.
- **Tick Size**: Minimum price increment allowed for a market (e.g., 0.01 or 0.001).

## Strategy Nuances
- **Scale-In Strategy (Dynamic)**:
    - Default `SCALE_IN_TIME_LEFT`: 450s (7.5 minutes).
    - Dynamic entry timing based on confidence (`edge`) and midpoint price:
        - 12m left: `edge` >= 90% and price >= $0.80.
        - 9m left: `edge` >= 80% and price >= $0.70.
        - 7m left: `edge` >= 70% and price >= $0.65.
- **Trend Agreement Bonus**: 1.1x multiplier if Binance and Polymarket trends align.
- **Lead/Lag Bonus**: 1.2x bonus or 0.8x penalty based on cross-exchange consistency.
- **Precision**: Use strict 0.0001 threshold for balance syncing.
- **Minimum Size**: Polymarket enforces a 5.0 share minimum for limit orders.

## Common Code Patterns

### Market Data
```python
from src.trading.orders import get_midpoint, get_spread, check_liquidity
# Get accurate midpoint price
price = get_midpoint(token_id)
# Check if liquidity is good
if check_liquidity(token_id, size=100, warn_threshold=0.05):
    # Safe to trade
```

### Order Placement
```python
from src.trading.orders import place_batch_orders, place_market_order
# Batch Orders
results = place_batch_orders(orders)
# Market Sell
result = place_market_order(token_id, amount=10.0, side="SELL", order_type="FAK")
```

## Standard Emoji Guide
- 👀 Monitoring
- 📈 Positive P&L / Scaling In
- 📉 Negative P&L / Exit plan
- 🛑 Stop loss triggered
- 🎯 Take profit filled
- ✅ Success / Order filled
- ❌ Error/Failure
- ⚠️ Warning
- 🔄 Reversal / Retry
- 🚀 Trade execution
- 💰 Money/Balance
