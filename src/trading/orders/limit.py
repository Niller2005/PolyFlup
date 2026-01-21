"""Limit order and market order placement logic"""

from typing import List, Dict, Optional, Any
from datetime import datetime, timedelta, timezone
from py_clob_client.clob_types import (
    OrderArgs,
    OrderType,
    PostOrdersArgs,
    MarketOrderArgs,
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
    otype: Any
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
        return client.post_order(signed, otype)  # type: ignore

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
            if "Insufficient funds" in emsg and side == "SELL":
                log(f"   ⚠️  {side} Order: {emsg} (likely already filled or locked)")
            else:
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


def place_market_order(
    token_id: str,
    size: float,
    side: str,
    order_type: str = "FOK",
    silent_on_balance_error: bool = False,
) -> dict:
    """
    Place a market order that executes at best available price immediately.

    Args:
        token_id: Token ID to trade
        size: Number of shares to trade
        side: "BUY" or "SELL"
        order_type: "FOK" (fill-or-kill, default) or "FAK" (fill-and-kill)
        silent_on_balance_error: If True, don't log balance errors

    Returns:
        dict with success, status, order_id, error
    """
    # Market orders don't need price validation, only size
    if size <= 0:
        return {
            "success": False,
            "status": "VALIDATION_ERROR",
            "error": "Size must be positive",
        }

    # Extract enum value
    otype: Any
    if order_type.upper() == "FAK":
        otype = OrderType.FAK
    else:
        otype = OrderType.FOK  # Default to FOK for immediate fill

    def _place():
        _ensure_api_creds(client)
        truncated_size = truncate_float(size, 2)

        # MarketOrderArgs: token_id, amount, side, price=0, order_type
        # The price parameter can be left as 0 for true market orders
        moa = MarketOrderArgs(
            token_id=token_id,
            amount=truncated_size,
            side=side,
            price=0,  # Market order - no price limit
            order_type=otype,
        )
        signed = client.create_market_order(moa)
        return client.post_order(signed, otype)  # type: ignore

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
            if "Insufficient funds" in emsg and side == "SELL":
                log(
                    f"   ⚠️  {side} Market Order: {emsg} (likely already filled or locked)"
                )
            else:
                log_error(f"{side} Market Order error: {emsg}")
        return {
            "success": bool(oid),
            "status": "ERROR" if not oid else "UNKNOWN",
            "order_id": oid,
            "error": emsg,
        }


def place_batch_orders(
    orders: List[Dict[str, Any]], order_type: str = "GTC"
) -> List[dict]:
    if not orders:
        return []
    validated = []
    results = []
    # Increased batch limit from 15 to 50 for better throughput
    # Polymarket supports larger batches, tested safe up to 50
    for op in orders[:50]:
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

        # Map order_type string to OrderType enum
        otype_map = {
            "GTC": OrderType.GTC,
            "FOK": OrderType.FOK,
            "FAK": OrderType.FAK,
            "GTD": OrderType.GTD,
        }
        otype = otype_map.get(order_type, OrderType.GTC)

        batch = []
        for op in validated:
            oa = OrderArgs(
                token_id=op["token_id"],
                price=op["price"],
                size=op["size"],
                side=op.get("side", BUY),
            )
            signed = client.create_order(oa)
            # Add postOnly flag for maker orders (earns rebate, ensures whole number fills)
            post_only = op.get("post_only", False)
            batch.append(
                PostOrdersArgs(order=signed, orderType=otype, postOnly=post_only)
            )  # type: ignore
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

        # Try to recover orders that may have been placed despite exception
        # This handles network timeouts where orders were sent but response was lost
        try:
            from .management import get_orders

            all_open_orders = get_orders()
            now = datetime.now(timezone.utc)

            for op in validated:
                found_match = False
                for o in all_open_orders:
                    if not isinstance(o, dict):
                        continue

                    # Match by token_id, price, size (within small epsilon)
                    o_price = float(o.get("price", 0))
                    o_size = float(o.get("original_size", 0))
                    o_token = str(o.get("asset_id", ""))
                    req_price = float(op["price"])
                    req_size = float(op["size"])
                    req_token = str(op["token_id"])

                    price_match = abs(o_price - req_price) < 0.0001
                    size_match = abs(o_size - req_size) < 0.01
                    token_match = o_token == req_token

                    if price_match and size_match and token_match:
                        # Order was placed - add to results
                        order_id = o.get("id") or o.get("orderID")
                        if order_id:
                            results.append(
                                {
                                    "success": True,
                                    "status": o.get("status", "LIVE"),
                                    "order_id": order_id,
                                    "error": None,
                                }
                            )
                            log(
                                f"   🔄 Recovered order {order_id[:10]} from batch exception"
                            )
                            found_match = True
                            break

                if not found_match:
                    # No match found - order likely failed
                    results.append(
                        {
                            "success": False,
                            "status": "ERROR",
                            "order_id": None,
                            "error": "Order not found in exchange after batch exception",
                        }
                    )
        except Exception as recovery_error:
            log(
                f"   ⚠️  Failed to recover orders from batch exception: {recovery_error}"
            )
            # Fall back to empty results for validated orders
            for _ in validated:
                results.append(
                    {
                        "success": False,
                        "status": "ERROR",
                        "order_id": None,
                        "error": str(e),
                    }
                )

        return results
