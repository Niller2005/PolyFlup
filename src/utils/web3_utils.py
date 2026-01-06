"""Web3 utilities for blockchain interactions"""

from web3 import Web3
from eth_account import Account
from src.config.settings import (
    POLYGON_RPC,
    USDC_ADDRESS,
    CTF_ADDRESS,
    CTF_ABI,
    PROXY_PK,
)
from src.utils.logger import log, log_error


w3 = Web3(Web3.HTTPProvider(POLYGON_RPC))


def get_balance(addr: str) -> float:
    """Get USDC balance for address"""
    try:
        abi = '[{"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"}]'
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(USDC_ADDRESS), abi=abi
        )
        raw = contract.functions.balanceOf(Web3.to_checksum_address(addr)).call()
        return raw / 1e6
    except Exception:
        return 0.0


def redeem_winnings(condition_id_hex: str, neg_risk: bool = False) -> bool:
    """
    Redeem winnings from CTF contract
    """
    try:
        if neg_risk:
            return False

        # Get contract instance
        ctf_contract = w3.eth.contract(
            address=Web3.to_checksum_address(CTF_ADDRESS), abi=CTF_ABI
        )

        # Get account
        account = Account.from_key(PROXY_PK)
        my_address = account.address

        # Parse condition_id
        if condition_id_hex.startswith("0x"):
            condition_id = bytes.fromhex(condition_id_hex[2:])
        else:
            condition_id = bytes.fromhex(condition_id_hex)

        # Polymarket standard parameters
        parent_collection_id = bytes(32)  # null bytes32
        index_sets = [1, 2]  # Standard for Polymarket binary markets

        # Build transaction
        tx = ctf_contract.functions.redeemPositions(
            Web3.to_checksum_address(USDC_ADDRESS),
            parent_collection_id,
            condition_id,
            index_sets,
        ).build_transaction(
            {
                "from": my_address,
                "nonce": w3.eth.get_transaction_count(my_address),
                "gas": 200000,
                "gasPrice": w3.eth.gas_price,
            }
        )

        # Sign transaction
        signed_tx = w3.eth.account.sign_transaction(tx, private_key=PROXY_PK)

        # Send transaction
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)

        # Wait for receipt
        try:
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            if receipt["status"] == 1:
                return True
            else:
                log("❌ Redemption failed")
                return False
        except Exception as e:
            log_error(f"TX receipt error: {e}")
            return False

    except Exception as e:
        log_error(f"Redeem error: {e}")
        return False
