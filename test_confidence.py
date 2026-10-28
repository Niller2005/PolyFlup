"""Robustness of the confidence calculation (pure `score_signals` + defensive `calculate_confidence`).

Run: uv run python test_confidence.py   (also collected by pytest)
"""

import math
import os

os.environ.setdefault("PROXY_PK", "0x" + "1" * 64)

from src.trading import strategy
from src.trading.strategy import _best_prices, _num, momentum_quality, score_signals


def sig(**over):
    base = {
        "momentum": {"direction": "NEUTRAL", "strength": 0.0, "acceleration": 0.0, "rsi": 50.0},
        "order_flow": {"buy_pressure": 0.5, "large_trade_direction": "NEUTRAL", "trade_intensity": 0.0},
        "divergence": {"divergence": 0.0, "opportunity": "NEUTRAL"},
        "vwm": {"momentum_quality": 0.0, "vwap_distance": 0.0},
        "pm_momentum": {"direction": "NEUTRAL", "strength": 0.0},
        "adx": 0.0,
    }
    base.update(over)
    return base


def test_weak_signal_is_never_evidence_against_its_own_direction():
    # 52% buy pressure is a (weak) UP signal: it used to count as evidence for DOWN in the Bayesian path
    none = score_signals(0.5, sig(), True, True)
    weak_up = score_signals(0.5, sig(order_flow={"buy_pressure": 0.52}), True, True)
    assert weak_up["bayesian_evidence"] >= none["bayesian_evidence"] == 0.0
    assert weak_up["bayesian_bias"] != "DOWN"


def test_nan_and_garbage_inputs_cannot_become_max_confidence():
    nan = float("nan")
    r = score_signals(
        nan,
        sig(momentum={"direction": "UP", "strength": nan, "acceleration": None, "rsi": "x"},
            order_flow={"buy_pressure": nan}, divergence={"divergence": float("inf"), "opportunity": "BUY_UP"},
            vwm={"momentum_quality": None, "vwap_distance": nan}, pm_momentum={"direction": "UP", "strength": nan}, adx=nan),
        True, False,
    )
    assert 0.0 <= r["confidence"] < 0.85 and math.isfinite(r["bayesian_confidence"])
    assert _num(float("nan"), 0.3) == 0.3 and _num(None, 1.0) == 1.0 and _num(5, 0.0, 0.0, 1.0) == 1.0


def test_up_and_down_are_mirror_images():
    up = sig(momentum={"direction": "UP", "strength": 0.9, "acceleration": 1, "rsi": 55},
             order_flow={"buy_pressure": 0.62, "large_trade_direction": "UP", "trade_intensity": 0.6},
             pm_momentum={"direction": "UP", "strength": 0.7}, vwm={"momentum_quality": 0.8, "vwap_distance": 5})
    down = sig(momentum={"direction": "DOWN", "strength": 0.9, "acceleration": -1, "rsi": 45},
               order_flow={"buy_pressure": 0.38, "large_trade_direction": "DOWN", "trade_intensity": 0.6},
               pm_momentum={"direction": "DOWN", "strength": 0.7}, vwm={"momentum_quality": 0.8, "vwap_distance": -5})
    for bayes in (False, True):
        a, b = score_signals(0.4, up, True, bayes), score_signals(0.6, down, True, bayes)
        assert a["bias"] == "UP" and b["bias"] == "DOWN"
        assert abs(a["additive_confidence"] - b["additive_confidence"]) < 1e-9
        assert abs(a["bayesian_confidence"] - b["bayesian_confidence"]) < 1e-9


def test_momentum_quality_is_direction_symmetric():
    assert momentum_quality("UP", 75, 0.5) == momentum_quality("DOWN", 25, 0.5) == 0.8  # stretched in its own direction
    assert momentum_quality("UP", 25, 0.5) == momentum_quality("DOWN", 75, 0.5) == 1.2  # room to run
    assert momentum_quality("UP", 50, 0.9) > 1.0 and momentum_quality("NEUTRAL", 10, 1.0) == 1.0


def test_confidence_is_bounded_and_capped():
    strong = sig(momentum={"direction": "UP", "strength": 1.0, "acceleration": 1, "rsi": 20},
                 order_flow={"buy_pressure": 0.9, "trade_intensity": 1.0}, divergence={"divergence": 0.3, "opportunity": "BUY_UP"},
                 vwm={"momentum_quality": 1.0, "vwap_distance": 1}, pm_momentum={"direction": "UP", "strength": 1.0}, adx=60)
    r = score_signals(0.9, strong, True, True)
    assert 0.0 < r["confidence"] <= 0.85 and r["additive_confidence"] <= 0.85


