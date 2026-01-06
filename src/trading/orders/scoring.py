"""Order reward scoring checks"""

from typing import List, Dict, Any
from py_clob_client.clob_types import OrderScoringParams, OrdersScoringParams
from src.utils.logger import log
from .client import client

def check_order_scoring(order_id: str) -> bool:
    """Check if order is scoring for rewards"""
    try:
        params: Any = OrderScoringParams(orderId=order_id)
        resp: Any = client.is_order_scoring(params)
        if isinstance(resp, dict):
            return resp.get("isScoring", False)
        elif resp is not None and hasattr(resp, "is_scoring"):
            return getattr(resp, "is_scoring")
        return False
    except Exception as e:
        if "404" not in str(e):
            log(f"⚠️  Error checking scoring {order_id}: {e}")
        return False

def check_orders_scoring(order_ids: List[str]) -> Dict[str, bool]:
    """Check scoring for multiple orders"""
    if not order_ids:
        return {}
    try:
        params: Any = OrdersScoringParams(orderIds=order_ids)
        resp: Any = client.are_orders_scoring(params)
        result = {}
        if isinstance(resp, list):
            for item in resp:
                o_id = (
                    item.get("orderId")
                    if isinstance(item, dict)
                    else getattr(item, "order_id", None)
                )
                scoring = (
                    item.get("isScoring")
                    if isinstance(item, dict)
                    else getattr(item, "is_scoring", False)
                )
                if o_id:
                    result[o_id] = scoring
        elif isinstance(resp, dict):
            return resp
        return result
    except Exception as e:
        log(f"⚠️  Error checking scoring: {e}")
        return {o_id: False for o_id in order_ids}
