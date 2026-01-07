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
- Master Log: `logs/trades_2025.log`
- Window Logs: `logs/window_YYYY-MM-DD_HH-mm.log`
- Error Log: `logs/errors.log`
