# Agent Guidelines for PolyAstra Trading Bot

This document provides coding standards and guidelines for AI agents working on the PolyAstra trading bot codebase.

## Project Overview

PolyAstra is an automated trading bot for 15-minute crypto prediction markets on Polymarket. It consists of:
- **Backend**: Python trading bot (`src/`)
- **Frontend**: Svelte dashboard (`ui/`)
- **Database**: SQLite (`trades.db`)

## Build, Lint, and Test Commands

### Python Backend

```bash
# Run the trading bot
python polyastra.py

# Run a specific test script
python test_price_buffer.py

# Install dependencies
pip install -r requirements.txt

# Alternative with uv (if available)
uv pip install -r requirements.txt

# Check database
python check_db.py

# Database migration
python migrate_db.py
```

**Note**: No pytest configuration found. Test files are standalone scripts run directly with Python.

### Svelte UI

```bash
# Install dependencies
cd ui && npm install

# Development mode (runs server + vite)
npm run dev

# Production build
npm run build

# Preview production build
npm run preview

# Start production server
npm start
```

### Docker

```bash
# Build and run both bot and UI
docker compose up -d --build

# View bot logs
docker logs -f polyastra-bot

# Stop containers
docker compose down
```

## Code Style Guidelines

### Python (Backend)

#### Imports
- Use absolute imports from `src.*`
- Group imports: standard library → third-party → local
- Example:
  ```python
  import time
  from datetime import datetime
  from eth_account import Account
  from src.config.settings import MARKETS, PROXY_PK
  from src.utils.logger import log
  from src.data.database import init_database
  ```

#### Formatting
- **Indentation**: 4 spaces
- **Line length**: ~90 characters (see bot.py:276-282)
- **Strings**: Double quotes preferred
- **Docstrings**: Triple double-quotes with brief description
  ```python
  """Calculate confidence score and directional bias"""
  ```

#### Types
- Type hints encouraged but not strictly enforced
- Use type hints in function signatures where helpful:
  ```python
  def _determine_trade_side(bias: str, confidence: float) -> tuple[str, float]:
  ```

#### Naming Conventions
- **Functions**: `snake_case` (e.g., `calculate_confidence`, `get_balance`)
- **Classes**: `PascalCase` (if added)
- **Constants**: `UPPER_SNAKE_CASE` (e.g., `MIN_EDGE`, `ADX_ENABLED`)
- **Private functions**: Prefix with `_` (e.g., `_determine_trade_side`)
- **Files/Modules**: `snake_case.py`

#### Error Handling
- Use try-except blocks with specific exceptions when possible
- Always log errors with context:
  ```python
  try:
      result = place_order(token_id, price, size)
  except Exception as e:
      log(f"[{symbol}] Order failed: {e}")
      return
  ```
- Graceful degradation: return neutral/safe values on error
- Send critical errors to Discord webhook via `send_discord()`

#### Logging
- Use the `log()` function from `src.utils.logger`
- Include context: `log(f"[{symbol}] message")`
- **Always start log lines with relevant emojis** for visual scanning
- Keep logs concise - only log significant events and state changes
- Use verbose cycles (every 60s) for routine monitoring logs

**Standard Emoji Guide:**
  - 👀 Monitoring/watching positions
  - 📈 Position with positive P&L
  - 📉 Position with negative P&L / Exit plan
  - 🛑 Stop loss triggered
  - 🎯 Take profit / Exit plan filled
  - ✅ Success / Order filled
  - ❌ Error/Failure
  - ⚠️ Warning
  - 🔄 Reversal / Retry / Update
  - 🔔 Notification
  - 💪 Holding despite negative P&L (on winning side)
  - ⏳ Waiting / Processing
  - 📊 Position size/average price updates
  - 💰 Money/Balance
  - 🚀 Trade execution

**Logging Best Practices:**
- Only log position details on verbose cycles (60s) and only when P&L is significant (>20% or <-30%)
- Don't log routine order status checks (LIVE, DELAYED, UNMATCHED) unless there's an issue
- Consolidate related information into single log lines
- Use emojis consistently to make log scanning effortless

#### Configuration
- All config in `src/config/settings.py`
- Load from environment variables with defaults
- Validate critical settings (e.g., PROXY_PK must exist and start with "0x")
- Use type conversion: `float()`, `int()`, `.upper() == "YES"`

### JavaScript/Svelte (Frontend)

#### Formatting (Biome)
- **Indentation**: 2 spaces (enforced by biome.json)
- **Style**: Space indent, recommended rules enabled
- Ignore CSS files from formatting

