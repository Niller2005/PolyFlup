---
name: polymarket-trading
description: Polymarket-specific terminology, trading strategies, and API reference.
---

## Terminology & Concepts

- **Atomic Hedging**: Simultaneous entry+hedge pair placement via batch API (primary risk management).
- **Combined Price Threshold**: Maximum allowed sum of entry + hedge prices (default $0.99) to guarantee profit.
- **Emergency Liquidation**: Time-aware progressive pricing to recover value from partial fills.
- **Pre-Settlement Exit**: Confidence-based early exit of losing side (T-180s to T-45s before resolution).
- **MIN_ORDER_SIZE Smart Hold**: Hold winning positions <5.0 shares, orphan losing ones.
- **Underdog**: Side trading below $0.50. Requires >40% confidence for entry.
- **Tick Size**: Minimum price increment allowed for a market (e.g., 0.01 or 0.001).
- **POST_ONLY**: Order type that only adds liquidity (earns maker rebates, fails if crossing).
- **GTC (Good-Til-Cancel)**: Order type that fills immediately as taker (pays taker fees).
- **Orphaned Position**: Position <5.0 shares that cannot be sold due to exchange minimum.

## Strategy Nuances

### Atomic Hedging Execution (v0.6.0+)

**Overview**: The bot places entry+hedge pairs simultaneously to guarantee profit structure.

**Batch Placement**:
```python
orders = [
    {"token_id": entry_token, "price": entry_price, "side": BUY, "post_only": use_post_only},
    {"token_id": hedge_token, "price": hedge_price, "side": BUY, "post_only": use_post_only}
]
# Both submitted in single API call
results = place_batch_orders(orders)
```

**Combined Price Constraint**:
- Entry price + Hedge price ≤ COMBINED_PRICE_THRESHOLD (default $0.99)
- Ensures guaranteed profit on resolution: $1.00 - $0.99 = $0.01 minimum
- Example: Entry $0.68 UP + Hedge $0.31 DOWN = $0.99 combined ✅

**POST_ONLY → GTC Fallback** (Bug #10 Fix):
- **Default**: Use POST_ONLY orders to earn maker rebates (0.15%)
- **Failure Tracking**: Per-symbol counter tracks POST_ONLY crossing errors
- **Smart Switching**: After 3 POST_ONLY failures, switch to GTC (accepts 1.54% taker fees)
- **Reset**: Counter resets to 0 on successful atomic placement
- **Rationale**: Better to pay taker fees than miss profitable trades

**Fill Monitoring**:
- **Timeout**: 120 seconds (configurable via HEDGE_FILL_TIMEOUT_SECONDS)
- **Polling**: Every 5 seconds (configurable via HEDGE_POLL_INTERVAL_SECONDS)
- **Success**: Both orders fill → Trade complete
- **Partial Fill**: One fills, other times out → Emergency liquidation
- **No Fill**: Neither fills → Cancel both, retry

**Emergency Liquidation** (Partial Fill Recovery):
- **Time-Aware Pricing**: Adapts urgency based on time remaining
  - **PATIENT** (>600s): Small drops (1¢), long waits (10-20s) - maximize recovery
  - **BALANCED** (300-600s): Moderate drops (2-5¢), medium waits (6-10s)
  - **AGGRESSIVE** (<300s): Rapid drops (5-10¢), short waits (5-10s) - ensure liquidation
- **MIN_ORDER_SIZE Check**:
  - If size < 5.0 shares AND winning → HOLD through resolution
  - If size < 5.0 shares AND losing → ORPHAN (accept small loss)
- **Progressive Pricing**: Start high, drop gradually until filled or floor reached

**Pre-Settlement Exit** (Confidence-Based):
- **Timing Window**: T-180s (3 min) to T-45s (45 sec) before resolution
- **Trigger**: Confidence > 80% on one side
- **Action**: Sell losing side early via emergency liquidation
- **Benefit**: Recover value on losing side, keep winning side for $1.00 resolution

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

### Technical Requirements
- **Precision**: Use strict 0.0001 threshold for balance syncing
- **Minimum Size**: Polymarket enforces a 5.0 share minimum for limit orders
- **Rounding**: Use `truncate_float(value, 2)` for all order sizes to match exchange precision

## Fees & Rebates (15-Minute Crypto Markets)

- **Maker Rebates**: Earn 0.15% rebate for orders that add liquidity (POST_ONLY orders).
- **Taker Fees**: Pay 1.54% fee for orders that remove liquidity (GTC/IOC/FOK orders).
- **Fee Deduction**: 
    - **BUY**: Fee is taken in **Tokens** from the proceeds.
    - **SELL**: Fee is taken in **USDC** from the proceeds.
- **Strategy Implications**: 
    - **Prefer POST_ONLY orders** to earn maker rebates and avoid taker fees.
    - Bot uses POST_ONLY by default, switches to GTC after 3 POST_ONLY crossing failures.
    - Maker orders may not fill immediately but earn 0.15% rebate when filled.
    - GTC orders fill immediately but pay 1.54% taker fee - still worth it for profitable trades.
- **Effective Rates** (legacy info): 
    - Buying peaks at ~1.6% at $0.50 price.
    - Selling peaks at ~3.7% at $0.30 price (USDC deducted directly).

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
# Atomic Batch Orders (Entry + Hedge)
orders = [
    {"token_id": entry_token, "price": 0.68, "side": "BUY", "size": 50.0, "post_only": True},
    {"token_id": hedge_token, "price": 0.31, "side": "BUY", "size": 50.0, "post_only": True}
]
results = place_batch_orders(orders)
# Market Sell (for emergency liquidation)
result = place_market_order(token_id, amount=10.0, side="SELL", order_type="FAK")
```

## Standard Emoji Guide

### Position Monitoring
- 👀 Monitoring positions
- 📈 UP side position (winning if PnL positive)
- 📉 DOWN side position (winning if PnL positive)
- 📦 Position size display
- 🧮 PnL percentage display

### Position Status (Atomic Hedging)
- 📊 Atomic pair (both entry + hedge active)
- 🎯 Pre-settlement exit candidate (confidence > 80%)
- 🚨 Emergency liquidation in progress
- 🔒 Orphaned position (<5.0 shares, cannot sell)
- 💰 Resolved position (waiting for redemption)

### Order Lifecycle
- 🚀 Atomic pair placement (entry+hedge)
- 🎯 Batch order submission (POST_ONLY/GTC)
- ✅ Success / Both orders filled
- 🧹 Order cancellation (timeout or retry)
- ❌ Error/Failure (POST_ONLY crossing)

### Risk Management
- 🛑 Emergency liquidation triggered (partial fill)
- 🔄 GTC fallback (after 3 POST_ONLY failures)
- ⚠️ Warning / Validation issue
- 🔧 Position repair/sync
- 🎯 Pre-settlement exit (confidence-based)

### System
- 💰 Money/Balance/Settlement
- 🌍 Geographic restriction
- 🧟 Ghost trade detection
