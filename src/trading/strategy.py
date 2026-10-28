"""Trading strategy logic"""

from py_clob_client_v2 import ClobClient
from src.markets import asset_of
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
    ENABLE_PRICE_VALIDATION,
    PRICE_VALIDATION_MAX_MOVEMENT,
    PRICE_VALIDATION_MIN_CONFIDENCE,
    BAYESIAN_CONFIDENCE,
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
    validate_price_movement_for_trade,
)
import math
import requests


_NEUTRAL_MOMENTUM = {"direction": "NEUTRAL", "strength": 0.0, "acceleration": 0.0, "rsi": 50.0}
_NEUTRAL_FLOW = {"buy_pressure": 0.5, "large_trade_direction": "NEUTRAL", "trade_intensity": 0.0}
_NEUTRAL_DIVERGENCE = {"divergence": 0.0, "opportunity": "NEUTRAL", "binance_price": 0}
_NEUTRAL_VWM = {"momentum_quality": 0.0, "vwap_distance": 0.0}
_NEUTRAL_PM = {"direction": "NEUTRAL", "strength": 0.0}
_NEUTRAL_RESULT = (0.0, "NEUTRAL")


def _num(x, default=0.0, lo=None, hi=None) -> float:
    """float(x), or `default` for None / NaN / inf / garbage; optionally clamped.

    NaN is the dangerous one: `min(1.0, nan)` is 1.0 in Python, so an unsanitised NaN score would
    turn into a *maximum* confidence.
    """
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(v):
        return default
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


def _dir(x) -> str:
    return x if x in ("UP", "DOWN") else "NEUTRAL"


def _sign_dir(v: float, eps: float = 1e-12) -> str:
    return "UP" if v > eps else "DOWN" if v < -eps else "NEUTRAL"


def _best_prices(bids, asks):
    """(best_bid, best_ask) from raw book levels in ANY order (V2 books are worst-first)."""

    def prices(levels):
        out = []
        for lv in levels or []:
            raw = lv.price if hasattr(lv, "price") else (lv.get("price") if isinstance(lv, dict) else None)
            v = _num(raw, 0.0)
            if 0.0 < v < 1.0:
                out.append(v)
        return out

    b, a = prices(bids), prices(asks)
    return (max(b) if b else None), (min(a) if a else None)


def momentum_quality(direction: str, rsi: float, strength: float) -> float:
    """Quality multiplier of the momentum signal (0.8 - 1.43).

    Symmetric for both directions: an RSI extreme IN the direction of momentum means the move is
    stretched (exhaustion -> 0.8); an RSI extreme AGAINST it means there is room to run (1.2).
    """
    if direction not in ("UP", "DOWN"):
        return 1.0
    q = 1.0
    if direction == "UP":
        q = 0.8 if rsi > 70 else 1.2 if rsi < 30 else 1.0
    else:
        q = 0.8 if rsi < 30 else 1.2 if rsi > 70 else 1.0
    return q * (1.1 if strength > 0.8 else 1.0)


