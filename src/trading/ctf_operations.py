"""Conditional Token Framework (CTF) operations for merging and redeeming tokens"""

from typing import Optional, Dict
from eth_account import Account
from web3 import Web3
from src.config.settings import (
    PROXY_PK,
    FUNDER_PROXY,
    POLY_BUILDER_API_KEY,
    POLY_BUILDER_SECRET,
    POLY_BUILDER_PASSPHRASE,
    ENABLE_RELAYER_CLIENT,
)
from src.utils.logger import log, log_error

# CTF Contract Address on Polygon
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

# USDCe Address on Polygon
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

# Relayer Configuration
RELAYER_URL = "https://relayer-v2.polymarket.com"
POLYGON_CHAIN_ID = 137

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


def _get_relayer_client():
    """
    Get RelayClient for gasless CTF operations.

    Returns RelayClient if credentials are configured, None otherwise.
    """
    if not ENABLE_RELAYER_CLIENT:
        return None

    if not all([POLY_BUILDER_API_KEY, POLY_BUILDER_SECRET, POLY_BUILDER_PASSPHRASE]):
        log("   ⚠️  Relayer credentials not configured, using manual Web3")
        return None

    try:
        from py_builder_relayer_client.client import RelayClient
        from py_builder_signing_sdk.config import BuilderConfig
        from py_builder_signing_sdk.sdk_types import BuilderApiKeyCreds

        # Configure local signing with Builder API credentials
        builder_config = BuilderConfig(
            local_builder_creds=BuilderApiKeyCreds(
                key=POLY_BUILDER_API_KEY,
                secret=POLY_BUILDER_SECRET,
                passphrase=POLY_BUILDER_PASSPHRASE,
            )
        )

        # Create RelayClient (no wallet type specified = defaults to SAFE)
        client = RelayClient(RELAYER_URL, POLYGON_CHAIN_ID, PROXY_PK, builder_config)

        return client
    except Exception as e:
        log_error(f"Failed to initialize RelayClient: {e}")
        return None


def _encode_merge_positions(condition_id: str, amount: int) -> str:
    """
    Encode mergePositions function call for CTF contract.

    Args:
        condition_id: Condition ID (bytes32)
        amount: Amount to merge in wei (6 decimals for USDC)

    Returns:
        Encoded function data as hex string
    """
    web3 = Web3()
    ctf_contract = web3.eth.contract(
        address=Web3.to_checksum_address(CTF_ADDRESS), abi=CTF_ABI
    )

    encoded = ctf_contract.encode_abi(
        abi_element_identifier="mergePositions",
        args=[
            Web3.to_checksum_address(USDC_ADDRESS),  # collateralToken
            b"\x00" * 32,  # parentCollectionId (null)
            bytes.fromhex(condition_id.replace("0x", "")),  # conditionId
            [1, 2],  # partition (YES | NO)
            amount,  # amount
        ],
    )

    # encode_abi returns hex string with 0x prefix, remove it for Relayer
    return encoded.replace("0x", "")


def _encode_redeem_positions(condition_id: str) -> str:
    """
    Encode redeemPositions function call for CTF contract.

    Args:
        condition_id: Condition ID (bytes32)

    Returns:
        Encoded function data as hex string
    """
    web3 = Web3()
    ctf_contract = web3.eth.contract(
        address=Web3.to_checksum_address(CTF_ADDRESS), abi=CTF_ABI
    )

    encoded = ctf_contract.encode_abi(
        abi_element_identifier="redeemPositions",
        args=[
            Web3.to_checksum_address(USDC_ADDRESS),  # collateralToken
            b"\x00" * 32,  # parentCollectionId (null)
            bytes.fromhex(condition_id.replace("0x", "")),  # conditionId
            [1, 2],  # indexSets (redeem both YES and NO)
        ],
    )

    # encode_abi returns hex string with 0x prefix, remove it for Relayer
    return encoded.replace("0x", "")


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
    # Try gasless RelayClient first
    relay_client = _get_relayer_client()

    if relay_client:
        try:
            from py_builder_relayer_client.models import SafeTransaction, OperationType

            log(f"🌐 [{symbol}] #{trade_id} Using gasless Relayer for CTF merge")

            # Encode merge function call
            encoded_data = _encode_merge_positions(condition_id, amount)

            # Create SafeTransaction for relayer
            merge_tx = SafeTransaction(
                to=CTF_ADDRESS,
                operation=OperationType.Call,
                data=f"0x{encoded_data}",
                value="0",
            )

            # Execute via relayer (gasless!)
            response = relay_client.execute(
                [merge_tx], f"Merge {amount / 1_000_000:.1f} USDC"
            )

            # Check if we got a transaction hash
            if (
                response
                and hasattr(response, "transaction_hash")
                and response.transaction_hash
            ):
                tx_hash = response.transaction_hash
                log(
                    f"   📡 [{symbol}] #{trade_id} Relayer submitted merge tx: {tx_hash[:16]}..."
                )

                # Wait for transaction confirmation and verify success
                try:
                    web3 = get_web3_client()
                    tx_receipt = web3.eth.wait_for_transaction_receipt(
                        tx_hash, timeout=60
                    )

                    # Check transaction status (1 = success, 0 = failure)
                    if tx_receipt.get("status") == 1:
                        log(
                            f"✅ [{symbol}] #{trade_id} MERGE SUCCESS (GASLESS): {amount / 1_000_000:.1f} USDC freed"
                        )
                        log(f"   Tx: {tx_hash}")
                        return tx_hash
                    else:
                        log_error(
                            f"[{symbol}] #{trade_id} Merge transaction FAILED on-chain (status=0)"
                        )
                        log(f"   Failed tx: {tx_hash}")
                        return None

                except Exception as confirm_err:
                    log_error(
                        f"[{symbol}] #{trade_id} Could not confirm merge transaction: {confirm_err}"
                    )
                    log(f"   Tx (unconfirmed): {tx_hash}")
                    # Return None since we can't confirm success
                    return None
            else:
                log_error(
                    f"[{symbol}] #{trade_id} Relayer merge failed - no transaction hash returned"
                )
                return None
        except Exception as e:
            error_str = str(e)

            # Check if error is due to quota exceeded (429)
            if "429" in error_str or "quota exceeded" in error_str:
                log(
                    f"   ⚠️  [{symbol}] #{trade_id} Relayer quota exhausted, skipping merge (position will settle normally)"
                )
                return None

            log_error(
                f"[{symbol}] #{trade_id} Relayer error: {e} - skipping merge (position will settle normally)"
            )
            return None

    # No relayer client available
    log(
        f"   ⚠️  [{symbol}] #{trade_id} Relayer client unavailable, skipping merge (position will settle normally)"
    )
    return None


