"""Data-driven parameters: pure statistics and the DB-backed estimates.

Run: uv run python test_adaptive.py   (also collected by pytest)
"""

import os
import sqlite3
import tempfile

os.environ.setdefault("PROXY_PK", "0x" + "1" * 64)

import src.data.db_connection as dbc
from src.data.migrations import migration_014_create_measurement_tables, migration_016_add_lock_arm
from src.trading import adaptive


def test_shrink_moves_from_prior_to_data_as_n_grows():
    assert adaptive.shrink(0, 0, 0.3, 20) == 0.3
    assert abs(adaptive.shrink(10, 20, 0.3, 20) - 0.4) < 1e-9  # half data (0.5), half prior (0.3)
    assert adaptive.shrink(900, 1000, 0.3, 20) > 0.88  # lots of data dominates the prior


def test_p_quantile():
    assert adaptive.p_quantile([], 0.9) is None
    assert adaptive.p_quantile(list(range(1, 11)), 0.9) == 10 and adaptive.p_quantile([5], 0.9) == 5


def test_choose_arm_explores_until_every_arm_has_data_then_exploits():
    import random

    arms = [0.01, 0.03, 0.06]
    rng = random.Random(0)
    # an arm without enough results -> uniform exploration over all arms
    picks = {adaptive.choose_arm({0.01: (50, 0.0), 0.03: (5, 0.0)}, arms, 20, 0.0, rng) for _ in range(200)}
    assert picks == set(arms)
    # everything measured: best mean wins when not exploring
    stats = {0.01: (40, -0.05), 0.03: (40, -0.02), 0.06: (40, -0.08)}
    assert all(adaptive.choose_arm(stats, arms, 20, 0.0, rng) == 0.03 for _ in range(50))
    # epsilon keeps exploring the others sometimes
    assert len({adaptive.choose_arm(stats, arms, 20, 0.5, rng) for _ in range(200)}) == 3


def make_db(rows):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    c = sqlite3.connect(path)
    migration_014_create_measurement_tables(c)
    migration_016_add_lock_arm(c)
    c.executemany(
        "INSERT INTO pair_attempts (symbol, outcome, first_leg, size, final_entry_price, final_hedge_price, second_fill_s, sell_price, sell_size, sell_fee, resolution) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    c.commit()
    c.close()
    dbc.DB_FILE = path
    adaptive.clear_cache()
    return path


def test_negative_ev_market_trades_at_minimum_size_and_positive_at_full():
    # BTC: 8 leg-outs losing ~0.15/sh and 2 completions (+1c) -> clearly negative EV
    bad = [("BTC", "timeout", "entry", 40, 0.50, 0.49, None, 0.35, 40, 0.5, "sell")] * 8 + [("BTC", "both", "entry", 40, 0.50, 0.49, 20, None, None, None, None)] * 2
    # ETH: 2 leg-outs and 40 completions at +2c -> positive
    good = [("ETH", "timeout", "entry", 40, 0.50, 0.48, None, 0.45, 40, 0.1, "sell")] * 2 + [("ETH", "both", "hedge", 40, 0.50, 0.48, 12, None, None, None, None)] * 40
    make_db(bad + good)
    size, note = adaptive.pair_size("BTC", 60.0)
    assert size == 5.0 and "explore" in note
    size, note = adaptive.pair_size("ETH", 60.0)
    assert size == 60.0 and "EV +" in note
    assert adaptive.pair_ev("BTC") < 0 < adaptive.pair_ev("ETH")


def test_too_little_data_keeps_full_size_and_defaults():
    make_db([("BTC", "timeout", "entry", 40, 0.50, 0.49, None, 0.35, 40, 0.5, "sell")] * 3)
    assert adaptive.pair_size("BTC", 60.0)[0] == 60.0
    assert adaptive.timeout_seconds("BTC", 90.0) == 90.0  # fewer than ADAPTIVE_MIN_N completions


def test_timeout_follows_completion_time_p90_but_never_exceeds_default():
    make_db([("ETH-5M", "both", "entry", 40, 0.5, 0.48, float(t), None, None, None, None) for t in list(range(5, 30)) * 1])
    got = adaptive.timeout_seconds("ETH-5M", 40.0)
    assert 25 <= got <= 40  # p90 of 5..29 is 27 -> 32
    assert adaptive.timeout_seconds("ETH-5M", 20.0) == 20.0




def test_arm_stats_use_realised_results_per_share():
    path = make_db([])
    c = sqlite3.connect(path)
    cols = "symbol, outcome, first_leg, size, final_entry_price, final_hedge_price, realized_pnl, lock_arm"
    c.executemany(f"INSERT INTO pair_attempts ({cols}) VALUES (?,?,?,?,?,?,?,?)", [
        ("BTC", "both", "entry", 10, 0.50, 0.49, None, 0.03),    # lock +1c
        ("BTC", "both", "entry", 10, 0.52, 0.51, None, 0.03),    # lock -3c
        ("BTC", "timeout", "entry", 10, 0.50, 0.49, -1.5, 0.03),  # -15c per share
        ("BTC", "timeout", "entry", 10, 0.50, 0.49, None, 0.03),  # unresolved hold: skipped
        ("BTC", "both", "entry", 10, 0.50, 0.49, None, None),     # old attempt without an arm: skipped
    ])
    c.commit()
    c.close()
    n, mean = adaptive._arm_stats()[0.03]
    assert n == 3 and abs(mean - (0.01 - 0.03 - 0.15) / 3) < 1e-9


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
