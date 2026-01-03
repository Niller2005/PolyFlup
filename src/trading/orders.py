"""Order placement and management"""

import os
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType, ApiCreds
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


def place_order(token_id: str, price: float, size: float) -> dict:
    """Place BUY order on CLOB"""
    try:
        order_client = client

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
                log(f"⚠ Error setting API creds in place_order: {e}")

        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=BUY,
        )

        signed_order = order_client.create_order(order_args)
        resp = order_client.post_order(signed_order, OrderType.GTC)

        status = resp.get("status", "UNKNOWN") if resp else "UNKNOWN"
        order_id = resp.get("orderID") if resp else None

        return {"success": True, "status": status, "order_id": order_id, "error": None}

    except Exception as e:
        log(f"❌ Order error: {e}")
        import traceback

        log(traceback.format_exc())
        return {"success": False, "status": "ERROR", "order_id": None, "error": str(e)}


def sell_position(token_id: str, size: float, current_price: float) -> dict:
    """Sell existing position (market sell to CLOB)"""
    try:
        # Sell at slightly below current market price for quick fill
        sell_price = max(0.01, current_price - 0.01)

        sell_client = client
        if not hasattr(sell_client, "builder_config"):
            sell_client.builder_config = None

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
                sell_client.set_api_creds(creds)
            except Exception as e:
                log(f"⚠ Error setting API creds in sell_position: {e}")

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
        log(f"❌ Sell error: {e}")
        import traceback

        log(traceback.format_exc())
        return {"success": False, "error": str(e)}
