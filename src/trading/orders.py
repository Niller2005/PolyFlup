"""Order placement and management"""

import os
from typing import List, Dict, Optional, Any, cast
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    OrderArgs,
    OrderType,
    ApiCreds,
    PostOrdersArgs,
    BalanceAllowanceParams,
    AssetType,
    TradeParams,
    DropNotificationParams,
    OpenOrderParams,
    OrderScoringParams,
    OrdersScoringParams,
    MarketOrderArgs,
    BookParams,
)
from py_clob_client.order_builder.constants import BUY, SELL
from dotenv import set_key
from src.config.settings import (
    CLOB_HOST,
    PROXY_PK,
    CHAIN_ID,
    SIGNATURE_TYPE,
    FUNDER_PROXY,
)
from src.utils.logger import log, send_discord

# API Constraints
MIN_TICK_SIZE = 0.01
MIN_ORDER_SIZE = 5.0  # Minimum size in shares

# Retry Configuration
MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]  # Exponential backoff in seconds

# Known API Error Messages
API_ERRORS = {
    "INVALID_ORDER_MIN_TICK_SIZE": "Price breaks minimum tick size rules",
    "INVALID_ORDER_MIN_SIZE": "Size lower than minimum",
    "INVALID_ORDER_DUPLICATED": "Order already placed",
    "INVALID_ORDER_NOT_ENOUGH_BALANCE": "Not enough balance/allowance",
    "INVALID_ORDER_EXPIRATION": "Invalid expiration time",
    "INVALID_ORDER_ERROR": "Could not insert order",
    "EXECUTION_ERROR": "Could not execute trade",
    "ORDER_DELAYED": "Order delayed due to market conditions",
    "DELAYING_ORDER_ERROR": "Error delaying order",
    "FOK_ORDER_NOT_FILLED_ERROR": "FOK order not fully filled",
    "MARKET_NOT_READY": "Market not ready for orders",
}

# Initialize client
client = ClobClient(
    host=CLOB_HOST,
    key=PROXY_PK or "",
    chain_id=CHAIN_ID,
    signature_type=SIGNATURE_TYPE,
    funder=FUNDER_PROXY or None,
)

# Hotfix: ensure client has builder_config attribute
if not hasattr(client, "builder_config"):
    setattr(client, "builder_config", None)


def get_clob_client() -> ClobClient:
    """Get the initialized CLOB client"""
    return client


def get_midpoint(token_id: str) -> Optional[float]:
    """Get midpoint price for a token"""
    try:
        result: Any = client.get_midpoint(token_id)
        if isinstance(result, dict):
            mid = result.get("mid")
            if mid:
                return float(mid)
        elif result is not None and hasattr(result, "mid"):
            val = getattr(result, "mid")
            if val is not None:
                return float(val)
        return None
    except Exception as e:
        log(f"⚠️ Error getting midpoint for {token_id[:10]}...: {e}")
        return None


def get_tick_size(token_id: str) -> float:
    """Get the minimum tick size for a token"""
    try:
        tick_size = client.get_tick_size(token_id)
        if tick_size:
            return float(tick_size)
        return MIN_TICK_SIZE
    except Exception as e:
        log(f"⚠️ Error getting tick size for {token_id[:10]}...: {e}")
        return MIN_TICK_SIZE


def get_spread(token_id: str) -> Optional[float]:
    """Get the spread for a token"""
    try:
        result: Any = client.get_spread(token_id)
        if isinstance(result, dict):
            spread = result.get("spread")
            if spread:
                return float(spread)
        elif result is not None and hasattr(result, "spread"):
            val = getattr(result, "spread")
            if val is not None:
                return float(val)
        return None
    except Exception as e:
        log(f"⚠️ Error getting spread for {token_id[:10]}...: {e}")
        return None


