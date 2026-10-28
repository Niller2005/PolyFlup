"""pair_log: recording must work on the real schema and must never raise into trading.

Run: uv run python test_pair_log.py   (also collected by pytest)
"""

import os
import sqlite3
import tempfile

os.environ.setdefault("PROXY_PK", "0x" + "1" * 64)

import src.data.db_connection as dbc
from src.data.migrations import migration_014_create_measurement_tables, migration_015_create_market_data_tables
from src.trading import pair_log


def fresh():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    c = sqlite3.connect(path)
    migration_014_create_measurement_tables(c)
    migration_015_create_market_data_tables(c)
    migration_015_create_market_data_tables(c)  # idempotent
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


def test_evaluation_is_finished_with_the_trade_outcome():
    path = fresh()
    pair_log.evaluation("BTC-5M", "BAYESIAN", 0.42, "UP", 0.61, 0.60, 0.62, {"a": 1}, {"bayesian_confidence": 0.42, "additive_confidence": 0.3})
    pair_log.finish_evaluation("no_trade")
    pair_log.finish_evaluation("trade", 5)  # second finish without a new evaluation must not touch the row
    r = rows(path, "select symbol, method, bias, decision, trade_id, bayesian_confidence, additive_confidence, secs_into_window is not null from evaluations")
    assert r == [("BTC-5M", "BAYESIAN", "UP", "no_trade", None, 0.42, 0.3, 1)]


def test_new_market_data_tables_exist():
    path = fresh()
    names = {r[0] for r in rows(path, "select name from sqlite_master where type='table'")}
    assert {"market_ticks", "market_trades", "market_windows", "evaluations", "account_snapshots"} <= names
    assert {"entry_book", "hedge_book", "other_book_at_first"} <= {r[1] for r in rows(path, "pragma table_info(pair_attempts)")}


def test_snapshot_counts_a_token_once_even_if_it_is_in_trades_and_holdings():
    path = fresh()
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT, slug TEXT, side TEXT, token_id TEXT, size REAL, entry_price REAL, is_hedged INT, hedge_order_price REAL, window_end TEXT, settled INT DEFAULT 0, exited_early INT DEFAULT 0)")
    c.execute("CREATE TABLE paper_positions (token_id TEXT PRIMARY KEY, size REAL, avg_price REAL, slug TEXT, opened_at TEXT, outcome TEXT)")
    c.execute("CREATE TABLE paper_account (id INTEGER PRIMARY KEY, cash REAL, start_cash REAL, created_at TEXT)")
    c.execute("INSERT INTO paper_account VALUES (1, 900, 1000, 't')")
    c.execute("INSERT INTO trades (id,symbol,slug,side,token_id,size,entry_price,is_hedged,hedge_order_price) VALUES (1,'BTC','s','UP','tUP',10,0.5,1,0.49)")
    c.executemany("INSERT INTO paper_positions VALUES (?,?,?,?,?,?)", [("tUP", 10, 0.5, "s", "x", "Up"), ("tDN", 10, 0.49, "s", "x", "Down")])
    c.commit()
    c.close()
    pair_log._top = lambda token: (0.4, 0.6)  # mid 0.5 for both legs
    pair_log._last_mark = 0.0
    assert pair_log.snapshot_positions(every=0.0) == 2
    assert rows(path, "select count(*), count(distinct token_id) from position_marks") == [(2, 2)]
    assert rows(path, "select round(held,2), round(equity,2), open_legs from account_snapshots") == [(10.0, 910.0, 2)]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
