# PolyFlup Quick Start Guide

Get up and running with PolyFlup in under 5 minutes.

---

## Prerequisites

- **Python 3.9+** (3.11 recommended)
- **Node.js 18+** (for dashboard)
- **Docker** (optional, for containerized deployment)
- **Polygon wallet** with USDC balance

---

## Quick Install

### Option 1: Docker (Recommended for Production)

```bash
# 1. Clone repository
git clone https://github.com/Niller2005/polyflup.git
cd polyflup

# 2. Configure environment
cp .env.example .env
nano .env  # Add your PROXY_PK (Polygon private key)

# 3. Start bot + dashboard
docker compose up -d --build

# 4. View logs
docker logs -f polyflup-bot

# 5. Open dashboard
# http://localhost:3001
```

**That's it!** Bot is trading and dashboard is live.

---

### Option 2: Local Development

```bash
# 1. Clone repository
git clone https://github.com/Niller2005/polyflup.git
cd polyflup

# 2. Install dependencies
uv sync

# 3. Configure environment
cp .env.example .env
nano .env  # Add your PROXY_PK

# 4. Start bot
uv run polyflup.py

# 5. Start dashboard (separate terminal)
cd ui
npm install
npm start

# Dashboard: http://localhost:5173
# API: http://localhost:3001
```

---

## Essential Configuration

Edit `.env` with these minimum settings:

```bash
# Required
PROXY_PK=0xYOUR_PRIVATE_KEY_HERE    # Polygon wallet (MUST HAVE USDC)
MARKETS=BTC,ETH,XRP,SOL             # Which markets to trade

# Recommended (Balanced profile)
BET_PERCENT=5.0                     # Position size (% of balance)
MIN_EDGE=0.35                       # Minimum confidence to enter (35%)
MAX_SPREAD=0.15                     # Maximum allowed spread (15%)
CONFIDENCE_SCALING_FACTOR=5.0       # Position scaling multiplier
STOP_LOSS_PRICE=0.30                # Stop loss trigger ($0.30 midpoint)

# Risk Management
ENABLE_STOP_LOSS=YES
ENABLE_REVERSAL=YES
ENABLE_EXIT_PLAN=YES
EXIT_PRICE_TARGET=0.99              # Sell at 99 cents
```

See [docs/RISK_PROFILES.md](docs/RISK_PROFILES.md) for pre-configured settings.

---

## Verify Setup

### 1. Check Database

```bash
uv run check_db.py
```

**Expected**: Database initialized, schema current

---

### 2. Check Balance

```bash
uv run python -c "from src.utils.web3_utils import get_balance; from src.config.settings import PROXY_PK; from eth_account import Account; print(f'Balance: {get_balance(Account.from_key(PROXY_PK).address)} USDC')"
```

**Expected**: Shows your USDC balance

---

### 3. Check API Connection

```bash
curl https://clob.polymarket.com/ok
```

**Expected**: `{"status":"ok"}`

---

### 4. Check Logs

```bash
tail -f logs/trades_2025.log
```

**Expected**: 
```
✅ Database initialized
✅ WebSocket: Connected
🚀 Bot started successfully
💤 No open positions. Monitoring markets...
```

---

## Common Commands

### Bot Control

```bash
# Start bot
uv run polyflup.py

# Stop bot (Ctrl+C or)
pkill -f polyflup.py

# View live logs
tail -f logs/trades_2025.log

# Check errors
tail -f logs/errors.log
```

---

### Database

```bash
# Verify database integrity
uv run check_db.py

# Check migration status
uv run check_migration_status.py

# Run migrations manually
uv run migrate_db.py

# View statistics
sqlite3 trades.db "SELECT COUNT(*) as trades, SUM(pnl_usd) as total_pnl FROM trades WHERE settled=1"
```

---

### Dashboard

```bash
# Development mode (hot reload)
cd ui && npm run dev

# Production build
cd ui && npm run build && npm start

# API health check
curl http://localhost:3001/api/stats
```

---

### Docker

```bash
# Start services
docker compose up -d

# Stop services
docker compose down

# View bot logs
docker logs -f polyflup-bot

# View dashboard logs
docker logs -f polyflup-dashboard

# Restart bot only
docker restart polyflup-bot

# Rebuild after code changes
docker compose up -d --build
```

---

## Understanding Your First Trade

When the bot finds an opportunity, you'll see:

