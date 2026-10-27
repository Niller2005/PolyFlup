# PolyFlup Trading Bot 🚀

Automated trading bot for **5- and 15-minute crypto up/down prediction markets** on Polymarket (CLOB V2, pUSD collateral).

## ✨ Key Features

### 📊 Trading Strategy
- **Multi-Source Signal Integration**: Combines Polymarket order book data with real-time Binance market data to identify true mispricings
- **Dual Confidence Calculation**: A/B testing framework comparing additive and Bayesian confidence methods
  - **Bayesian Method** (new, optional): Proper probability theory with log-likelihood accumulation and market priors
  - **Additive Method** (default): Directional voting with weighted signal aggregation
  - Both methods always calculated and stored for performance comparison
- **Edge Calculation**: Directional voting system where external Binance signals validate Polymarket pricing (100% total weight):
  - **Price Momentum (30%)**: Velocity, acceleration, and RSI analysis over 15-minute lookback
  - **Polymarket Momentum (20%)**: Internal price action confirmation on the CLOB
  - **Order Flow (20%)**: Buy/sell pressure from Binance taker volume
  - **Cross-Exchange Divergence (20%)**: Detects when Polymarket pricing differs from Binance trends
  - **Volume-Weighted Momentum (10%)**: VWAP distance with volume quality filtering
- **Dynamic Position Sizing**: Confidence-based scaling with portfolio exposure limits

> 📖 For detailed strategy logic and signal breakdowns, see [docs/STRATEGY.md](docs/STRATEGY.md)

### 🛡️ Risk Management
- **🎯 Atomic Hedging Strategy**: Places an entry+hedge pair so one side always pays $1
  - **Fee-aware**: a pair opens only if its guaranteed profit **after** taker fees (`0.07·p·(1−p)` per share) is ≥ `MIN_NET_EDGE` (1¢). Two taker legs near 50/50 cost ~3.5¢, so most "0.97 combined" pairs are rejected
  - **Maker pair**: otherwise rests POST_ONLY bids on both legs (no fee, rebate share) while both bids are in a sane band
  - **Trailing second leg**: when one leg fills, the other is re-quoted at the best bid, never above `1 − filled − MIN_NET_EDGE`
  - **Leg-risk check**: a missing leg is bought as taker only if that beats flattening or holding
  - Fill timeout and entry cutoff scale with the window length (15m vs 5m)
- **📐 Sizing**: each entry gets `BET_PERCENT` of cash not already committed and is clipped so open exposure (both legs of every pair) stays under `MAX_EXPOSURE_PERCENT` of equity; optional fractional Kelly (`KELLY_SIZING`)
- **🎚️ Risk profiles**: conservative / balanced / aggressive / custom, switchable live from the TUI ([docs/RISK_PROFILES.md](docs/RISK_PROFILES.md))
- **🕐 Late-window mode**: scores how far the leader is from the strike near the close using the Chainlink feed Polymarket resolves on; shadow-logged by default, live only with `LATE_WINDOW_LIVE=YES`
- **⏰ Pre-Settlement Exit**: Evaluates positions T-180s to T-45s before resolution
  - Sells losing side early if confidence > 80% on one side
  - Keeps winning side for full resolution profit
- **🚨 Time-Aware Emergency Liquidation**: Adapts pricing based on time remaining
  - **PATIENT (>600s)**: Small price drops (1¢), long waits (10-20s) - maximize recovery
  - **BALANCED (300-600s)**: Moderate drops (2-5¢), balanced waits (6-10s)
  - **AGGRESSIVE (<300s)**: Rapid drops (5-10¢), short waits (5-10s) - ensure liquidation
- **🔬 Smart MIN_ORDER_SIZE Handling**: Positions < 5.0 shares held if winning, orphaned if losing
- **⚡ Real-Time Monitoring**: 1-second position checking cycle with WebSocket-powered price updates
- **🛡️ Balance Cross-Validation**: Symbol-specific validation with fallback to position data for API reliability issues
- **📊 Settlement Auditing**: Automated P&L verification against exchange data (logs discrepancies > $0.10)

### 🚀 What's new in v0.7.0 (Oct 2026)
- **Polymarket CLOB V2**: `py-clob-client-v2`, pUSD collateral, Data API v2, redemption via `CtfCollateralAdapter` (the V1 client is rejected by production since 2026-04-28)
- **5-minute markets**: `BTC-5M` style market keys next to `BTC` (15m)
- **Paper trading** (`uv run polyflup.py --paper`) on live order books, no keys needed
- **Terminal dashboard** (`uv run tui.py`): positions with locked PnL and hedge status, performance, paper account, log, live risk-profile switching
- Fee model corrected, trailing second leg, exposure-aware and Kelly sizing, shadow late-window signals (`analyze_strategy.py`)

See [CHANGELOG.md](CHANGELOG.md) for details and the breaking changes.

