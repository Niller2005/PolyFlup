"""Order placement and management"""

import os
from typing import List, Dict, Optional
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType, ApiCreds, PostOrdersArgs
from py_clob_client.order_builder.constants import BUY, SELL
from dotenv import set_key
from src.config.settings import (
    CLOB_HOST,
    PROXY_PK,
    CHAIN_ID,
    SIGNATURE_TYPE,
    FUNDER_PROXY,
)
from src.utils.logger import log, send_discord

# API Constraints
MIN_TICK_SIZE = 0.01
MIN_ORDER_SIZE = 5.0  # Minimum size in shares

# Retry Configuration
MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]  # Exponential backoff in seconds

# Known API Error Messages
API_ERRORS = {
    "INVALID_ORDER_MIN_TICK_SIZE": "Price breaks minimum tick size rules",
    "INVALID_ORDER_MIN_SIZE": "Size lower than minimum",
    "INVALID_ORDER_DUPLICATED": "Order already placed",
    "INVALID_ORDER_NOT_ENOUGH_BALANCE": "Not enough balance/allowance",
    "INVALID_ORDER_EXPIRATION": "Invalid expiration time",
    "INVALID_ORDER_ERROR": "Could not insert order",
    "EXECUTION_ERROR": "Could not execute trade",
    "ORDER_DELAYED": "Order delayed due to market conditions",
    "DELAYING_ORDER_ERROR": "Error delaying order",
    "FOK_ORDER_NOT_FILLED_ERROR": "FOK order not fully filled",
    "MARKET_NOT_READY": "Market not ready for orders",
}

# Initialize client
client = ClobClient(
    host=CLOB_HOST,
    key=PROXY_PK or "",
    chain_id=CHAIN_ID,
    signature_type=SIGNATURE_TYPE,
    funder=FUNDER_PROXY or "",
)

# Hotfix: ensure client has builder_config attribute
if not hasattr(client, "builder_config"):
    client.builder_config = None


def get_clob_client() -> ClobClient:
    """Get the initialized CLOB client"""
    return client


def get_midpoint(token_id: str) -> Optional[float]:
    """
    Get midpoint price (average of best bid and ask) for a token

    Args:
        token_id: Token ID to get price for

    Returns:
        Midpoint price or None if unavailable
    """
    try:
        result = client.get_midpoint(token_id)

        if isinstance(result, dict):
            mid = result.get("mid")
            if mid:
                return float(mid)
        elif hasattr(result, "mid"):
            val = getattr(result, "mid")
            return float(val) if val else None

        return None

    except Exception as e:
        log(f"⚠️ Error getting midpoint for {token_id[:10]}...: {e}")
        return None


def get_tick_size(token_id: str) -> float:
    """
    Get the minimum tick size (price increment) for a token

    Args:
        token_id: Token ID

    Returns:
        Tick size (e.g., 0.01, 0.001, 0.0001) or 0.01 as default
    """
    try:
        tick_size = client.get_tick_size(token_id)

        if tick_size:
            return float(tick_size)

        return MIN_TICK_SIZE  # Default fallback

    except Exception as e:
        log(f"⚠️ Error getting tick size for {token_id[:10]}...: {e}")
        return MIN_TICK_SIZE


def get_spread(token_id: str) -> Optional[float]:
    """
    Get the spread (difference between best ask and bid) for a token

    Args:
        token_id: Token ID

    Returns:
        Spread value or None if unavailable
    """
    try:
        result = client.get_spread(token_id)

        if isinstance(result, dict):
            spread = result.get("spread")
            if spread:
                return float(spread)
        elif hasattr(result, "spread"):
            val = getattr(result, "spread")
            return float(val) if val else None

        return None

    except Exception as e:
        log(f"⚠️ Error getting spread for {token_id[:10]}...: {e}")
        return None


def get_server_time() -> Optional[int]:
    """
    Get current server timestamp for accurate time synchronization

    Returns:
        Unix timestamp in seconds or None if unavailable
    """
    try:
        timestamp = client.get_server_time()

        if isinstance(timestamp, (int, float)):
            return int(timestamp)

        return None

    except Exception as e:
        log(f"⚠️ Error getting server time: {e}")
        return None


