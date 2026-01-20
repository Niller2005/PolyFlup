"""Market information and pricing"""

import time
from datetime import datetime, timezone
from typing import List, Dict, Optional, Any
from py_clob_client.clob_types import BookParams, TradeParams
from src.utils.logger import log
from .client import client
from .utils import is_404_error

_last_midpoint_error_time = 0


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
        if is_404_error(e):
            return {}

        now = time.time()
        if now - _last_midpoint_error_time > 60:  # Log other errors once per minute
            log(f"⚠️  Error getting bulk midpoints (falling back to single calls): {e}")
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
        if not is_404_error(e):
            log(f"⚠️  Error getting midpoint for {token_id[:10]}...: {e}")
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

        if not is_404_error(e):
            log(f"⚠️  Error getting tick size for {token_id[:10]}...: {e}")
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
        if not is_404_error(e):
            log(f"⚠️  Error getting spread for {token_id[:10]}...: {e}")
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
        if not is_404_error(e):
            log(f"⚠️  Error getting bulk spreads: {e}")
        return {}


def get_server_time() -> Optional[int]:
    """Get current server timestamp"""
    try:
        timestamp = client.get_server_time()
        if isinstance(timestamp, (int, float)):
            return int(timestamp)
        return None
    except Exception as e:
        log(f"⚠️  Error getting server time: {e}")
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
        log(f"⚠️  Error getting trades: {e}")
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
        log(f"⚠️  Error getting user trades: {e}")
        return []


def check_liquidity(token_id: str, size: float, warn_threshold: float = 0.05) -> bool:
    spread = get_spread(token_id)
    if spread is None:
        return True
    if spread > warn_threshold:
        log(f"⚠️  Wide spread detected: {spread:.3f} - Low liquidity!")
        return False
    return True


def _check_fill_velocity(
    token_id: str, min_velocity: float, lookback_seconds: int
) -> tuple[bool, float]:
    """
    Check if token has sufficient recent fill activity (velocity).

    Prevents trading on illiquid markets that show orderbook depth but no actual fills.

    Args:
        token_id: Token ID to check
        min_velocity: Minimum shares filled per minute required
        lookback_seconds: How far back to look for trades (e.g., 120 = last 2 minutes)

    Returns:
        tuple[bool, float]:
            - is_sufficient: True if velocity meets minimum, False otherwise
            - actual_velocity: Measured shares per minute
    """
    if min_velocity <= 0:
        # Velocity check disabled
        return True, 0.0

    try:
        # Get recent trades for this token
        trades = get_trades(asset_id=token_id, limit=200)

        if not trades:
            # No recent trades = illiquid market
            return False, 0.0

        # Calculate cutoff timestamp
        now = datetime.now(timezone.utc)
        cutoff_time = now.timestamp() - lookback_seconds

        # Sum filled shares within lookback window
        total_filled = 0.0
        for trade in trades:
            # Parse trade timestamp (format varies by API)
            trade_ts = None
            if isinstance(trade, dict):
                # Try different timestamp field names
                ts_field = (
                    trade.get("timestamp")
                    or trade.get("created_at")
                    or trade.get("time")
                )
                if ts_field:
                    try:
                        # Handle both unix timestamp (int/float) and ISO string
                        if isinstance(ts_field, (int, float)):
                            trade_ts = float(ts_field)
                        else:
                            # Parse ISO string
                            dt = datetime.fromisoformat(
                                str(ts_field).replace("Z", "+00:00")
                            )
                            trade_ts = dt.timestamp()
                    except Exception:
                        continue

            if trade_ts and trade_ts >= cutoff_time:
                # Sum the size of this trade
                size = trade.get("size", 0) if isinstance(trade, dict) else 0
                total_filled += float(size) if size else 0.0

        # Calculate velocity (shares per minute)
        lookback_minutes = lookback_seconds / 60.0
        velocity = total_filled / lookback_minutes if lookback_minutes > 0 else 0.0

        return velocity >= min_velocity, velocity

    except Exception as e:
        log(f"   ⚠️  Fill velocity check error: {e}")
        # On error, fail-open (return True) since this is an additional safety check
        # The core depth/spread checks are still required
        return True, 0.0


