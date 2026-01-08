# Agent Guidelines for PolyFlup Trading Bot

This document provides coding standards and guidelines for AI agents working on the PolyFlup trading bot codebase.

## Project Overview

PolyFlup is an automated trading bot for 15-minute crypto prediction markets on Polymarket. It consists of:
- **Backend**: Python trading bot (`src/`)
- **Frontend**: Svelte dashboard (`ui/`)
- **Database**: SQLite (`trades.db`)

## Skills & Instructions

This project uses **OpenCode Skills** for detailed instructions. Agents should load these skills on-demand to understand specific parts of the system:

- `python-bot-standards`: Coding standards and modular architecture for the Python backend.
- `svelte-ui-standards`: Guidelines for the Svelte/Tailwind/shadcn-svelte frontend.
- `database-sqlite`: SQLite connection management and migration system.
- `polymarket-trading`: Trading strategies, terminology, and Polymarket API nuances.
- `polyflup-ops`: Operational commands (Docker, uv, npm) and environment configuration.
- `polyflup-history`: Chronological log of recent system improvements.

## Subagents

Specialized subagents are available for complex, multi-step tasks. Use the `task` tool with the appropriate `subagent_type`:

- `log-analyzer`: Specialized in syncing logs and finding requested information.
- `database-analyzer`: Specialized in syncing the database and looking for requested data.
- `trainer`: Responsible for keeping skills and internal knowledge base up to date.
- `bookkeeper`: Responsible for keeping project documentation and summaries up-to-date.

## Quick Start (Core Commands)

### Backend
```bash
uv run polyflup.py      # Start trading bot
uv run check_db.py      # Verify database integrity
```

### Frontend
```bash
cd ui && npm run dev    # Start dashboard development server
```

### Logs
- `logs/trades_2025.log`: Master audit log.
- `logs/errors.log`: Dedicated error stack traces.
- `logs/window_*.log`: Specific 15-minute window history.

## Important Mandates

1. **Database Safety**: ALWAYS use `db_connection()` context manager. NEVER call `conn.commit()` manually.
2. **Deadlock Prevention**: Pass active cursors when calling write functions from within an existing transaction.
3. **Logging**: Start log lines with relevant emojis (🚀, ✅, ❌, 👀) and include `[SYMBOL]` context.
4. **Precision**: Use `0.0001` threshold for share balance comparisons.
5. **Min Size**: Enforce 5.0 share minimum for all limit orders.
6. **Fees**: Taker fees apply to 15m crypto markets (BUY=Tokens, SELL=USDC). Prefer Maker orders to avoid fees and earn rebates.

## Glossary

- **Orders**: Buy/sell orders to enter/modify/exit a position. These are submitted to the exchange and can be open (unfilled) or closed (filled).
- **Position**: The net exposure from all filled orders on a given side during a trading window. Represents the actual shares held.

## Sync Functions

The bot uses a three-tier sync system on startup:

1. **`sync_with_exchange(user_address)`**: Master sync function that calls both order and position syncs
2. **`sync_orders_with_exchange()`**: Syncs open order status from the CLOB order book
3. **`sync_positions_with_exchange(user_address)`**: Syncs position sizes and entry prices from Data API
