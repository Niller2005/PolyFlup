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
- `src.trading.position_manager`: Position lifecycle management (entry, exit, scale-in, stop-loss, reconciliation).

### Standard Trade Execution
```python
from src.trading import execute_trade
# Execute a trade with full tracking and notifications
trade_id = execute_trade(trade_params, is_reversal=False)
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

## v0.4.2 Release Highlights

### Major Stability Improvements
- **Confidence Algorithm Fixes**: Eliminated false 100% signals with proper capping and validation
- **Scale-in Order Race Condition Prevention**: Implemented order fill confirmation before cancellation
- **Synchronization Locks**: Added comprehensive thread safety across all trading operations
- **Reconciliation System**: Built order tracking reconciliation to prevent ghost trades
- **Smart Exit Pricing**: Implemented 0.999 exit pricing for improved winning trade fills
- **Enhanced Audit Trail**: Comprehensive logging for scale-in orders and position management
- **Notification System**: Fixed unknown order ID extraction with robust multi-field parsing
- **Exit Plan Self-Healing**: Improved balance validation consistency and grace period logic

This comprehensive update represents a significant improvement in system stability and reliability, with enhanced safety mechanisms and comprehensive audit trail logging.