### 🚀 Earlier Improvements (Jan 2026)
- **🎯 Atomic Hedging Strategy** (v0.6.0): Complete overhaul to simultaneous entry+hedge pairs
  - Batch API placement with combined price threshold (≤ $0.99)
  - POST_ONLY orders for maker rebates, GTC fallback after 3 failures
  - 120-second fill timeout with immediate cancellation
  - Time-aware emergency liquidation (PATIENT/BALANCED/AGGRESSIVE)
  - Smart MIN_ORDER_SIZE handling (hold if winning, orphan if losing)
  - Pre-settlement exit strategy (T-180s to T-45s)
- **Bayesian Confidence Calculation** (v0.5.0): New probabilistic method using log-likelihood accumulation with market priors. Both additive and Bayesian methods calculated for A/B testing. Toggle via `BAYESIAN_CONFIDENCE` setting. Compare performance with `compare_bayesian_additive.py`.
- **Enhanced Position Reports** (v0.4.3): Clean, aligned format with directional emojis (📈📉) and status indicators showing position health at a glance
- **Real-Time WebSocket Integration**: Near-instant P&L and order fill updates via Polymarket's User Channel (fills/cancels) and Market Channel (midpoint prices)
- **Batch API Optimization**: Fetch midpoints for all positions in a single call, drastically reducing API overhead
- **Enhanced Balance Validation**: Symbol-specific tolerance for API reliability issues (especially XRP), with cross-validation between balance and position data
- **Intelligent Position Sync**: Startup logic detects and "adopts" untracked exchange positions for automated management
- **Settlement Auditing**: Automated verification of local P&L against the Data API v2 `CLOSED` positions
- **Modular Backend**: Fully refactored `src/trading/orders` and `src/data/market_data` for better maintainability

> 💡 **Tip**: See [docs/RISK_PROFILES.md](docs/RISK_PROFILES.md) for the built-in risk profiles and how to switch them from the TUI

### 💰 Automated Operations
- **Auto-Claim**: Redeems winnings through the pUSD `CtfCollateralAdapter` (gasless via the relayer when configured)
- **Dashboard**: Interactive HTML dashboard with live stats
- **Discord**: Real-time trade notifications
- **Database**: Full SQLite tracking of all trades

## 🛠️ Installation

> 💡 **New to PolyFlup?** See [QUICKSTART.md](QUICKSTART.md) for a 5-minute setup guide.

1. **Clone the repository**
   ```bash
   git clone https://github.com/Niller2005/polyflup.git
   cd polyflup
   ```

2. **Install dependencies**
   ```bash
   uv sync
   ```

3. **Configure environment**
   ```bash
   cp .env.example .env
   # Edit .env with your private key (PROXY_PK) and settings
   ```

## ⚙️ Configuration

Key settings in `.env` (Balanced profile shown):

```env
# Trading & Position Sizing
BET_PERCENT=5.0                      # Base position size (% of balance)
MIN_EDGE=0.35                        # Minimum confidence to enter (35%)
MAX_SPREAD=0.15                      # Max allowed spread (15%)
CONFIDENCE_SCALING_FACTOR=5.0        # Position scaling multiplier
LOSING_SIDE_MIN_CONFIDENCE=0.40      # Higher threshold for underdog entries

# Confidence Calculation Method
BAYESIAN_CONFIDENCE=NO               # Use Bayesian method (YES) or Additive method (NO)

# Binance Advanced Strategy
ENABLE_MOMENTUM_FILTER=YES           # Price velocity, acceleration, RSI (30% weight)
ENABLE_ORDER_FLOW=YES                # Buy/sell pressure analysis (20% weight)
ENABLE_DIVERGENCE=YES                # Cross-exchange mismatch detection (20% weight)
ENABLE_VWM=YES                       # Volume-weighted momentum (10% weight)
MOMENTUM_LOOKBACK_MINUTES=15         # Momentum analysis window

# Atomic Hedging Configuration
COMBINED_PRICE_THRESHOLD=0.99        # Max combined price for entry+hedge (99¢)
HEDGE_FILL_TIMEOUT_SECONDS=120       # Timeout for both orders to fill
HEDGE_POLL_INTERVAL_SECONDS=5        # Check fill status interval

# Emergency Liquidation Timing
EMERGENCY_SELL_ENABLE_PROGRESSIVE=YES # Enable time-aware pricing
EMERGENCY_SELL_WAIT_SHORT=5          # Wait time for aggressive mode (<300s)
EMERGENCY_SELL_WAIT_MEDIUM=8         # Wait time for balanced mode (300-600s)
EMERGENCY_SELL_WAIT_LONG=10          # Wait time for patient mode (>600s)

# Pre-Settlement Exit Strategy
ENABLE_PRE_SETTLEMENT_EXIT=YES       # Exit losing side before resolution
PRE_SETTLEMENT_MIN_CONFIDENCE=0.80   # Min confidence to trigger early exit
PRE_SETTLEMENT_EXIT_SECONDS=180      # Start checking at T-180s
PRE_SETTLEMENT_CHECK_INTERVAL=30     # Check every 30s
```

> 💡 **Need a different risk profile?** Check [docs/RISK_PROFILES.md](docs/RISK_PROFILES.md) for Conservative, Aggressive, and Ultra Aggressive configurations.

## 🚀 Running the Bot

