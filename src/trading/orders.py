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
from src.utils.logger import log

# API Constraints
MIN_TICK_SIZE = 0.01
MIN_ORDER_SIZE = 5.0  # Minimum size in shares

# Initialize client
client = ClobClient(
    host=CLOB_HOST,
    key=PROXY_PK,
    chain_id=CHAIN_ID,
    signature_type=SIGNATURE_TYPE,
    funder=FUNDER_PROXY or None,
)

# Hotfix: ensure client has builder_config attribute
if not hasattr(client, "builder_config"):
    client.builder_config = None


def get_clob_client() -> ClobClient:
    """Get the initialized CLOB client"""
    return client


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


def _validate_price(price: float) -> tuple[bool, Optional[str]]:
    """
    Validate order price meets minimum tick size requirements

    Returns:
        (is_valid, error_message)
    """
    if price <= 0:
        return False, "Price must be greater than 0"

    if price < 0.01 or price > 0.99:
        return False, "Price must be between 0.01 and 0.99"

    # Check tick size (must be multiple of 0.01)
    if round(price, 2) != price:
        return (
            False,
            f"Price must be rounded to minimum tick size of {MIN_TICK_SIZE} (got {price})",
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


def place_limit_order(
    token_id: str,
    price: float,
    size: float,
    side: str,
    silent_on_balance_error: bool = False,
) -> dict:
    """Place a limit order (BUY or SELL) on CLOB with validation"""
    # Validate order parameters
    is_valid, error_msg = _validate_order(price, size)
    if not is_valid:
        log(f"❌ Order validation failed: {error_msg}")
        return {
            "success": False,
            "status": "VALIDATION_ERROR",
            "order_id": None,
            "error": error_msg,
        }

    try:
        order_client = client

        _ensure_api_creds(order_client)

        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=side,
        )

        signed_order = order_client.create_order(order_args)
        resp = order_client.post_order(signed_order, OrderType.GTC)

        status = resp.get("status", "UNKNOWN") if resp else "UNKNOWN"
        order_id = resp.get("orderID") if resp else None

        return {"success": True, "status": status, "order_id": order_id, "error": None}

    except Exception as e:
        error_str = str(e)
        # Only log if not a balance error during retry, or if we want full logging
        if not (silent_on_balance_error and "not enough balance" in error_str.lower()):
            log(f"❌ {side} Order error: {e}")
            import traceback

            log(traceback.format_exc())
        return {
            "success": False,
            "status": "ERROR",
            "order_id": None,
            "error": error_str,
        }


def place_order(token_id: str, price: float, size: float) -> dict:
    """Place BUY order on CLOB"""
    return place_limit_order(token_id, price, size, BUY)


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

        return results

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

        return results


def get_order_status(order_id: str) -> str:
    """Get current status of an order"""
    try:
        order_data = client.get_order(order_id)
        if isinstance(order_data, dict):
            return order_data.get("status", "UNKNOWN")
        return getattr(order_data, "status", "UNKNOWN")
    except Exception as e:
        if "404" in str(e):
            return "NOT_FOUND"
        log(f"⚠️ Error checking order status {order_id}: {e}")
        return "ERROR"


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


def sell_position(
    token_id: str, size: float, current_price: float, max_retries: int = 3
) -> dict:
    """Sell existing position (market sell to CLOB) with retry logic"""
    retry_delays = [2, 3, 5]

    for attempt in range(max_retries):
        try:
            if attempt > 0:
                log(
                    f"🔄 Retry {attempt}/{max_retries - 1} selling position - waiting {retry_delays[attempt - 1]}s..."
                )
                import time

                time.sleep(retry_delays[attempt - 1])

            # Sell at slightly below current market price for quick fill
            sell_price = max(0.01, current_price - 0.01)

            sell_client = client
            _ensure_api_creds(sell_client)

            # Create SELL order
            order_args = OrderArgs(
                token_id=token_id,
                price=sell_price,
                size=size,
                side=SELL,
            )

            signed_order = sell_client.create_order(order_args)
            resp = sell_client.post_order(signed_order, OrderType.GTC)

            status = resp.get("status", "UNKNOWN") if resp else "UNKNOWN"
            order_id = resp.get("orderID") if resp else None

            return {
                "success": True,
                "sold": size,
                "price": sell_price,
                "status": status,
                "order_id": order_id,
            }

        except Exception as e:
            error_str = str(e)
            if "not enough balance" in error_str.lower() and attempt < max_retries - 1:
                log(f"⏳ Balance for sell not yet available, will retry...")
                continue

            log(f"❌ Sell error: {e}")
            if attempt == max_retries - 1:
                import traceback

                log(traceback.format_exc())
                return {"success": False, "error": error_str}

    return {"success": False, "error": "Max retries exceeded"}
