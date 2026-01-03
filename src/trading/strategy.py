"""Trading strategy logic"""

from py_clob_client.client import ClobClient
from src.config.settings import (
    MAX_SPREAD,
    ADX_ENABLED,
    ADX_THRESHOLD,
    BFXD_URL,
    MIN_EDGE,
)
from src.utils.logger import log
from src.data.market_data import get_funding_bias, get_fear_greed, get_adx_from_binance
import requests


def calculate_edge(symbol: str, up_token: str, client: ClobClient):
    """Calculate edge for trading decision (UP leg as reference)"""
    try:
        book = client.get_order_book(up_token)
        if isinstance(book, dict):
            bids = book.get("bids", []) or []
            asks = book.get("asks", []) or []
        else:
            bids = getattr(book, "bids", []) or []
            asks = getattr(book, "asks", []) or []
    except Exception as e:
        log(f"[{symbol}] Order book error: {e}")
        return 0.5, "order book error", 0.5, None, None, 0.5

    if not bids or not asks:
        return 0.5, "empty order book", 0.5, None, None, 0.5

    best_bid = None
    best_ask = None

    if bids:
        best_bid = (
            float(bids[-1].price)
            if hasattr(bids[-1], "price")
            else float(bids[-1].get("price", 0))
        )
    if asks:
        best_ask = (
            float(asks[-1].price)
            if hasattr(asks[-1], "price")
            else float(asks[-1].get("price", 0))
        )

    if not best_bid or not best_ask:
        return 0.5, "no bid/ask", 0.5, best_bid, best_ask, 0.5

    spread = best_ask - best_bid
    if spread > MAX_SPREAD:
        log(f"[{symbol}] Spread too wide: {spread:.2%}")
        return 0.5, f"spread {spread:.2%}", 0.5, best_bid, best_ask, 0.5

    p_up = (best_bid + best_ask) / 2.0
    imbalance_raw = best_bid - (1.0 - best_ask)
    imbalance = max(min((imbalance_raw + 0.1) / 0.2, 1.0), 0.0)

    # 70% price + 30% imbalance
    edge = 0.7 * p_up + 0.3 * imbalance
    edge += get_funding_bias(symbol)

    fg = get_fear_greed()
    if fg < 30:
        edge += 0.03  # extreme fear -> bullish bias (UP)
    elif fg > 70:
        edge -= 0.03  # extreme greed -> bearish bias (DOWN)

    log(
        f"[{symbol}] Edge calculation: p_up={p_up:.4f} bid={best_bid:.4f} ask={best_ask:.4f} imb={imbalance:.4f} edge={edge:.4f}"
    )
    return edge, "OK", p_up, best_bid, best_ask, imbalance


def adx_allows_trade(symbol: str) -> bool:
    """Check if ADX filter allows trade"""
    if not ADX_ENABLED:
        log(f"[{symbol}]   ↳ ADX filter disabled")
        return True

    adx_value = get_adx_from_binance(symbol)

    if adx_value < 0:
        log(f"[{symbol}]   ↳ ADX calculation failed, allowing trade (fail-open)")
        return True

    if adx_value >= ADX_THRESHOLD:
        log(
            f"[{symbol}]   ↳ ADX={adx_value:.2f} >= {ADX_THRESHOLD:.2f} ✅ Strong trend"
        )
        return True
    else:
        log(f"[{symbol}]   ↳ ADX={adx_value:.2f} < {ADX_THRESHOLD:.2f} ❌ Weak trend")
        return False


def bfxd_allows_trade(symbol: str, direction: str) -> bool:
    """External BTC trend filter"""
    symbol_u = symbol.upper()
    direction_u = direction.upper()

    if not BFXD_URL:
        log(f"[{symbol}]   ↳ BFXD URL not set, skipping filter")
        return True

    if symbol_u != "BTC":
        log(f"[{symbol}]   ↳ BFXD only applies to BTC, skipping for {symbol_u}")
        return True

    try:
        r = requests.get(BFXD_URL, timeout=5)
        r.raise_for_status()
        data = r.json()

        if not isinstance(data, dict):
            log(f"[{symbol}]   ↳ BFXD invalid response, allowing trade")
            return True

        trend = str(data.get("BTC/USDT", "")).upper()
        if not trend:
            log(f"[{symbol}]   ↳ BFXD no trend data, allowing trade")
            return True

        if trend not in ("UP", "DOWN"):
            log(f"[{symbol}]   ↳ BFXD unknown trend '{trend}', allowing trade")
            return True

        match = trend == direction_u

        if match:
            log(f"[{symbol}]   ↳ BFXD trend={trend}, side={direction_u} ✅ Match")
            return True
        else:
            log(f"[{symbol}]   ↳ BFXD trend={trend}, side={direction_u} ❌ Mismatch")
            return False

    except Exception as e:
        log(f"[{symbol}]   ↳ BFXD error ({e}), allowing trade")
        return True
