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

## Fees & Rebates (Crypto Markets, CLOB V2)

Source: https://docs.polymarket.com/trading/fees (verified 2026-10-27). Supersedes the older flat "1.54% taker / 0.15% rebate" notes.

- **Formula**: `fee = C × feeRate × p × (1 − p)` (C = shares, p = price). Crypto `feeRate = 0.07`.
- **Denomination**: pUSD for BUY and SELL alike (the "BUY fee in tokens" rule is obsolete).
- **Symmetric**: 100 shares at 0.50 → $1.75; same dollar fee at 0.30 and 0.70. Effective rate on notional = `0.07 × (1 − p)`: 3.5% at 0.50, 4.9% at 0.30, 0.7% at 0.90.
- **Makers pay 0** and share a **20%** rebate pool of collected taker fees (paid daily).
- **Protocol-set at match time**; never put fees in orders. Read live params with `client.get_clob_market_info(condition_id)` → `fd = {"r": rate, "e": exponent, "to": takerOnly}`.
- **Precision**: 5 decimals (min 0.00001).
- **Strategy Implications**:
    - **Prefer POST_ONLY orders** (no fee + rebate). The bot uses POST_ONLY by default and switches to GTC after 3 POST_ONLY crossing failures.
    - A taker GTC leg near p≈0.50 costs ~3.5% of notional, which is larger than the 1¢ edge implied by `COMBINED_PRICE_THRESHOLD=0.99`. Taker fallbacks erase the atomic-hedge edge; check before enabling them.

## CLOB V2 (live 2026-04-28)

Polymarket hard-cut over to CLOB V2. The old `py-clob-client` stopped working against production (V1-signed orders rejected, repo archived). PolyFlup uses **`py-clob-client-v2`** (`from py_clob_client_v2 import ...`).

- **Collateral is pUSD** (`0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB`, 6 decimals), not USDC.e. API-only wallets wrap USDC.e via `CollateralOnramp.wrap(USDC.e, to, amount)` (`0x93070a847efEf7F70739046A929D47a521F5B8ee`) after approving the Onramp. `get_balance()` in `src/utils/web3_utils.py` reads pUSD.
- **Redeem/merge for 15m markets** (CTF positions, Gamma `version: "v1"`) go through **`CtfCollateralAdapter`** (`0xAdA100Db00Ca00073811820692005400218FcE1f`), which has the CTF function signatures (`redeemPositions`, `mergePositions`, `splitPosition`) and returns pUSD. The holder must first call `CTF.setApprovalForAll(adapter, true)`; `ctf_operations._adapter_approval_txs()` prepends it to relayer batches when `isApprovedForAll` is false. Negative-risk markets use `NegRiskCtfCollateralAdapter` (`0xadA2005600Dec949baf300f4C6120000bDB6eAab`).
- **Renames**: `create_or_derive_api_creds` → `create_or_derive_api_key`; `get_orders` → `get_open_orders`; `cancel(id)` → `cancel_order(OrderPayload(orderID=id))`; `cancel_market_orders(OrderMarketCancelParams(...))`.
- **Removed**: `get_balance()` / `get_positions()` on the client. Positions come from the Data API.
- **Order args**: no `nonce`, `fee_rate_bps`, `taker`. New optional `builder_code`, `metadata`, `user_usdc_balance`. `post_orders(batch, post_only=...)` takes `post_only` per call (not per order); `place_batch_orders` groups by it. Batch items are `PostOrdersV2Args`.
- **Side**: plain `"BUY"`/`"SELL"` strings still work (`Side` is an IntEnum).
- **Signature types**: 0 EOA, 1 POLY_PROXY, 2 POLY_GNOSIS_SAFE (default), 3 POLY_1271 deposit wallet (recommended for new API users). Set via `SIGNATURE_TYPE`.
- **Auth unchanged**: existing `API_KEY/API_SECRET/API_PASSPHRASE` keep working. Resting V1 orders were wiped at cutover.
- **WebSocket**: URLs and payloads unchanged.
- **Relayer**: `py-builder-relayer-client>=0.0.3`. Builder `POLY_BUILDER_*` headers are still used for the relayer (not for orders).
- **Unified SDK**: `polymarket-client` (py-sdk, Python 3.11+) is the recommended successor; `py-clob-client-v2` is deprecated but functional. Not drop-in: https://docs.polymarket.com/migrate/clob-sdk-to-unified-sdk.

