# PolyFlup Trading Bot 🚀

Automated trading bot for **15-minute crypto prediction markets** on Polymarket.

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
- **🎯 Atomic Hedging Strategy**: Places simultaneous entry+hedge pairs using batch API for guaranteed profit structure
  - **POST_ONLY orders by default** to earn maker rebates (0.15%)
  - **Combined price threshold** ≤ $0.99 ensures profitability on complete fills
  - **Smart fallback**: Switches to GTC orders after 3 POST_ONLY failures (accepts 1.54% taker fees)
  - **120-second fill timeout** with immediate cancellation of unfilled orders
  - **No unhedged positions** - all trades are atomic pairs
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

### 🚀 Recent Improvements (Jan 2026)
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
- **Settlement Auditing**: Automated verification of local P&L against official `closed-positions` API data
- **Modular Backend**: Fully refactored `src/trading/orders` and `src/data/market_data` for better maintainability

> 💡 **Tip**: See [docs/RISK_PROFILES.md](docs/RISK_PROFILES.md) for pre-configured profiles (Conservative, Balanced, Aggressive, Ultra Aggressive)

### 💰 Automated Operations
- **Auto-Claim**: Automatically redeems winnings via CTF contract
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

#### Start Dashboard
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
│   ├── bot.py                    # Main bot loop
│   ├── config/                   # Configuration & settings
│   ├── data/
│   │   ├── database.py           # SQLite operations
│   │   ├── migrations.py         # Database schema migrations
│   │   └── market_data/          # Price data, indicators, Binance integration
│   ├── trading/
│   │   ├── orders/               # Order placement, CLOB client, market info
│   │   ├── position_manager/    # Entry, exit, scale-in, stop loss, reversal
│   │   ├── strategy.py           # Signal calculation & edge detection
│   │   └── settlement.py         # Market settlement & auto-claim
│   └── utils/                    # Logging, WebSocket, Web3, notifications
├── ui/
│   ├── server.js                 # Express API server for dashboard
│   ├── src/                      # Svelte components
│   └── package.json              # Node.js dependencies
├── polyflup.py                   # Bot entry point
├── trades.db                     # Shared SQLite database
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