def get_bulk_spreads(token_ids: List[str]) -> Dict[str, float]:
    """Get spreads for multiple tokens in a single call"""
    if not token_ids:
        return {}
    try:
        params = [BookParams(token_id=tid) for tid in token_ids]
        resp: Any = client.get_spreads(params)
        result = {}
        if isinstance(resp, list):
            for item in resp:
                tid = (
                    item.get("asset_id")
                    if isinstance(item, dict)
                    else getattr(item, "asset_id", None)
                )
                spread = (
                    item.get("spread")
                    if isinstance(item, dict)
                    else getattr(item, "spread", None)
                )
                if tid and spread is not None:
                    result[tid] = float(spread)
        return result
    except Exception as e:
        log(f"⚠️ Error getting bulk spreads: {e}")
        return {}


def get_server_time() -> Optional[int]:
    """Get current server timestamp"""
    try:
        timestamp = client.get_server_time()
        if isinstance(timestamp, (int, float)):
            return int(timestamp)
        return None
    except Exception as e:
        log(f"⚠️ Error getting server time: {e}")
        return None


def get_trades(
    market: Optional[str] = None, asset_id: Optional[str] = None, limit: int = 100
) -> List[dict]:
    """Get trade history (filled orders)"""
    try:
        params = cast(Any, TradeParams(market=market or "", asset_id=asset_id or ""))
        trades = client.get_trades(params)
        if not isinstance(trades, list):
            trades = [trades] if trades else []
        return trades[:limit]
    except Exception as e:
        log(f"⚠️ Error getting trades: {e}")
        return []


def get_balance_allowance(token_id: Optional[str] = None) -> Optional[dict]:
    """Get balance and allowance"""
    try:
        atype = AssetType.CONDITIONAL if token_id else AssetType.COLLATERAL
        params = cast(
            Any,
            BalanceAllowanceParams(
                asset_type=cast(Any, atype), token_id=token_id or ""
            ),
        )
        result: Any = client.get_balance_allowance(params)
        if isinstance(result, dict):
            return {
                "balance": float(result.get("balance", 0)) / 1_000_000.0,
                "allowance": float(result.get("allowance", 0)) / 1_000_000.0,
            }
        elif (
            result is not None
            and hasattr(result, "balance")
            and hasattr(result, "allowance")
        ):
            return {
                "balance": float(getattr(result, "balance")) / 1_000_000.0,
                "allowance": float(getattr(result, "allowance")) / 1_000_000.0,
            }
        return None
    except Exception as e:
        log(f"⚠️ Error getting balance/allowance: {e}")
        return None


def get_notifications() -> List[dict]:
    """Get all notifications"""
    try:
        notifications = client.get_notifications()
        if not isinstance(notifications, list):
            notifications = [notifications] if notifications else []
        result = []
        for notif in notifications:
            if isinstance(notif, dict):
                result.append(notif)
            else:
                notif_dict = {
                    "id": getattr(notif, "id", None),
                    "owner": getattr(notif, "owner", ""),
                    "payload": getattr(notif, "payload", {}),
                    "timestamp": getattr(notif, "timestamp", None),
                    "type": getattr(notif, "type", None),
                }
                result.append(notif_dict)
        return result
    except Exception as e:
        log(f"⚠️ Error getting notifications: {e}")
        return []


def drop_notifications(notification_ids: List[str]) -> bool:
    """Mark notifications as read"""
    try:
        params = cast(Any, DropNotificationParams(ids=notification_ids))
        client.drop_notifications(params)
        return True
    except Exception as e:
        log(f"⚠️ Error dropping notifications: {e}")
        return False


def get_current_positions(user_address: str) -> List[dict]:
    """Get current positions for a user from Data API"""
    try:
        from src.config.settings import DATA_API_BASE
        import requests

        url = f"{DATA_API_BASE}/positions?user={user_address}"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        return data.get("positions", []) if isinstance(data, dict) else []
    except Exception as e:
        log(f"⚠️ Error getting positions from Data API: {e}")
        return []


