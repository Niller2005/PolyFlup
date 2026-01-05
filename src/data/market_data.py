"""Market data fetching from APIs"""

import time
import json
import requests
from typing import Any, Tuple, Optional, Dict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from src.config.settings import (
    GAMMA_API_BASE,
    BINANCE_FUNDING_MAP,
    ADX_PERIOD,
    ADX_INTERVAL,
    WINDOW_START_PRICE_BUFFER_PCT,
)
from src.utils.logger import log

# Cache for window start prices
_window_start_prices: Dict[str, float] = {}


def _create_klines_dataframe(klines: Any) -> Any:
    """Create DataFrame from Binance klines data"""
    try:
        import pandas as pd

        if klines is None:
            return None
        cols = [
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
        return pd.DataFrame(klines, columns=cast(Any, cols))
    except:
        return None


def get_current_slug(symbol: str) -> str:
    """Generate slug for current 15-minute window"""
    now_et = datetime.now(tz=ZoneInfo("America/New_York"))
    minute_slot = (now_et.minute // 15) * 15
    window_start_et = now_et.replace(minute=minute_slot, second=0, microsecond=0)
    window_start_utc = window_start_et.astimezone(ZoneInfo("UTC"))
    ts = int(window_start_utc.timestamp())
    return f"{symbol.lower()}-updown-15m-{ts}"


def get_window_times(symbol: str):
    """Get window start and end times in ET"""
    now_et = datetime.now(tz=ZoneInfo("America/New_York"))
    minute_slot = (now_et.minute // 15) * 15
    window_start_et = now_et.replace(minute=minute_slot, second=0, microsecond=0)
    window_end_et = window_start_et + timedelta(minutes=15)
    return window_start_et, window_end_et


def get_token_ids(symbol: str):
    """Get UP and DOWN token IDs from Gamma API"""
    slug = get_current_slug(symbol)
    for attempt in range(1, 13):
        try:
            r = requests.get(f"{GAMMA_API_BASE}/markets/slug/{slug}", timeout=5)
            if r.status_code == 200:
                m = r.json()
                clob_ids = m.get("clobTokenIds") or m.get("clob_token_ids")
                if isinstance(clob_ids, str):
                    try:
                        clob_ids = json.loads(clob_ids)
                    except:
                        clob_ids = [
                            x.strip().strip('"')
                            for x in clob_ids.strip("[]").split(",")
                        ]
                if isinstance(clob_ids, list) and len(clob_ids) >= 2:
                    return clob_ids[0], clob_ids[1]
        except:
            pass
        if attempt < 12:
            time.sleep(4)
    return None, None


def get_funding_bias(symbol: str) -> float:
    """Get funding rate bias from Binance futures"""
    pair = BINANCE_FUNDING_MAP.get(symbol)
    if not pair:
        return 0.0
    try:
        url = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={pair}"
        return float(requests.get(url, timeout=5).json()["lastFundingRate"]) * 1000.0
    except:
        return 0.0


def get_fear_greed() -> int:
    """Get Fear & Greed Index"""
    try:
        return int(
            requests.get("https://api.alternative.me/fng/", timeout=5).json()["data"][
                0
            ]["value"]
        )
    except:
        return 50


def get_polymarket_momentum(token_id: str, interval: str = "1m") -> dict:
    """Calculate momentum based on Polymarket's own price history"""
    try:
        from src.config.settings import CLOB_HOST

        url = f"{CLOB_HOST}/prices-history"
        params = {"interval": interval, "token_id": token_id}
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        history = resp.json()
        if not history or not isinstance(history, list) or len(history) < 5:
            return {"velocity": 0.0, "direction": "NEUTRAL", "strength": 0.0}
        prices = []
        for h in history:
            p = h.get("p") or h.get("price")
            if p is not None:
                prices.append(float(p))
        if len(prices) < 5:
            return {"velocity": 0.0, "direction": "NEUTRAL", "strength": 0.0}
        velocity = (
            ((prices[-1] - prices[0]) / prices[0]) * 100.0 if prices[0] > 0 else 0
        )
        direction = (
            "UP" if velocity > 0.005 else "DOWN" if velocity < -0.005 else "NEUTRAL"
        )
        return {
            "velocity": velocity,
            "direction": direction,
            "strength": min(abs(velocity) * 20, 1.0),
            "last_price": prices[-1],
        }
    except:
        return {"velocity": 0.0, "direction": "NEUTRAL", "strength": 0.0}


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


def get_adx_from_binance(symbol: str) -> float:
    """Calculate ADX for symbol/USDT pair"""
    try:
        import pandas as pd
        from ta.trend import ADXIndicator

        pair = BINANCE_FUNDING_MAP.get(symbol.upper())
        if not pair:
            return -1.0
        url = f"https://api.binance.com/api/v3/klines?symbol={pair}&interval={ADX_INTERVAL}&limit={ADX_PERIOD * 3 + 10}"
        klines = requests.get(url, timeout=10).json()
        df: Any = _create_klines_dataframe(klines)
        if df is None:
            return -1.0
        adx_indicator = ADXIndicator(
            high=pd.to_numeric(df["high"]),
            low=pd.to_numeric(df["low"]),
            close=pd.to_numeric(df["close"]),
            window=ADX_PERIOD,
        )
        return float(adx_indicator.adx().iloc[-1])
    except:
        return -1.0


def get_price_momentum(symbol: str, lookback_minutes: int = 15) -> dict:
    """Calculate price momentum from Binance spot data"""
    try:
        import pandas as pd
        from ta.momentum import RSIIndicator

        pair = BINANCE_FUNDING_MAP.get(symbol.upper())
        if not pair:
            return {
                "velocity": 0.0,
                "acceleration": 0.0,
                "rsi": 50.0,
                "direction": "NEUTRAL",
                "strength": 0.0,
            }
        url = f"https://api.binance.com/api/v3/klines?symbol={pair}&interval=1m&limit={max(30, lookback_minutes + 20)}"
        klines = requests.get(url, timeout=10).json()
        df: Any = _create_klines_dataframe(klines)
        if df is None or len(df) < lookback_minutes:
            return {
                "velocity": 0.0,
                "acceleration": 0.0,
                "rsi": 50.0,
                "direction": "NEUTRAL",
                "strength": 0.0,
            }
        close = pd.to_numeric(df["close"])
        vel = (
            (close.iloc[-1] - close.iloc[-lookback_minutes])
            / close.iloc[-lookback_minutes]
        ) * 100.0
        rsi = (
            float(RSIIndicator(close=close, window=14).rsi().iloc[-1])
            if len(close) >= 15
            else 50.0
        )
        return {
            "velocity": vel,
            "acceleration": 0.0,
            "rsi": rsi,
            "direction": "UP" if vel > 0 else "DOWN" if vel < 0 else "NEUTRAL",
            "strength": min(abs(vel) / 2.0, 1.0),
        }
    except:
        return {
            "velocity": 0.0,
            "acceleration": 0.0,
            "rsi": 50.0,
            "direction": "NEUTRAL",
            "strength": 0.0,
        }


def get_order_flow_analysis(symbol: str) -> dict:
    """Analyze Binance order flow"""
    pair = BINANCE_FUNDING_MAP.get(symbol.upper())
    if not pair:
        return {
            "buy_pressure": 0.5,
            "volume_ratio": 0.5,
            "large_trade_direction": "NEUTRAL",
            "trade_intensity": 0.0,
        }
    try:
        import pandas as pd

        url = f"https://api.binance.com/api/v3/klines?symbol={pair}&interval=1m&limit=5"
        df: Any = _create_klines_dataframe(requests.get(url, timeout=10).json())
        if df is None:
            return {
                "buy_pressure": 0.5,
                "volume_ratio": 0.5,
                "large_trade_direction": "NEUTRAL",
                "trade_intensity": 0.0,
            }
        vol, t_buy = (
            pd.to_numeric(df["volume"]).sum(),
            pd.to_numeric(df["taker_buy_base"]).sum(),
        )
        ratio = t_buy / vol if vol > 0 else 0.5
        return {
            "buy_pressure": ratio,
            "volume_ratio": ratio,
            "large_trade_direction": "BUY"
            if ratio > 0.55
            else "SELL"
            if ratio < 0.45
            else "NEUTRAL",
            "trade_intensity": pd.to_numeric(df["trades"]).mean(),
        }
    except:
        return {
            "buy_pressure": 0.5,
            "volume_ratio": 0.5,
            "large_trade_direction": "NEUTRAL",
            "trade_intensity": 0.0,
        }


def get_cross_exchange_divergence(symbol: str, polymarket_p_up: float) -> dict:
    """Compare Polymarket vs Binance movement"""
    pair = BINANCE_FUNDING_MAP.get(symbol.upper())
    if not pair:
        return {
            "binance_direction": "NEUTRAL",
            "polymarket_direction": "NEUTRAL",
            "divergence": 0.0,
            "opportunity": "NEUTRAL",
        }
    try:
        import pandas as pd

        url = (
            f"https://api.binance.com/api/v3/klines?symbol={pair}&interval=1m&limit=15"
        )
        df: Any = _create_klines_dataframe(requests.get(url, timeout=10).json())
        if df is None or len(df) < 10:
            return {
                "binance_direction": "NEUTRAL",
                "polymarket_direction": "NEUTRAL",
                "divergence": 0.0,
                "opportunity": "NEUTRAL",
            }
        p_chg = (
            (pd.to_numeric(df["close"]).iloc[-1] - pd.to_numeric(df["open"]).iloc[0])
            / pd.to_numeric(df["open"]).iloc[0]
        ) * 100.0
        b_p_up = 0.5 + (0.05 if p_chg > 0.5 else -0.05 if p_chg < -0.5 else 0)
        div = polymarket_p_up - b_p_up
        return {
            "binance_direction": "UP"
            if p_chg > 0.5
            else "DOWN"
            if p_chg < -0.5
            else "NEUTRAL",
            "polymarket_direction": "UP"
            if polymarket_p_up > 0.55
            else "DOWN"
            if polymarket_p_up < 0.45
            else "NEUTRAL",
            "divergence": div,
            "opportunity": "BUY_UP"
            if div < -0.1
            else "BUY_DOWN"
            if div > 0.1
            else "NEUTRAL",
        }
    except:
        return {
            "binance_direction": "NEUTRAL",
            "polymarket_direction": "NEUTRAL",
            "divergence": 0.0,
            "opportunity": "NEUTRAL",
        }


def get_volume_weighted_momentum(symbol: str) -> dict:
    """Calculate volume-weighted indicators"""
    pair = BINANCE_FUNDING_MAP.get(symbol.upper())
    if not pair:
        return {"vwap_distance": 0.0, "volume_trend": "STABLE", "momentum_quality": 0.0}
    try:
        import pandas as pd

        url = (
            f"https://api.binance.com/api/v3/klines?symbol={pair}&interval=1m&limit=15"
        )
        df: Any = _create_klines_dataframe(requests.get(url, timeout=10).json())
        if df is None:
            return {
                "vwap_distance": 0.0,
                "volume_trend": "STABLE",
                "momentum_quality": 0.0,
            }
        c, h, l, v = (
            pd.to_numeric(df["close"]),
            pd.to_numeric(df["high"]),
            pd.to_numeric(df["low"]),
            pd.to_numeric(df["volume"]),
        )
        tp = (h + l + c) / 3
        vwap = (tp * v).sum() / v.sum() if v.sum() > 0 else c.iloc[-1]
        return {
            "vwap_distance": ((c.iloc[-1] - vwap) / vwap) * 100.0,
            "volume_trend": "STABLE",
            "momentum_quality": 0.0,
        }
    except:
        return {"vwap_distance": 0.0, "volume_trend": "STABLE", "momentum_quality": 0.0}
