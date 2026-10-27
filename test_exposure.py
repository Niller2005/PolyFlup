"""Tests for cash- and exposure-aware sizing (temporary DB).

Run: uv run python test_exposure.py   (also collected by pytest)
"""

import os
import sqlite3
import tempfile

os.environ.setdefault("PROXY_PK", "0x" + "1" * 64)
os.environ["BET_PERCENT"] = "10"
os.environ["MAX_EXPOSURE_PERCENT"] = "40"
os.environ["KELLY_SIZING"] = "NO"
os.environ["MAX_SIZE"] = "500"

import src.data.db_connection as dbc
from src.trading import exposure, inflight


def fresh_db(rows=()):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    c = sqlite3.connect(path)
    c.execute(
        """CREATE TABLE trades (id INTEGER PRIMARY KEY, settled INT DEFAULT 0, size REAL, entry_price REAL,
           bet_usd REAL, is_hedged INT DEFAULT 0, hedge_order_price REAL, exited_early INT DEFAULT 0,
           hedge_exited_early INT DEFAULT 0)"""
    )
    c.executemany(
        "INSERT INTO trades (settled,size,entry_price,bet_usd,is_hedged,hedge_order_price,exited_early,hedge_exited_early) VALUES (?,?,?,?,?,?,?,?)",
        rows,
    )
    c.commit()
    c.close()
    dbc.DB_FILE = path
    for s in list(inflight.entries_in_flight()):
        inflight.clear(s)


def test_hedged_pair_counts_both_legs_and_settled_is_ignored():
    fresh_db(
        [
            (0, 10, 0.64, 6.4, 1, 0.35, 0, 0),  # hedged: 10 * 0.99 = 9.9
            (0, 10, 0.50, 5.0, 0, None, 0, 0),  # unhedged: just the entry cost
            (0, 10, 0.50, 5.0, 1, 0.40, 1, 0),  # entry sold: only the held hedge leg (4.0) is exposed
            (0, 10, 0.50, 5.0, 1, 0.40, 0, 1),  # hedge sold: only the held entry leg (5.0) is exposed
            (0, 10, 0.50, 5.0, 1, 0.40, 1, 1),  # both sold: nothing
            (0, 10, 0.50, 5.0, 0, None, 1, 0),  # unhedged entry sold: nothing
            (1, 10, 0.50, 5.0, 1, 0.40, 0, 0),  # settled
        ]
    )
    assert abs(exposure.open_exposure_usd() - (9.9 + 5.0 + 4.0 + 5.0)) < 1e-9


def test_allocation_scales_with_available_cash():
    fresh_db()
    assert abs(exposure.sizing_state(1000.0)["allocation"] - 100.0) < 1e-9  # 10% of cash
    assert abs(exposure.sizing_state(200.0)["allocation"] - 20.0) < 1e-9


def test_inflight_reservations_shrink_the_next_entry():
    fresh_db()
    inflight.mark("BTC", 100.0)
    st = exposure.sizing_state(1000.0)
    assert st["available"] == 900.0 and abs(st["allocation"] - 90.0) < 1e-9


def test_exposure_cap_clips_and_then_blocks():
    # equity = 1000 cash + 300 exposure = 1300; 40% cap = 520; headroom = 220
    fresh_db([(0, 300, 0.5, 150.0, 1, 0.5, 0, 0)])
    st = exposure.sizing_state(1000.0)
    assert abs(st["equity"] - 1300.0) < 1e-9 and abs(st["headroom"] - 220.0) < 1e-9
    assert abs(st["allocation"] - 100.0) < 1e-9  # 10% of cash still fits under headroom
    inflight.mark("ETH", 200.0)
    st = exposure.sizing_state(1000.0)
    assert abs(st["headroom"] - 20.0) < 1e-9 and abs(st["allocation"] - 20.0) < 1e-9  # clipped
    inflight.mark("SOL", 20.0)
    assert exposure.sizing_state(1000.0)["allocation"] == 0.0  # cap reached


def test_sizing_code_honours_thread_cap():
    from src.trading.logic import _calculate_bet_size

    fresh_db()
    exposure.set_allocation_cap(23.0)
    try:
        size, cost = _calculate_bet_size(10_000.0, 0.5, 0.5)
    finally:
        exposure.set_allocation_cap(None)
    assert size == 23 and abs(cost - 11.5) < 1e-9  # 10% of 10k would be 1000: clipped to the reserved 23


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
