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

### Confidence Calculation Methods (v0.5.0+)

The bot supports two confidence calculation methods for A/B testing:

**Additive Method (Default)**:
- Directional voting system with weighted signal aggregation
- Confidence = (winning_total - (losing_total × 0.2)) × lead_lag_bonus
- Trend agreement bonus (1.1x) when Binance and Polymarket momentum align

**Bayesian Method (Alternative)**:
- Statistically principled probability theory using log-odds
- Starts with market prior from Polymarket orderbook
- Accumulates evidence via log-likelihood ratios:
  ```python
  evidence = (score - 0.5) × 2  # Scale to -1 to +1
  log_LR = evidence × 3.0 × quality  # Quality factor (0.7-1.5x)
  log_odds += log_LR × weight
  confidence = 1 / (1 + exp(-log_odds))
  ```
- Naturally handles conflicting signals (they cancel out)
- Market prior anchors calculation to Polymarket reality

**A/B Testing**:
- Both methods calculated simultaneously on every trade
- Results stored in database for comparison
- Toggle via `BAYESIAN_CONFIDENCE` environment variable (default: NO)
- Compare win rates after 100+ trades:
  ```sql
  SELECT
      AVG(CASE WHEN bias='UP' THEN edge ELSE -edge END) as avg_edge,
      COUNT(*) as total,
      SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) as wins,
      CAST(wins AS REAL) / COUNT(*) as win_rate
  FROM trades
  WHERE settled = 1
  GROUP BY method;
  ```

### Exit Plan Optimization
- **Smart Exit Pricing**: 
  - High confidence trades (≥85%): Use $0.999 exit price for maximum profit
  - Standard trades: Use $0.99 exit price (configurable via EXIT_PRICE_TARGET)
  - Only apply 0.999 for winning positions (UP side with price ≥ entry OR DOWN side with price ≤ entry)
- **Immediate Placement**: Exit plans placed immediately after position fill (no age requirement)
- **Real-Time Size Validation**: Exit order size validated every 1-second cycle
- **Repair Logic**: 
  - Automatic repair if exit order size mismatches database size
  - Uses 0.05 share threshold to prevent infinite repair loops from exchange rounding
  - Cancels incorrect order and places new one with correct size
- **Adoption Logic**: Discovers and adopts existing SELL orders on exchange to prevent duplicates

### Scale-In Strategy (Dynamic)
- Default `SCALE_IN_TIME_LEFT`: 450s (7.5 minutes)
- Dynamic early entry timing based on confidence (`edge`) and midpoint price:
  - 12m left: `edge` >= 90% and price >= $0.80
  - 9m left: `edge` >= 80% and price >= $0.70
  - 7m left: `edge` >= 70% and price >= $0.65
- Uses Market Orders (FAK) to ensure immediate fills
- Multiplier: 1.5x (adds 150% more capital, creating 2.5x total position)

### Signal Bonuses
- **Trend Agreement Bonus**: 1.1x multiplier if Binance and Polymarket trends align
- **Lead/Lag Bonus**: 1.2x bonus or 0.8x penalty based on cross-exchange consistency

### Technical Requirements
- **Precision**: Use strict 0.0001 threshold for balance syncing
- **Minimum Size**: Polymarket enforces a 5.0 share minimum for limit orders
- **Rounding**: Use `truncate_float(value, 2)` for all order sizes to match exchange precision

## Fees & Rebates (15-Minute Crypto Markets)

- **Taker Fees**: Applied ONLY to 15-minute crypto markets.
- **Fee Deduction**: 
    - **BUY**: Fee is taken in **Tokens** from the proceeds.
    - **SELL**: Fee is taken in **USDC** from the proceeds.
- **Effective Rates**: 
    - **Buying**: Peaks at ~1.6% at $0.50 price.
    - **Selling**: Peaks at ~3.7% at $0.30 price. Selling is generally more expensive because USDC is taken directly.
- **Fee Precision**: Rounded to 4 decimal places (min 0.0001 USDC). Small trades at extremes might be fee-free.
- **Maker Rebates**: Distributed daily in USDC to makers who provide liquidity that gets filled.
- **Strategy Implications**: 
    - Prefer **Maker orders** (Limit orders that add liquidity) to avoid fees and potentially earn rebates.
    - Selling at low prices as a taker is particularly costly.

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

### Position Monitoring
- 👀 Monitoring positions
- 📈 UP side position (winning if PnL positive)
- 📉 DOWN side position (winning if PnL positive)
- 📦 Position size display
- 🧮 PnL percentage display

### Position Status
- ⏰ Exit plan active (limit sell order placed)
- ⏳ Exit plan pending (waiting for conditions)
- ⏭️ Exit skipped (position below MIN_SIZE threshold)
- 📊 Scaled in position indicator

### Order Lifecycle
- 🚀 Trade execution / Entry
- 🎯 Limit order placement
- ✅ Success / Order filled
- 🧹 Order cancellation
- ❌ Error/Failure

### Risk Management
- 🛑 Stop loss triggered
- 🔄 Reversal / Trend flip
- ⚠️ Warning / Validation issue
- 🔧 Position repair/sync

### System
- 💰 Money/Balance/Settlement
- 🌍 Geographic restriction
- 🧟 Ghost trade detection
