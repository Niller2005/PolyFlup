---
name: polyflup-ops
description: Operational commands, environment configuration, and deployment for PolyFlup.
---

## Commands & Operations

### Python Backend
```bash
uv run polyflup.py      # Run bot
uv run check_db.py      # Check database
uv run migrate_db.py    # Run migrations
uv pip install -r requirements.txt
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
- `PROXY_PK`: Private key (Required, starts with 0x)
- `BET_PERCENT`: Position size (default 5.0)
- `MIN_EDGE`: Min confidence (default 0.565)
- `ENABLE_STOP_LOSS`, `ENABLE_TAKE_PROFIT`, `ENABLE_REVERSAL`: YES/NO
- `MARKETS`: Comma-separated symbols (e.g., BTC,ETH)

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

#### Database Locked
- Ensure only one bot instance is running
- Check for zombie processes: `ps aux | grep polyflup`
- Database uses WAL mode for better concurrency

#### Balance API Issues
- XRP markets have known reliability issues (15m grace period configured)
- Enhanced balance validation with retry logic automatically handles this
- Check `ENABLE_ENHANCED_BALANCE_VALIDATION=YES` in .env

#### Position Sync Issues
- Bot performs startup sync with exchange on launch
- Uses both CLOB order book and Data API for position validation
- Automatic self-healing logic corrects size mismatches
