"""Pre-settlement exits must book the sale against the leg that was actually sold.

Run: uv run python test_exit_attribution.py   (also collected by pytest)
"""

import os
import sqlite3
import tempfile

os.environ.setdefault("PROXY_PK", "0x" + "1" * 64)

import src.data.db_connection as dbc
from src.trading.pre_settlement_exit import exit_order_id
from src.trading.position_manager import sync


def test_losing_entry_leg_is_attributed_to_the_entry_order():
    # trade entered DOWN, hedged with UP; UP is winning, so the losing DOWN (= entry) is sold
    assert exit_order_id("DOWN", "DOWN", "entry-id", "hedge-id") == "entry-id"


def test_losing_hedge_leg_is_attributed_to_the_hedge_order():
    assert exit_order_id("DOWN", "UP", "entry-id", "hedge-id") == "hedge-id"


def test_sold_entry_leg_is_not_marked_sync_missing():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    c = sqlite3.connect(path)
    c.execute(
        """CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT, side TEXT, size REAL, token_id TEXT,
           entry_price REAL, settled INT DEFAULT 0, timestamp TEXT, exited_early INT DEFAULT 0,
           slug TEXT, is_hedged INT DEFAULT 0, final_outcome TEXT, pnl_usd REAL, roi_pct REAL)"""
    )
    old = "2020-01-01T00:00:00+00:00"
    c.execute("INSERT INTO trades (symbol,side,size,token_id,entry_price,timestamp,exited_early,is_hedged) VALUES ('ETH-5M','DOWN',50,'111',0.4,?,1,1)", (old,))
    c.execute("INSERT INTO trades (symbol,side,size,token_id,entry_price,timestamp,exited_early,is_hedged) VALUES ('ETH-5M','UP',50,'222',0.4,?,0,1)", (old,))
    c.commit()
    c.close()
    dbc.DB_FILE = path
    sync.get_current_positions = lambda addr: []
    sync.sync_positions_with_exchange("0xabc")
    rows = dict(sqlite3.connect(path).execute("SELECT side, final_outcome FROM trades").fetchall())
    assert rows["DOWN"] is None, "entry sold on purpose must stay open until its hedge resolves"
    assert rows["UP"] == "SYNC_MISSING"  # a really missing position is still flagged
    os.unlink(path)


def test_migration_reopens_mis_booked_entry_exit_and_drops_adopted_twin():
    from src.data.migrations import migration_013_repair_misattributed_hedge_exits as mig

    c = sqlite3.connect(":memory:")
    c.execute(
        """CREATE TABLE trades (id INTEGER PRIMARY KEY, slug TEXT, side TEXT, is_hedged INT, order_id TEXT,
           hedge_order_id TEXT, final_outcome TEXT, settled INT, exited_early INT, exit_price REAL,
           hedge_exited_early INT, hedge_exit_price REAL, pnl_usd REAL, roi_pct REAL, settled_at TEXT)"""
    )
    c.execute("INSERT INTO trades VALUES (1,'eth-5m-1','DOWN',1,'o','h','SYNC_MISSING',1,0,NULL,1,0.01,0,0,'t')")
    c.execute("INSERT INTO trades VALUES (5,'eth-5m-1','UP',0,NULL,NULL,'RESOLVED',1,0,1.0,0,NULL,29.1,50,'t')")
    # a genuinely hedge-sold, correctly settled trade must be untouched
    c.execute("INSERT INTO trades VALUES (2,'btc-5m-1','UP',1,'o','h','RESOLVED',1,0,1.0,1,0.03,2.3,5,'t')")
    mig(c)
    t1 = c.execute("SELECT settled, final_outcome, exited_early, exit_price, hedge_exited_early, hedge_exit_price FROM trades WHERE id=1").fetchone()
    assert t1 == (0, None, 1, 0.01, 0, None)
    assert c.execute("SELECT COUNT(*) FROM trades WHERE id=5").fetchone()[0] == 0
    assert c.execute("SELECT settled, hedge_exited_early FROM trades WHERE id=2").fetchone() == (1, 1)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
