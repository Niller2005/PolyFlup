"""Polymarket CLOB Client initialization"""

import os
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds
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
    key=PROXY_PK or "",
    chain_id=CHAIN_ID,
    signature_type=SIGNATURE_TYPE,
    funder=FUNDER_PROXY or "",
)

# Hotfix: ensure client has builder_config attribute
if not hasattr(client, "builder_config"):
    setattr(client, "builder_config", None)

def get_clob_client() -> ClobClient:
    """Get the initialized CLOB client"""
    return client

def setup_api_creds() -> None:
    """Setup API credentials"""
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
    """Ensure API credentials are set"""
    api_key = os.getenv("API_KEY")
    api_secret = os.getenv("API_SECRET")
    api_passphrase = os.getenv("API_PASSPHRASE")
    if api_key and api_secret and api_passphrase:
        try:
            creds = ApiCreds(
                api_key=api_key, api_secret=api_secret, api_passphrase=api_passphrase
            )
            order_client.set_api_creds(creds)
        except Exception as e:
            log(f"⚠ Error setting API creds: {e}")
