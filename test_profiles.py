"""Tests for risk profiles: hot apply across modules, restore, DB request/poll.

Run: uv run python test_profiles.py   (also collected by pytest)
"""

import os
import tempfile

os.environ.setdefault("PROXY_PK", "0x" + "1" * 64)
os.environ["BET_PERCENT"] = "8"
os.environ["MAX_SIZE"] = "10"

import sqlite3

import src.data.db_connection as dbc
from src import profiles, risk_runtime
from src.config import settings
import src.trading.logic as logic


def fresh():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    dbc.DB_FILE = path
    risk_runtime._base.clear()
    risk_runtime._active = None
    return path


def test_every_profile_key_exists_and_profiles_share_keys():
    keys = set(profiles.PROFILES["balanced"])
    for name, p in profiles.PROFILES.items():
        assert set(p) == keys, name
    for k in profiles.KEYS:
        assert hasattr(settings, k), k


def test_profiles_are_ordered_by_risk():
    c, b, a = (profiles.PROFILES[n] for n in ("conservative", "balanced", "aggressive"))
    for k in ("BET_PERCENT", "MAX_EXPOSURE_PERCENT", "MAX_SIZE", "HEDGE_FILL_TIMEOUT_SECONDS", "TRAIL_MAX_TICKS", "KELLY_MAX_FRACTION"):
        assert c[k] < b[k] < a[k], k
    for k in ("MIN_EDGE", "MIN_NET_EDGE", "LATE_WINDOW_MIN_EV"):
        assert c[k] >= b[k] >= a[k], k  # more conservative = more demanding
    assert c["MAKER_PAIR_PRICE_BAND"] > b["MAKER_PAIR_PRICE_BAND"] > a["MAKER_PAIR_PRICE_BAND"]


def test_apply_rebinds_names_imported_by_value_and_custom_restores():
    fresh()
    before = (settings.BET_PERCENT, settings.MAX_SIZE)
    assert logic.BET_PERCENT == before[0] == 8.0 and logic.MAX_SIZE == 10.0
    risk_runtime.apply_profile("aggressive")
    assert settings.BET_PERCENT == logic.BET_PERCENT == 10.0
    assert settings.MAX_SIZE == logic.MAX_SIZE == 500.0
    assert isinstance(settings.TRAIL_MAX_TICKS, int) and settings.TRAIL_MAX_TICKS == 3
    risk_runtime.apply_profile("conservative")
    assert logic.BET_PERCENT == 3.0 and logic.MAX_SIZE == 50.0
    risk_runtime.apply_profile("custom")  # back to the .env values, even though names were rebound
    assert (settings.BET_PERCENT, logic.MAX_SIZE) == before


def test_sizing_uses_the_active_profile():
    fresh()
    risk_runtime.apply_profile("conservative")
    size_c, _ = logic._calculate_bet_size(1000.0, 0.5, 0.5)
    risk_runtime.apply_profile("aggressive")
    size_a, _ = logic._calculate_bet_size(1000.0, 0.5, 0.5)
    assert size_c == 30 and size_a == 100  # 3% vs 10% of $1000, both under their MAX_SIZE
    risk_runtime.apply_profile("custom")


def test_unknown_profile_falls_back_to_custom():
    assert profiles.normalize("YOLO") == "custom" and profiles.normalize(None) == "custom"
    assert profiles.normalize(" Balanced ") == "balanced"


def test_request_then_poll_applies_and_acknowledges():
    path = fresh()
    risk_runtime.init()  # env default: balanced
    assert risk_runtime.active_profile() == "balanced"
    profiles.request_profile(path, "aggressive")  # what the TUI does
    risk_runtime.poll()
    assert risk_runtime.active_profile() == "aggressive" and settings.BET_PERCENT == 10.0
    conn = sqlite3.connect(path)
    assert profiles.get(conn, "risk_profile_applied") == "aggressive"
    conn.close()
    risk_runtime.poll()  # nothing pending: no-op
    risk_runtime.apply_profile("custom")


def test_startup_honours_a_profile_chosen_while_the_bot_was_down():
    path = fresh()
    conn = sqlite3.connect(path)
    profiles.put(conn, "risk_profile", "conservative")
    conn.commit()
    conn.close()
    risk_runtime.init()
    assert risk_runtime.active_profile() == "conservative" and settings.MAX_EXPOSURE_PERCENT == 25.0
    risk_runtime.apply_profile("custom")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