def redeem_winning_tokens(
    trade_id: int,
    symbol: str,
    condition_id: str,
) -> Optional[str]:
    """
    Redeem winning tokens after market resolution.

    This is called after the market resolves and we know the outcome.
    Only winning tokens have value; losing tokens are worthless.

    Args:
        trade_id: Database ID of the trade
        symbol: Trading symbol
        condition_id: Condition ID from market data (bytes32)

    Returns:
        Transaction hash if successful, None otherwise
    """
    # Try gasless RelayClient first
    relay_client = _get_relayer_client()

    if relay_client:
        try:
            from py_builder_relayer_client.models import SafeTransaction, OperationType

            log(f"🌐 [{symbol}] #{trade_id} Using gasless Relayer for redemption")

            # Encode redeem function call
            encoded_data = _encode_redeem_positions(condition_id)

            # Create SafeTransaction for relayer
            redeem_tx = SafeTransaction(
                to=CTF_ADDRESS,
                operation=OperationType.Call,
                data=f"0x{encoded_data}",
                value="0",
            )

            # Execute via relayer (gasless!)
            response = relay_client.execute([redeem_tx], "Redeem winning tokens")

            # Check if we got a transaction hash
            if (
                response
                and hasattr(response, "transaction_hash")
                and response.transaction_hash
            ):
                tx_hash = response.transaction_hash
                log(
                    f"   📡 [{symbol}] #{trade_id} Relayer submitted redeem tx: {tx_hash[:16]}..."
                )

                # Wait for transaction confirmation and verify success
                try:
                    web3 = get_web3_client()
                    tx_receipt = web3.eth.wait_for_transaction_receipt(
                        tx_hash, timeout=60
                    )

                    # Check transaction status (1 = success, 0 = failure)
                    if tx_receipt.get("status") == 1:
                        log(f"✅ [{symbol}] #{trade_id} REDEEM SUCCESS (GASLESS)")
                        log(f"   Tx: {tx_hash}")
                        return tx_hash
                    else:
                        log_error(
                            f"[{symbol}] #{trade_id} Redeem transaction FAILED on-chain (status=0)"
                        )
                        log(f"   Failed tx: {tx_hash}")
                        return None

                except Exception as confirm_err:
                    log_error(
                        f"[{symbol}] #{trade_id} Could not confirm redeem transaction: {confirm_err}"
                    )
                    log(f"   Tx (unconfirmed): {tx_hash}")
                    # Return None since we can't confirm success
                    return None
            else:
                log_error(
                    f"[{symbol}] #{trade_id} Relayer redeem failed - no transaction hash returned"
                )
                return None
        except Exception as e:
            error_str = str(e)

            # Check if error is due to quota exceeded (429)
            if "429" in error_str or "quota exceeded" in error_str.lower():
                log(
                    f"   ⚠️  [{symbol}] #{trade_id} Relayer quota exhausted, skipping redemption"
                )
                return None

            log_error(
                f"[{symbol}] #{trade_id} Relayer error: {e} - skipping redemption"
            )
            return None

    # No relayer client available
    log(f"   ⚠️  [{symbol}] #{trade_id} Relayer client unavailable, skipping redemption")
    return None