def get_trades(
    market: Optional[str] = None, asset_id: Optional[str] = None, limit: int = 100
) -> List[dict]:
    """
    Get trade history (filled orders)

    Args:
        market: Filter by market condition ID
        asset_id: Filter by token ID
        limit: Maximum number of trades to return

    Returns:
        List of trade dictionaries
    """
    try:
        from py_clob_client.clob_types import TradeParams

        params = TradeParams()
        if market:
            params.market = market
        if asset_id:
            params.asset_id = asset_id

        trades = client.get_trades(params)

        if not isinstance(trades, list):
            trades = [trades] if trades else []

        # Limit results
        return trades[:limit]

    except Exception as e:
        log(f"⚠️ Error getting trades: {e}")
        return []


def get_balance_allowance(token_id: Optional[str] = None) -> Optional[dict]:
    """
    Get balance and allowance for USDC collateral or specific conditional token

    Args:
        token_id: Optional token ID for conditional token. If None, checks USDC collateral

    Returns:
        Dict with 'balance' and 'allowance' or None if error
    """
    try:
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

        params = BalanceAllowanceParams(
            asset_type=AssetType.CONDITIONAL if token_id else AssetType.COLLATERAL,
            token_id=token_id or "",
        )

        result = client.get_balance_allowance(params)

        if isinstance(result, dict):
            return {
                "balance": float(result.get("balance", 0)) / 1_000_000.0,
                "allowance": float(result.get("allowance", 0)) / 1_000_000.0,
            }
        elif hasattr(result, "balance") and hasattr(result, "allowance"):
            return {
                "balance": float(getattr(result, "balance")) / 1_000_000.0,
                "allowance": float(getattr(result, "allowance")) / 1_000_000.0,
            }

        return None

    except Exception as e:
        log(f"⚠️ Error getting balance/allowance: {e}")
        return None


def get_notifications() -> List[dict]:
    """
    Get all notifications (order fills, cancellations, market resolutions)

    Notification types:
    - 1: Order Cancellation
    - 2: Order Fill (maker or taker)
    - 4: Market Resolved

    Returns:
        List of notification dicts with id, owner, payload, timestamp, type
    """
    try:
        notifications = client.get_notifications()

        if not isinstance(notifications, list):
            notifications = [notifications] if notifications else []

        # Convert to dicts if needed
        result = []
        for notif in notifications:
            if isinstance(notif, dict):
                result.append(notif)
            else:
                # Convert object to dict
                notif_dict = {
                    "id": getattr(notif, "id", None),
                    "owner": getattr(notif, "owner", ""),
                    "payload": getattr(notif, "payload", {}),
                    "timestamp": getattr(notif, "timestamp", None),
                    "type": getattr(notif, "type", None),
                }
                result.append(notif_dict)

        return result

    except Exception as e:
        log(f"⚠️ Error getting notifications: {e}")
        return []


def drop_notifications(notification_ids: List[str]) -> bool:
    """
    Mark notifications as read/dismissed

    Args:
        notification_ids: List of notification IDs to dismiss

    Returns:
        True if successful
    """
    try:
        from py_clob_client.clob_types import DropNotificationParams

        params = DropNotificationParams(ids=notification_ids)
        client.drop_notifications(params)
        return True

    except Exception as e:
        log(f"⚠️ Error dropping notifications: {e}")
        return False


def get_current_positions(user_address: str) -> List[dict]:
    """
    Get current open positions for a user from Gamma API

    Args:
        user_address: Ethereum address of the user

    Returns:
        List of position dictionaries
    """
    try:
        from src.config.settings import GAMMA_API_BASE
        import requests

        url = f"{GAMMA_API_BASE}/positions?user={user_address}"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()

        data = resp.json()
        if isinstance(data, list):
            return data

        return data.get("positions", []) if isinstance(data, dict) else []

    except Exception as e:
        log(f"⚠️ Error getting positions from Gamma: {e}")
        return []


def setup_api_creds() -> None:
    """Setup API credentials from .env or generate new ones"""
    api_key = os.getenv("API_KEY")
    api_secret = os.getenv("API_SECRET")
    api_passphrase = os.getenv("API_PASSPHRASE")

    if api_key and api_secret and api_passphrase:
        try:
            creds = ApiCreds(
                api_key=api_key, api_secret=api_secret, api_passphrase=api_passphrase
            )
            client.set_api_creds(creds)
            log("✓ API credentials loaded from .env")
            return
        except Exception as e:
            log(f"⚠ Error loading API creds from .env: {e}")

    try:
        creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)
        set_key(".env", "API_KEY", creds.api_key)
        set_key(".env", "API_SECRET", creds.api_secret)
        set_key(".env", "API_PASSPHRASE", creds.api_passphrase)
        log("✓ API credentials generated and saved")
    except Exception as e:
        log(f"❌ FATAL: API credentials error: {e}")
        raise


