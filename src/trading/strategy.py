"""Trading strategy logic"""

from py_clob_client.client import ClobClient
from src.config.settings import (
    MAX_SPREAD,
    ADX_ENABLED,
    BFXD_URL,
    MIN_EDGE,
    MOMENTUM_LOOKBACK_MINUTES,
    ENABLE_MOMENTUM_FILTER,
    ENABLE_ORDER_FLOW,
    ENABLE_DIVERGENCE,
    ENABLE_VWM,
    ENABLE_BFXD,
)
from src.utils.logger import log, log_error
from src.trading.orders.utils import is_404_error
from src.data.market_data import (
    get_funding_bias,
    get_fear_greed,
    get_adx_from_binance,
    get_price_momentum,
    get_order_flow_analysis,
    get_cross_exchange_divergence,
    get_volume_weighted_momentum,
    get_current_spot_price,
    get_polymarket_momentum,
)
import requests


def calculate_confidence(symbol: str, up_token: str, client: ClobClient):
    """
    Calculate confidence score and directional bias combining Polymarket and Binance data.
    Goal: Higher quality entries, fewer stop losses.

    Returns:
        tuple: (confidence, bias, p_up, best_bid, best_ask, signals)
        - confidence: 0.0 to 1.0 (sizing)
        - bias: "UP", "DOWN", or "NEUTRAL"
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
        if is_404_error(e):
            # Log the token ID occasionally or during 404 to help debug "wrong ID" vs "not ready"
            log(f"[{symbol}] Order book not ready for token {up_token[:10]}... (404)")
        else:
            log_error(f"[{symbol}] Order book error for {up_token}: {e}")
        return 0.0, "NEUTRAL", 0.5, None, None, {}

    if not bids or not asks:
        return 0.0, "NEUTRAL", 0.5, None, None, {}

    best_bid = float(
        bids[-1].price if hasattr(bids[-1], "price") else bids[-1].get("price", 0)
    )
    best_ask = float(
        asks[-1].price if hasattr(asks[-1], "price") else asks[-1].get("price", 0)
    )

    if not best_bid or not best_ask:
        return 0.0, "NEUTRAL", 0.5, best_bid, best_ask, {}

    spread = best_ask - best_bid
    if spread > MAX_SPREAD:
        return 0.0, "NEUTRAL", 0.5, best_bid, best_ask, {"reason": "spread_too_wide"}

    # Base Polymarket probability
    p_up = (best_bid + best_ask) / 2.0

    # 1. Price Momentum (Binance) - Weight: 0.35
    momentum_score = 0.0
    momentum_dir = "NEUTRAL"
    momentum = get_price_momentum(symbol, lookback_minutes=MOMENTUM_LOOKBACK_MINUTES)
    if momentum["direction"] != "NEUTRAL":
        momentum_score = momentum["strength"]
        momentum_dir = momentum["direction"]
        if momentum["acceleration"] > 0 and momentum_dir == "UP":
            momentum_score *= 1.2
        if momentum["acceleration"] < 0 and momentum_dir == "DOWN":
            momentum_score *= 1.2

    # 2. Order Flow (Binance) - Weight: 0.25
    flow_score = 0.0
    flow_dir = "NEUTRAL"
    order_flow = get_order_flow_analysis(symbol)
    flow_score = abs(order_flow["buy_pressure"] - 0.5) * 2.0  # 0 to 1
    flow_dir = "UP" if order_flow["buy_pressure"] > 0.5 else "DOWN"

    # 3. Divergence (Poly vs Binance) - Weight: 0.25
    divergence_score = 0.0
    divergence_dir = "NEUTRAL"
    divergence = get_cross_exchange_divergence(symbol, p_up)
    divergence_score = min(
        abs(divergence["divergence"]) * 5.0, 1.0
    )  # Scale 0.2 div to 1.0 score
    divergence_dir = (
        "UP"
        if divergence["opportunity"] == "BUY_UP"
        else "DOWN"
        if divergence["opportunity"] == "BUY_DOWN"
        else "NEUTRAL"
    )

    # 4. VWAP / VWM - Weight: 0.10
    vwm_score = 0.0
    vwm_dir = "NEUTRAL"
    vwm = get_volume_weighted_momentum(symbol)
    vwm_score = vwm["momentum_quality"]
    vwm_dir = "UP" if vwm["vwap_distance"] > 0 else "DOWN"

    # 5. Polymarket Native Momentum - Weight: 0.20 (New confirmed source)
    pm_mom_score = 0.0
    pm_mom_dir = "NEUTRAL"
    pm_momentum = get_polymarket_momentum(up_token)
    if pm_momentum["direction"] != "NEUTRAL":
        pm_mom_score = pm_momentum["strength"]
        pm_mom_dir = pm_momentum["direction"]

    # 5.1 Lead/Lag Indicator (Experimental)
    lead_lag_bonus = 1.0
    if momentum_dir != "NEUTRAL" and pm_mom_dir != "NEUTRAL":
        if momentum_dir == pm_mom_dir:
            # Both agree - strong signal
            lead_lag_bonus = 1.2
        else:
            # Divergence in momentum - cautionary
            lead_lag_bonus = 0.8

    # 6. ADX (Trend Strength) - Weight: 0.15
    adx_score = 0.0
    adx_dir = "NEUTRAL"
    adx_val = 0.0
    if ADX_ENABLED:
        adx_val = get_adx_from_binance(symbol)
        if adx_val > 0:
            # Normalize ADX (25-50 range maps to 0.5-1.0 score)
            adx_score = min(adx_val / 50.0, 1.0)
            # ADX follows the strongest current directional signal
            adx_dir = (
                momentum_dir
                if momentum_dir != "NEUTRAL"
                else pm_mom_dir
                if pm_mom_dir != "NEUTRAL"
                else divergence_dir
            )

    # Aggregate Scores for each direction
    up_total = 0.0
    down_total = 0.0

    # Adjust weights to include PM momentum
    adx_weight = 0.15 if ADX_ENABLED else 0.0
    mom_weight = 0.25 if ADX_ENABLED else 0.30
    pm_mom_weight = 0.15 if ADX_ENABLED else 0.20
    flow_weight = 0.15 if ADX_ENABLED else 0.20
    div_weight = 0.20 if ADX_ENABLED else 0.20
    vwm_weight = 0.10 if ADX_ENABLED else 0.10

    # Apply weights and directions
    for score, direction, weight in [
        (momentum_score, momentum_dir, mom_weight),
        (pm_mom_score, pm_mom_dir, pm_mom_weight),
        (flow_score, flow_dir, flow_weight),
        (divergence_score, divergence_dir, div_weight),
        (vwm_score, vwm_dir, vwm_weight),
        (adx_score, adx_dir, adx_weight),
    ]:
        if direction == "UP":
            up_total += score * weight
        elif direction == "DOWN":
            down_total += score * weight

    # Final Decision
    if up_total > down_total:
        bias = "UP"
        # Confirmation bonus: if Binance and PM both agree on direction
        if momentum_dir == pm_mom_dir and momentum_dir == "UP":
            up_total *= 1.1
        confidence = (up_total - (down_total * 0.5)) * lead_lag_bonus
    elif down_total > up_total:
        bias = "DOWN"
        if momentum_dir == pm_mom_dir and momentum_dir == "DOWN":
            down_total *= 1.1
        confidence = (down_total - (up_total * 0.5)) * lead_lag_bonus
    else:
        bias = "NEUTRAL"
        confidence = 0.0

    # Normalize confidence to 0-1
    confidence = max(0.0, min(1.0, confidence))

    # Get current spot price for entry logic
    current_spot = divergence.get("binance_price", 0)
    if current_spot == 0:
        current_spot = get_current_spot_price(symbol)

    signals = {
        "momentum": momentum,
        "pm_momentum": pm_momentum,
        "order_flow": order_flow,
        "divergence": divergence,
        "vwm": vwm,
        "adx": {"value": adx_val, "score": adx_score},
        "scores": {"up": up_total, "down": down_total},
        "current_spot": current_spot,
    }

    return confidence, bias, p_up, best_bid, best_ask, signals


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