```
🚀 [BTC] Entry triggered: UP @ $0.52, confidence=0.65, size=100.00
✅ [BTC] Trade #1 BUY UP @ $0.52 filled (100 shares)
📈 [BTC] #1 EXIT PLAN placed: size=100.00 @ $0.99 (order: 0xabc...)
```

**What this means**:
1. Bot detected 65% confidence that BTC will go UP
2. Bought 100 shares of UP token at $0.52 each ($52 total)
3. Placed limit sell order at $0.99 to lock in profit

**Position monitoring** (every 1 second):
- ✅ Exit plan active (will sell at 99¢)
- ⏰ Stop loss active (sells if price drops to 30¢)
- 📈 Scale-in ready (adds more if price reaches 60-75¢ with 7.5min left)

**Exit scenarios**:
1. **Exit plan fills** at 99¢ → ~90% profit ($90 profit on $52 bet)
2. **Market settles** correctly → 100% profit ($100 - $52 = $48 profit)
3. **Stop loss triggers** at 30¢ → ~42% loss ($22 loss)

---

## Monitoring Performance

### Real-Time Dashboard

Open [http://localhost:3001](http://localhost:3001) to see:
- Total P&L and ROI
- Win rate and trade count  
- Active positions with live prices
- Recent trade history
- Performance charts

### Logs

```bash
# Main trading log
tail -f logs/trades_2025.log

# Error log (stack traces)
tail -f logs/errors.log

# Window-specific logs
ls logs/window_*.log
```

### Position Reports

Every 60 seconds, the bot prints a summary:

```
📊 Position Report (3 active):
  BTC UP  | 📈 100.00 @ $0.52 → $0.68 | +$16.00 (+30.8%) | ⏰ 8m 30s | 📊 Exit active
  ETH DOWN| 📉 50.00 @ $0.48 → $0.55 | +$3.50 (+14.6%) | ⏰ 12m 15s | ⏳ Waiting
  XRP UP  | 📈 75.00 @ $0.61 → $0.59 | -$1.50 (-3.3%) | ⏰ 3m 45s | 📊 Exit active
```

---

## Troubleshooting

### "Missing PROXY_PK in .env"
- Add your Polygon private key (0x...) to `.env`
- Ensure key has USDC balance

### "Balance shows 0.0000"
- Check wallet has USDC on Polygon network
- Use `get_balance()` to verify

### "No trades executing"
- Check `MIN_EDGE` isn't too high (try 0.30)
- Verify markets are active (BTC, ETH, XRP, SOL)
- Check logs for "No edge detected" messages

### "Exit plans not placing"
- Ensure `ENABLE_EXIT_PLAN=YES` in `.env`
- Check balance validation logs for XRP issues
- Verify position has at least 5.0 shares (`MIN_SIZE`)

### "WebSocket disconnected"
- Bot auto-reconnects, no action needed
- If persistent, check internet connection
- Fallback to polling is automatic

### Dashboard shows no data
- Ensure `trades.db` exists in project root
- Check bot has executed at least one trade
- Verify port 3001 is not blocked

---

## Next Steps

1. **Read Strategy Documentation**: [docs/STRATEGY.md](docs/STRATEGY.md)
2. **Choose Risk Profile**: [docs/RISK_PROFILES.md](docs/RISK_PROFILES.md)
3. **Understand Position Flow**: [docs/POSITION_FLOW.md](docs/POSITION_FLOW.md)
4. **Review API Integration**: [docs/API.md](docs/API.md)

---

## Production Checklist

Before running with real money:

- [ ] Tested with small position size (BET_PERCENT=1.0)
- [ ] Verified stop loss triggers correctly
- [ ] Confirmed exit plans place and fill
- [ ] Monitored for 24 hours without issues
- [ ] Reviewed at least 10 completed trades
- [ ] Understood all risk management settings
- [ ] Set up Discord notifications (optional)
- [ ] Backed up `.env` securely
- [ ] Documented your configuration choices

---

## Getting Help

- **Documentation**: Check [docs/](docs/) folder
- **Logs**: Review `logs/trades_2025.log` and `logs/errors.log`
- **Discord**: (Add your community Discord if available)
- **Issues**: GitHub Issues for bug reports

---

## Safety Reminder

⚠️ **This bot trades real money on Polymarket.**

- Start with small position sizes
- Never risk more than you can afford to lose
- Monitor performance regularly
- Understand all settings before changing them
- Keep your private key secure (never commit to Git)

---

**Happy Trading!** 🚀
