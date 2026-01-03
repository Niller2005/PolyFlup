# PolyAstra Trading Bot 🚀

Automated trading bot for **15-minute crypto prediction markets** on Polymarket.

## ✨ Key Features

### 📊 Trading Strategy
- **Edge Calculation**: Analyzes price vs. implied probability (70% price + 30% imbalance)
- **ADX Trend Filter**: Only trades when trend strength > 25 (configurable)
- **BFXD Trend Filter**: External BTC trend confirmation
- **Funding Rate Bias**: Adjusts edge based on Binance funding rates
- **Dynamic Sizing**: Bets percentage of balance (default 5%)

### 🛡️ Position Management
- **🛑 Stop Loss**: Auto-exits losing positions (default -50%)
- **🎯 Take Profit**: Auto-exits winning positions (default +80%)
- **🔄 Auto Reversal**: Opens opposite position on stop loss (optional)
- **📈 Scale In**: Doubles down on winning positions near expiry (70-90% probability)
- **⚡ Real-Time Monitoring**: Checks positions every **1 second**

### 💰 Automated Operations
- **Auto-Claim**: Automatically redeems winnings via CTF contract
- **Dashboard**: Interactive HTML dashboard with live stats
- **Discord**: Real-time trade notifications
- **Database**: Full SQLite tracking of all trades

## 🛠️ Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/your-repo/polyastra.git
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
BET_PERCENT=5.0          # Percent of balance per trade
MIN_EDGE=0.60            # Minimum edge to enter (60%)
MAX_SPREAD=0.10          # Max allowed spread (10%)
WINDOW_DELAY_SEC=30      # Seconds to wait after window opens

# Position Management
ENABLE_STOP_LOSS=YES     # Enable stop loss
STOP_LOSS_PERCENT=50.0   # Exit at -50%
ENABLE_TAKE_PROFIT=YES   # Enable take profit
TAKE_PROFIT_PERCENT=80.0 # Exit at +80%
ENABLE_REVERSAL=NO       # Reverse position on stop loss

# Scaling
ENABLE_SCALE_IN=YES      # Add to winners near expiry
SCALE_IN_MIN_PRICE=0.70  # Min price (70 cents)
SCALE_IN_MAX_PRICE=0.90  # Max price (90 cents)
SCALE_IN_TIME_LEFT=120   # Seconds before expiry
SCALE_IN_MULTIPLIER=1.0  # Add 100% more (double position)

# Filters
ADX=YES
ADX_THRESHOLD=25.0       # Strong trend only
ADX_INTERVAL=15m         # Timeframe
ADX_PERIOD=10            # Period
```

## 🚀 Running the Bot

### Start Trading
```bash
python polyastra.py
```

### View Dashboard
```bash
# Generate dashboard
python generate_dashboard.py

# Serve dashboard (view at http://localhost:8000)
python -m http.server 8000
```

## 📂 Project Structure

```
polyastra/
├── src/
│   ├── config/          # Settings & constants
│   ├── data/            # Database & market data API
│   ├── trading/         # Strategy, orders, positions, settlement
│   ├── utils/           # Logging & Web3 helpers
│   └── bot.py           # Main application loop
├── polyastra.py         # Entry point
├── generate_dashboard.py # Stats generator
└── trades.db            # SQLite database
```

## ⚠️ Disclaimer
This software is for educational purposes only. Use at your own risk. Crypto markets are volatile and you can lose money.
