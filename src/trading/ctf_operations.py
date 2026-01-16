"""Conditional Token Framework (CTF) operations for merging and redeeming tokens"""

from typing import Optional, Dict
from eth_account import Account
from web3 import Web3
from src.config.settings import PROXY_PK, FUNDER_PROXY
from src.utils.logger import log, log_error

# CTF Contract Address on Polygon
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

# USDCe Address on Polygon
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

# CTF Contract ABI (only the functions we need)
CTF_ABI = [
    {
        "inputs": [
            {
                "internalType": "contract IERC20",
                "name": "collateralToken",
                "type": "address",
            },
            {
                "internalType": "bytes32",
                "name": "parentCollectionId",
                "type": "bytes32",
            },
            {"internalType": "bytes32", "name": "conditionId", "type": "bytes32"},
            {"internalType": "uint256[]", "name": "partition", "type": "uint256[]"},
            {"internalType": "uint256", "name": "amount", "type": "uint256"},
        ],
        "name": "mergePositions",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {
                "internalType": "contract IERC20",
                "name": "collateralToken",
                "type": "address",
            },
            {
                "internalType": "bytes32",
                "name": "parentCollectionId",
                "type": "bytes32",
            },
            {"internalType": "bytes32", "name": "conditionId", "type": "bytes32"},
            {"internalType": "uint256[]", "name": "indexSets", "type": "uint256[]"},
        ],
        "name": "redeemPositions",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]


def get_web3_client() -> Web3:
    """Get Web3 client connected to Polygon"""
    # Try multiple RPC endpoints for better reliability
    rpc_urls = [
        "https://polygon-rpc.com",
        "https://rpc-mainnet.matic.network",
        "https://polygon-mainnet.public.blastapi.io",
    ]

    for rpc_url in rpc_urls:
        try:
            web3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 10}))
            if web3.is_connected():
                return web3
        except Exception:
            continue

    raise Exception("Failed to connect to any Polygon RPC endpoint")


def get_wallet_address() -> str:
    """Get the wallet address from PROXY_PK"""
    if FUNDER_PROXY and FUNDER_PROXY.startswith("0x"):
        return FUNDER_PROXY

    account = Account.from_key(PROXY_PK)
    return account.address


def merge_hedged_position(
    trade_id: int,
    symbol: str,
    condition_id: str,
    amount: int,
) -> Optional[str]:
    """
    Merge UP + DOWN tokens back to USDC immediately after hedge fills.

    This frees up capital without waiting for market resolution.

    Args:
        trade_id: Database ID of the trade
        symbol: Trading symbol (e.g., "BTC")
        condition_id: Condition ID from market data (bytes32)
        amount: Number of full sets to merge (in wei, 6 decimals for USDC)

    Returns:
        Transaction hash if successful, None otherwise

    Example:
        merge_hedged_position(123, "BTC", "0xabc...", 10_000_000)
        # Merges 10 full sets (10 UP + 10 DOWN) → 10 USDC
    """
    try:
        web3 = get_web3_client()
        wallet_address = get_wallet_address()

        # Load CTF contract
        ctf_contract = web3.eth.contract(
            address=Web3.to_checksum_address(CTF_ADDRESS), abi=CTF_ABI
        )

        # Prepare merge transaction
        # parentCollectionId = 0 (null) for Polymarket
        # partition = [1, 2] means YES (1) and NO (2)
        # amount = number of full sets to merge (in token's smallest unit)

        tx = ctf_contract.functions.mergePositions(
            Web3.to_checksum_address(USDC_ADDRESS),  # collateralToken
            b"\x00" * 32,  # parentCollectionId (null)
            bytes.fromhex(condition_id.replace("0x", "")),  # conditionId
            [1, 2],  # partition (YES | NO)
            amount,  # amount
        )

        # Build transaction
        account = Account.from_key(PROXY_PK)
        nonce = web3.eth.get_transaction_count(wallet_address)

        # Get current gas price with 10% premium for faster confirmation
        gas_price = int(web3.eth.gas_price * 1.1)

        tx_params = {
            "from": wallet_address,
            "nonce": nonce,
            "gas": 200000,  # Will be updated by estimation
            "gasPrice": gas_price,
        }

        # Estimate gas
        try:
            gas_estimate = tx.estimate_gas(tx_params)
            tx_params["gas"] = int(gas_estimate * 1.2)  # Add 20% buffer
            log(
                f"   ⛽ [{symbol}] Gas estimate: {tx_params['gas']} (${(tx_params['gas'] * gas_price / 1e18):.4f})"
            )
        except Exception as e:
            log_error(f"[{symbol}] Gas estimation failed: {e}")
            # Use conservative default
            tx_params["gas"] = 300000

        # Build and sign transaction
        built_tx = tx.build_transaction(tx_params)
        signed_tx = account.sign_transaction(built_tx)

        # Send transaction
        tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)

        log(f"🔄 [{symbol}] #{trade_id} MERGE TX submitted: {tx_hash.hex()}")

        # Wait for confirmation (with timeout)
        try:
            tx_receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

            if tx_receipt["status"] == 1:
                gas_used = tx_receipt["gasUsed"]
                gas_cost = gas_used * gas_price / 1e18
                log(
                    f"✅ [{symbol}] #{trade_id} MERGE SUCCESS: {amount / 1_000_000:.1f} USDC freed (Gas: ${gas_cost:.4f})"
                )
                return tx_hash.hex()
            else:
                log_error(f"[{symbol}] #{trade_id} MERGE FAILED: Transaction reverted")
                return None
        except Exception as e:
            log_error(
                f"[{symbol}] #{trade_id} Merge confirmation timeout or error: {e}"
            )
            # Transaction might still succeed, return hash for tracking
            log(f"   ⚠️  [{symbol}] Transaction may still be pending: {tx_hash.hex()}")
            return tx_hash.hex()

    except Exception as e:
        log_error(f"[{symbol}] #{trade_id} Merge error: {e}")
        return None