def _ensure_api_creds(order_client: ClobClient) -> None:
    """Ensure API credentials are set on the client"""
    if not hasattr(order_client, "builder_config"):
        order_client.builder_config = None

    api_key = os.getenv("API_KEY")
    api_secret = os.getenv("API_SECRET")
    api_passphrase = os.getenv("API_PASSPHRASE")
    if api_key and api_secret and api_passphrase:
        try:
            creds = ApiCreds(
                api_key=api_key,
                api_secret=api_secret,
                api_passphrase=api_passphrase,
            )
            order_client.set_api_creds(creds)
        except Exception as e:
            log(f"⚠ Error setting API creds: {e}")


def _validate_price(
    price: float, tick_size: float = MIN_TICK_SIZE
) -> tuple[bool, Optional[str]]:
    """
    Validate order price meets minimum tick size requirements

    Args:
        price: Price to validate
        tick_size: Minimum tick size (default: 0.01)

    Returns:
        (is_valid, error_message)
    """
    if price <= 0:
        return False, "Price must be greater than 0"

    if price < 0.01 or price > 0.99:
        return False, "Price must be between 0.01 and 0.99"

    # Determine decimal places from tick size
    if tick_size == 0.1:
        decimal_places = 1
    elif tick_size == 0.01:
        decimal_places = 2
    elif tick_size == 0.001:
        decimal_places = 3
    elif tick_size == 0.0001:
        decimal_places = 4
    else:
        decimal_places = 2  # Default

    # Check if price is properly rounded to tick size
    if round(price, decimal_places) != price:
        return (
            False,
            f"Price must be rounded to minimum tick size of {tick_size} (got {price})",
        )

    return True, None


def _validate_size(size: float) -> tuple[bool, Optional[str]]:
    """
    Validate order size meets minimum requirements

    Returns:
        (is_valid, error_message)
    """
    if size < MIN_ORDER_SIZE:
        return (
            False,
            f"Order size must be at least {MIN_ORDER_SIZE} shares (got {size})",
        )

    return True, None


def _validate_order(price: float, size: float) -> tuple[bool, Optional[str]]:
    """
    Validate order parameters before placement

    Returns:
        (is_valid, error_message)
    """
    # Validate price
    is_valid, error = _validate_price(price)
    if not is_valid:
        return False, error

    # Validate size
    is_valid, error = _validate_size(size)
    if not is_valid:
        return False, error

    return True, None


def _parse_api_error(error_str: str) -> str:
    """
    Parse API error message and return user-friendly description

    Args:
        error_str: Raw error string from API

    Returns:
        Human-readable error message
    """
    error_upper = error_str.upper()

    # Check for known API errors
    for error_code, description in API_ERRORS.items():
        if error_code in error_upper:
            return f"{error_code}: {description}"

    # Check for common error patterns
    if "BALANCE" in error_upper or "ALLOWANCE" in error_upper:
        return "Insufficient balance or allowance"

    if "RATE" in error_upper and "LIMIT" in error_upper:
        return "API rate limit exceeded"

    if "TIMEOUT" in error_upper:
        return "Request timeout"

    if "404" in error_str:
        return "Resource not found"

    # Return original error if no match
    return error_str


def _should_retry(error_str: str) -> bool:
    """
    Determine if an error is retryable

    Args:
        error_str: Error message

    Returns:
        True if error is retryable
    """
    error_upper = error_str.upper()

    # Retryable conditions
    retryable_keywords = [
        "TIMEOUT",
        "RATE LIMIT",
        "RATE_LIMIT",
        "TOO MANY REQUESTS",
        "503",
        "502",
        "CONNECTION",
        "NETWORK",
    ]

    return any(keyword in error_upper for keyword in retryable_keywords)


def _execute_with_retry(func, *args, **kwargs):
    """
    Execute function with exponential backoff retry logic

    Args:
        func: Function to execute
        *args: Positional arguments
        **kwargs: Keyword arguments

    Returns:
        Function result or raises last exception
    """
    import time

    last_error = None

    for attempt in range(MAX_RETRIES):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_error = e
            error_str = str(e)

            # Don't retry if error is not retryable
            if not _should_retry(error_str):
                raise

            # Don't retry on last attempt
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAYS[attempt]
                log(
                    f"⏳ Retryable error, waiting {delay}s before retry {attempt + 2}/{MAX_RETRIES}: {_parse_api_error(error_str)}"
                )
                time.sleep(delay)
            else:
                log(f"❌ Max retries reached, giving up")

    # Raise last error if all retries failed
    if last_error:
        raise last_error


