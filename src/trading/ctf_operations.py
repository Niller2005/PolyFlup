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
    ENABLE_MANUAL_REDEMPTION_FALLBACK,
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
    DISABLED: Merge function is disabled to save transaction quota.

    User prefers to only use redemptions after market resolution,
    not immediate merges after hedge fills.

    Returns:
        None (function disabled)
    """
    log(f"   ⚠️  [{symbol}] #{trade_id} Merge disabled - will redeem after resolution")
    return None


def _manual_web3_redeem(
    trade_id: int,
    symbol: str,
    condition_id: str,
) -> Optional[str]:
    """
    Manual Web3 redemption using wallet private key (REQUIRES GAS).

    This is a fallback method when the relayer quota is exhausted.
    It requires the wallet to have MATIC for gas fees.

    Args:
        trade_id: Database ID of the trade
        symbol: Trading symbol
        condition_id: Condition ID from market data (bytes32)

    Returns:
        Transaction hash if successful, None otherwise
    """
    try:
        web3 = get_web3_client()
        account = Account.from_key(PROXY_PK)

        # Create contract instance
        ctf_contract = web3.eth.contract(
            address=Web3.to_checksum_address(CTF_ADDRESS), abi=CTF_ABI
        )

        # Build redemption transaction
        redeem_fn = ctf_contract.functions.redeemPositions(
            Web3.to_checksum_address(USDC_ADDRESS),  # collateralToken
            b"\x00" * 32,  # parentCollectionId (null)
            bytes.fromhex(condition_id.replace("0x", "")),  # conditionId
            [1, 2],  # indexSets (redeem both YES and NO)
        )

        # Estimate gas
        try:
            gas_estimate = redeem_fn.estimate_gas({"from": account.address})
            gas_limit = int(gas_estimate * 1.2)  # Add 20% buffer
        except Exception as e:
            log_error(f"[{symbol}] #{trade_id} Gas estimation failed: {e}")
            gas_limit = 300000  # Default fallback

        # Get current gas price
        gas_price = web3.eth.gas_price

        # Build transaction
        nonce = web3.eth.get_transaction_count(account.address)
        tx = redeem_fn.build_transaction(
            {
                "from": account.address,
                "gas": gas_limit,
                "gasPrice": gas_price,
                "nonce": nonce,
                "chainId": POLYGON_CHAIN_ID,
            }
        )

        # Sign transaction
        signed_tx = web3.eth.account.sign_transaction(tx, PROXY_PK)

        # Send transaction
        tx_hash = web3.eth.send_raw_transaction(signed_tx.raw_transaction)
        tx_hash_hex = tx_hash.hex()

        log(
            f"   📡 [{symbol}] #{trade_id} Manual redeem tx sent: {tx_hash_hex[:16]}..."
        )

        # Wait for confirmation
        tx_receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)

        if tx_receipt.get("status") == 1:
            gas_used = tx_receipt.get("gasUsed", 0)
            gas_cost_matic = web3.from_wei(gas_used * gas_price, "ether")
            log(
                f"✅ [{symbol}] #{trade_id} MANUAL REDEEM SUCCESS (Gas: {gas_cost_matic:.6f} MATIC)"
            )
            log(f"   Tx: {tx_hash_hex}")
            return tx_hash_hex
        else:
            log_error(
                f"[{symbol}] #{trade_id} Manual redeem transaction FAILED on-chain"
            )
            log(f"   Failed tx: {tx_hash_hex}")
            return None

    except Exception as e:
        log_error(f"[{symbol}] #{trade_id} Manual Web3 redemption error: {e}")
        return None


def redeem_winning_tokens(
    trade_id: int,
    symbol: str,
    condition_id: str,
    use_manual_fallback: bool = False,
) -> Optional[str]:
    """
    Redeem winning tokens after market resolution.

    This is called after the market resolves and we know the outcome.
    Only winning tokens have value; losing tokens are worthless.

    Args:
        trade_id: Database ID of the trade
        symbol: Trading symbol
        condition_id: Condition ID from market data (bytes32)
        use_manual_fallback: If True, will fallback to manual Web3 redemption if relayer fails

    Returns:
        Transaction hash if successful, None otherwise
    """
    # Try gasless RelayClient first
    relay_client = _get_relayer_client()
    relayer_failed = False

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
                        relayer_failed = True

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
                relayer_failed = True
        except Exception as e:
            error_str = str(e)

            # Check if error is due to quota exceeded (429)
            if "429" in error_str or "quota exceeded" in error_str.lower():
                log(f"   ⚠️  [{symbol}] #{trade_id} Relayer quota exhausted")
                relayer_failed = True
            else:
                log_error(f"[{symbol}] #{trade_id} Relayer error: {e}")
                relayer_failed = True
    else:
        # No relayer client available
        log(f"   ⚠️  [{symbol}] #{trade_id} Relayer client unavailable")
        relayer_failed = True

    # Fallback to manual Web3 redemption if enabled and relayer failed
    if use_manual_fallback and relayer_failed:
        log(
            f"   🔧 [{symbol}] #{trade_id} Attempting manual Web3 redemption (REQUIRES GAS)"
        )
        return _manual_web3_redeem(trade_id, symbol, condition_id)

    return None


def batch_redeem_winning_tokens(
    redemptions: list[tuple[int, str, str]],
    use_manual_fallback: bool = False,
) -> Optional[str]:
    """
    Redeem winning tokens for multiple markets in a single batch transaction.

    More efficient than individual redemptions - uses only 1 transaction
    for multiple markets instead of N transactions.

    Args:
        redemptions: List of (trade_id, symbol, condition_id) tuples to redeem
        use_manual_fallback: If True, will fallback to individual manual redemptions if relayer fails

    Returns:
        Transaction hash if successful, None otherwise

    Example:
        batch_redeem_winning_tokens([
            (123, "BTC", "0xabc..."),
            (124, "ETH", "0xdef..."),
            (125, "SOL", "0x123...")
        ])
    """
    if not redemptions:
        return None

    # Try gasless RelayClient
    relay_client = _get_relayer_client()
    relayer_failed = False

    if not relay_client:
        log("   ⚠️  [Batch Redeem] Relayer client unavailable")
        relayer_failed = True
    else:
        try:
            from py_builder_relayer_client.models import SafeTransaction, OperationType

            # Build list of redemption transactions
            redeem_txs = []
            symbols_str = ", ".join(set(sym for _, sym, _ in redemptions))

            log(
                f"🌐 [Batch Redeem] Preparing {len(redemptions)} redemptions ({symbols_str})"
            )

            for trade_id, symbol, condition_id in redemptions:
                # Encode redeem function call for this market
                encoded_data = _encode_redeem_positions(condition_id)

                # Create SafeTransaction for this redemption
                redeem_tx = SafeTransaction(
                    to=CTF_ADDRESS,
                    operation=OperationType.Call,
                    data=f"0x{encoded_data}",
                    value="0",
                )
                redeem_txs.append(redeem_tx)
                log(f"   📦 [{symbol}] #{trade_id} added to batch")

            # Execute all redemptions in one batch transaction (gasless!)
            response = relay_client.execute(
                redeem_txs, f"Batch redeem {len(redemptions)} markets"
            )

            # Check if we got a transaction hash
            if (
                response
                and hasattr(response, "transaction_hash")
                and response.transaction_hash
            ):
                tx_hash = response.transaction_hash
                log(
                    f"   📡 [Batch Redeem] Relayer submitted batch tx: {tx_hash[:16]}..."
                )

                # Wait for transaction confirmation
                try:
                    web3 = get_web3_client()
                    tx_receipt = web3.eth.wait_for_transaction_receipt(
                        tx_hash,
                        timeout=90,  # Longer timeout for batch
                    )

                    # Check transaction status
                    if tx_receipt.get("status") == 1:
                        log(
                            f"✅ [Batch Redeem] SUCCESS: {len(redemptions)} markets redeemed (GASLESS)"
                        )
                        log(f"   Tx: {tx_hash}")
                        return tx_hash
                    else:
                        log_error(
                            f"[Batch Redeem] Transaction FAILED on-chain (status=0)"
                        )
                        log(f"   Failed tx: {tx_hash}")
                        relayer_failed = True

                except Exception as confirm_err:
                    log_error(
                        f"[Batch Redeem] Could not confirm transaction: {confirm_err}"
                    )
                    log(f"   Tx (unconfirmed): {tx_hash}")
                    return None
            else:
                log_error(
                    "[Batch Redeem] Relayer failed - no transaction hash returned"
                )
                relayer_failed = True

        except Exception as e:
            error_str = str(e)

            # Check if error is due to quota exceeded (429)
            if "429" in error_str or "quota exceeded" in error_str.lower():
                log("   ⚠️  [Batch Redeem] Relayer quota exhausted")
                relayer_failed = True
            else:
                log_error(f"[Batch Redeem] Relayer error: {e}")
                relayer_failed = True

    # Fallback to individual manual Web3 redemptions if enabled and relayer failed
    if use_manual_fallback and relayer_failed:
        log(
            f"   🔧 [Batch Redeem] Attempting individual manual redemptions (REQUIRES GAS)"
        )

        success_count = 0
        for trade_id, symbol, condition_id in redemptions:
            tx_hash = _manual_web3_redeem(trade_id, symbol, condition_id)
            if tx_hash:
                success_count += 1

        if success_count > 0:
            log(
                f"✅ [Manual Redeem] Successfully redeemed {success_count}/{len(redemptions)} trades"
            )
            return "manual_batch"  # Return marker for successful manual batch

    return None
