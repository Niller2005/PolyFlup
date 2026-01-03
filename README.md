# PolyAstra Trading Bot 🚀

Automated trading bot for **15-minute crypto prediction markets** on Polymarket.

## ✨ Key Features

### 📊 Trading Strategy
- **Edge Calculation**: Analyzes price vs. implied probability (70% price + 30% imbalance)
- **Advanced Strategy**: Integrates Binance data for enhanced decision making:
  - **Momentum Filter**: Tracks price momentum over configurable lookback periods
  - **Order Flow**: Analyzes Binance order flow for volume-weighted confirmation
  - **Divergence Analysis**: Identifies price/momentum divergences
  - **Volume-Weighted Metrics**: Sophisticated entry signals based on real-time volume
- **ADX Trend Filter**: Only trades when trend strength > 20 (configurable)
- **BFXD Trend Filter**: External BTC trend confirmation
- **Funding Rate Bias**: Adjusts edge based on Binance funding rates
- **Dynamic Sizing**: Bets percentage of balance (default 5%)

### 🛡️ Position Management
- **🛑 Stop Loss**: Auto-exits losing positions (default -50%)
- **🎯 Take Profit**: Auto-exits winning positions (default +80%)
- **🔄 Auto Reversal**: Automatically flips position with target price tracking on stop loss
- **📈 Scale In**: Doubles down on winning positions near expiry (70-90% probability)
- **⚡ Real-Time Monitoring**: High-frequency position checking every **1 second**

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
   pip install -r requirements.txt
   ```

3. **Configure environment**
   ```bash
   cp .env.example .env
   # Edit .env with your private key (PROXY_PK) and settings
   ```

## ⚙️ Configuration

Key settings in `.env`:

```env
# Trading
BET_PERCENT=5.0                # Percent of balance per trade
MIN_EDGE=0.565                 # Minimum edge to enter (56.5%)
MAX_SPREAD=0.15                # Max allowed spread (15%)
WINDOW_DELAY_SEC=12            # Seconds to wait after window opens

# Binance Advanced Strategy
ENABLE_MOMENTUM_FILTER=YES     # Filter trades by price momentum
ENABLE_ORDER_FLOW=YES          # Analyze Binance order flow
ENABLE_DIVERGENCE=YES          # Use price/momentum divergence
ENABLE_VWM=YES                 # Volume-Weighted Metrics

# Position Management
ENABLE_STOP_LOSS=YES           # Enable stop loss
STOP_LOSS_PERCENT=50.0         # Exit at -50%
ENABLE_TAKE_PROFIT=NO          # Enable take profit
TAKE_PROFIT_PERCENT=80.0       # Exit at +80%
ENABLE_REVERSAL=NO             # Reverse position on stop loss

# Scaling
ENABLE_SCALE_IN=YES            # Add to winners near expiry
SCALE_IN_MIN_PRICE=0.70        # Min price (70 cents)
SCALE_IN_MAX_PRICE=0.90        # Max price (90 cents)
SCALE_IN_TIME_LEFT=120         # Seconds before expiry
SCALE_IN_MULTIPLIER=1.0        # Add 100% more (double position)

# Filters
ADX=NO
ADX_THRESHOLD=20.0             # Trend strength threshold
ADX_INTERVAL=15m               # Timeframe
ADX_PERIOD=10                  # Period
```

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
python polyastra.py
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

## ⚠️ Disclaimer
This software is for educational purposes only. Use at your own risk. Crypto markets are volatile and you can lose money.