def score_signals(p_up: float, sig: dict, adx_enabled: bool, use_bayesian: bool) -> dict:
    """Pure combination of sanitised signals into additive + Bayesian confidence (unit-tested).

    `sig` keys: momentum, order_flow, divergence, vwm, pm_momentum (dicts), adx (float).
    Every score is in [0, 1]; a score is evidence FOR its direction only (a weak signal is weak
    evidence, never evidence against - the old (score-0.5)*2 formula turned e.g. 52% buy pressure
    into a vote for DOWN).
    """
    momentum, order_flow, divergence = sig["momentum"], sig["order_flow"], sig["divergence"]
    vwm, pm_momentum, adx_val = sig["vwm"], sig["pm_momentum"], _num(sig["adx"], 0.0, 0.0)

    # 1. Price momentum (Binance)
    momentum_dir = _dir(momentum.get("direction"))
    momentum_score = _num(momentum.get("strength"), 0.0, 0.0, 1.0) if momentum_dir != "NEUTRAL" else 0.0
    accel = _num(momentum.get("acceleration"), 0.0)
    if (accel > 0 and momentum_dir == "UP") or (accel < 0 and momentum_dir == "DOWN"):
        momentum_score = min(1.0, momentum_score * 1.2)

    # 2. Order flow: 55% / 45% buy pressure = full strength
    bp = _num(order_flow.get("buy_pressure"), 0.5, 0.0, 1.0)
    flow_score = min(abs(bp - 0.5) * 10.0, 1.0)
    flow_dir = _sign_dir(bp - 0.5)

    # 3. Divergence (Polymarket vs Binance): 0.1 = full strength
    div_val = _num(divergence.get("divergence"), 0.0)
    divergence_score = min(abs(div_val) * 10.0, 1.0)
    opp = divergence.get("opportunity")
    divergence_dir = "UP" if opp == "BUY_UP" else "DOWN" if opp == "BUY_DOWN" else "NEUTRAL"

    # 4. VWAP / volume-weighted momentum
    vwm_score = _num(vwm.get("momentum_quality"), 0.0, 0.0, 1.0)
    vwm_dir = _sign_dir(_num(vwm.get("vwap_distance"), 0.0))

    # 5. Polymarket native momentum
    pm_dir = _dir(pm_momentum.get("direction"))
    pm_score = _num(pm_momentum.get("strength"), 0.0, 0.0, 1.0) if pm_dir != "NEUTRAL" else 0.0

    lead_lag_bonus = 1.0
    if momentum_dir != "NEUTRAL" and pm_dir != "NEUTRAL":
        lead_lag_bonus = 1.2 if momentum_dir == pm_dir else 0.8

    # 6. ADX trend strength follows the strongest directional signal
    adx_score, adx_dir = 0.0, "NEUTRAL"
    if adx_enabled and adx_val > 0:
        adx_score = min(adx_val / 50.0, 1.0)
        adx_dir = momentum_dir if momentum_dir != "NEUTRAL" else pm_dir if pm_dir != "NEUTRAL" else divergence_dir

    adx_weight = 0.15 if adx_enabled else 0.0
    mom_weight = 0.25 if adx_enabled else 0.30
    pm_weight = 0.20 if adx_enabled else 0.25
    flow_weight = 0.15
    div_weight = 0.20
    vwm_weight = 0.05 if adx_enabled else 0.10

    mom_quality = momentum_quality(momentum_dir, _num(momentum.get("rsi"), 50.0), momentum_score)
    flow_quality = 1.0
    if flow_dir != "NEUTRAL":
        if abs(bp - 0.5) > 0.20:  # symmetric: 70%+ buying and 30%- buying are equally extreme
            flow_quality = 1.3
        elif _dir(order_flow.get("large_trade_direction")) != "NEUTRAL" and abs(bp - 0.5) > 0.10:
            flow_quality = 1.1
        if _num(order_flow.get("trade_intensity"), 0.0) > 0.5:
            flow_quality *= 1.05
    divergence_quality = 1.0
    if divergence_dir != "NEUTRAL":
        divergence_quality = 1.0 + min(abs(div_val), 0.3)
        if opp != "NEUTRAL":
            divergence_quality *= 1.15
        elif abs(div_val) < 0.05:
            divergence_quality *= 0.8
    vwm_quality = 0.8 + vwm_score * 0.5
    if adx_score > 0:
        adx_quality = (
            1.3 if adx_val > 40 else 1.15 if adx_val > 30 else 1.05 if adx_val > 25
            else 0.9 if adx_val > 20 else 0.8 if adx_val > 15 else 0.7
        )
    else:
        adx_quality = 1.0

    parts = [
        (momentum_score, momentum_dir, mom_weight, mom_quality),
        (pm_score, pm_dir, pm_weight, 1.0),
        (flow_score, flow_dir, flow_weight, flow_quality),
        (divergence_score, divergence_dir, div_weight, divergence_quality),
        (vwm_score, vwm_dir, vwm_weight, vwm_quality),
        (adx_score, adx_dir, adx_weight, adx_quality),
    ]
    up_total = sum(s * w * q for s, d, w, q in parts if d == "UP")
    down_total = sum(s * w * q for s, d, w, q in parts if d == "DOWN")

    # Additive confidence
    if up_total > down_total:
        additive_bias = "UP"
        boosted = up_total * (1.1 if momentum_dir == pm_dir == "UP" else 1.0)
        additive_confidence = (boosted - down_total * 0.2) * lead_lag_bonus
    elif down_total > up_total:
        additive_bias = "DOWN"
        boosted = down_total * (1.1 if momentum_dir == pm_dir == "DOWN" else 1.0)
        additive_confidence = (boosted - up_total * 0.2) * lead_lag_bonus
    else:
        additive_bias, additive_confidence = "NEUTRAL", 0.0
    additive_confidence = _num(additive_confidence, 0.0, 0.0, 1.0)

    # Bayesian confidence: market prior + signal evidence (log-odds)
    p_prior = _num(p_up, 0.5, 0.02, 0.98)
    prior_log_odds = math.log(p_prior / (1.0 - p_prior))
    evidence = 0.0
    for s, d, w, q in parts:
        if d == "NEUTRAL":
            continue
        llr = max(0.0, (s - 0.5) * 2.0) * 3.0 * q  # strong signals only; below 0.5 = no evidence either way
        evidence += (llr if d == "UP" else -llr) * w
    log_odds = prior_log_odds + evidence
    prob_up = 1.0 / (1.0 + math.exp(-log_odds))
    bayesian_confidence = _num(abs(prob_up - 0.5) * 2.0 * lead_lag_bonus, 0.0, 0.0, 1.0)
    bayesian_bias = _sign_dir(log_odds, 1e-9)

    key = [
        (momentum_score, momentum_dir), (pm_score, pm_dir), (flow_score, flow_dir),
        (divergence_score, divergence_dir), (adx_score, adx_dir),
    ]

    def confirm(confidence: float, bias: str) -> float:
        """Multi-confirmation (above 60% need >= 3 of the 5 key signals strongly aligned) and the 85% cap."""
        if confidence > 0.60:
            aligned = sum(1 for s, d in key if s > 0.5 and d == bias and d != "NEUTRAL")
            if aligned < 3:
                factor = (confidence - 0.60) / 0.25  # 0 at 60%, 1 at 85%
                confidence = max(0.60, confidence - (3 - aligned) * 0.10 * factor)
        return _num(min(confidence, 0.85), 0.0, 0.0, 1.0)

    additive_final = confirm(additive_confidence, additive_bias)
    bayesian_final = confirm(bayesian_confidence, bayesian_bias)
    confidence, bias = (bayesian_final, bayesian_bias) if use_bayesian else (additive_final, additive_bias)

    return {
        "confidence": confidence, "bias": bias,
        "additive_confidence": additive_final, "additive_bias": additive_bias,
        "bayesian_confidence": bayesian_final, "bayesian_bias": bayesian_bias,
        "additive_raw": additive_confidence, "bayesian_raw": bayesian_confidence,
        "bayesian_evidence": evidence, "prior_log_odds": prior_log_odds,
        "up_total": up_total, "down_total": down_total, "lead_lag_bonus": lead_lag_bonus,
        "momentum_score": momentum_score, "momentum_dir": momentum_dir,
        "flow_score": flow_score, "flow_dir": flow_dir,
        "divergence_score": divergence_score, "divergence_dir": divergence_dir,
        "vwm_score": vwm_score, "vwm_dir": vwm_dir,
        "pm_mom_score": pm_score, "pm_mom_dir": pm_dir,
        "adx_score": adx_score, "adx_dir": adx_dir, "adx_val": adx_val,
    }


