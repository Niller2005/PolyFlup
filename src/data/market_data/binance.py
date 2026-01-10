"""Binance price data fetching"""

import requests
from typing import Dict, Tuple, Any
from datetime import datetime
from zoneinfo import ZoneInfo
from src.config.settings import BINANCE_FUNDING_MAP, WINDOW_START_PRICE_BUFFER_PCT

# Cache for window start prices
_window_start_prices: Dict[str, float] = {}

def _create_klines_dataframe(klines: Any) -> Any:
    """Create DataFrame from Binance klines data"""
    try:
        import pandas as pd

        if klines is None:
            return None
        cols: Any = [
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
            "quote_volume",
            "trades",
            "taker_buy_base",
            "taker_buy_quote",
            "ignore",
        ]
        return pd.DataFrame(klines, columns=cols)
    except:
        return None

def get_window_start_price(symbol: str) -> float:
    """Get the spot price at the ACTUAL START of the window"""
    now_utc = datetime.now(tz=ZoneInfo("UTC"))
    minute_slot = (now_utc.minute // 15) * 15
    window_start_utc = now_utc.replace(minute=minute_slot, second=0, microsecond=0)
    window_start_ts = int(window_start_utc.timestamp())
    cache_key = f"{symbol}_{window_start_ts}"
    if cache_key in _window_start_prices:
        return _window_start_prices[cache_key]
    pair = BINANCE_FUNDING_MAP.get(symbol.upper())
    if not pair:
        return -1.0
    lateness = (now_utc - window_start_utc).total_seconds()
    try:
        if lateness < 10:
            url = f"https://api.binance.com/api/v3/ticker/price?symbol={pair}"
            price = float(requests.get(url, timeout=5).json()["price"])
        else:
            url = f"https://api.binance.com/api/v3/klines?symbol={pair}&interval=1m&startTime={window_start_ts * 1000}&limit=1"
            klines = requests.get(url, timeout=5).json()
            if not klines:
                return -1.0
            price = float(klines[0][1])
        _window_start_prices[cache_key] = price
        if len(_window_start_prices) > 10:
            del _window_start_prices[min(_window_start_prices.keys())]
        return price
    except:
        return -1.0

def get_window_start_price_range(symbol: str) -> Tuple[float, float, float]:
    center_price = get_window_start_price(symbol)
    if center_price <= 0:
        return -1.0, -1.0, -1.0
    buffer = center_price * (WINDOW_START_PRICE_BUFFER_PCT / 100.0)
    return center_price, center_price - buffer, center_price + buffer

def get_current_spot_price(symbol: str) -> float:
    pair = BINANCE_FUNDING_MAP.get(symbol.upper())
    if not pair:
        return -1.0
    try:
        url = f"https://api.binance.com/api/v3/ticker/price?symbol={pair}"
        return float(requests.get(url, timeout=5).json()["price"])
    except:
        return -1.0
