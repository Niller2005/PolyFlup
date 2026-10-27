---
name: polyflup-ops
description: Operational commands, environment configuration, and deployment for PolyFlup.
---

## Commands & Operations

### Python Backend
```bash
uv run polyflup.py      # Run bot
uv run polyflup.py --paper  # Paper trading (simulated fills on live books, trades_paper.db, no keys needed)
uv run tui.py           # Terminal dashboard (read-only): q quit, r refresh, p pause, l toggle log page; --db PATH, --once, --no-wallet
uv run check_db.py      # Check database
uv run migrate_db.py    # Run migrations
uv sync                 # Install dependencies (pyproject.toml + uv.lock)
```

### Svelte UI
```bash
cd ui && npm install
npm run dev             # Dev mode
npm run build           # Build
npm start               # Start production
```

### Docker
```bash
docker compose up -d --build
docker logs -f polyflup-bot
docker compose down
```

## Environment Variables

### Core Settings
- `PROXY_PK`: Private key (Required, starts with 0x)
- `FUNDER_PROXY`: Wallet address holding pUSD (proxy/Safe/deposit wallet)
- `SIGNATURE_TYPE`: 0 EOA, 1 POLY_PROXY, 2 POLY_GNOSIS_SAFE (default), 3 POLY_1271
- `POLYGON_RPC`: RPC endpoint for pUSD balance reads (default https://polygon-rpc.com)
- `BET_PERCENT`: Position size (default 5.0)
- `MIN_EDGE`: Min confidence (default 0.35)
- `MIN_NET_EDGE` (0.01), `ENABLE_MAKER_PAIR` (YES), `MAKER_PAIR_PRICE_BAND` (0.20), `MAKER_PAIR_IMPROVE_TICKS` (0), `TAKER_FEE_RATE` (0.07)
- `LATE_WINDOW_ENABLED` (YES, shadow), `LATE_WINDOW_LIVE` (NO), `LATE_WINDOW_SECONDS` (90), `LATE_WINDOW_MIN_EV` (0.015), `LATE_WINDOW_MIN_DISTANCE_PCT` (0.02), `LATE_WINDOW_BET_USD` (5)
- `MARKETS`: Comma-separated market keys. `BTC` = 15-minute market, `BTC-5M` = 5-minute market (e.g., `BTC,ETH,BTC-5M`)
- `MAX_ENTRY_LATENESS_SEC_5M`: Optional override for 5m entry cutoff (default: 15m value scaled, 600s -> 200s)

### Atomic Hedging Configuration (v0.6.0+)
- `COMBINED_PRICE_THRESHOLD`: Max combined price for entry+hedge (default 0.99)
- `HEDGE_FILL_TIMEOUT_SECONDS`: Timeout for both orders to fill (default 120)
- `HEDGE_POLL_INTERVAL_SECONDS`: Polling interval for fill checks (default 5)
- `MAX_POST_ONLY_ATTEMPTS`: Switch to GTC after this many POST_ONLY failures (default 3)

### Pre-Settlement Exit (v0.6.0+)
- `ENABLE_PRE_SETTLEMENT_EXIT`: Enable confidence-based early exit (default YES)
- `PRE_SETTLEMENT_MIN_CONFIDENCE`: Min confidence to trigger exit (default 0.80)
- `PRE_SETTLEMENT_EXIT_SECONDS`: Start checking this many seconds before resolution (default 180)
- `PRE_SETTLEMENT_CHECK_INTERVAL`: Check interval in seconds (default 30)

### Emergency Liquidation (v0.6.0+)
- `EMERGENCY_SELL_ENABLE_PROGRESSIVE`: Enable time-aware pricing (default YES)
- `EMERGENCY_SELL_WAIT_SHORT`: Wait time for AGGRESSIVE mode (default 5s)
- `EMERGENCY_SELL_WAIT_MEDIUM`: Wait time for BALANCED mode (default 8s)
- `EMERGENCY_SELL_WAIT_LONG`: Wait time for PATIENT mode (default 10s)
- `EMERGENCY_SELL_FALLBACK_PRICE`: Minimum price floor (default 0.10)
- `EMERGENCY_SELL_HOLD_IF_WINNING`: Hold positions <5.0 shares if winning (default YES)
- `EMERGENCY_SELL_MIN_PROFIT_CENTS`: Min profit in cents to hold small position (default 2)

### Signal Calculation (v0.5.0+)
- `BAYESIAN_CONFIDENCE`: Use Bayesian method (YES) or Additive method (NO, default)

### Deprecated Settings (v0.6.0 - No longer used)
- `ENABLE_STOP_LOSS`, `STOP_LOSS_PRICE`, `STOP_LOSS_PERCENT`: Replaced by emergency liquidation
- `ENABLE_TAKE_PROFIT`, `TAKE_PROFIT_PERCENT`: Not used with atomic hedging
- `ENABLE_REVERSAL`, `ENABLE_HEDGED_REVERSAL`: All trades are hedged by default
- `ENABLE_EXIT_PLAN`, `EXIT_PRICE_TARGET`, `EXIT_MIN_POSITION_AGE`: Replaced by atomic hedging
- `ENABLE_SCALE_IN`, `SCALE_IN_*`: Not compatible with atomic hedging strategy
- `CANCEL_UNFILLED_ORDERS`, `UNFILLED_TIMEOUT_SECONDS`: Automatic with atomic hedging
- `ENABLE_REWARD_OPTIMIZATION`: Not applicable to atomic pairs
- `ENABLE_ENHANCED_BALANCE_VALIDATION`: Not primary focus with atomic hedging

## Debugging

### Log Files
- **Master Log**: `logs/trades_2025.log` - All trading activity and monitoring
- **Window Logs**: `logs/window_YYYY-MM-DD_HH-mm.log` - Specific 15-minute window history
- **Error Log**: `logs/errors.log` - Dedicated error stack traces and exceptions

### Database Operations
```bash
# Check database integrity
uv run check_db.py

# Run migrations manually
uv run migrate_db.py

# Check migration status
uv run check_migration_status.py
```

### Production Sync
The bot includes specialized tools for syncing production data:
- **sync_db**: Download production `trades.db` via SSH
- **sync_logs**: Update local logs from production server

### Common Issues

#### POST_ONLY Crossing Failures
- Bot tracks POST_ONLY failures per symbol
- After 3 failures, automatically switches to GTC orders (accepts taker fees)
- Counter resets on successful atomic placement
- Check logs for "POST_ONLY crossing detected" messages

#### Partial Fill Recovery
- Emergency liquidation activates when one side fills, other times out
- Time-aware pricing: PATIENT (>600s) / BALANCED (300-600s) / AGGRESSIVE (<300s)
- MIN_ORDER_SIZE check: Hold if winning & <5.0 shares, orphan if losing
- Check logs for "Emergency liquidation" and urgency level

#### Atomic Pair Timeouts
- Default 120-second timeout for both orders to fill
- If neither fills, both are cancelled and retry attempted
- Check `HEDGE_FILL_TIMEOUT_SECONDS` in .env
- Monitor logs for "Both orders failed to fill" messages

#### Database Locked
- Ensure only one bot instance is running
- Check for zombie processes: `ps aux | grep polyflup`
- Database uses WAL mode for better concurrency

#### Position Sync Issues
- Bot performs startup sync with exchange on launch
- Uses both CLOB order book and Data API for position validation
- Atomic hedging eliminates most sync issues (no unhedged positions)
