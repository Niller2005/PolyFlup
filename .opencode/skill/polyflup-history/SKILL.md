---
name: polyflup-history
description: Chronological log of system improvements and session summaries for PolyFlup.
---

## Session Improvements Summary

### 2026-01-07
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