def place_limit_order(
    token_id: str,
    price: float,
    size: float,
    side: str,
    silent_on_balance_error: bool = False,
    order_type: str = "GTC",
    expiration: Optional[int] = None,
) -> dict:
    """
    Place a limit order (BUY or SELL) on CLOB with validation and retry logic

    Args:
        token_id: Token ID to trade
        price: Order price (0.01-0.99)
        size: Order size in shares (min 5.0)
        side: BUY or SELL
        silent_on_balance_error: Suppress balance error logs
        order_type: Order type (GTC, FOK, FAK, GTD)
        expiration: Unix timestamp for GTD orders (required if order_type='GTD')
                   Must be at least 61 seconds in the future (security threshold + desired time)

    Returns:
        Dict with success, status, order_id, error, errorMsg, orderHashes
    """
    # Validate order parameters
    is_valid, error_msg = _validate_order(price, size)
    if not is_valid:
        log(f"❌ Order validation failed: {error_msg}")
        return {
            "success": False,
            "status": "VALIDATION_ERROR",
            "order_id": None,
            "error": error_msg,
            "errorMsg": error_msg,
            "orderHashes": [],
        }

    # Validate GTD expiration
    if order_type.upper() == "GTD":
        if not expiration:
            error_msg = "GTD order type requires expiration timestamp"
            log(f"❌ {error_msg}")
            return {
                "success": False,
                "status": "VALIDATION_ERROR",
                "order_id": None,
                "error": error_msg,
                "errorMsg": error_msg,
                "orderHashes": [],
            }

        import time

        now = int(time.time())
        min_expiration = now + 61  # 1 minute security threshold + 1 second buffer

        if expiration < min_expiration:
            error_msg = f"GTD expiration must be at least 61 seconds in the future (got {expiration - now}s)"
            log(f"❌ {error_msg}")
            return {
                "success": False,
                "status": "VALIDATION_ERROR",
                "order_id": None,
                "error": error_msg,
                "errorMsg": error_msg,
                "orderHashes": [],
            }

    # Map string order type to OrderType enum
    order_type_map = {
        "GTC": OrderType.GTC,
        "FOK": OrderType.FOK,
        "FAK": OrderType.FAK,
        "GTD": OrderType.GTD,
    }

    order_type_enum = order_type_map.get(order_type.upper(), OrderType.GTC)

    def _place():
        order_client = client
        _ensure_api_creds(order_client)

        # Build order args with optional expiration
        order_args_dict = {
            "token_id": token_id,
            "price": price,
            "size": size,
            "side": side,
        }

        # Add expiration for GTD orders
        if order_type.upper() == "GTD" and expiration:
            order_args_dict["expiration"] = expiration

        order_args = OrderArgs(**order_args_dict)
        signed_order = order_client.create_order(order_args)
        return order_client.post_order(signed_order, order_type_enum)

    try:
        # Execute with retry logic
        resp = _execute_with_retry(_place)

        # Enhanced response parsing
        status = resp.get("status", "UNKNOWN") if isinstance(resp, dict) else "UNKNOWN"
        order_id = resp.get("orderID") if isinstance(resp, dict) else None
        error_msg = resp.get("errorMsg", "") if isinstance(resp, dict) else ""
        order_hashes = resp.get("orderHashes", []) if isinstance(resp, dict) else []
        success = resp.get("success", True) if isinstance(resp, dict) else True

        # CRITICAL FIX: If we got an order_id, the order was placed successfully even if there's an error message
        # This happens when Polymarket API returns "Insufficient balance" warnings but still places the order
        has_error = bool(error_msg) and not bool(order_id)

        return {
            "success": (success and not has_error)
            or bool(order_id),  # Success if we got an order_id
            "status": status,
            "order_id": order_id,
            "error": error_msg if has_error else None,
            "errorMsg": error_msg,
            "orderHashes": order_hashes,
        }

    except Exception as e:
        error_str = str(e)
        parsed_error = _parse_api_error(error_str)

        # CRITICAL FIX: Try to extract order_id from exception if it exists
        # Sometimes the API returns an error but still creates the order
        order_id_from_error = None
        if hasattr(e, "response"):
            resp_attr = getattr(e, "response")
            if hasattr(resp_attr, "json"):
                try:
                    error_json = resp_attr.json()
                    order_id_from_error = error_json.get("orderID")
                except:
                    pass

        # Only log if not a balance error during retry, or if we want full logging
        if not (silent_on_balance_error and "balance" in error_str.lower()):
            log(f"❌ {side} Order error: {parsed_error}")
            if (
                "VALIDATION" not in error_str.upper()
                and "BALANCE" not in error_str.upper()
            ):
                import traceback

                log(traceback.format_exc())

        return {
            "success": bool(
                order_id_from_error
            ),  # Success if we got an order_id despite error
            "status": "ERROR" if not order_id_from_error else "UNKNOWN",
            "order_id": order_id_from_error,
            "error": parsed_error,
            "errorMsg": parsed_error,
            "orderHashes": [],
        }


