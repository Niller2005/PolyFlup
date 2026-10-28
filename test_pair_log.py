"""pair_log: recording must work on the real schema and must never raise into trading.

Run: uv run python test_pair_log.py   (also collected by pytest)
"""

import os
import sqlite3
import tempfile

os.environ.setdefault("PROXY_PK", "0x" + "1" * 64)

import src.data.db_connection as dbc
from src.data.migrations import migration_014_create_measurement_tables
from src.trading import pair_log


def fresh():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    c = sqlite3.connect(path)
    migration_014_create_measurement_tables(c)
    c.commit()
    c.close()
    dbc.DB_FILE = path
    pair_log.end()
    return path


def rows(path, sql):
    return sqlite3.connect(path).execute(sql).fetchall()


def test_attempt_lifecycle_and_sale_aggregation():
    path = fresh()
    aid = pair_log.begin("BTC-5M", "btc-updown-5m-1", "MAKER", "UP", 40.0, 0.50, 0.49, secs_left=200, timeout_s=30, tokens=("a", "b"), confidence=0.6)
    assert aid
    pair_log.event("place", token_id="a", order_id="o1", side="BUY", price=0.5, size=40.0)
    pair_log.update(outcome="timeout", first_leg="entry", first_fill_s=10.0, final_entry_price=0.50, final_hedge_price=0.49)
    pair_log.record_sale("BTC-5M", 20.0, 0.30, "hedge timeout")
    pair_log.record_sale("BTC-5M", 20.0, 0.20, "hedge timeout")
    row = rows(path, "select outcome, first_leg, resolution, sell_size, round(sell_price,3), confidence from pair_attempts")[0]
    assert row == ("timeout", "entry", "sell", 40.0, 0.25, 0.6)
    assert rows(path, "select count(*) from order_events where attempt_id=%d" % aid)[0][0] == 3  # place + 2 sells


def test_skipped_opportunity_is_recorded_without_an_attempt_context():
    path = fresh()
    pair_log.skipped("ETH", "maker band", entry_side="UP", size=10.0)
    assert rows(path, "select outcome, note, entry_side from pair_attempts") == [("skipped", "maker band", "UP")]


def test_recording_never_raises_when_tables_are_missing():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    dbc.DB_FILE = path  # empty database: no tables at all
    pair_log.end()
    assert pair_log.begin("BTC", "s", "MAKER", "UP", 5.0, 0.5, 0.49) is None
    pair_log.update(outcome="both")
    pair_log.event("place", token_id="x")
    pair_log.skipped("BTC", "x")
    pair_log.record_sale("BTC", 5.0, 0.4, "r")


def test_unknown_columns_are_ignored():
    fresh()
    pair_log.begin("BTC", "s", "MAKER", "UP", 5.0, 0.5, 0.49)
    pair_log.update(bogus="drop table pair_attempts; --", outcome="both")  # must not be interpolated


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
