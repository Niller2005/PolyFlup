"""Order management and cancellation"""

from typing import Optional, List, Any
from py_clob_client_v2 import (
    OpenOrderParams,
    OrderMarketCancelParams,
    OrderPayload,
)
from src.utils.logger import log
from .client import client
from src.config.settings import PAPER_TRADING
from src import paper

def get_order_status(order_id: str) -> str:
    if PAPER_TRADING:
        o = paper.get_order(order_id)
        return o["status"].upper() if o else "NOT_FOUND"
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
        log(f"⚠️  Error checking order status {order_id}: {e}")
        return "ERROR"

def get_order(order_id: str) -> Optional[dict]:
    if PAPER_TRADING:
        return paper.get_order(order_id)
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
            log(f"⚠️  Error fetching order {order_id}: {e}")
        return None

def get_orders(
    market: Optional[str] = None, asset_id: Optional[str] = None
) -> List[dict]:
    if PAPER_TRADING:
        return paper.open_orders(asset_id or None)
    try:
        params = OpenOrderParams(market=market or "", asset_id=asset_id or "")
        orders = client.get_open_orders(params)
        return orders if isinstance(orders, list) else ([orders] if orders else [])
    except Exception as e:
        log(f"⚠️  Error fetching orders: {e}")
        return []

def cancel_order(order_id: str) -> bool:
    if PAPER_TRADING:
        return paper.cancel(order_id)
    try:
        status = get_order_status(order_id)
        if status in ["FILLED", "CANCELED", "EXPIRED", "NOT_FOUND"]:
            return True
        resp = client.cancel_order(OrderPayload(orderID=order_id))
        return resp == "OK" or (isinstance(resp, dict) and resp.get("status") == "OK")
    except Exception as e:
        if "404" in str(e) or "not found" in str(e).lower():
            return True
        log(f"⚠️  Error cancelling order {order_id}: {e}")
        return False

def cancel_orders(order_ids: List[str]) -> dict:
    if PAPER_TRADING:
        return {"canceled": [i for i in order_ids if paper.cancel(i)], "not_canceled": {}}
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
        log(f"⚠️  Error bulk cancelling: {e}")
        return {"canceled": [], "not_canceled": {o: str(e) for o in order_ids}}

def cancel_market_orders(
    market: Optional[str] = None, asset_id: Optional[str] = None
) -> dict:
    if PAPER_TRADING:
        return {"canceled": paper.cancel_all(asset_id or None), "not_canceled": {}}
    if not market and not asset_id:
        return {"canceled": [], "not_canceled": {}}
    try:
        resp: Any = client.cancel_market_orders(
            OrderMarketCancelParams(market=market or "", asset_id=asset_id or "")
        )
        if isinstance(resp, dict):
            return {
                "canceled": resp.get("canceled", []),
                "not_canceled": resp.get("not_canceled", {}),
            }
        return {"canceled": [], "not_canceled": {}}
    except Exception as e:
        log(f"⚠️  Error cancelling market orders: {e}")
        return {"canceled": [], "not_canceled": {}}

def cancel_all() -> dict:
    if PAPER_TRADING:
        return {"canceled": paper.cancel_all(), "not_canceled": {}}
    try:
        log("⚠️  CANCELLING ALL OPEN ORDERS...")
        resp: Any = client.cancel_all()
        if isinstance(resp, dict):
            return {
                "canceled": resp.get("canceled", []),
                "not_canceled": resp.get("not_canceled", {}),
            }
        return {"canceled": [], "not_canceled": {}}
    except Exception as e:
        log(f"⚠️  Error cancelling all orders: {e}")
        return {"canceled": [], "not_canceled": {}}