def place_order(token_id: str, price: float, size: float) -> dict:
    """Place BUY order on CLOB"""
    return place_limit_order(token_id, price, size, BUY)


def place_market_order(
    token_id: str,
    amount: float,
    side: str,
    order_type: str = "FOK",
    silent_on_error: bool = False,
) -> dict:
    """
    Place a market order for immediate execution

    Args:
        token_id: Token ID to trade
        amount: Dollar amount for BUY, number of shares for SELL
        side: BUY or SELL
        order_type: FOK (fill all or kill) or FAK (fill partial and kill rest)

    Returns:
        Dict with success, status, order_id, error, errorMsg, orderHashes
    """
    try:
        order_client = client
        _ensure_api_creds(order_client)

        # Map order type
        order_type_map = {
            "FOK": OrderType.FOK,
            "FAK": OrderType.FAK,
        }
        order_type_enum = order_type_map.get(order_type.upper(), OrderType.FOK)

        # Create market order (simplified - no price needed)
        from py_clob_client.clob_types import MarketOrderArgs

        if not silent_on_error:
            log(f"   📊 Placing {side} Market Order: {amount} units")

        market_order_args = MarketOrderArgs(
            token_id=token_id,
            amount=amount,
            side=side,
        )

        signed_order = order_client.create_market_order(market_order_args)
        resp = order_client.post_order(signed_order, order_type_enum)

        # Enhanced response parsing
        status = resp.get("status", "UNKNOWN") if isinstance(resp, dict) else "UNKNOWN"
        order_id = resp.get("orderID") if isinstance(resp, dict) else None
        error_msg = resp.get("errorMsg", "") if isinstance(resp, dict) else ""
        order_hashes = resp.get("orderHashes", []) if isinstance(resp, dict) else []
        success = resp.get("success", True) if isinstance(resp, dict) else True

        has_error = bool(error_msg)

        return {
            "success": success and not has_error,
            "status": status,
            "order_id": order_id,
            "error": error_msg if has_error else None,
            "errorMsg": error_msg,
            "orderHashes": order_hashes,
        }

    except Exception as e:
        error_str = str(e)
        parsed_error = _parse_api_error(error_str)

        # Only log if not silenced (retry logic will handle logging)
        if not silent_on_error:
            log(f"❌ {side} Market Order error: {parsed_error}")

        return {
            "success": False,
            "status": "ERROR",
            "order_id": None,
            "error": parsed_error,
            "errorMsg": parsed_error,
            "orderHashes": [],
        }


def check_liquidity(token_id: str, size: float, warn_threshold: float = 0.05) -> bool:
    """
    Check if there's sufficient liquidity for an order

    Args:
        token_id: Token ID to check
        size: Order size in shares
        warn_threshold: Warn if spread is above this (default 0.05 = 5%)

    Returns:
        True if liquidity looks good, False if spread is too wide
    """
    spread = get_spread(token_id)

    if spread is None:
        # Can't determine spread, assume OK
        return True

    if spread > warn_threshold:
        log(
            f"⚠️ Wide spread detected: {spread:.3f} ({spread * 100:.1f}%) - Low liquidity!"
        )
        return False

    return True


