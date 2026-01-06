"""Market information and pricing"""

import time
from typing import List, Dict, Optional, Any
from py_clob_client.clob_types import BookParams, TradeParams
from src.utils.logger import log
from .client import client

_last_midpoint_error_time = 0


def _is_404_error(e: Exception) -> bool:
    """Check if the exception is a 404/No Orderbook error"""
    err_str = str(e).lower()
    return "404" in err_str or "no orderbook" in err_str or "not found" in err_str


def get_multiple_market_prices(token_ids: List[str]) -> Dict[str, float]:
    """Get market prices for multiple tokens in a single call"""
    global _last_midpoint_error_time
    if not token_ids:
        return {}
    try:
        # Use BookParams for consistency with other bulk methods
        params = [BookParams(token_id=str(tid)) for tid in token_ids]
        resp: Any = client.get_midpoints(params)
        result = {}
        if isinstance(resp, dict):
            for tid, val in resp.items():
                if val is not None:
                    result[str(tid)] = float(val)
        elif isinstance(resp, list):
            for item in resp:
                tid = (
                    item.get("asset_id")
                    if isinstance(item, dict)
                    else getattr(item, "asset_id", None)
                )
                mid = (
                    item.get("mid")
                    if isinstance(item, dict)
                    else getattr(item, "mid", None)
                )
                if tid and mid is not None:
                    result[str(tid)] = float(mid)
        return result
    except Exception as e:
        if _is_404_error(e):
            return {}

        now = time.time()
        if now - _last_midpoint_error_time > 60:  # Log other errors once per minute
            log(f"⚠️ Error getting bulk midpoints (falling back to single calls): {e}")
            _last_midpoint_error_time = now
        return {}


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
        if not _is_404_error(e):
            log(f"⚠️ Error getting midpoint for {token_id[:10]}...: {e}")
        return None


def get_tick_size(token_id: str) -> float:
    """Get the minimum tick size for a token"""
    try:
        tick_size = client.get_tick_size(token_id)
        if tick_size:
            return float(tick_size)
        from .constants import MIN_TICK_SIZE

        return MIN_TICK_SIZE
    except Exception as e:
        from .constants import MIN_TICK_SIZE

        if not _is_404_error(e):
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
        if not _is_404_error(e):
            log(f"⚠️ Error getting spread for {token_id[:10]}...: {e}")
        return None


def get_bulk_spreads(token_ids: List[str]) -> Dict[str, float]:
    """Get spreads for multiple tokens in a single call"""
    if not token_ids:
        return {}
    try:
        params = [BookParams(token_id=str(tid)) for tid in token_ids]
        resp: Any = client.get_spreads(params)
        result = {}
        if isinstance(resp, dict):
            for tid, val in resp.items():
                if val is not None:
                    result[str(tid)] = float(val)
        elif isinstance(resp, list):
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
                    result[str(tid)] = float(spread)
        return result
    except Exception as e:
        if not _is_404_error(e):
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
        params = TradeParams(market=market or "", asset_id=asset_id or "")
        trades = client.get_trades(params)
        if not isinstance(trades, list):
            trades = [trades] if trades else []
        return trades[:limit]
    except Exception as e:
        log(f"⚠️ Error getting trades: {e}")
        return []


def get_trades_for_user(
    user: str,
    market: Optional[str] = None,
    asset_id: Optional[str] = None,
    limit: int = 100,
) -> List[dict]:
    """Get trade history for a specific user from Data API"""
    try:
        from src.config.settings import DATA_API_BASE
        import requests

        url = f"{DATA_API_BASE}/trades?user={user}"
        if market:
            url += f"&market={market}"
        if asset_id:
            url += f"&asset_id={asset_id}"
        if limit:
            url += f"&limit={limit}"

        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        return data.get("trades", []) if isinstance(data, dict) else []
    except Exception as e:
        log(f"⚠️ Error getting user trades: {e}")
        return []


def check_liquidity(token_id: str, size: float, warn_threshold: float = 0.05) -> bool:
    spread = get_spread(token_id)
    if spread is None:
        return True
    if spread > warn_threshold:
        log(f"⚠️ Wide spread detected: {spread:.3f} - Low liquidity!")
        return False
    return True
