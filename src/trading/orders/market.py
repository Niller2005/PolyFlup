"""Market order placement logic"""

from typing import Any
from py_clob_client_v2 import MarketOrderArgs, OrderType
from src.utils.logger import log, log_error
from .client import client, _ensure_api_creds
from .utils import _parse_api_error
from src.config.settings import PAPER_TRADING
from src import paper


def place_market_order(
    token_id: str,
    amount: float,
    side: str,
    order_type: str = "FOK",
    silent_on_error: bool = False,
) -> dict:
    if PAPER_TRADING:
        # `amount` is pUSD for BUY and shares for SELL (CLOB semantics)
        try:
            if side.upper() == "BUY":
                _, asks = paper._book(token_id)
                if not asks:
                    return {"success": False, "status": "ERROR", "order_id": None, "error": "no liquidity"}
                shares = int(amount / asks[0][0] * 100) / 100
                price = 0.99
            else:
                shares, price = amount, 0.01
            r = paper.place_order(token_id, price, shares, side, "FAK" if order_type.upper() == "FAK" else "FOK")
            emsg = r.get("errorMsg") or None
            return {"success": bool(r.get("success")), "status": r.get("status", "UNKNOWN"), "order_id": r.get("orderID"), "error": emsg}
        except Exception as e:
            return {"success": False, "status": "ERROR", "order_id": None, "error": str(e)}
    try:
        _ensure_api_creds(client)
        otype: Any = OrderType.FAK if order_type.upper() == "FAK" else OrderType.FOK
        if not silent_on_error:
            log(f"   📊 Placing {side} Market Order: {amount} units")
        moa = MarketOrderArgs(
            token_id=token_id, amount=amount, side=side, order_type=otype
        )
        resp: Any = client.create_and_post_market_order(moa, order_type=otype)
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
