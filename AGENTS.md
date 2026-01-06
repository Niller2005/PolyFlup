# Agent Guidelines for PolyFlup Trading Bot

This document provides coding standards and guidelines for AI agents working on the PolyFlup trading bot codebase.

## Project Overview

PolyFlup is an automated trading bot for 15-minute crypto prediction markets on Polymarket. It consists of:
- **Backend**: Python trading bot (`src/`)
- **Frontend**: Svelte dashboard (`ui/`)
- **Database**: SQLite (`trades.db`)

## Helper Tools

The following tools are installed and available for system tasks:
- **ripgrep (`rg`)**: Fast content search across the codebase.
- **sqlite3**: CLI tool for inspecting and modifying the `trades.db` database.

## Build, Lint, and Test Commands

### Python Backend

```bash
# Run the trading bot
uv run polyflup.py

# Run a specific test script
uv run test_price_buffer.py

# Install dependencies
uv pip install -r requirements.txt

# Alternative with uv sync (if pyproject.toml exists)
uv sync

# Check database
uv run check_db.py

# Database migration
uv run migrate_db.py
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
docker logs -f polyflup-bot

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
- Always start log lines with relevant emojis and include the symbol and trade ID in brackets: `EMOJI [SYMBOL] #ID message`
- For trade actions (Scale-in, Exit Plan, etc.), prepend the current position summary: `  EMOJI [SYMBOL] Trade #ID SIDE PnL% | Action message`
- Use dynamic emojis (📈/📉) based on current PnL in log prefixes
- Only log position details on verbose cycles (60s) and only when P&L is significant (>20% or <-30%)
- Don't log routine order status checks (LIVE, DELAYED, UNMATCHED) unless there's an issue
- Consolidate related information into single log lines (e.g., add `📋 Scale-in pending` or `📊 Scaled in` to status summary)
- Use emojis consistently to make log scanning effortless
- **Exit Plan Visibility**: Always show current status (Pending/Active) in verbose monitoring logs.

**Action & Settlement Log Examples:**
```text
  📈 [XRP] Trade #154 UP PnL=+71.1% | 📈 SCALE IN triggered: price=$0.78, 119s left
  📈 [XRP] Trade #154 UP PnL=+71.1% | ✅ SCALE IN order placed: 28.92 shares @ $0.78 (status: live)
  📈 [XRP] Trade #154 UP PnL=+71.1% | ⏳ Exit plan pending (45s/60s)
🎯 [BTC] EXIT PLAN SUCCESS: Trade #143 MATCHED at 0.99! (matched 30.58 shares)
💰 [BTC] #143 UP: +4.89$ (+19.2%)
✅ [BTC] #154 UP PnL=+71.1% | ⏰ Exit plan active (120s) | ✅ SCORING
```


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
├── data/           # Database and market data
│   ├── market_data/ # Modular data fetching (Polymarket, Binance, Indicators)
│   └── ...
├── trading/        # Core trading logic
│   ├── orders/      # Modular order management (Limit, Market, Batch)
│   ├── position_manager/ # Modular position monitoring
│   └── strategy.py  # Entry signals and confidence logic
└── utils/          # Logging, web3 utilities, WebSocket manager
```

### Key Patterns
1. **Separation of Concerns**: Each module has a clear responsibility
2. **Database Connection**: Use `db_connection()` context manager
   - **NEVER** call `conn.commit()` manually - handled by context manager
   - Context manager automatically commits on success, rolls back on error
3. **Timing**: UTC timezone via `ZoneInfo('UTC')`
4. **Position Monitoring**: High-frequency checks (10 second intervals)
5. **Hedged Reversal**: Bot can hold both UP and DOWN positions simultaneously for the same window. Reversals don't close existing positions; the losing side is cleared via stop loss.
6. **Midpoint Stop Loss**: Primary stop loss trigger is the midpoint price (default <= $0.30) rather than percentage-based PnL.
7. **Trade Execution**: Validate before saving to database
8. **Order Status**: Treat both `FILLED` and `MATCHED` statuses as successful executions in the position manager to ensure trades are settled promptly and redundant logging is avoided.

### Data Flow
```
polyflup.py (main loop)
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
- The bot uses local SQLite (`trades.db`) for all operations.
- Data is stored in the project root.

## Database Schema

Main table: `trades`
- Core fields: `id`, `timestamp`, `symbol`, `side`, `entry_price`, `size`
- Settlement: `settled`, `final_outcome`, `exit_price`, `pnl_usd`, `roi_pct`
- Metadata: `order_id`, `order_status`, `target_price`, `is_reversal`

## Debugging & Monitoring

### Local Logs
For deep context on historical trades, strategy decisions, and execution details beyond what is available in the database, refer to the local log files:
- **`logs/trades_2025.log`**: Contains verbose history of bot cycles, signal evaluations, order placements, and settlement details. Use this file when debugging unexpected bot behavior or auditing past trades.

### Commands
- **View real-time container logs**: `docker logs -f polyflup-bot`
- **Inspect database**: `sqlite3 trades.db`
- **Check DB integrity**: `uv run check_db.py`

## Common Pitfalls

1. **Don't** modify git config or run destructive commands
2. **Don't** commit `.env` files (secrets)
3. **Don't** skip validation before database inserts
4. **Don't** call `conn.commit()` manually - the context manager handles it
5. **Do** check order success before saving trades
6. **Do** use context managers for database connections
7. **Do** handle timezone conversions properly (always use UTC)
8. **Do** log with appropriate context and symbols

## Testing

- Test scripts are standalone Python files (e.g., `test_price_buffer.py`)
- Run directly: `uv run test_<name>.py`
- No pytest framework currently in use
- Test database operations with `uv run check_db.py`

## Deployment

- Production uses Docker Compose
- Two containers: bot + UI
- Database: Local SQLite only
- UI serves on port 3001 (API + frontend)

## Contributing

When modifying code:
1. Maintain existing patterns and style
2. Add logging for new operations
3. Update error handling appropriately
4. Test with actual API if possible
5. Consider Discord notifications for user-facing events
6. Document complex trading logic in comments
7. Provide "Before and After" Examples: When making visual changes (e.g., logging formatting, UI layouts) or significant logic refactors, provide a brief "before and after" comparison in your response to help the user visualize the impact. Ensure these are wrapped in code blocks (markdown triple backticks) to preserve exact formatting.

## Documentation Reference

For additional information about the codebase and recent improvements:

- **instructions/SESSION_IMPROVEMENTS.md** - Full details on all improvements from latest session (bug fixes, features, API integrations)
- **instructions/QUICK_REFERENCE.md** - Code examples and quick lookup for new features (batch orders, market orders, notifications, etc.)
- **instructions/MIGRATIONS.md** - How to add database migrations (step-by-step guide with examples)
- **instructions/DATABASE_BEST_PRACTICES.md** - Database connection patterns and best practices
- **instructions/POLYMARKET.md** - Polymarket API documentation and reference
- **instructions/SHADCN_SVELTE.md** - Shadcn-svelte component documentation and registry
- **AGENTS.md** - This file - General coding standards and project overview