#### File Structure
- Components in `ui/src/lib/components/`
- UI components in `ui/src/lib/components/ui/`
- Each component exports from `index.ts`
- Stores in `ui/src/lib/stores/`

#### Naming
- **Components**: `PascalCase.svelte` (e.g., `App.svelte`)
- **Utilities**: `camelCase.js` (e.g., `theme.js`)
- **CSS classes**: Tailwind utility classes

#### Svelte Patterns
- Use reactive declarations: `$: winRate = ...`
- Prefer `{#snippet}` for chart tooltips (Svelte 5 syntax)
- Use `onMount` for initialization and intervals
- Clean up intervals in return function

#### API Integration
- Fetch from `http://${hostname}:3001/api/stats`
- Poll every 5 seconds for real-time updates
- Handle loading and error states

## Architecture Patterns

### Backend Module Structure
```
src/
├── config/         # Settings and environment variables
├── data/           # Database, market data fetching
├── trading/        # Orders, strategy, position management
└── utils/          # Logging, web3 utilities
```

### Key Patterns
1. **Separation of Concerns**: Each module has a clear responsibility
2. **Database Connection**: Use `db_connection()` context manager
3. **Timing**: UTC timezone via `ZoneInfo('UTC')`
4. **Position Monitoring**: High-frequency checks (1 second intervals)
5. **Trade Execution**: Validate before saving to database

### Data Flow
```
polyastra.py (main loop)
  → trade_symbol()
    → calculate_confidence() [strategy]
    → place_order() [orders]
    → save_trade() [database]
  → check_open_positions() [position_manager]
  → check_and_settle_trades() [settlement]
```

## Environment Variables

Key settings (see `.env.example`):
- `PROXY_PK`: Private key (required)
- `BET_PERCENT`: Position size (default: 5.0)
- `MIN_EDGE`: Minimum confidence (default: 0.565)
- `ENABLE_STOP_LOSS`: YES/NO
- `ENABLE_TAKE_PROFIT`: YES/NO
- `ENABLE_REVERSAL`: YES/NO
- `MARKETS`: Comma-separated symbols (e.g., "BTC,ETH,SOL,XRP")

### Database Configuration
- `USE_TURSO`: YES/NO (direct remote Turso connection)
- `USE_EMBEDDED_REPLICA`: YES/NO (local replica synced with Turso - **recommended for local dev**)
- `TURSO_DATABASE_URL`: Turso database URL
- `TURSO_AUTH_TOKEN`: Turso authentication token
- `EMBEDDED_REPLICA_FILE`: Local replica file path (default: trades_replica.db)

## Database Schema

Main table: `trades`
- Core fields: `id`, `timestamp`, `symbol`, `side`, `entry_price`, `size`
- Settlement: `settled`, `final_outcome`, `exit_price`, `pnl_usd`, `roi_pct`
- Metadata: `order_id`, `order_status`, `target_price`, `is_reversal`

## Common Pitfalls

1. **Don't** modify git config or run destructive commands
2. **Don't** commit `.env` files (secrets)
3. **Don't** skip validation before database inserts
4. **Do** check order success before saving trades
5. **Do** use context managers for database connections
6. **Do** handle timezone conversions properly (always use UTC)
7. **Do** log with appropriate context and symbols

## Testing

- Test scripts are standalone Python files (e.g., `test_price_buffer.py`)
- Run directly: `python test_<name>.py`
- No pytest framework currently in use
- Test database operations with `check_db.py`

## Deployment

- Production uses Docker Compose
- Two containers: bot + UI
- Shared SQLite database via volume mount
- UI serves on port 3001 (API + frontend)

## Contributing

When modifying code:
1. Maintain existing patterns and style
2. Add logging for new operations
3. Update error handling appropriately
4. Test with actual API if possible
5. Consider Discord notifications for user-facing events
6. Document complex trading logic in comments

## Documentation Reference

For additional information about the codebase and recent improvements:

- **instructions/SESSION_IMPROVEMENTS.md** - Full details on all improvements from latest session (bug fixes, features, API integrations)
- **instructions/QUICK_REFERENCE.md** - Code examples and quick lookup for new features (batch orders, market orders, notifications, etc.)
- **instructions/MIGRATIONS.md** - How to add database migrations (step-by-step guide with examples)
- **instructions/TURSO_MIGRATION.md** - Complete guide for Turso database setup and migration
- **AGENTS.md** - This file - General coding standards and project overview