### Option 1: Docker (Recommended)
This runs both the bot and the real-time Svelte dashboard in containers.

```bash
docker compose up -d --build
```

- **Dashboard**: [http://localhost:3001](http://localhost:3001)
- **API Stats**: [http://localhost:3001/api/stats](http://localhost:3001/api/stats)
- **Bot Logs**: `docker logs -f polyflup-bot`

### Option 2: Local Installation
#### Start Trading
```bash
uv run polyflup.py
```

#### Paper Trading
```bash
uv run polyflup.py --paper   # simulated account on live order books; no private key or API credentials needed
uv run tui.py --paper        # watch it
```
Fills use the real book at the moment of each order (taker fee `0.07·p·(1−p)` included). Resting maker orders fill when the book crosses them (`PAPER_MAKER_FILL_MODE=cross`, conservative) or when the midpoint reaches them (`touch`, optimistic); queue position is not modelled, so maker fill rates are approximate. State lives in `trades_paper.db` (delete to reset; `PAPER_STARTING_BALANCE`, default 1000).

#### Terminal Dashboard
```bash
uv run tui.py                       # live: q quit, r refresh, p pause, l log page, m risk-profile dialog (arrows + Enter)
uv run tui.py --db prod/trades.db   # inspect a downloaded production database
```
Read-only Rich dashboard: open positions with live midpoints, today / 7d / all-time performance, per-symbol PnL, equity sparkline, recent trades and a log tail. Safe to run next to the bot.

#### Start Web Dashboard
```bash
cd ui
npm install
npm start
```
*The dashboard will be available at [http://localhost:5173](http://localhost:5173)*

## 📂 Project Structure

```
polyflup/
├── src/
│   ├── bot.py                    # Main loop, per-market entry workers
│   ├── markets.py                # Market keys (BTC / BTC-5M), window math, slugs
│   ├── paper.py                  # Paper-trading exchange (simulated fills on live books)
│   ├── profiles.py               # Risk profile definitions + state helpers
│   ├── risk_runtime.py           # Applies profiles to the running bot
│   ├── config/                   # Configuration & settings
│   ├── data/
│   │   ├── database.py           # SQLite operations
│   │   ├── migrations.py         # Database schema migrations
│   │   └── market_data/          # Price data, indicators, Binance, PolyBolt (Chainlink)
│   ├── trading/
│   │   ├── orders/               # Order placement, CLOB V2 client, market info
│   │   ├── position_manager/     # Monitoring, sync, stop loss, reversal
│   │   ├── edge.py               # Fee, edge, Kelly and late-window math (pure)
│   │   ├── exposure.py           # Cash- and exposure-aware sizing
│   │   ├── late_window.py        # Late-window conviction mode + shadow signals
│   │   ├── execution.py          # Atomic pair placement, trailing, leg risk
│   │   ├── ctf_operations.py     # Redeem/merge via CtfCollateralAdapter
│   │   ├── strategy.py           # Signal calculation & edge detection
│   │   └── settlement.py         # Market settlement & auditing
│   ├── tui/                      # Read-only terminal dashboard
│   └── utils/                    # Logging, WebSocket, Web3, notifications
├── ui/                           # Svelte web dashboard + Express API
├── polyflup.py                   # Bot entry point (--paper for paper trading)
├── tui.py                        # Terminal dashboard (--paper, --db, --once)
├── analyze_strategy.py           # Early exits, calibration, shadow-signal analysis
├── trades.db / trades_paper.db   # SQLite databases (real / paper)
├── .env.example                  # Configuration template
└── docs/                         # Strategy, risk profiles, migrations
```

## 🔬 Analysis Tools

### Bayesian vs Additive Comparison

After collecting 50+ trades with migration 007 data, compare performance:

```bash
uv run python compare_bayesian_additive.py
```

This script shows:
- Overall win rates for both methods
- Performance by confidence buckets (0-25%, 25-35%, etc.)
- Average edge and PnL per trade
- Recommendation on which method to use

**Note**: Only new trades (after migration 007) will have both confidence values. Run the bot for ~100 trades before comparing.

### Database Migrations

Run pending migrations manually (usually auto-runs on bot startup):

```bash
uv run python run_migrations.py
```

## 📚 Documentation

- **[CHANGELOG.md](CHANGELOG.md)** - Version history and release notes
- **[AGENTS.md](AGENTS.md)** - Coding standards and guidelines for AI agents/contributors
- **[docs/STRATEGY.md](docs/STRATEGY.md)** - Deep dive into the trading strategy and Binance integration
- **[docs/RISK_PROFILES.md](docs/RISK_PROFILES.md)** - Risk management profiles (Conservative, Balanced, Aggressive)
- **[docs/POSITION_FLOW.md](docs/POSITION_FLOW.md)** - Complete position lifecycle documentation
- **[docs/API.md](docs/API.md)** - API reference for Polymarket, Binance, and Dashboard endpoints
- **[docs/MIGRATIONS.md](docs/MIGRATIONS.md)** - Database migration guide for developers

## ⚠️ Disclaimer
This software is for educational purposes only. Use at your own risk. Crypto markets are volatile and you can lose money.
