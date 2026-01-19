---
name: python-bot-standards
description: Coding standards, modular architecture, and common execution patterns for the PolyFlup Python backend.
---

## Python Coding Standards

### Modular Architecture
The backend is modularized into packages:
- `src.trading.orders`: Order management (limit, market, batch, positions).
- `src.data.market_data`: Data fetching (polymarket, binance, indicators, price validation).
- `src.trading`: Centralized execution logic (`execution.py`, `logic.py`, `strategy.py`).
- `src.trading.execution`: Atomic hedging execution (batch placement, fill monitoring, emergency liquidation).
- `src.trading.pre_settlement_exit`: Pre-settlement exit strategy (confidence-based early exit).

### Standard Trade Execution (Atomic Hedging)
```python
from src.trading.execution import execute_atomic_trade
# Execute an atomic entry+hedge pair with full tracking
trade_id = execute_atomic_trade(symbol, entry_side, entry_price, hedge_price, size)
```

### Imports
- Use absolute imports from `src.*`
- Group imports: standard library → third-party → local
- Always import from `src.config.settings` for configuration constants

### Formatting
- **Indentation**: 4 spaces
- **Line length**: ~90 characters
- **Strings**: Double quotes preferred
- **Docstrings**: Triple double-quotes with brief description

### Error Handling & Logging
- Use `log_error(text, include_traceback=True)` from `src.utils.logger` for all exceptions.
- Always log errors with context: `log(f"[{symbol}] Order failed: {e}")`.
- Graceful degradation: return neutral/safe values on error.
- Use emojis in logs (👀 Monitoring, 🚀 Execution, ✅ Success, ❌ Error).

### Thread Safety & Synchronization (v0.4.2+)
- Use threading locks for shared resources: `with position_lock: ...`
- Always confirm order fills before cancellation to prevent race conditions
- Implement reconciliation systems for order tracking consistency
- Use `recent_fills` tracking to prevent duplicate processing
- Add synchronization locks for all critical trading operations

### Confidence Algorithm Standards (v0.4.2+)
- Cap confidence at 85% maximum to prevent overconfidence
- Validate price movements before high-confidence trades (>75%)
- Implement multi-confirmation systems for extreme confidence levels
- Use directional voting with weighted signal aggregation
- Test confidence ranges (0-100%) and bias validation thoroughly

### Bayesian Confidence Calculation (v0.5.0+)
- **Dual Calculation System**: Both additive and Bayesian methods are calculated simultaneously for A/B testing
- **Configuration Flag**: `BAYESIAN_CONFIDENCE` in `src/config/settings.py` toggles between methods (default: NO)
- **Bayesian Formula**:
  ```python
  # Start with market prior from Polymarket orderbook
  prior_odds = p_up / (1 - p_up)
  log_odds = ln(prior_odds)

  # Accumulate evidence from all signals
  for each signal:
      evidence = (score - 0.5) * 2  # -1 to +1
      log_LR = evidence * 3.0 * quality  # Calibration with quality
      log_odds += log_LR × weight

  # Convert to probability
  confidence = 1 / (1 + exp(-log_odds))
  ```
- **Quality Factors** (0.7-1.5x): Applied to log-likelihood strength for each signal
- **Market Prior**: Anchors calculation to Polymarket orderbook probability
- **A/B Testing**: Both methods stored in `raw_scores` dictionary and database for comparison
- **Database Schema**: Migration 007 adds `additive_confidence`, `additive_bias`, `bayesian_confidence`, `bayesian_bias`, `market_prior_p_up` columns

### Audit Trail Requirements (v0.4.2+)
- Log all scale-in order lifecycle events with consistent formatting
- Use standardized emojis: 🎯 (placement), ✅ (filled), 🧹 (cancellation), ⚠️ (race condition)
- Include order IDs, sizes, prices, and timestamps in all audit logs
- Maintain separate audit trails for notifications, reconciliation, and position management
- Log notification processing with order ID extraction details

### Database Safety Standards
- Always use `db_connection()` context manager
- Pass active cursors to write functions within transactions
- Implement migration system for schema changes
- Use `schema_version` table to track database versions
- Prevent deadlocks with proper cursor management

### Configuration Management
- Centralize all settings in `src.config.settings`
- Use environment variables for sensitive data
- Implement validation for critical thresholds (MIN_EDGE, confidence caps)
- Document all configuration options in `.env.example`
- Add price validation settings for high-confidence trades

### Testing Standards
- Create comprehensive test suites for algorithm validation
- Test confidence ranges (0-100%), bias validation, and safety features
- Implement integration tests for multi-confirmation systems
- Validate price movement detection and manipulation prevention
- Test race condition prevention and synchronization

### Performance Optimization
- Use efficient API calls with proper caching
- Implement batch processing for multiple orders
- Monitor execution times and optimize critical paths
- Use WebSocket connections for real-time data where possible
- Optimize notification processing with efficient order ID extraction

### Documentation Standards
- Update README.md with latest features and improvements
- Maintain detailed strategy documentation in docs/STRATEGY.md
- Document all risk profiles in docs/RISK_PROFILES.md
- Keep migration guide updated in docs/MIGRATIONS.md
- Document new position flow in docs/POSITION_FLOW.md

### Code Quality Standards
- Use type hints for all function parameters and return values
- Implement proper error handling with specific exception types
- Write comprehensive docstrings for all public functions
- Follow PEP 8 style guidelines with project-specific adaptations
- Implement comprehensive audit trail logging

### Security Standards
- Never commit private keys or sensitive credentials
- Use secure methods for API key storage and access
- Implement proper input validation for all external data
- Use parameterized queries for database operations
- Secure notification processing with robust parsing