def check_order_scoring(order_id: str) -> bool:
    """Check if order is scoring for rewards"""
    try:
        params = cast(Any, OrderScoringParams(orderId=order_id))
        resp: Any = client.is_order_scoring(params)
        if isinstance(resp, dict):
            return resp.get("isScoring", False)
        elif resp is not None and hasattr(resp, "is_scoring"):
            return getattr(resp, "is_scoring")
        return False
    except Exception as e:
        if "404" not in str(e):
            log(f"⚠️ Error checking scoring {order_id}: {e}")
        return False


def check_orders_scoring(order_ids: List[str]) -> Dict[str, bool]:
    """Check scoring for multiple orders"""
    if not order_ids:
        return {}
    try:
        params = cast(Any, OrdersScoringParams(orderIds=order_ids))
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
        log(f"⚠️ Error checking scoring: {e}")
        return {o_id: False for o_id in order_ids}


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
            log(f"⚠ Error loading API creds: {e}")
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


def _validate_price(
    price: float, tick_size: float = MIN_TICK_SIZE
) -> tuple[bool, Optional[str]]:
    if price <= 0:
        return False, "Price must be > 0"
    if price < 0.01 or price > 0.99:
        return False, "Price must be 0.01-0.99"
    decimal_places = 2
    if tick_size == 0.1:
        decimal_places = 1
    elif tick_size == 0.01:
        decimal_places = 2
    elif tick_size == 0.001:
        decimal_places = 3
    elif tick_size == 0.0001:
        decimal_places = 4
    if round(price, decimal_places) != price:
        return False, f"Price must be rounded to {tick_size}"
    return True, None


def _validate_size(size: float) -> tuple[bool, Optional[str]]:
    if size < MIN_ORDER_SIZE:
        return False, f"Order size must be at least {MIN_ORDER_SIZE}"
    return True, None


def _validate_order(price: float, size: float) -> tuple[bool, Optional[str]]:
    valid, err = _validate_price(price)
    if not valid:
        return False, err
    valid, err = _validate_size(size)
    if not valid:
        return False, err
    return True, None


def _parse_api_error(error_str: str) -> str:
    error_upper = error_str.upper()
    for code, desc in API_ERRORS.items():
        if code in error_upper:
            return f"{code}: {desc}"
    if "BALANCE" in error_upper or "ALLOWANCE" in error_upper:
        return "Insufficient funds"
    if "RATE" in error_upper and "LIMIT" in error_upper:
        return "Rate limit"
    return error_str


def _should_retry(error_str: str) -> bool:
    error_upper = error_str.upper()
    return any(
        k in error_upper for k in ["TIMEOUT", "RATE LIMIT", "503", "502", "CONNECTION"]
    )


def _execute_with_retry(func, *args, **kwargs):
    import time

    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_err = e
            if not _should_retry(str(e)):
                raise
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAYS[attempt]
                log(
                    f"⏳ Retry {attempt + 2}/{MAX_RETRIES} after {delay}s: {_parse_api_error(str(e))}"
                )
                time.sleep(delay)
    if last_err:
        raise last_err


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

    otype_str = order_type.upper()
    if otype_str == "FOK":
        otype = OrderType.FOK
    elif otype_str == "FAK":
        otype = OrderType.FAK
    elif otype_str == "GTD":
        otype = OrderType.GTD
    else:
        otype = OrderType.GTC

    def _place():
        _ensure_api_creds(client)
        oa = OrderArgs(token_id=token_id, price=price, size=size, side=side)
        if otype == OrderType.GTD and expiration:
            oa.expiration = expiration
        signed = client.create_order(oa)
        return client.post_order(signed, cast(Any, otype))

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
            r = getattr(e, "response", None)
            if r and hasattr(r, "json"):
                oid = r.json().get("orderID")
        except:
            pass
        if not (silent_on_balance_error and "balance" in str(e).lower()):
            log(f"❌ {side} Order error: {emsg}")
        return {
            "success": bool(oid),
            "status": "ERROR" if not oid else "UNKNOWN",
            "order_id": oid,
            "error": emsg,
        }


