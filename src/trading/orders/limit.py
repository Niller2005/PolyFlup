"""Limit order placement logic"""

from typing import List, Dict, Optional, Any
from py_clob_client.clob_types import (
    OrderArgs,
    OrderType,
    PostOrdersArgs,
)
from src.utils.logger import log, log_error
from .client import client, _ensure_api_creds
from .constants import BUY
from .utils import (
    _validate_order,
    truncate_float,
    _execute_with_retry,
    _parse_api_error,
)


def place_limit_order(
    token_id: str,
    price: float,
    size: float,
    side: str,
    silent_on_balance_error: bool = False,
    order_type: str = "GTC",
    expiration: Optional[int] = None,
) -> dict:
    valid, err = _validate_order(price, size)
    if not valid:
        return {"success": False, "status": "VALIDATION_ERROR", "error": err}

    # Extract enum value to avoid Literal vs Enum issues in type checker
    if order_type.upper() == "FOK":
        otype = OrderType.FOK
    elif order_type.upper() == "FAK":
        otype = OrderType.FAK
    elif order_type.upper() == "GTD":
        otype = OrderType.GTD
    else:
        otype = OrderType.GTC

    def _place():
        _ensure_api_creds(client)
        # Use truncate_float to ensure we don't round up and exceed balance
        truncated_size = truncate_float(size, 2)
        oa = OrderArgs(token_id=token_id, price=price, size=truncated_size, side=side)
        if otype == OrderType.GTD and expiration:
            oa.expiration = expiration
        signed = client.create_order(oa)
        return client.post_order(signed, otype)

    try:
        resp: Any = _execute_with_retry(_place)
        status = resp.get("status", "UNKNOWN") if isinstance(resp, dict) else "UNKNOWN"
        oid = resp.get("orderID") if isinstance(resp, dict) else None
        emsg = resp.get("errorMsg", "") if isinstance(resp, dict) else ""
        success = resp.get("success", True) if isinstance(resp, dict) else True
        has_err = bool(emsg) and not bool(oid)
        return {
            "success": (success and not has_err) or bool(oid),
            "status": status,
            "order_id": oid,
            "error": emsg if has_err else None,
        }
    except Exception as e:
        emsg = _parse_api_error(str(e))
        oid = None
        try:
            r: Any = getattr(e, "response", None)
            if r and hasattr(r, "json"):
                oid = r.json().get("orderID")
        except:
            pass
        if not (silent_on_balance_error and "balance" in str(e).lower()):
            log_error(f"{side} Order error: {emsg}")
        return {
            "success": bool(oid),
            "status": "ERROR" if not oid else "UNKNOWN",
            "order_id": oid,
            "error": emsg,
        }


def place_order(token_id: str, price: float, size: float) -> dict:
    """Convenience function for BUY limit orders"""
    return place_limit_order(token_id, price, size, BUY)


def place_batch_orders(orders: List[Dict[str, Any]]) -> List[dict]:
    if not orders:
        return []
    validated = []
    results = []
    for op in orders[:15]:
        p, s = op.get("price"), op.get("size")
        if p is None or s is None:
            results.append(
                {
                    "success": False,
                    "status": "VALIDATION_ERROR",
                    "error": "Price/size required",
                }
            )
            continue
        valid, err = _validate_order(p, s)
        if not valid:
            results.append(
                {"success": False, "status": "VALIDATION_ERROR", "error": err}
            )
            continue
        validated.append(op)
    if not validated:
        return results
    try:
        _ensure_api_creds(client)
        batch = []
        for op in validated:
            oa = OrderArgs(
                token_id=op["token_id"],
                price=op["price"],
                size=op["size"],
                side=op.get("side", BUY),
            )
            signed = client.create_order(oa)
            batch.append(PostOrdersArgs(order=signed, orderType=OrderType.GTC))
        responses: Any = client.post_orders(batch)
        for r in responses:
            if isinstance(r, dict):
                results.append(
                    {
                        "success": r.get("success", True) and not r.get("errorMsg"),
                        "status": r.get("status", "UNKNOWN"),
                        "order_id": r.get("orderID"),
                        "error": r.get("errorMsg"),
                    }
                )
            else:
                results.append(
                    {
                        "success": False,
                        "status": "ERROR",
                        "error": "Invalid response format",
                    }
                )
        return results
    except Exception as e:
        log_error(f"Batch order error: {e}")
        return results