### Deployment Standards
- Use Docker for consistent deployment environments
- Implement proper logging aggregation for production
- Monitor system health and performance metrics
- Use proper process management and restart policies
- Deploy with comprehensive monitoring and alerting

### Monitoring & Alerting
- Implement comprehensive audit trail logging
- Monitor for race conditions and synchronization issues
- Track order fill rates and reconciliation success
- Alert on unusual patterns or system errors
- Monitor confidence algorithm performance and validation
- Track notification processing success rates

### Version Control Standards
- Use semantic versioning (MAJOR.MINOR.PATCH)
- Create detailed release notes for each version
- Tag releases with comprehensive change descriptions
- Maintain backward compatibility where possible
- Document all improvements in release tags

### Atomic Hedging Execution (v0.6.0+)
- **Atomic Pair Placement**: Entry + Hedge submitted simultaneously via batch API
  - Both orders placed together to guarantee profit structure
  - Combined price must be ≤ COMBINED_PRICE_THRESHOLD (default 0.99)
  - POST_ONLY by default to earn maker rebates (0.15%)
  - GTC fallback after 3 POST_ONLY failures (accepts 1.54% taker fees)
- **Fill Monitoring**: 120-second timeout with 5-second polling
  - Both must fill for success
  - If one fills, other times out → Emergency liquidation
  - If neither fills → Cancel both, retry
- **POST_ONLY Failure Tracking**: Per-symbol counter tracks crossing failures
  - Incremented on POST_ONLY crossing error
  - Reset to 0 on successful atomic placement
  - Switch to GTC when counter ≥ MAX_POST_ONLY_ATTEMPTS (3)
- **Emergency Liquidation**: Time-aware progressive pricing
  - PATIENT (>600s): Small drops (1¢), long waits (10-20s)
  - BALANCED (300-600s): Moderate drops (2-5¢), medium waits (6-10s)
  - AGGRESSIVE (<300s): Rapid drops (5-10¢), short waits (5-10s)
  - MIN_ORDER_SIZE check: Hold if winning & <5.0 shares, orphan if losing
- **Pre-Settlement Exit**: Evaluate positions T-180s to T-45s
  - Calculate confidence using strategy signals
  - If confidence > 80% on one side: Sell losing side
  - Keep winning side for full resolution profit ($1.00)

### Position Monitoring & Reporting (v0.6.0+)
- **Atomic Pair Display**: Show both sides of hedge together
- **Position Status**: Track entry, hedge, and resolution separately
- **Trade ID Display**: Show trade ID for all positions in monitoring output
- **Spam Reduction**: Removed debug logging spam for cleaner production logs
- **Min Size Handling**: Silent hold for winning positions below MIN_SIZE (5.0 shares), orphan if losing

### MIN_ORDER_SIZE Smart Hold Logic (v0.6.0+)
- **Exchange Minimum**: Polymarket enforces 5.0 share minimum for limit orders
- **Smart Decision**:
  ```python
  if size < MIN_ORDER_SIZE:
      current_price = get_current_price()
      if current_price > entry_price:  # WINNING
          log("🎯 HOLDING through resolution - too small to sell but profitable")
          return True  # Let it resolve for profit
      else:  # LOSING
          log("🔒 Position ORPHANED - too small to sell, will lose on resolution")
          return False  # Accept small loss
  ```
- **Example**: 3.77 shares @ $0.31 entry, current $0.50 → HOLD (profit $0.72)

## Version History

### v0.6.0 (Jan 2026)
- **Atomic Hedging Strategy**: Complete overhaul to simultaneous entry+hedge pairs via batch API
- **POST_ONLY → GTC Fallback**: Smart switching after 3 POST_ONLY crossing failures (Bug #10 fix)
- **Time-Aware Emergency Liquidation**: PATIENT/BALANCED/AGGRESSIVE modes based on time remaining
- **MIN_ORDER_SIZE Smart Hold**: Hold winning positions <5.0 shares, orphan losing ones
- **Pre-Settlement Exit**: Confidence-based early exit of losing side (T-180s to T-45s)
- **Deprecated**: Exit plan, stop loss, scale-in, hedged reversal (replaced by atomic hedging)

### v0.5.0 (Jan 2026)
- **Bayesian Confidence Calculation**: Implemented statistically principled probability calculation using log-odds and likelihood ratios
- **Dual Calculation System**: Both additive and Bayesian methods calculated simultaneously for A/B testing
- **Market Prior Integration**: Bayesian method uses Polymarket orderbook probability as prior for anchored calculations
- **Quality Factor System**: Evidence strength modulated by signal quality (0.7-1.5x multiplier on log-likelihood)
- **Database Migration 007**: Added columns for storing both methods' results and market prior
- **Configuration Toggle**: `BAYESIAN_CONFIDENCE` flag enables Bayesian method for production use

### v0.4.4 (Jan 2026)
- **Exit Order Repair Fix**: Prevent infinite repair loops from exchange rounding differences (0.05 threshold)
- **Real-Time Validation**: Exit order size validation every 1-second cycle to catch mismatches immediately
- **Log Cleanup**: Remove noisy balance API warnings for near-zero values
- **Position Display**: Show "Exit skipped" status for positions below MIN_SIZE threshold

### v0.4.3 (Jan 2026)
- **Enhanced Position Reports**: Clean, aligned format with directional emojis and status indicators
- **Professional Display**: Removed debug spam and redundant logging for cleaner trading logs
- **Visual Clarity**: Perfect alignment with trade IDs, position sizes, and PnL percentages
- **Unified Format**: Consolidated position monitoring with consistent visual indicators