def redeem_winning_tokens(
    trade_id: int,
    symbol: str,
    condition_id: str,
) -> Optional[Dict]:
    """
    Redeem winning tokens after market resolution.

    This is called after the market resolves and we know the outcome.
    Only winning tokens have value; losing tokens are worthless.

    Args:
        trade_id: Database ID of the trade
        symbol: Trading symbol
        condition_id: Condition ID from market data (bytes32)

    Returns:
        Dict with transaction details if successful, None otherwise
    """
    try:
        web3 = get_web3_client()
        wallet_address = get_wallet_address()

        # Load CTF contract
        ctf_contract = web3.eth.contract(
            address=Web3.to_checksum_address(CTF_ADDRESS), abi=CTF_ABI
        )

        # Prepare redeem transaction
        # indexSets = [1, 2] means redeem both YES and NO positions
        # Only winning tokens will have value

        tx = ctf_contract.functions.redeemPositions(
            Web3.to_checksum_address(USDC_ADDRESS),  # collateralToken
            b"\x00" * 32,  # parentCollectionId (null)
            bytes.fromhex(condition_id.replace("0x", "")),  # conditionId
            [1, 2],  # indexSets (redeem both YES and NO)
        )

        # Build transaction
        account = Account.from_key(PROXY_PK)
        nonce = web3.eth.get_transaction_count(wallet_address)

        tx_params = {
            "from": wallet_address,
            "nonce": nonce,
            "gas": 200000,
            "gasPrice": web3.eth.gas_price,
        }

        # Estimate gas
        try:
            gas_estimate = tx.estimate_gas(tx_params)
            tx_params["gas"] = int(gas_estimate * 1.2)
        except Exception as e:
            log_error(f"[{symbol}] Gas estimation failed: {e}")
            tx_params["gas"] = 300000

        # Build and sign transaction
        built_tx = tx.build_transaction(tx_params)
        signed_tx = account.sign_transaction(built_tx)

        # Send transaction
        tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)

        log(f"💰 [{symbol}] #{trade_id} REDEEM TX submitted: {tx_hash.hex()}")

        # Wait for confirmation
        tx_receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

        if tx_receipt["status"] == 1:
            log(f"✅ [{symbol}] #{trade_id} REDEEM SUCCESS")
            return {
                "success": True,
                "tx_hash": tx_hash.hex(),
                "gas_used": tx_receipt["gasUsed"],
            }
        else:
            log_error(f"[{symbol}] #{trade_id} REDEEM FAILED: Transaction reverted")
            return None

    except Exception as e:
        log_error(f"[{symbol}] #{trade_id} Redeem error: {e}")
        return None
