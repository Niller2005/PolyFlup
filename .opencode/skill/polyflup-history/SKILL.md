---
name: polyflup-history
description: Chronological log of system improvements and session summaries for PolyFlup.
---

## Session Improvements Summary

### 2026-01-12 (v0.5.0 Release)
- **Bayesian Confidence Calculation**: Implemented alternative confidence calculation using proper Bayesian evidence accumulation with log-odds and likelihood ratios
- **Dual Calculation System**: Both additive and Bayesian methods calculated simultaneously for comprehensive A/B testing capabilities
- **Market Prior Integration**: Bayesian method starts with Polymarket orderbook probability as prior, anchoring calculations to market reality
- **Quality Factor System**: Signal evidence strength modulated by per-signal quality factors (0.7-1.5x multiplier on log-likelihood)
- **Database Schema Update**: Migration 007 added `additive_confidence`, `additive_bias`, `bayesian_confidence`, `bayesian_bias`, `market_prior_p_up` columns
- **Configuration Toggle**: `BAYESIAN_CONFIDENCE` flag in settings allows switching between additive and Bayesian methods
- **A/B Testing Workflow**: Collect 100+ trades with both methods stored, then compare win rates using SQL queries
- **Advantages**: Bayesian method naturally handles conflicting signals (they cancel out), properly combines independent evidence, and provides better uncertainty handling

### 2026-01-10 (v0.4.4 Release)
- **Exit Order Repair Fix**: Implemented 0.05 share threshold to prevent infinite repair loops from exchange rounding differences
- **Real-Time Exit Validation**: Added 1-second cycle exit order size validation to catch mismatches immediately
- **Balance API Cleanup**: Removed noisy warnings for near-zero balance values to reduce log spam
- **MIN_SIZE Display**: Added "Exit skipped" status indicator for positions below 5.0 share threshold
- **MIN_EDGE Enforcement**: Fixed threshold enforcement to prevent low-confidence entries from bypassing filters
- **Notification Logging**: Improved concise format with symbol and price info for better readability
- **Duplicate Log Removal**: Eliminated duplicate position logging in monitoring loop for cleaner reports

### 2026-01-08 (v0.4.3 Release)
- **Enhanced Position Reports**: Implemented clean, aligned format with directional emojis (📈📉) and status indicators (⏰⏳📊)
- **Professional Display**: Removed debug spam and redundant logging for cleaner trading logs
- **Visual Clarity**: Perfect alignment with trade IDs, position sizes, and PnL percentages
- **Unified Format**: Consolidated position monitoring with consistent visual indicators

### 2026-01-08 (v0.4.2 Release)
- **Confidence Algorithm**: Fixed false 100% confidence signals with proper capping at 85% and enhanced validation
- **Scale-in Order Safety**: Implemented race condition prevention with order fill confirmation before cancellation
- **Synchronization**: Added comprehensive locks for thread safety across all trading operations
- **Reconciliation System**: Built order tracking reconciliation to prevent ghost trades and ensure accurate position sync
- **Smart Exit Pricing**: Implemented 0.999 exit pricing strategy for winning trades with improved fill rates
- **Audit Trail**: Enhanced comprehensive logging for scale-in orders, notifications, and position management
- **Notification System**: Fixed unknown order ID extraction with robust multi-field parsing
- **Exit Plan Self-Healing**: Improved balance validation consistency and grace period logic
- **Price Validation**: Added multi-timeframe price movement validation to prevent extreme reversal trades

### 2026-01-07
- **Knowledge Update**: Added Polymarket Maker Rebates and Taker Fee details to `polymarket-trading` skill. Noted that taker fees are token-based for BUYS and USDC-based for SELLS, with higher effective rates for low-price sells.
- **Scale-In**: Enhanced with confidence-weighted dynamic timing. Expanded default window to 7.5m (450s) and implemented tiered early entry (up to 12m) for high-confidence (>=90%) and high-price (>=0.80) winners.
- **Precision**: Reduced balance sync threshold to 0.0001.
- **Min Size**: Added pre-flight check for 5.0 share minimum.
- **Stability**: Fixed NoneType safety in P&L summing and bet calculation.

### 2026-01-06
- **Deadlocks**: Fixed "database is locked" errors by passing active cursors to write functions.
- **Duplicate Protection**: Added `has_side_for_window()` check to prevent over-leveraging.
- **Stop Loss**: Implemented "Triple Check" Reversal-First stop loss (Hedge duration > 120s, confidence > 30%, absolute floor $0.15).
- **Logging**: Implemented window-specific log files.

### 2026-01-05
- **WebSocket**: Replaced polling with real-time WebSocketManager for prices and user updates.
- **Modularity**: Full refactor of `src/trading/orders` and `src/data/market_data` into packages.
- **Audit**: Integrated Data API for automated settlement auditing.
