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
5. **Min Size**: Enforce 5.0 share minimum for all limit orders. Hold winning positions <5.0 shares, orphan losing ones.
6. **Fees**: Maker rebates (0.15%) for POST_ONLY orders, taker fees (1.54%) for GTC orders. Bot uses POST_ONLY by default, switches to GTC after 3 crossing failures.
7. **Atomic Hedging**: All trades are entry+hedge pairs placed simultaneously. Combined price must be ≤ $0.99 to guarantee profit.

## Glossary (v0.6.0+)

- **Atomic Hedging**: Simultaneous entry+hedge pair placement via batch API (primary risk management).
- **Trade**: An atomic pair consisting of entry side + hedge side (both UP and DOWN).
- **Combined Price Threshold**: Maximum allowed sum of entry + hedge prices (default $0.99).
- **Emergency Liquidation**: Time-aware progressive pricing to recover value from partial fills.
- **Pre-Settlement Exit**: Confidence-based early exit of losing side (T-180s to T-45s before resolution).
- **POST_ONLY**: Order type that only adds liquidity (earns maker rebates, fails if crossing).
- **GTC**: Order type that fills immediately as taker (pays taker fees).
- **Orphaned Position**: Position <5.0 shares that cannot be sold due to exchange minimum.

## Sync Functions

The bot uses a three-tier sync system on startup:

1. **`sync_with_exchange(user_address)`**: Master sync function that calls both order and position syncs
2. **`sync_orders_with_exchange()`**: Syncs open order status from the CLOB order book
3. **`sync_positions_with_exchange(user_address)`**: Syncs position sizes and entry prices from Data API
