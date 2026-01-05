"""Technical indicators and momentum calculations"""

import requests
from typing import Any
from src.config.settings import BINANCE_FUNDING_MAP, ADX_INTERVAL, ADX_PERIOD
from .binance import _create_klines_dataframe

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
        close: Any = pd.to_numeric(df["close"])
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
