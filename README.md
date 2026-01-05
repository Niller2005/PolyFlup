# PolyAstra Trading Bot 🚀

Automated trading bot for **15-minute crypto prediction markets** on Polymarket.

## ✨ Key Features

### 📊 Trading Strategy
- **Multi-Source Signal Integration**: Combines Polymarket order book data with real-time Binance market data to identify true mispricings
- **Edge Calculation**: Weighted combination of signals (100% total):
  - **Base Signals (60%)**: Polymarket price (40%), order book imbalance (20%)
  - **Binance Integration (40%)**: Price momentum (15%), order flow (10%), cross-exchange divergence (10%), volume-weighted momentum (5%)
- **Advanced Binance Strategy**:
  - **Price Momentum**: Velocity, acceleration, and RSI analysis over 15-minute lookback
  - **Order Flow**: Buy/sell pressure from Binance taker volume
  - **Divergence Detection**: Identifies when Polymarket pricing differs from Binance trends
  - **Volume-Weighted Metrics**: VWAP distance with volume quality filtering
- **Dynamic Position Sizing**: Confidence-based scaling with portfolio exposure limits

> 📖 For detailed strategy logic and signal breakdowns, see [docs/STRATEGY.md](docs/STRATEGY.md)

### 🛡️ Risk Management
- **Smart Breakeven Protection**: Activates at 20%+ PnL, exits if price reverses 5% from peak (doesn't cap upside)
- **Confidence-Based Sizing**: Position size scales with signal strength (configurable multiplier)
- **Exit Plan**: Places limit sell orders at 99 cents for near-guaranteed profitable exits
- **🛑 Stop Loss**: Configurable auto-exit on losing positions (default -50%, validated against spot price)
- **🎯 Take Profit**: Optional profit targets (disabled by default to let winners run)
- **🔄 Auto Reversal**: Automatically flips position with target price tracking on stop loss
- **📈 Scale In**: Adds to winning positions near expiry (70-90% probability, configurable multiplier)
- **⚡ Real-Time Monitoring**: High-frequency position checking with order status tracking

> 💡 **Tip**: See [docs/RISK_PROFILES.md](docs/RISK_PROFILES.md) for pre-configured profiles (Conservative, Balanced, Aggressive, Ultra Aggressive)

### 💰 Automated Operations
- **Auto-Claim**: Automatically redeems winnings via CTF contract
- **Dashboard**: Interactive HTML dashboard with live stats
- **Discord**: Real-time trade notifications
- **Database**: Full SQLite tracking of all trades

## 🛠️ Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/Niller2005/polyastra.git
   cd polyastra
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
MIN_EDGE=0.565                       # Minimum edge to enter (56.5%)
MAX_SPREAD=0.15                      # Max allowed spread (15%)
CONFIDENCE_SCALING_FACTOR=5.0        # Position scaling multiplier (higher = more aggressive on strong signals)

# Binance Advanced Strategy
ENABLE_MOMENTUM_FILTER=YES           # Price velocity, acceleration, RSI (35% weight)
ENABLE_ORDER_FLOW=YES                # Buy/sell pressure analysis (25% weight)
ENABLE_DIVERGENCE=YES                # Cross-exchange mismatch detection (25% weight)
ENABLE_VWM=YES                       # Volume-weighted momentum (10% weight)
MOMENTUM_LOOKBACK_MINUTES=15         # Momentum analysis window
ADX=NO                               # Optional ADX trend strength filter (15% weight if enabled)

# Risk Management
ENABLE_STOP_LOSS=YES                 # Stop loss + breakeven protection
STOP_LOSS_PERCENT=50.0               # Exit at -50% loss (validated against spot price)
ENABLE_TAKE_PROFIT=NO                # Let winners run (breakeven protection active)
TAKE_PROFIT_PERCENT=80.0             # Take profit level (if enabled)
ENABLE_REVERSAL=NO                   # Reverse position on stop loss

# Exit Plan (Aggressive Profit Taking)
ENABLE_EXIT_PLAN=YES                 # Place limit sell orders at target price
EXIT_PRICE_TARGET=0.99               # Target exit price (99 cents)
EXIT_MIN_POSITION_AGE=60             # Wait 1 minute before placing exit order

# Position Scaling
ENABLE_SCALE_IN=YES                  # Add to winners near expiry
SCALE_IN_MIN_PRICE=0.70              # Min price to scale (70%)
SCALE_IN_MAX_PRICE=0.90              # Max price to scale (90%)
SCALE_IN_TIME_LEFT=120               # Scale in when ≤2 minutes left
SCALE_IN_MULTIPLIER=1.0              # Add 100% more (2x total position)
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
- **Bot Logs**: `docker logs -f polyastra-bot`

### Option 2: Local Installation
#### Start Trading
```bash
uv run polyastra.py
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
polyastra/
├── src/          # Bot source code
├── ui/           # Real-time Svelte dashboard
├── polyastra.py  # Bot entry point
└── trades.db     # Shared SQLite database
```

## 📚 Documentation

- **[AGENTS.md](AGENTS.md)** - Coding standards and guidelines for AI agents/contributors
- **[docs/STRATEGY.md](docs/STRATEGY.md)** - Deep dive into the trading strategy and Binance integration
- **[docs/RISK_PROFILES.md](docs/RISK_PROFILES.md)** - Risk management profiles (Conservative, Balanced, Aggressive)
- **[docs/MIGRATIONS.md](docs/MIGRATIONS.md)** - Database migration guide for developers

## ⚠️ Disclaimer
This software is for educational purposes only. Use at your own risk. Crypto markets are volatile and you can lose money.