def place_batch_orders(orders: List[Dict[str, any]]) -> List[dict]:
    """
    Place multiple orders in a single batch (up to 15 orders)

    Args:
        orders: List of order dictionaries with keys:
            - token_id: str
            - price: float
            - size: float
            - side: str (BUY or SELL)

    Returns:
        List of result dictionaries with success, status, order_id, error
    """
    if not orders:
        return []

    if len(orders) > 15:
        log(f"⚠️ Batch order limit is 15, got {len(orders)}. Truncating to first 15.")
        orders = orders[:15]

    # Validate all orders first
    validated_orders = []
    results = []

    for i, order_params in enumerate(orders):
        token_id = order_params.get("token_id")
        price = order_params.get("price")
        size = order_params.get("size")
        side = order_params.get("side", BUY)

        # Validate
        if price is None or size is None:
            results.append(
                {
                    "success": False,
                    "status": "VALIDATION_ERROR",
                    "order_id": None,
                    "error": "Price and size are required",
                }
            )
            continue

        is_valid, error_msg = _validate_order(price, size)
        if not is_valid:
            log(f"❌ Batch order {i + 1} validation failed: {error_msg}")
            results.append(
                {
                    "success": False,
                    "status": "VALIDATION_ERROR",
                    "order_id": None,
                    "error": error_msg,
                }
            )
            continue

        validated_orders.append((i, order_params))

    if not validated_orders:
        return results

    try:
        order_client = client
        _ensure_api_creds(order_client)

        # Build batch order args
        batch_orders = []
        for _, order_params in validated_orders:
            order_args = OrderArgs(
                token_id=order_params["token_id"],
                price=order_params["price"],
                size=order_params["size"],
                side=order_params.get("side", BUY),
            )
            signed_order = order_client.create_order(order_args)
            batch_orders.append(
                PostOrdersArgs(
                    order=signed_order,
                    orderType=OrderType.GTC,
                )
            )

        # Place batch order
        responses = order_client.post_orders(batch_orders)

        # Process responses
        for idx, (original_idx, _) in enumerate(validated_orders):
            if idx < len(responses):
                resp = responses[idx]
                status = (
                    resp.get("status", "UNKNOWN")
                    if isinstance(resp, dict)
                    else "UNKNOWN"
                )
                order_id = resp.get("orderID") if isinstance(resp, dict) else None
                error_msg = resp.get("errorMsg", "") if isinstance(resp, dict) else ""
                success = resp.get("success", True) if isinstance(resp, dict) else True

                # Insert result at correct position
                while len(results) <= original_idx:
                    results.append(None)

                results[original_idx] = {
                    "success": success and not error_msg,
                    "status": status,
                    "order_id": order_id,
                    "error": error_msg or None,
                }
            else:
                while len(results) <= original_idx:
                    results.append(None)
                results[original_idx] = {
                    "success": False,
                    "status": "ERROR",
                    "order_id": None,
                    "error": "No response from batch order API",
                }

        # Filter out None values that might have been added to fill original indices
        return [r for r in results if r is not None]

    except Exception as e:
        error_str = str(e)
        log(f"❌ Batch order error: {e}")
        import traceback

        log(traceback.format_exc())

        # Return errors for all remaining orders
        for i, _ in validated_orders:
            while len(results) <= i:
                results.append(None)
            if results[i] is None:
                results[i] = {
                    "success": False,
                    "status": "ERROR",
                    "order_id": None,
                    "error": error_str,
                }

        return [r for r in results if r is not None]


def get_order_status(order_id: str) -> str:
    """
    Get current status of an order

    Possible statuses:
    - matched: Order placed and matched with existing resting order
    - live: Order placed and resting on the book
    - delayed: Order marketable but subject to matching delay
    - unmatched: Order marketable but failure delaying, placement successful
    - FILLED: Order completely filled
    - CANCELED: Order cancelled
    - EXPIRED: Order expired
    - NOT_FOUND: Order not found (404)
    - ERROR: Error fetching status
    """
    try:
        order_data = client.get_order(order_id)
        if isinstance(order_data, dict):
            status = order_data.get("status", "UNKNOWN")
        else:
            status = getattr(order_data, "status", "UNKNOWN")

        # Normalize status for consistency
        status_upper = status.upper() if status else "UNKNOWN"

        # Map known statuses
        if status_upper in [
            "MATCHED",
            "LIVE",
            "DELAYED",
            "UNMATCHED",
            "FILLED",
            "CANCELED",
            "EXPIRED",
        ]:
            return status_upper

        return status if status else "UNKNOWN"

    except Exception as e:
        if "404" in str(e):
            return "NOT_FOUND"
        log(f"⚠️ Error checking order status {order_id}: {e}")
        return "ERROR"


