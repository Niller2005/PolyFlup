"""Tests for market keys and window math (5m + 15m up/down markets).

Run: uv run python test_markets.py   (also collected by pytest)
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from src.markets import (
    asset_of,
    parse_market,
    parse_slug,
    scale_seconds,
    slug_for,
    window_bounds,
)

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def test_parse_market_normalises_aliases():
    assert parse_market("btc").key == "BTC"
    assert parse_market("BTC-15M").key == "BTC"  # 15m keeps the legacy key
    for alias in ("BTC-5M", "btc:5m", "BTC5M", "btc_5m"):
        spec = parse_market(alias)
        assert (spec.key, spec.asset, spec.minutes) == ("BTC-5M", "BTC", 5), alias


def test_parse_market_rejects_unsupported_window():
    for bad in ("BTC-7M", "BTC-1M", ""):
        try:
            parse_market(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} should be rejected")


def test_asset_of_is_tolerant():
    assert asset_of("SOL-5M") == "SOL"
    assert asset_of("ETH") == "ETH"
    assert asset_of("some title") == "SOME TITLE"  # adopted-position fallback, no crash


def test_window_bounds_align_to_slots():
    now = datetime(2026, 10, 27, 14, 47, 31, tzinfo=ET)
    s5, e5 = window_bounds("BTC-5M", now)
    s15, e15 = window_bounds("BTC", now)
    assert (s5.minute, e5.minute) == (45, 50)
    assert (s15.minute, e15.minute) == (45, 0) and e15.hour == 15


def test_slug_matches_polymarket_format():
    # Verified against gamma: btc-updown-5m-1793126700 is "2:45PM-2:50PM ET" on 2026-10-27
    now = datetime(2026, 10, 27, 14, 47, 31, tzinfo=ET)
    assert slug_for("BTC-5M", now) == "btc-updown-5m-1793126700"
    assert slug_for("BTC", now) == "btc-updown-15m-1793126700"
    assert slug_for("btc-5m", now.replace(minute=52)) == "btc-updown-5m-1793127000"


def test_parse_slug_roundtrip():
    key, start, end = parse_slug("eth-updown-5m-1793126700")
    assert key == "ETH-5M" and (end - start).total_seconds() == 300
    assert parse_slug("eth-updown-15m-1793126700")[0] == "ETH"
    assert parse_slug("eth-updown-7m-1793126700") is None
    assert parse_slug("will-it-rain") is None


def test_scale_seconds():
    assert scale_seconds(90, "BTC") == 90
    assert scale_seconds(90, "BTC-5M") == 30
    assert scale_seconds(5, "BTC-5M", floor=5) == 5  # floor wins


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
