"""Market order placement logic"""

from typing import Any
from py_clob_client.clob_types import (
    OrderType,
    MarketOrderArgs,
)
from src.utils.logger import log, log_error
from .client import client, _ensure_api_creds
from .utils import _parse_api_error


def place_market_order(
    token_id: str,
    amount: float,
    side: str,
    order_type: str = "FOK",
    silent_on_error: bool = False,
) -> dict:
    try:
        _ensure_api_creds(client)
        otype: Any = OrderType.FAK if order_type.upper() == "FAK" else OrderType.FOK
        if not silent_on_error:
            log(f"   📊 Placing {side} Market Order: {amount} units")
        moa = MarketOrderArgs(token_id=token_id, amount=amount, side=side)
        signed = client.create_market_order(moa)
        resp: Any = client.post_order(signed, otype)
        status = resp.get("status", "UNKNOWN") if isinstance(resp, dict) else "UNKNOWN"
        oid = resp.get("orderID") if isinstance(resp, dict) else None
        emsg = resp.get("errorMsg", "") if isinstance(resp, dict) else ""
        success = resp.get("success", True) if isinstance(resp, dict) else True
        return {
            "success": success and not bool(emsg),
            "status": status,
            "order_id": oid,
            "error": emsg if emsg else None,
        }
    except Exception as e:
        emsg = _parse_api_error(str(e))
        if not silent_on_error:
            if "Insufficient funds" in emsg and side == "SELL":
                log(
                    f"   ⚠️  {side} Market Order: {emsg} (likely already filled or locked)"
                )
            elif "no orders found to match" in emsg.lower():
                log(
                    f"   ⚠️  {side} Market Order: No liquidity found to match (FAK order killed)"
                )
            else:
                log_error(f"{side} Market Order error: {emsg}")
        return {"success": False, "status": "ERROR", "order_id": None, "error": emsg}