def calculate_confidence(symbol: str, up_token: str, client: ClobClient):
    """
    Calculate confidence score and directional bias combining Polymarket and Binance data.

    Robustness: book levels are parsed order-independently and a crossed/locked/empty book is NEUTRAL;
    every data source is fetched defensively (a failing source counts as missing/neutral, three or more
    missing sources = NEUTRAL, fewer scale the confidence down); all inputs are sanitised (NaN/None/inf)
    and every score is clamped to [0, 1] before `score_signals` combines them.

    Returns:
        tuple: (confidence, bias, p_up, best_bid, best_ask, signals, raw_scores)
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
            log(f"[{symbol}] Order book not ready for token {up_token[:10]}... (404)")
        else:
            log_error(f"[{symbol}] Order book error for {up_token}: {e}")
        return 0.0, "NEUTRAL", 0.5, None, None, {}, {}

    best_bid, best_ask = _best_prices(bids, asks)
    if best_bid is None or best_ask is None:
        return 0.0, "NEUTRAL", 0.5, best_bid, best_ask, {"reason": "empty_book"}, {}
    if best_bid >= best_ask:
        return 0.0, "NEUTRAL", 0.5, best_bid, best_ask, {"reason": "crossed_book"}, {}
    if best_ask - best_bid > MAX_SPREAD:
        return 0.0, "NEUTRAL", 0.5, best_bid, best_ask, {"reason": "spread_too_wide"}, {}

    p_up = (best_bid + best_ask) / 2.0
    missing: list = []

    def fetch(name, fn, default):
        try:
            v = fn()
            if not isinstance(default, dict):
                return v
            return v if isinstance(v, dict) else (missing.append(name) or default)
        except Exception as e:
            missing.append(name)
            log_error(f"[{symbol}] signal source {name} failed: {e}")
            return default

    momentum = fetch("momentum", lambda: get_price_momentum(symbol, lookback_minutes=MOMENTUM_LOOKBACK_MINUTES), _NEUTRAL_MOMENTUM)
    order_flow = fetch("order_flow", lambda: get_order_flow_analysis(symbol), _NEUTRAL_FLOW)
    divergence = fetch("divergence", lambda: get_cross_exchange_divergence(symbol, p_up), _NEUTRAL_DIVERGENCE)
    vwm = fetch("vwm", lambda: get_volume_weighted_momentum(symbol), _NEUTRAL_VWM)
    pm_momentum = fetch("pm_momentum", lambda: get_polymarket_momentum(up_token), _NEUTRAL_PM)
    adx_val = fetch("adx", lambda: get_adx_from_binance(symbol), 0.0) if ADX_ENABLED else 0.0

    if len(missing) >= 3:
        log(f"[{symbol}] ⚪ {len(missing)} signal sources unavailable ({', '.join(missing)}) - NEUTRAL")
        return 0.0, "NEUTRAL", p_up, best_bid, best_ask, {"reason": "insufficient_data", "missing": missing}, {}

    r = score_signals(
        p_up,
        {"momentum": momentum, "order_flow": order_flow, "divergence": divergence, "vwm": vwm,
         "pm_momentum": pm_momentum, "adx": adx_val},
        ADX_ENABLED, BAYESIAN_CONFIDENCE,
    )
    current_spot = _num(divergence.get("binance_price"), 0.0)
    if current_spot == 0:
        current_spot = _num(get_current_spot_price(symbol), 0.0)

    signals = {
        "momentum": momentum, "pm_momentum": pm_momentum, "order_flow": order_flow,
        "divergence": divergence, "vwm": vwm,
        "adx": {"value": r["adx_val"], "score": r["adx_score"]},
        "scores": {"up": r["up_total"], "down": r["down_total"]},
        "current_spot": current_spot, "missing": missing,
    }

    def finalize(confidence: float, bias: str, verbose: bool) -> tuple:
        """Everything that happens AFTER the method's own confidence, applied identically to both
        methods (the caller may pick either at random for the A/B test and must get the final value):
        scale down for missing sources, then price-movement validation of high-confidence trades."""
        confidence = _num(confidence, 0.0, 0.0, 1.0)
        if missing:
            confidence *= 1.0 - 0.15 * len(missing)
        if ENABLE_PRICE_VALIDATION and confidence >= PRICE_VALIDATION_MIN_CONFIDENCE:
            try:
                res = validate_price_movement_for_trade(
                    symbol=symbol, confidence=confidence, current_spot=current_spot,
                    max_movement_threshold=PRICE_VALIDATION_MAX_MOVEMENT,
                    min_confidence_threshold=PRICE_VALIDATION_MIN_CONFIDENCE,
                )
                adjusted = _num(res.get("adjusted_confidence"), confidence, 0.0, 1.0)
            except Exception as e:
                log_error(f"[{symbol}] price validation failed: {e}")
                res, adjusted = {}, confidence
            if adjusted < confidence:
                before, confidence = confidence, adjusted
                signals["price_validation"] = res.get("price_data")
                if verbose and res.get("reduction_reason"):
                    log(f"[{symbol}] 📉 Price validation reduced confidence: {before:.1%} → {confidence:.1%} | {res['reduction_reason']}")
                if confidence < MIN_EDGE:
                    if verbose:
                        log(f"[{symbol}] ⚠️  Price validation blocked trade (confidence too low after reduction)")
                    return 0.0, "NEUTRAL"
        return _num(confidence, 0.0, 0.0, 1.0), bias

    add_conf, add_bias = finalize(r["additive_confidence"], r["additive_bias"], not BAYESIAN_CONFIDENCE)
    bay_conf, bay_bias = finalize(r["bayesian_confidence"], r["bayesian_bias"], BAYESIAN_CONFIDENCE)
    confidence, bias = (bay_conf, bay_bias) if BAYESIAN_CONFIDENCE else (add_conf, add_bias)

    raw_scores = {k: r[k] for k in (
        "up_total", "down_total", "momentum_score", "momentum_dir", "flow_score", "flow_dir",
        "divergence_score", "divergence_dir", "vwm_score", "vwm_dir", "pm_mom_score", "pm_mom_dir",
        "adx_score", "adx_dir", "lead_lag_bonus", "additive_raw", "bayesian_raw",
    )}
    # FINAL per-method values (after multi-confirmation, cap, missing-source scaling and price validation):
    # the entry code picks one of these for the A/B test, so they must be what trading may act on
    raw_scores.update(
        additive_confidence=add_conf, additive_bias=add_bias, bayesian_confidence=bay_conf, bayesian_bias=bay_bias
    )
    raw_scores["market_prior_p_up"] = p_up
    raw_scores["bayesian_evidence"] = r["bayesian_evidence"]  # signal-only log-odds (excludes the market prior)
    raw_scores["missing_sources"] = len(missing)

    return confidence, bias, p_up, best_bid, best_ask, signals, raw_scores


def bfxd_allows_trade(symbol: str, direction: str) -> tuple[bool, str]:
    """
    External BTC trend filter
    """
    if not ENABLE_BFXD:
        return True, "DISABLED"

    symbol_u = asset_of(symbol)
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
