# PolyAstra Refactoring Plan

## Current Status
- **polyastra.py**: 1364 lines (too large, hard to maintain)

## Proposed Structure

```
polyastra/
├── src/
│   ├── __init__.py
│   ├── config/
│   │   ├── __init__.py
│   │   └── settings.py          # All configuration variables
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── logger.py             # Logging and Discord notifications
│   │   └── web3_utils.py         # Web3 helpers, balance, redemption
│   ├── data/
│   │   ├── __init__.py
│   │   ├── database.py           # Database operations
│   │   └── market_data.py        # API calls to Gamma/Binance
│   ├── trading/
│   │   ├── __init__.py
│   │   ├── strategy.py           # Edge calculation, ADX, BFXD filters
│   │   ├── orders.py             # Place orders, sell positions
│   │   ├── position_manager.py   # Stop loss, take profit, reversal
│   │   └── settlement.py         # Trade settlement and reporting
│   └── bot.py                    # Main bot loop
├── polyastra.py                  # Simple entry point that imports from src/
├── .env
└── requirements.txt
```

## Benefits

1. **Modularity**: Each file has a single responsibility
2. **Maintainability**: Easy to find and fix bugs
3. **Testability**: Can test individual modules
4. **Readability**: Smaller files are easier to understand
5. **Collaboration**: Multiple people can work on different modules

## File Breakdown

### src/config/settings.py (~80 lines)
- All environment variables
- Constants (API endpoints, contract addresses)
- Configuration validation

### src/utils/logger.py (~30 lines)
- `log()` function
- `send_discord()` function

### src/utils/web3_utils.py (~120 lines)
- Web3 connection
- `get_balance()`
- `redeem_winnings()`

### src/data/database.py (~100 lines)
- `init_database()`
- `save_trade()`
- `generate_statistics()`

### src/data/market_data.py (~150 lines)
- `get_current_slug()`
- `get_token_ids()`
- `get_funding_bias()`
- `get_fear_greed()`
- `get_adx_from_binance()`

### src/trading/strategy.py (~200 lines)
- `calculate_edge()`
- `adx_allows_trade()`
- `bfxd_allows_trade()`

### src/trading/orders.py (~150 lines)
- CLOB client setup
- `setup_api_creds()`
- `place_order()`
- `sell_position()`

### src/trading/position_manager.py (~200 lines)
- `check_open_positions()`
- Stop loss logic
- Take profit logic
- Position reversal

### src/trading/settlement.py (~150 lines)
- `get_market_resolution()`
- `check_and_settle_trades()`

### src/bot.py (~200 lines)
- `trade_symbol()` - main trading logic
- `main()` - bot loop

### polyastra.py (~20 lines)
```python
#!/usr/bin/env python3
from src.bot import main

if __name__ == "__main__":
    main()
```

## Migration Steps

1. ✅ Create directory structure
2. ✅ Create src/config/settings.py
3. ✅ Create src/utils/logger.py
4. ⏳ Create remaining modules
5. ⏳ Update imports in polyastra.py
6. ⏳ Test thoroughly
7. ⏳ Update documentation

## Status

- **Phase 1 (Structure)**: ✅ Complete
- **Phase 2 (Config & Utils)**: ✅ Complete  
- **Phase 3 (Full Refactor)**: ⏳ In Progress
- **Phase 4 (Testing)**: ⏳ Pending
