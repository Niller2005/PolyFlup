"""Trading strategy logic"""

from py_clob_client.client import ClobClient
from src.config.settings import (
    MAX_SPREAD,
    ADX_ENABLED,
    ADX_THRESHOLD,
    BFXD_URL,
    MIN_EDGE,
    MOMENTUM_LOOKBACK_MINUTES,
    ENABLE_MOMENTUM_FILTER,
    ENABLE_ORDER_FLOW,
    ENABLE_DIVERGENCE,
    ENABLE_VWM,
    ENABLE_BFXD,
)
from src.utils.logger import log
from src.data.market_data import (
    get_funding_bias,
    get_fear_greed,
    get_adx_from_binance,
    get_price_momentum,
    get_order_flow_analysis,
    get_cross_exchange_divergence,
    get_volume_weighted_momentum,
)
import requests


def calculate_edge(symbol: str, up_token: str, client: ClobClient):
    """
    Calculate edge for trading decision combining Polymarket and Binance data.

    Returns:
        tuple: (edge, reason, p_up, best_bid, best_ask, imbalance, signals_dict)
    """
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
        return 0.5, "order book error", 0.5, None, None, 0.5, {}

    if not bids or not asks:
        return 0.5, "empty order book", 0.5, None, None, 0.5, {}

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
        return 0.5, "no bid/ask", 0.5, best_bid, best_ask, 0.5, {}

    spread = best_ask - best_bid
    if spread > MAX_SPREAD:
        log(f"[{symbol}] Spread too wide: {spread:.2%}")
        return 0.5, f"spread {spread:.2%}", 0.5, best_bid, best_ask, 0.5, {}

    # Base Polymarket probability
    p_up = (best_bid + best_ask) / 2.0
    imbalance_raw = best_bid - (1.0 - best_ask)
    imbalance = max(min((imbalance_raw + 0.1) / 0.2, 1.0), 0.0)

    # Start with base edge (40% price + 20% imbalance = 60% base)
    edge = 0.4 * p_up + 0.2 * imbalance

    # Initialize signals dictionary for logging
    signals = {}

    # ============================================================
    # NEW: BINANCE INTEGRATION (40% of edge calculation)
    # ============================================================

    # 1. Price Momentum (15% weight)
    momentum_adjustment = 0.0
    if ENABLE_MOMENTUM_FILTER:
        momentum = get_price_momentum(
            symbol, lookback_minutes=MOMENTUM_LOOKBACK_MINUTES
        )
        signals["momentum"] = momentum

        if momentum["direction"] == "UP":
            # Positive momentum -> increase UP probability
            momentum_adjustment = momentum["strength"] * 0.15
            if momentum["acceleration"] > 0:
                momentum_adjustment *= 1.2  # Bonus for accelerating trends
        elif momentum["direction"] == "DOWN":
            # Negative momentum -> decrease UP probability
            momentum_adjustment = -momentum["strength"] * 0.15
            if momentum["acceleration"] < 0:
                momentum_adjustment *= 1.2

        # RSI adjustment
        if momentum["rsi"] > 70:
            momentum_adjustment -= 0.02  # Overbought -> bearish
        elif momentum["rsi"] < 30:
            momentum_adjustment += 0.02  # Oversold -> bullish

        edge += momentum_adjustment

    # 2. Order Flow (10% weight)
    flow_adjustment = 0.0
    if ENABLE_ORDER_FLOW:
        order_flow = get_order_flow_analysis(symbol)
        signals["order_flow"] = order_flow

        # Buy pressure > 0.55 = bullish, < 0.45 = bearish
        flow_adjustment = (order_flow["buy_pressure"] - 0.5) * 0.2  # Max ±0.1
        edge += flow_adjustment

    # 3. Cross-Exchange Divergence (10% weight)
    divergence_adjustment = 0.0
    if ENABLE_DIVERGENCE:
        divergence = get_cross_exchange_divergence(symbol, p_up)
        signals["divergence"] = divergence

        # Negative divergence = Polymarket underpricing UP (buy UP opportunity)
        # Positive divergence = Polymarket overpricing UP (buy DOWN opportunity)
        divergence_adjustment = -divergence["divergence"] * 0.5  # Max ±0.05 typically
        edge += divergence_adjustment

    # 4. Volume-Weighted Momentum (5% weight)
    vwm_adjustment = 0.0
    if ENABLE_VWM:
        vwm = get_volume_weighted_momentum(symbol)
        signals["vwm"] = vwm

        # Price above VWAP with high quality = bullish
        if vwm["vwap_distance"] > 0.1:  # Above VWAP
            vwm_adjustment = vwm["momentum_quality"] * 0.05
        elif vwm["vwap_distance"] < -0.1:  # Below VWAP
            vwm_adjustment = -vwm["momentum_quality"] * 0.05

        edge += vwm_adjustment

    # ============================================================
    # EXISTING SIGNALS (keeping legacy funding + fear/greed)
    # ============================================================

    funding_bias = get_funding_bias(symbol)
    edge += funding_bias
    signals["funding_bias"] = funding_bias

    fg = get_fear_greed()
    signals["fear_greed"] = fg
    if fg < 30:
        edge += 0.02  # extreme fear -> bullish bias (reduced from 0.03)
    elif fg > 70:
        edge -= 0.02  # extreme greed -> bearish bias

    # Clamp edge to reasonable range
    edge = max(0.0, min(1.0, edge))

    return edge, "OK", p_up, best_bid, best_ask, imbalance, signals


def adx_allows_trade(symbol: str) -> tuple[bool, float]:
    """Check if ADX filter allows trade"""
    if not ADX_ENABLED:
        return True, -1.0

    adx_value = get_adx_from_binance(symbol)

    if adx_value < 0:
        return True, -1.0

    if adx_value >= ADX_THRESHOLD:
        return True, adx_value
    else:
        return False, adx_value


def bfxd_allows_trade(symbol: str, direction: str) -> tuple[bool, str]:
    """
    External BTC trend filter
    """
    if not ENABLE_BFXD:
        return True, "DISABLED"

    symbol_u = symbol.upper()
    direction_u = direction.upper()

    if not BFXD_URL:
        return True, "NO_URL"

    if symbol_u != "BTC":
        return True, "N/A"

    try:
        r = requests.get(BFXD_URL, timeout=5)
        r.raise_for_status()
        data = r.json()

        if not isinstance(data, dict):
            return True, "INVALID"

        trend = str(data.get("BTC/USDT", "")).upper()
        if not trend:
            return True, "NONE"

        if trend not in ("UP", "DOWN"):
            return True, f"UNK({trend})"

        match = trend == direction_u
        return match, trend

    except Exception as e:
        return True, f"ERR({str(e)[:10]})"
