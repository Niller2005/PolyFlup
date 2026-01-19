# Changelog

All notable changes to the PolyFlup trading bot are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project uses semantic versioning.

---

## [Unreleased]

### In Progress
- Documentation updates for atomic hedging strategy (v0.6.0)

---

## [0.6.0] - 2026-01-19

### Added
- **🎯 Atomic Hedging Strategy**: Complete overhaul to simultaneous entry+hedge pairs
  - Batch API placement with combined price threshold (≤ $0.99)
  - POST_ONLY orders by default for maker rebates (0.15%)
  - Smart GTC fallback after 3 POST_ONLY failures (accepts 1.54% taker fees)
  - 120-second fill timeout with immediate cancellation of unfilled orders
  - Eliminates all unhedged positions
- **⏰ Pre-Settlement Exit Strategy**: Evaluates positions T-180s to T-45s before resolution
  - Sells losing side early if confidence > 80% on one side
  - Keeps winning side for full resolution profit
  - Uses same strategy signals as entry logic
- **🚨 Time-Aware Emergency Liquidation**: Adapts pricing based on time remaining
  - **PATIENT** (>600s): Small price drops (1¢), long waits (10-20s) - maximize recovery
  - **BALANCED** (300-600s): Moderate drops (2-5¢), balanced waits (6-10s)
  - **AGGRESSIVE** (<300s): Rapid drops (5-10¢), short waits (5-10s) - ensure liquidation
- **🔬 Smart MIN_ORDER_SIZE Handling**: Positions < 5.0 shares held if winning, orphaned if losing

### Changed
- **Execution Flow**: All trades now atomic pairs (entry+hedge simultaneously)
- **Risk Management**: Shifted from stop-loss to guaranteed profit structure
- **Configuration**: Removed deprecated exit plan, stop loss, and scale-in settings
- **Terminology**: "Trade" = atomic pair (UP + DOWN hedge), not single side

### Deprecated
- `ENABLE_EXIT_PLAN`: No longer primary strategy (atomic hedging replaces it)
- `EXIT_PRICE_TARGET`: Replaced by `COMBINED_PRICE_THRESHOLD`
- `EXIT_MIN_POSITION_AGE`: Not used with atomic hedging
- `ENABLE_STOP_LOSS`: Emergency liquidation replaces traditional stop loss
- `STOP_LOSS_PRICE`: Now used as emergency sell threshold
- `ENABLE_HEDGED_REVERSAL`: All trades are hedged by default
- `ENABLE_SCALE_IN`: Not compatible with atomic hedging strategy
- `SCALE_IN_*`: Scale-in settings deprecated
- `UNFILLED_TIMEOUT_SECONDS`: Replaced by `HEDGE_FILL_TIMEOUT_SECONDS`
- `CANCEL_UNFILLED_ORDERS`: Automatic with atomic hedging

### Technical Details
- **POST_ONLY Tracking**: Per-symbol counter tracks failures, resets on success
- **Batch API**: Atomic placement ensures both orders submitted together
- **Fill Monitoring**: Polls every 5s for up to 120s
- **Emergency Liquidation**: Progressive pricing based on time remaining
- **Pre-Settlement**: Confidence-driven exit logic at optimal timing window

### Documentation
- Updated `README.md` with atomic hedging as primary feature
- Updated `docs/STRATEGY.md` with execution flow sections
- Updated `docs/RISK_PROFILES.md` with atomic hedging parameters
- Cleaned up `.env.example` removing deprecated settings

---

## [0.5.0] - 2026-01-12

### Added
- **Bayesian Confidence Calculation**: New probabilistic method using log-likelihood accumulation with market priors
- **Dual Calculation Framework**: Both additive and Bayesian methods always calculated for A/B testing
- **Quality Factors**: Signal-specific multipliers (0.7-1.5x) based on RSI, buy pressure, divergence magnitude, ADX strength
- **Database Migration 007**: 5 new columns (additive_confidence, additive_bias, bayesian_confidence, bayesian_bias, market_prior_p_up)
- **Multi-Confirmation System**: Graduated reduction (60-85%) requires key signal agreement for high-confidence entries
- **A/B Testing Tools**: `compare_bayesian_additive.py` script for performance comparison, `check_bayesian_data.py` for data verification

### Changed
- **Strategy Module**: Refactored confidence calculation to support dual methods
- **Logging**: Shows Bayesian vs additive comparison when `BAYESIAN_CONFIDENCE=YES`
- **Settings**: Added `BAYESIAN_CONFIDENCE` flag (default: NO)

### Fixed
- **Missing Bayesian Parameters**: Added 5 new parameters to `save_trade()` calls in `src/bot.py` and `src/trading/execution.py`
- **Bayesian Data Population**: Confirmed Bayesian values being populated correctly after bot restart (trades #526-#529)
- **Formatting**: Normalized line endings and whitespace in 5 Python files (no functional changes)

### Technical Details
- **Bayesian Formula**: `confidence = 1 / (1 + exp(-ln(prior_odds) - Σ(log_LR × weight)))`
- **Log-Likelihood**: `evidence = (score - 0.5) × 2`, `log_LR = evidence × 3.0 × quality`
- **Market Prior**: Starts from Polymarket orderbook `p_up` as baseline probability
- **Quality Adjustment**: Applied to log-likelihood, not weighted scores
- **Preserved Features**: All recent improvements (scale-in, price validation, hedged reversal) work with either method

### Documentation
- Updated `README.md` with Bayesian feature overview and analysis tools section
- Updated `docs/STRATEGY.md` with Bayesian calculation details
- Updated `docs/MIGRATIONS.md` with migration 007 documentation
- Created `ISSUES_AND_PLAN.md` for issue tracking and implementation planning
- Updated `.env.example` with `BAYESIAN_CONFIDENCE` setting
- Updated all skills with v0.5.0 information (python-bot-standards, polymarket-trading, polyflup-history, database-sqlite)

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