def check_atomic_hedge_liquidity(
    entry_token_id: str, hedge_token_id: str, size: float
) -> tuple[bool, str, dict]:
    """
    Check if market has sufficient liquidity for atomic hedge to succeed.

    Pre-flight liquidity check to prevent catastrophic emergency liquidations.
    Checks orderbook depth, spread width, combined spread threshold, and fill velocity.

    Args:
        entry_token_id: Token ID for entry side
        hedge_token_id: Token ID for hedge side
        size: Trade size in shares

    Returns:
        tuple[bool, str, dict]:
            - is_liquid: True if sufficient liquidity, False to skip trade
            - skip_reason: Human-readable reason for skipping (empty if liquid)
            - metrics: Dict with spread/depth/velocity data for logging
    """
    from src.config.settings import (
        ENABLE_LIQUIDITY_CHECK,
        LIQUIDITY_MIN_DEPTH_RATIO,
        LIQUIDITY_MAX_SPREAD_PCT,
        LIQUIDITY_MAX_COMBINED_SPREAD_PCT,
        LIQUIDITY_MIN_FILL_VELOCITY,
        LIQUIDITY_FILL_LOOKBACK_SECONDS,
    )
    from src.utils.websocket_manager import ws_manager

    # Skip check if disabled
    if not ENABLE_LIQUIDITY_CHECK:
        return True, "", {}

    metrics = {
        "entry_spread": None,
        "hedge_spread": None,
        "combined_spread": None,
        "entry_depth": None,
        "hedge_depth": None,
        "entry_velocity": None,
        "hedge_velocity": None,
    }

    try:
        # Get bid/ask from websocket manager (faster than API)
        entry_bid, entry_ask = ws_manager.get_bid_ask(entry_token_id)
        hedge_bid, hedge_ask = ws_manager.get_bid_ask(hedge_token_id)

        # If websocket data unavailable, try API fallback
        if not all([entry_bid, entry_ask, hedge_bid, hedge_ask]):
            entry_spread_api = get_spread(entry_token_id)
            hedge_spread_api = get_spread(hedge_token_id)
            if entry_spread_api and hedge_spread_api:
                metrics["entry_spread"] = entry_spread_api
                metrics["hedge_spread"] = hedge_spread_api
                metrics["combined_spread"] = entry_spread_api + hedge_spread_api
            else:
                # No price data available - SKIP trade (fail-closed)
                # Critical: Without price data we cannot assess liquidity risk
                return (
                    False,
                    "No price data available (fail-closed for safety)",
                    metrics,
                )
        else:
            # Calculate spreads from bid/ask
            entry_spread = entry_ask - entry_bid
            hedge_spread = hedge_ask - hedge_bid
            metrics["entry_spread"] = entry_spread
            metrics["hedge_spread"] = hedge_spread
            metrics["combined_spread"] = entry_spread + hedge_spread

        # Check 1: Individual spread width (as percentage)
        entry_spread_pct = (
            (metrics["entry_spread"] / entry_ask) * 100 if entry_ask else 0
        )
        hedge_spread_pct = (
            (metrics["hedge_spread"] / hedge_ask) * 100 if hedge_ask else 0
        )

        if entry_spread_pct > LIQUIDITY_MAX_SPREAD_PCT:
            return (
                False,
                f"Entry spread too wide: {entry_spread_pct:.1f}% (max {LIQUIDITY_MAX_SPREAD_PCT}%)",
                metrics,
            )

        if hedge_spread_pct > LIQUIDITY_MAX_SPREAD_PCT:
            return (
                False,
                f"Hedge spread too wide: {hedge_spread_pct:.1f}% (max {LIQUIDITY_MAX_SPREAD_PCT}%)",
                metrics,
            )

        # Check 2: Combined spread threshold
        combined_spread_pct = (
            (metrics["combined_spread"] / (entry_ask + hedge_ask)) * 100
            if (entry_ask and hedge_ask)
            else 0
        )

        if combined_spread_pct > LIQUIDITY_MAX_COMBINED_SPREAD_PCT:
            return (
                False,
                f"Combined spread too wide: {combined_spread_pct:.1f}% (max {LIQUIDITY_MAX_COMBINED_SPREAD_PCT}%)",
                metrics,
            )

        # Check 3: Orderbook depth (need at least LIQUIDITY_MIN_DEPTH_RATIO of trade size)
        required_depth = size * LIQUIDITY_MIN_DEPTH_RATIO

        # Get orderbook depth from CLOB API
        try:
            entry_book = client.get_order_book(entry_token_id)
            hedge_book = client.get_order_book(hedge_token_id)

            # Parse entry book depth
            entry_depth = 0
            if entry_book:
                asks = (
                    entry_book.get("asks", [])
                    if isinstance(entry_book, dict)
                    else getattr(entry_book, "asks", [])
                )
                # Sum top 3 levels of ask depth
                for i, ask_level in enumerate(asks[:3]):
                    if isinstance(ask_level, dict):
                        entry_depth += float(ask_level.get("size", 0))
                    elif hasattr(ask_level, "size"):
                        entry_depth += float(ask_level.size)

            # Parse hedge book depth
            hedge_depth = 0
            if hedge_book:
                asks = (
                    hedge_book.get("asks", [])
                    if isinstance(hedge_book, dict)
                    else getattr(hedge_book, "asks", [])
                )
                # Sum top 3 levels of ask depth
                for i, ask_level in enumerate(asks[:3]):
                    if isinstance(ask_level, dict):
                        hedge_depth += float(ask_level.get("size", 0))
                    elif hasattr(ask_level, "size"):
                        hedge_depth += float(ask_level.size)

            metrics["entry_depth"] = entry_depth
            metrics["hedge_depth"] = hedge_depth

            # Check if depth sufficient
            if entry_depth < required_depth:
                return (
                    False,
                    f"Entry depth insufficient: {entry_depth:.1f} shares (need {required_depth:.1f})",
                    metrics,
                )

            if hedge_depth < required_depth:
                return (
                    False,
                    f"Hedge depth insufficient: {hedge_depth:.1f} shares (need {required_depth:.1f})",
                    metrics,
                )

        except Exception as depth_err:
            # Orderbook API failed - SKIP trade for safety (fail-closed)
            # Without depth data we cannot assess if hedge will fill
            if not is_404_error(depth_err):
                log(f"   ⚠️  Orderbook depth check failed: {depth_err}")
                return False, "Unable to check orderbook depth (fail-closed)", metrics

        # Check 4: Fill velocity (recent trading activity)
        # This prevents "liquidity mirages" where orderbook shows depth but no actual fills
        if LIQUIDITY_MIN_FILL_VELOCITY > 0:
            entry_sufficient, entry_velocity = _check_fill_velocity(
                entry_token_id,
                LIQUIDITY_MIN_FILL_VELOCITY,
                LIQUIDITY_FILL_LOOKBACK_SECONDS,
            )
            hedge_sufficient, hedge_velocity = _check_fill_velocity(
                hedge_token_id,
                LIQUIDITY_MIN_FILL_VELOCITY,
                LIQUIDITY_FILL_LOOKBACK_SECONDS,
            )

            metrics["entry_velocity"] = entry_velocity
            metrics["hedge_velocity"] = hedge_velocity

            if not entry_sufficient:
                return (
                    False,
                    f"Entry velocity too low: {entry_velocity:.1f} shares/min (need {LIQUIDITY_MIN_FILL_VELOCITY:.1f})",
                    metrics,
                )

            if not hedge_sufficient:
                return (
                    False,
                    f"Hedge velocity too low: {hedge_velocity:.1f} shares/min (need {LIQUIDITY_MIN_FILL_VELOCITY:.1f})",
                    metrics,
                )

        # All checks passed - log success for visibility
        velocity_str = ""
        if LIQUIDITY_MIN_FILL_VELOCITY > 0:
            entry_vel = metrics.get("entry_velocity", 0)
            hedge_vel = metrics.get("hedge_velocity", 0)
            velocity_str = (
                f", entry_vel={entry_vel:.1f}/min, hedge_vel={hedge_vel:.1f}/min"
            )

        log(
            f"   ✅ Liquidity check passed: "
            f"entry_spread={metrics.get('entry_spread', 'N/A'):.3f}, "
            f"hedge_spread={metrics.get('hedge_spread', 'N/A'):.3f}, "
            f"entry_depth={metrics.get('entry_depth', 'N/A'):.1f}, "
            f"hedge_depth={metrics.get('hedge_depth', 'N/A'):.1f}"
            f"{velocity_str}"
        )
        return True, "", metrics

    except Exception as e:
        # Fail-closed: if liquidity check crashes unexpectedly, skip trade for safety
        log(f"   ⚠️  Liquidity check error: {e}")
        return (
            False,
            "Liquidity check failed with unexpected error (fail-closed)",
            metrics,
        )
