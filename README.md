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
- **Confidence-Based Sizing**: Position size scales with signal strength (configurable multiplier)
- **Exit Plan**: Places limit sell orders at 99 cents for near-guaranteed profitable exits
- **🛑 Midpoint Stop Loss**: Primary safety net triggers at $0.30 midpoint price (configurable)
- **🔄 Hedged Reversal**: Supports holding both sides during trend flips, clearing losers via stop loss
- **📈 Dynamic Scale-In**: Adds to winning positions with confidence-weighted timing (up to 12m early for high-conviction trades)
- **⚡ Real-Time Monitoring**: 1-second position checking cycle with WebSocket-powered price updates
- **🛡️ Balance Cross-Validation**: Symbol-specific validation with fallback to position data for API reliability issues
- **📊 Settlement Auditing**: Automated P&L verification against exchange data (logs discrepancies > $0.10)

### 🚀 Recent Improvements (Jan 2026)
- **Bayesian Confidence Calculation** (v0.4.4): New probabilistic method using log-likelihood accumulation with market priors. Both additive and Bayesian methods calculated for A/B testing. Toggle via `BAYESIAN_CONFIDENCE` setting.
- **Enhanced Position Reports** (v0.4.3): Clean, aligned format with directional emojis (📈📉) and status indicators showing position health at a glance
- **Real-Time WebSocket Integration**: Near-instant P&L and order fill updates via Polymarket's User Channel (fills/cancels) and Market Channel (midpoint prices)
- **Batch API Optimization**: Fetch midpoints for all positions in a single call, drastically reducing API overhead
- **Enhanced Scale-In Logic**: Confidence-weighted dynamic timing allows high-conviction winners to be scaled as early as 12 minutes before expiry
- **Enhanced Balance Validation**: Symbol-specific tolerance for API reliability issues (especially XRP), with cross-validation between balance and position data
- **Reward Optimization**: Exit plans automatically adjust prices to ensure they earn liquidity rewards via `check_scoring` API
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

# Risk Management
ENABLE_STOP_LOSS=YES                 # Global stop loss switch
STOP_LOSS_PRICE=0.30                 # Midpoint stop loss trigger ($0.30)
ENABLE_TAKE_PROFIT=NO                # Let winners run
ENABLE_HEDGED_REVERSAL=YES           # Hold both sides during trend flip

# Exit Plan (Aggressive Profit Taking)
ENABLE_EXIT_PLAN=YES                 # Place limit sell orders at target price
EXIT_PRICE_TARGET=0.99               # Target exit price (99 cents)
EXIT_MIN_POSITION_AGE=60             # Wait 60s before placing exit order
ENABLE_REWARD_OPTIMIZATION=YES       # Optimize exit orders for liquidity rewards

# Position Scaling
ENABLE_SCALE_IN=YES                  # Add to winners near expiry
SCALE_IN_MIN_PRICE=0.60              # Min price to scale (60%)
SCALE_IN_MAX_PRICE=0.90              # Max price to scale (90%)
SCALE_IN_TIME_LEFT=450               # Default scale-in at ≤7.5m (Dynamic up to 12m)
SCALE_IN_MULTIPLIER=1.5              # Add 150% more (2.5x total position)

# Order Management
UNFILLED_TIMEOUT_SECONDS=300         # Cancel stale orders after 5 minutes
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
