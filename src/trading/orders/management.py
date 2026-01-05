"""Order management and cancellation"""

from typing import Optional, List, Any
from py_clob_client.clob_types import OpenOrderParams
from src.utils.logger import log
from .client import client

def get_order_status(order_id: str) -> str:
    try:
        order_data: Any = client.get_order(order_id)
        status = (
            order_data.get("status", "UNKNOWN")
            if isinstance(order_data, dict)
            else getattr(order_data, "status", "UNKNOWN")
        )
        return status.upper() if status else "UNKNOWN"
    except Exception as e:
        if "404" in str(e):
            return "NOT_FOUND"
        log(f"⚠️ Error checking order status {order_id}: {e}")
        return "ERROR"

def get_order(order_id: str) -> Optional[dict]:
    try:
        order_data: Any = client.get_order(order_id)
        if isinstance(order_data, dict):
            return order_data
        res = {}
        for f in [
            "id",
            "status",
            "market",
            "original_size",
            "size_matched",
            "outcome",
            "price",
            "side",
            "asset_id",
        ]:
            if hasattr(order_data, f):
                res[f] = getattr(order_data, f)
        return res if res else None
    except Exception as e:
        if "404" not in str(e):
            log(f"⚠️ Error fetching order {order_id}: {e}")
        return None

def get_orders(
    market: Optional[str] = None, asset_id: Optional[str] = None
) -> List[dict]:
    try:
        params = OpenOrderParams(market=market or "", asset_id=asset_id or "")
        orders = client.get_orders(params)
        return orders if isinstance(orders, list) else ([orders] if orders else [])
    except Exception as e:
        log(f"⚠️ Error fetching orders: {e}")
        return []

def cancel_order(order_id: str) -> bool:
    try:
        status = get_order_status(order_id)
        if status in ["FILLED", "CANCELED", "EXPIRED", "NOT_FOUND"]:
            return True
        resp = client.cancel(order_id)
        return resp == "OK" or (isinstance(resp, dict) and resp.get("status") == "OK")
    except Exception as e:
        if "404" in str(e) or "not found" in str(e).lower():
            return True
        log(f"⚠️ Error cancelling order {order_id}: {e}")
        return False

def cancel_orders(order_ids: List[str]) -> dict:
    if not order_ids:
        return {"canceled": [], "not_canceled": {}}
    try:
        resp: Any = client.cancel_orders(order_ids)
        if isinstance(resp, dict):
            return {
                "canceled": resp.get("canceled", []),
                "not_canceled": resp.get("not_canceled", {}),
            }
        return {"canceled": [], "not_canceled": {}}
    except Exception as e:
        log(f"⚠️ Error bulk cancelling: {e}")
        return {"canceled": [], "not_canceled": {o: str(e) for o in order_ids}}

def cancel_market_orders(
    market: Optional[str] = None, asset_id: Optional[str] = None
) -> dict:
    if not market and not asset_id:
        return {"canceled": [], "not_canceled": {}}
    try:
        resp: Any = client.cancel_market_orders(
            market=market or "", asset_id=asset_id or ""
        )
        if isinstance(resp, dict):
            return {
                "canceled": resp.get("canceled", []),
                "not_canceled": resp.get("not_canceled", {}),
            }
        return {"canceled": [], "not_canceled": {}}
    except Exception as e:
        log(f"⚠️ Error cancelling market orders: {e}")
        return {"canceled": [], "not_canceled": {}}

def cancel_all() -> dict:
    try:
        log("⚠️ CANCELLING ALL OPEN ORDERS...")
        resp: Any = client.cancel_all()
        if isinstance(resp, dict):
            return {
                "canceled": resp.get("canceled", []),
                "not_canceled": resp.get("not_canceled", {}),
            }
        return {"canceled": [], "not_canceled": {}}
    except Exception as e:
        log(f"⚠️ Error cancelling all orders: {e}")
        return {"canceled": [], "not_canceled": {}}