## 5-Minute Markets

Slug `{asset}-updown-5m-{window_start_unix}` (same Gamma/CLOB/fee/redeem behavior as 15m: min size 5, tick 0.01, CTF "v1" positions, `crypto_fees_v2` 0.07 rate). Enabled per market in `MARKETS` with a `-5M` suffix (`BTC-5M`); that key is the `symbol` stored in the DB. `src/markets.py`: `parse_market`, `asset_of`, `window_bounds`, `slug_for`, `parse_slug`, `scale_seconds`.

- Scaled by window length: max entry lateness (600s -> 200s, `MAX_ENTRY_LATENESS_SEC_5M` overrides), hedge fill timeout (90s -> 30s) and GTC fallback (30s -> 10s).
- Pre-settlement exit tiers with more than 40% of the window left are skipped (5m: only the 120s/60s/45s tiers).
- Not scaled (tune manually if needed): `MOMENTUM_LOOKBACK_MINUTES` (15), `HEDGE_POLL_INTERVAL_SECONDS` (5s), `PRE_SETTLEMENT_*` confidence values, strategy weights. The strategy was calibrated on 15m data; backtest before trusting 5m results.

## Strategy Notes (fee-aware)

- Observed 2026-10-27 (8 markets, ~75s): best-ask sum of UP+DOWN was 1.001–1.019, best-bid sum ≤ 0.999, so a taker pair below 0.97 essentially never exists; two taker legs near 50/50 cost ~3.5¢/share in fees (break-even combined ≈ 0.96).
- Pair entry order of preference: taker pair (net edge after fees ≥ `MIN_NET_EDGE`) → maker pair at bids (both bids within `MAKER_PAIR_PRICE_BAND`) → skip. Leg risk: buy the missing leg as taker only if `taker_hedge_ev ≥ max(flatten_ev, hold_ev)`.
- Late-window mode: z = ln(spot/strike)/(σ₁ₘ·√(T/60)); p_use = min(Φ(|z|), measured hold rate). 7-day Binance study of the T-60s leader: BTC/ETH 5m hold 65–73% (|z|<0.5), ~85% (0.5–1), ~92–94% (1–1.5), ~97% (1.5–2), ≥99% (>2). Whether the market misprices this is unproven; judge it with `shadow_signals` + `analyze_strategy.py`.
- Resolution source is a Chainlink 60s TWAP, not Binance. PolyBolt (`wss://ws-live-v2.polymarket.com/ws`, `price.crypto` / `price.crypto.twap`, auth with CLOB API creds, symbols like `btcusd`) streams it; replaces the retired RTDS price topics.
- Useful official repos: `Polymarket/py-clob-client-v2` (trading), `Polymarket/py-builder-relayer-client` (gasless redeem), `Polymarket/py-sdk` (unified, Python 3.11+, newer successor).

## Data API v2 (v1 retired 2026-10-24)

Base `https://data-api.polymarket.com/v2/...`. Responses are `{"data": [...], "pagination": {"next_cursor": ...}}` with **snake_case** keys. Follow `next_cursor`; `offset` and `market=` return 400 (use `condition=`).

- `GET /v2/positions?user=&status=OPEN|REDEEMABLE|REDEEMABLE_LOST|MERGEABLE|CLOSED` replaces `/positions` and `/closed-positions`. Default dust floor is 0.1 shares; `filter_amount=0.001` keeps tiny balances.
- Row keys: `token_id` (was `asset`), `condition_id`, `current_size` (was `size`), `avg_price`, `realized_pnl`, `total_pnl`, `slug`, `title`, `outcome`, `redeemable`, `mergeable`, `negative_risk`.
- Helper: `src/trading/orders/positions.py::_fetch_positions_v2`.

## Terminal Dashboard

`uv run tui.py` (`src/tui/`): read-only Rich dashboard (open positions with live midpoints, performance, recent trades, log tail). Keys `q` quit, `r` refresh, `p` pause. `--db PATH` inspects a downloaded production DB, `--once` renders a single frame, `--no-wallet` skips the balance lookup. It opens the DB with `mode=ro`, so it is safe to run next to the bot.


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