def get_order(order_id: str) -> Optional[dict]:
    """
    Get detailed information about an order

    Returns:
        Dict with order details including:
        - id: Order ID
        - status: Current status
        - market: Market/condition ID
        - original_size: Original order size at placement
        - size_matched: Size matched/filled so far
        - outcome: Human readable outcome
        - maker_address: Maker address (funder)
        - owner: API key owner
        - price: Order price
        - side: BUY or SELL
        - asset_id: Token ID
        - expiration: Unix timestamp or 0
        - type: Order type (GTC, FOK, etc)
        - created_at: Unix timestamp when created
        - associate_trades: List of trade IDs
    """
    try:
        order_data = client.get_order(order_id)

        # Handle both dict and object responses
        if isinstance(order_data, dict):
            return order_data

        # Convert object to dict
        result = {}
        for field in [
            "id",
            "status",
            "market",
            "original_size",
            "size_matched",
            "outcome",
            "maker_address",
            "owner",
            "price",
            "side",
            "asset_id",
            "expiration",
            "type",
            "created_at",
            "associate_trades",
        ]:
            if hasattr(order_data, field):
                result[field] = getattr(order_data, field)

        return result if result else None

    except Exception as e:
        if "404" not in str(e):
            log(f"⚠️ Error fetching order {order_id}: {e}")
        return None


def get_orders(
    market: Optional[str] = None, asset_id: Optional[str] = None
) -> List[dict]:
    """
    Get active orders, optionally filtered by market or asset_id

    Args:
        market: Condition ID of market to filter by
        asset_id: Token ID to filter by

    Returns:
        List of active order dictionaries
    """
    try:
        from py_clob_client.clob_types import OpenOrderParams

        # Build params
        params = OpenOrderParams()
        if market:
            params.market = market
        if asset_id:
            params.asset_id = asset_id

        orders = client.get_orders(params)

        # Convert to list of dicts if needed
        if not isinstance(orders, list):
            orders = [orders] if orders else []

        return orders

    except Exception as e:
        log(f"⚠️ Error fetching orders: {e}")
        return []


def cancel_order(order_id: str) -> bool:
    """Cancel an open order on CLOB"""
    try:
        # Check status first to avoid unnecessary calls
        status = get_order_status(order_id)
        if status in ["FILLED", "CANCELED", "EXPIRED"]:
            return True

        resp = client.cancel(order_id)
        return resp == "OK" or (isinstance(resp, dict) and resp.get("status") == "OK")
    except Exception as e:
        log(f"⚠️ Error cancelling order {order_id}: {e}")
        return False


def cancel_orders(order_ids: List[str]) -> dict:
    """
    Cancel multiple orders in bulk

    Args:
        order_ids: List of order IDs to cancel

    Returns:
        Dict with 'canceled' (list) and 'not_canceled' (dict) fields
    """
    if not order_ids:
        return {"canceled": [], "not_canceled": {}}

    try:
        resp = client.cancel_orders(order_ids)

        # Response format: {"canceled": [...], "not_canceled": {...}}
        if isinstance(resp, dict):
            canceled = resp.get("canceled", [])
            not_canceled = resp.get("not_canceled", {})

            # Log summary
            if canceled:
                log(f"✅ Cancelled {len(canceled)} orders")
            if not_canceled:
                log(f"⚠️ Failed to cancel {len(not_canceled)} orders")
                for order_id, reason in list(not_canceled.items())[:5]:  # Show first 5
                    log(f"  - {order_id[:10]}...: {reason}")

            return {"canceled": canceled, "not_canceled": not_canceled}

        return {"canceled": [], "not_canceled": {}}

    except Exception as e:
        log(f"⚠️ Error cancelling bulk orders: {e}")
        return {
            "canceled": [],
            "not_canceled": {order_id: str(e) for order_id in order_ids},
        }


def cancel_market_orders(
    market: Optional[str] = None, asset_id: Optional[str] = None
) -> dict:
    """
    Cancel all orders for a specific market or asset

    Args:
        market: Condition ID of market to cancel orders for
        asset_id: Token ID to cancel orders for

    Returns:
        Dict with 'canceled' (list) and 'not_canceled' (dict) fields
    """
    if not market and not asset_id:
        log("⚠️ Must provide either market or asset_id")
        return {"canceled": [], "not_canceled": {}}

    try:
        resp = client.cancel_market_orders(market=market or "", asset_id=asset_id or "")

        # Response format: {"canceled": [...], "not_canceled": {...}}
        if isinstance(resp, dict):
            canceled = resp.get("canceled", [])
            not_canceled = resp.get("not_canceled", {})

            # Log summary
            market_info = (
                f"market {market[:10]}..." if market else f"asset {asset_id[:10]}..."
            )
            if canceled:
                log(f"✅ Cancelled {len(canceled)} orders for {market_info}")
            if not_canceled:
                log(f"⚠️ Failed to cancel {len(not_canceled)} orders for {market_info}")

            return {"canceled": canceled, "not_canceled": not_canceled}

        return {"canceled": [], "not_canceled": {}}

    except Exception as e:
        log(f"⚠️ Error cancelling market orders: {e}")
        return {"canceled": [], "not_canceled": {}}