def test_multi_confirmation_applies_to_both_methods():
    # high confidence from only two aligned signals is cut back (above 60% needs 3 of 5)
    two = sig(momentum={"direction": "UP", "strength": 1.0, "acceleration": 1, "rsi": 20}, pm_momentum={"direction": "UP", "strength": 1.0},
              divergence={"divergence": 0.3, "opportunity": "BUY_UP"})
    r = score_signals(0.5, two, False, False)
    assert r["additive_confidence"] <= r["additive_raw"]


def test_book_levels_are_parsed_in_any_order_and_garbage_ignored():
    worst_first = ([{"price": "0.40"}, {"price": "0.45"}], [{"price": "0.60"}, {"price": "0.55"}])
    best_first = ([{"price": "0.45"}, {"price": "0.40"}], [{"price": "0.55"}, {"price": "0.60"}])
    assert _best_prices(*worst_first) == _best_prices(*best_first) == (0.45, 0.55)
    assert _best_prices([{"price": "0"}, {"price": "nan"}, {"price": 1.0}], []) == (None, None)


class FakeClient:
    def __init__(self, bids, asks):
        self.book = {"bids": bids, "asks": asks}

    def get_order_book(self, token):
        return self.book


def patch_sources(**kw):
    defaults = dict(
        get_price_momentum=lambda s, lookback_minutes=0: {"direction": "UP", "strength": 0.8, "acceleration": 1, "rsi": 55},
        get_order_flow_analysis=lambda s: {"buy_pressure": 0.6},
        get_cross_exchange_divergence=lambda s, p: {"divergence": 0.05, "opportunity": "BUY_UP", "binance_price": 100.0},
        get_volume_weighted_momentum=lambda s: {"momentum_quality": 0.6, "vwap_distance": 1.0},
        get_polymarket_momentum=lambda t: {"direction": "UP", "strength": 0.6},
        get_adx_from_binance=lambda s: 30.0,
        get_current_spot_price=lambda s: 100.0,
        validate_price_movement_for_trade=lambda **k: {"valid": True, "adjusted_confidence": k["confidence"], "price_data": {}, "reduction_reason": None},
    )
    defaults.update(kw)
    for k, v in defaults.items():
        setattr(strategy, k, v)


def test_crossed_empty_and_wide_books_are_neutral():
    patch_sources()
    for bids, asks in (([{"price": "0.55"}], [{"price": "0.50"}]), ([], [{"price": "0.5"}]), ([{"price": "0.30"}], [{"price": "0.70"}])):
        r = strategy.calculate_confidence("BTC", "tok", FakeClient(bids, asks))
        assert r[0] == 0.0 and r[1] == "NEUTRAL"


def test_failing_sources_degrade_gracefully():
    ok = FakeClient([{"price": "0.49"}], [{"price": "0.51"}])
    patch_sources()
    full = strategy.calculate_confidence("BTC", "tok", ok)
    boom = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("api down"))
    patch_sources(get_order_flow_analysis=boom)  # one source down: scaled down, still trades
    one = strategy.calculate_confidence("BTC", "tok", ok)
    assert one[1] == full[1] and one[0] < full[0] and one[6]["missing_sources"] == 1
    patch_sources(get_order_flow_analysis=boom, get_polymarket_momentum=boom, get_cross_exchange_divergence=boom)
    three = strategy.calculate_confidence("BTC", "tok", ok)
    assert three[0] == 0.0 and three[1] == "NEUTRAL" and three[5]["reason"] == "insufficient_data"
    patch_sources(get_price_momentum=lambda s, lookback_minutes=0: float("nan"))  # non-dict result counts as missing
    assert strategy.calculate_confidence("BTC", "tok", ok)[6]["missing_sources"] == 1


def test_final_method_confidences_include_validation_and_nan_validator_is_ignored():
    ok = FakeClient([{"price": "0.49"}], [{"price": "0.51"}])
    patch_sources(validate_price_movement_for_trade=lambda **k: {"valid": True, "adjusted_confidence": 0.0, "price_data": {}, "reduction_reason": "test"})
    r = strategy.calculate_confidence("BTC", "tok", ok)
    if r[6]["additive_raw"] >= strategy.PRICE_VALIDATION_MIN_CONFIDENCE:  # only strong signals reach the validator
        assert r[6]["additive_confidence"] == 0.0 and r[6]["additive_bias"] == "NEUTRAL"
    patch_sources(validate_price_movement_for_trade=lambda **k: {"adjusted_confidence": float("nan")})
    r = strategy.calculate_confidence("BTC", "tok", ok)
    assert 0.0 <= r[0] <= 1.0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
