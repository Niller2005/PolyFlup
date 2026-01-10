"""Market analysis and signal divergence"""

import requests
from typing import Any
from src.config.settings import BINANCE_FUNDING_MAP
from .binance import _create_klines_dataframe


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
        close, open_p = pd.to_numeric(df["close"]), pd.to_numeric(df["open"])
        p_chg = ((close.iloc[-1] - open_p.iloc[0]) / open_p.iloc[0]) * 100.0

        # More aggressive probability mapping: 1% spot move = 20% prob move
        b_p_up = 0.5 + (p_chg / 5.0)
        b_p_up = max(0.01, min(0.99, b_p_up))

        div = polymarket_p_up - b_p_up

        return {
            "binance_direction": "UP"
            if p_chg > 0.05
            else "DOWN"
            if p_chg < -0.05
            else "NEUTRAL",
            "polymarket_direction": "UP"
            if polymarket_p_up > 0.52
            else "DOWN"
            if polymarket_p_up < 0.48
            else "NEUTRAL",
            "divergence": div,
            "opportunity": "BUY_UP"
            if div < -0.05
            else "BUY_DOWN"
            if div > 0.05
            else "NEUTRAL",
            "binance_price": float(close.iloc[-1]),
        }

    except:
        return {
            "binance_direction": "NEUTRAL",
            "polymarket_direction": "NEUTRAL",
            "divergence": 0.0,
            "opportunity": "NEUTRAL",
        }