def cancel_all() -> dict:
    """
    Cancel ALL open orders (emergency function)

    Returns:
        Dict with 'canceled' (list) and 'not_canceled' (dict) fields
    """
    try:
        log("⚠️ CANCELLING ALL OPEN ORDERS...")
        resp = client.cancel_all()

        # Response format: {"canceled": [...], "not_canceled": {...}}
        if isinstance(resp, dict):
            canceled = resp.get("canceled", [])
            not_canceled = resp.get("not_canceled", {})

            # Log summary
            if canceled:
                log(f"✅ Cancelled {len(canceled)} orders")
                send_discord(
                    f"🛑 **EMERGENCY CANCEL**: Cancelled {len(canceled)} orders"
                )
            if not_canceled:
                log(f"⚠️ Failed to cancel {len(not_canceled)} orders")

            return {"canceled": canceled, "not_canceled": not_canceled}

        return {"canceled": [], "not_canceled": {}}

    except Exception as e:
        log(f"⚠️ Error cancelling all orders: {e}")
        return {"canceled": [], "not_canceled": {}}


def sell_position(
    token_id: str,
    size: float,
    current_price: float,
    max_retries: int = 3,
    use_market_order: bool = True,
) -> dict:
    """
    Sell existing position (market sell to CLOB) with retry logic

    Args:
        token_id: Token ID to sell
        size: Number of shares to sell
        current_price: Current market price (used for fallback limit orders)
        max_retries: Maximum retry attempts
        use_market_order: If True, use market order (recommended). If False, use limit order

    Returns:
        Dict with success, sold, price, status, order_id, error
    """
    retry_delays = [2, 3, 5]

    for attempt in range(max_retries):
        try:
            if attempt > 0:
                log(
                    f"🔄 Retry {attempt}/{max_retries - 1} selling position - waiting {retry_delays[attempt - 1]}s..."
                )
                import time

                time.sleep(retry_delays[attempt - 1])

            # Use market order for faster, more reliable fills
            if use_market_order:
                # Market order: specify dollar amount (shares to sell)
                result = place_market_order(
                    token_id=token_id,
                    amount=size,  # Shares to sell
                    side=SELL,
                    order_type="FAK",  # Fill-And-Kill: fills partial, cancels rest
                    silent_on_error=(
                        attempt < max_retries - 1
                    ),  # Silent on retry attempts
                )
            else:
                # Fallback to limit order
                sell_price = max(0.01, current_price - 0.01)
                sell_price = round(sell_price, 2)

                order_type_to_use = "FAK" if attempt == 0 else "GTC"

                result = place_limit_order(
                    token_id=token_id,
                    price=sell_price,
                    size=size,
                    side=SELL,
                    silent_on_balance_error=(attempt < max_retries - 1),
                    order_type=order_type_to_use,
                )

            if result["success"]:
                # Get actual sell price from response if available
                actual_price = result.get("price", current_price)

                # Log success on retry
                if attempt > 0:
                    log(f"✅ Sell succeeded on retry {attempt + 1}/{max_retries}")

                return {
                    "success": True,
                    "sold": size,
                    "price": actual_price,
                    "status": result["status"],
                    "order_id": result["order_id"],
                }

            # Check if error is retryable
            error_str = result.get("error", "")

            if "balance" in error_str.lower() and attempt < max_retries - 1:
                # Silently retry for balance errors (will succeed once tokens settle)
                continue

            # Market order failed - try limit order on next attempt
            if (
                use_market_order
                and ("FOK" in error_str or "no match" in error_str.lower())
                and attempt < max_retries - 1
            ):
                # Silently retry - FAK will handle partial fills
                continue

            # Non-retryable error
            if attempt == max_retries - 1:
                log(f"❌ Sell error after {max_retries} attempts: {error_str}")
                return {"success": False, "error": error_str}

        except Exception as e:
            error_str = str(e)
            parsed_error = _parse_api_error(error_str)

            if "balance" in error_str.lower() and attempt < max_retries - 1:
                # Silently retry for balance errors (will succeed once tokens settle)
                continue

            if attempt == max_retries - 1:
                log(f"❌ Sell error: {parsed_error}")
                import traceback

                log(traceback.format_exc())
                return {"success": False, "error": parsed_error}

    return {"success": False, "error": "Max retries exceeded"}
