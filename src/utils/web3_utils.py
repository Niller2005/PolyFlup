"""Web3 utilities for blockchain interactions"""

from web3 import Web3
from src.config.settings import PAPER_TRADING, POLYGON_RPC, PUSD_ADDRESS

w3 = Web3(Web3.HTTPProvider(POLYGON_RPC))

_ERC20_BALANCE_ABI = '[{"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"}]'


def get_balance(addr: str) -> float:
    """Get pUSD (Polymarket collateral, 6 decimals) balance for address"""
    if PAPER_TRADING:
        from src import paper

        return paper.cash_balance()
    try:
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(PUSD_ADDRESS), abi=_ERC20_BALANCE_ABI
        )
        raw = contract.functions.balanceOf(Web3.to_checksum_address(addr)).call()
        return raw / 1e6
    except Exception:
        return 0.0
