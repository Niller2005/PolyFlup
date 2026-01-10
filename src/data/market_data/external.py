"""External market sentiment and bias data"""

import requests
from src.config.settings import BINANCE_FUNDING_MAP

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
