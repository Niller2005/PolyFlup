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
        from py_builder_signing_sdk import BuilderConfig, BuilderApiKeyCreds

        # Configure local signing with Builder API credentials
        builder_config = BuilderConfig(
            local_builder_creds=BuilderApiKeyCreds(
                key=POLY_BUILDER_API_KEY,
                secret=POLY_BUILDER_SECRET,
                passphrase=POLY_BUILDER_PASSPHRASE,
            )
        )

        # Create RelayClient
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

    return encoded.hex()


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

    return encoded.hex()


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
            log(f"🌐 [{symbol}] #{trade_id} Using gasless Relayer for CTF merge")

            # Encode merge function call
            encoded_data = _encode_merge_positions(condition_id, amount)

            # Create transaction for relayer
            merge_tx = {"to": CTF_ADDRESS, "data": f"0x{encoded_data}", "value": "0"}

            # Execute via relayer (gasless!)
            response = relay_client.execute(
                [merge_tx], f"Merge {amount / 1_000_000:.1f} USDC"
            )
            result = response.wait()

            if result and result.transaction_hash:
                log(
                    f"✅ [{symbol}] #{trade_id} MERGE SUCCESS (GASLESS): {amount / 1_000_000:.1f} USDC freed"
                )
                return result.transaction_hash
            else:
                log_error(
                    f"[{symbol}] #{trade_id} Relayer merge failed, falling back to Web3"
                )
        except Exception as e:
            log_error(
                f"[{symbol}] #{trade_id} Relayer error: {e}, falling back to Web3"
            )

    # Fallback to manual Web3 transaction (requires ETH for gas)
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
            log(f"🌐 [{symbol}] #{trade_id} Using gasless Relayer for redemption")

            # Encode redeem function call
            encoded_data = _encode_redeem_positions(condition_id)

            # Create transaction for relayer
            redeem_tx = {"to": CTF_ADDRESS, "data": f"0x{encoded_data}", "value": "0"}

            # Execute via relayer (gasless!)
            response = relay_client.execute([redeem_tx], "Redeem winning tokens")
            result = response.wait()

            if result and result.transaction_hash:
                log(f"✅ [{symbol}] #{trade_id} REDEEM SUCCESS (GASLESS)")
                return result.transaction_hash
            else:
                log_error(
                    f"[{symbol}] #{trade_id} Relayer redeem failed, falling back to Web3"
                )
        except Exception as e:
            log_error(
                f"[{symbol}] #{trade_id} Relayer error: {e}, falling back to Web3"
            )

    # Fallback to manual Web3 transaction (requires ETH for gas)
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

        # Get current gas price with 10% premium
        gas_price = int(web3.eth.gas_price * 1.1)

        tx_params = {
            "from": wallet_address,
            "nonce": nonce,
            "gas": 200000,
            "gasPrice": gas_price,
        }

        # Estimate gas
        try:
            gas_estimate = tx.estimate_gas(tx_params)
            tx_params["gas"] = int(gas_estimate * 1.2)
            log(
                f"   ⛽ [{symbol}] Gas estimate: {tx_params['gas']} (${(tx_params['gas'] * gas_price / 1e18):.4f})"
            )
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
        try:
            tx_receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

            if tx_receipt["status"] == 1:
                gas_used = tx_receipt["gasUsed"]
                gas_cost = gas_used * gas_price / 1e18
                log(f"✅ [{symbol}] #{trade_id} REDEEM SUCCESS (Gas: ${gas_cost:.4f})")
                return tx_hash.hex()
            else:
                log_error(f"[{symbol}] #{trade_id} REDEEM FAILED: Transaction reverted")
                return None
        except Exception as e:
            log_error(
                f"[{symbol}] #{trade_id} Redeem confirmation timeout or error: {e}"
            )
            # Transaction might still succeed, return hash for tracking
            log(f"   ⚠️  [{symbol}] Transaction may still be pending: {tx_hash.hex()}")
            return tx_hash.hex()

    except Exception as e:
        log_error(f"[{symbol}] #{trade_id} Redeem error: {e}")
        return None