def place_order(token_id: str, price: float, size: float) -> dict:
    return place_limit_order(token_id, price, size, BUY)


def place_market_order(
    token_id: str,
    amount: float,
    side: str,
    order_type: str = "FOK",
    silent_on_error: bool = False,
) -> dict:
    try:
        _ensure_api_creds(client)
        otype_str = order_type.upper()
        otype = OrderType.FAK if otype_str == "FAK" else OrderType.FOK

        if not silent_on_error:
            log(f"   📊 Placing {side} Market Order: {amount} units")
        moa = MarketOrderArgs(token_id=token_id, amount=amount, side=side)
        signed = client.create_market_order(moa)
        resp: Any = client.post_order(signed, cast(Any, otype))
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
            log(f"❌ {side} Market Order error: {emsg}")
        return {"success": False, "status": "ERROR", "order_id": None, "error": emsg}


def check_liquidity(token_id: str, size: float, warn_threshold: float = 0.05) -> bool:
    spread = get_spread(token_id)
    if spread is None:
        return True
    if spread > warn_threshold:
        log(f"⚠️ Wide spread detected: {spread:.3f} - Low liquidity!")
        return False
    return True


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
            batch.append(
                PostOrdersArgs(order=signed, orderType=cast(Any, OrderType.GTC))
            )
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
        log(f"❌ Batch order error: {e}")
        return results


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
        params = cast(
            Any, OpenOrderParams(market=market or "", asset_id=asset_id or "")
        )
        orders = client.get_orders(params)
        return orders if isinstance(orders, list) else ([orders] if orders else [])
    except Exception as e:
        log(f"⚠️ Error fetching orders: {e}")
        return []


def cancel_order(order_id: str) -> bool:
    try:
        status = get_order_status(order_id)
        if status in ["FILLED", "CANCELED", "EXPIRED"]:
            return True
        resp = client.cancel(order_id)
        return resp == "OK" or (isinstance(resp, dict) and resp.get("status") == "OK")
    except Exception as e:
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


def sell_position(
    token_id: str,
    size: float,
    current_price: float,
    max_retries: int = 3,
    use_market_order: bool = True,
) -> dict:
    retry_delays = [2, 3, 5]
    for attempt in range(max_retries):
        try:
            if attempt > 0:
                log(
                    f"🔄 Retry {attempt} selling... waiting {retry_delays[attempt - 1]}s"
                )
                import time

                time.sleep(retry_delays[attempt - 1])
            if use_market_order:
                result = place_market_order(
                    token_id=token_id,
                    amount=size,
                    side=SELL,
                    order_type="FAK",
                    silent_on_error=(attempt < max_retries - 1),
                )
            else:
                sell_price = round(max(0.01, current_price - 0.01), 2)
                result = place_limit_order(
                    token_id=token_id,
                    price=sell_price,
                    size=size,
                    side=SELL,
                    silent_on_balance_error=(attempt < max_retries - 1),
                    order_type="FAK" if attempt == 0 else "GTC",
                )
            if result["success"]:
                return {
                    "success": True,
                    "sold": size,
                    "price": result.get("price", current_price),
                    "status": result["status"],
                    "order_id": result["order_id"],
                }
            err = result.get("error", "").lower()
            if "balance" in err and attempt < max_retries - 1:
                continue
            if (
                use_market_order
                and ("fok" in err or "no match" in err)
                and attempt < max_retries - 1
            ):
                continue
            if attempt == max_retries - 1:
                return {"success": False, "error": err}
        except Exception as e:
            if "balance" in str(e).lower() and attempt < max_retries - 1:
                continue
            if attempt == max_retries - 1:
                return {"success": False, "error": str(e)}
    return {"success": False, "error": "Max retries exceeded"}
