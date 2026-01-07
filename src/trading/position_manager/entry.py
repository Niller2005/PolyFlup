"""First entry logic for initial trade positions"""

from typing import Optional
from src.trading.logic import _prepare_trade_params
from src.trading.execution import execute_trade


def execute_first_entry(
    symbol: str, balance: float, verbose: bool = True
) -> Optional[int]:
    """
    Execute a first entry trade if conditions are met.

    This function:
    1. Prepares trade parameters (checks all filters, determines side, size, etc.)
    2. Executes the trade if valid

    Args:
        symbol: The trading symbol (e.g., "BTC")
        balance: Available USDC balance
        verbose: Whether to log detailed information

    Returns:
        trade_id if trade executed, None otherwise
    """
    trade_params = _prepare_trade_params(
        symbol, balance, add_spacing=False, verbose=verbose
    )
    if not trade_params:
        return None

    return execute_trade(trade_params, is_reversal=False)
