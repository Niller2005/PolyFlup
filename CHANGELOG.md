# Changelog

All notable changes to the PolyFlup trading bot are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project uses semantic versioning.

---

## [0.4.3] - 2026-01-10

### Added
- **Enhanced Position Reports**: Clean, aligned format with directional emojis (📈📉) and status indicators
- **Real-Time WebSocket Integration**: Near-instant updates via User Channel (fills/cancels) and Market Channel (prices)
- **Batch API Optimization**: Single-call midpoint fetching for all positions
- **Enhanced Balance Validation**: Symbol-specific tolerance with cross-validation for API reliability issues
- **Settlement Auditing**: Automated P&L verification against exchange `closed-positions` API
- **Position Adoption**: Detects and manages untracked exchange positions on startup
- **Reward Optimization**: Exit plans auto-adjust prices to earn liquidity rewards via `check_scoring`
- **Lead/Lag Momentum**: Polymarket-native trend data with 1.2x bonus for agreement, 0.8x penalty for divergence

### Changed
- **Monitoring Cycle**: Reduced from 10s to 1s for real-time responsiveness
- **Exit Plan Logic**: Immediate placement after fill (no minimum age requirement)
- **Price Validation**: Enhanced for high-confidence trades with volatility detection
- **Log Formatting**: Cleaner, less verbose output with better emoji indicators

### Fixed
- **XRP Balance API Issues**: Cross-validation prevents zero-balance errors
- **Exit Order Size Mismatches**: Real-time validation and repair after scale-in
- **MIN_EDGE Enforcement**: Prevents low-confidence entries from bypassing threshold
- **Infinite Repair Loops**: Fixed rounding differences causing continuous order updates
- **Ghost Trade Detection**: Improved grace periods and position data fallback

### Deprecated
- `EXIT_CHECK_INTERVAL`: Now runs on 1-second monitoring cycle
- `EXIT_AGGRESSIVE_MODE`: Always active with 1-second cycle
- `STOP_LOSS_PERCENT`: Replaced by midpoint-based `STOP_LOSS_PRICE`
- `ENABLE_TAKE_PROFIT`: Use `EXIT_PLAN` instead

---

## [0.4.2] - 2026-01-05

### Added
- **WebSocket Foundation**: User and Market channel subscriptions
- **Batch Pricing**: `get_multiple_market_prices` for efficient midpoint fetching
- **Position Adoption**: Startup sync detects untracked exchange positions
- **Settlement Audit**: Automated verification against closed-positions API

### Changed
- **Startup Logic**: Enhanced position sync with market resolution verification
- **Error Handling**: Suppressed 404/Not Found during market transitions

### Fixed
- **API Rate Limiting**: Reduced calls via batch operations
- **Position Sync**: String-based ID comparison for Data API

---

## [0.4.1] - 2025-12

### Added
- **Dynamic Scale-In Timing**: Confidence-weighted entry windows (12m for high conviction)
- **Maker Orders**: Scale-in uses limit orders to earn rebates
- **Enhanced Monitoring**: 10-second position checking cycle
- **Database Migrations**: Automated schema version management

### Changed
- **Scale-In Logic**: Added confidence-based timing adjustments
- **Position Reports**: More detailed status indicators

### Fixed
- **Scale-In Fills**: Improved balance sync after secondary entries
- **Order Status**: Better tracking of unfilled orders

---

## [0.4.0] - 2025-12

### Added
- **Modular Backend**: Refactored `src/trading/orders` and `src/data/market_data`
- **Exit Plan System**: Automatic limit sell orders at 99¢ target
- **Hedged Reversal**: Hold both sides during trend flips
- **Stop Loss Enhancements**: Triple-check logic with 120s cooldown

### Changed
- **Architecture**: Separated concerns into specialized modules
- **Position Manager**: Split into entry, exit, scale, stop_loss, reversal modules
- **Database Schema**: Added reversal tracking columns

### Fixed
- **Deadlock Prevention**: Proper cursor passing in transactions
- **Balance Precision**: 0.0001 threshold for share comparisons

---

## [0.3.0] - 2025-11

### Added
- **Binance Integration**: Multi-source signal integration (momentum, order flow, divergence)
- **Advanced Strategy**: Directional voting system with weighted confidence
- **ADX Filter**: Optional trend strength confirmation
- **Volume-Weighted Momentum**: VWAP-based signals

### Changed
- **Signal Calculation**: Replaced simple edge with composite scoring
- **Position Sizing**: Confidence-based scaling with multiplier

### Fixed
- **Circular Reasoning**: External validation via Binance prevents self-referential pricing

---

## [0.2.0] - 2025-10

### Added
- **SQLite Database**: Complete trade tracking and audit logging
- **Svelte Dashboard**: Real-time UI with charts and statistics
- **Auto-Claim**: Automated winning redemption via CTF contract
- **Discord Notifications**: Real-time trade alerts

### Changed
- **Logging**: Structured logs with emoji indicators
- **Window Timing**: Configurable entry delay and lateness limits

---

## [0.1.0] - 2025-09

### Added
- **Initial Release**: Basic 15-minute crypto prediction market trading
- **Order Book Analysis**: Spread calculation and imbalance detection
- **Position Management**: Entry, monitoring, and settlement
- **Docker Support**: Containerized deployment with docker-compose

---

## Release Schedule

- **Patch versions** (0.x.Y): Bug fixes, minor improvements
- **Minor versions** (0.X.0): New features, enhancements
- **Major versions** (X.0.0): Breaking changes, major rewrites

---

## Unreleased

### Planned Features
- Machine learning signal optimization
- Multi-market correlation analysis
- Advanced backtesting framework
- Portfolio rebalancing logic
- Enhanced risk management profiles

---

For detailed technical documentation, see:
- [README.md](README.md) - Project overview and setup
- [docs/STRATEGY.md](docs/STRATEGY.md) - Trading strategy deep dive
- [docs/POSITION_FLOW.md](docs/POSITION_FLOW.md) - Position lifecycle
- [docs/MIGRATIONS.md](docs/MIGRATIONS.md) - Database schema changes
