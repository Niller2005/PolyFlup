"""Tests for open-position PnL in the TUI data model (pure, no network).

Run: uv run python test_tui_pnl.py   (also collected by pytest)
"""

from src.tui.data import OpenPosition


def pos(**kw):
    base = dict(id=1, symbol="BTC", side="UP", size=10.0, entry_price=0.64, bet_usd=6.4, token_id="t",
                window_end=None, has_exit_order=False, scaled_in=False, is_reversal=False,
                is_hedged=True, hedge_price=0.35)
    base.update(kw)
    return OpenPosition(**base)


def test_hedged_pair_shows_locked_edge_regardless_of_mid():
    pnl, pct, locked = pos().pnl(0.10)
    assert locked and abs(pnl - 0.10) < 1e-9 and abs(pct - 0.01 / 0.99) < 1e-9


def test_hedge_sold_books_the_loss_and_marks_the_entry_leg():
    # hedge bought 0.35, sold 0.20: -1.50 gross, fee 10*0.07*0.2*0.8=0.112; entry marked at 0.60 (-0.40)
    p = pos(hedge_sold=True, hedge_exit_price=0.20)
    realized = p.realized()
    assert abs(realized - (-1.5 - 0.112)) < 1e-9
    pnl, pct, locked = p.pnl(0.60)
    assert not locked and abs(pnl - (realized - 0.4)) < 1e-9
    assert abs(pct - pnl / (10 * 0.99)) < 1e-9
    assert p.pnl(None)[0] is None  # open leg needs a mark


def test_entry_sold_marks_hedge_as_complement():
    # entry 0.64 sold 0.50 (-1.40 - fee 0.175); hedge 0.35 marked at 1-0.30=0.70 (+3.50)
    p = pos(entry_sold=True, exit_price=0.50)
    pnl, _, _ = p.pnl(0.30)
    assert abs(pnl - (-1.4 - 0.175 + 3.5)) < 1e-9


def test_both_sold_needs_no_mark():
    p = pos(entry_sold=True, exit_price=0.70, hedge_sold=True, hedge_exit_price=0.20)
    pnl, _, locked = p.pnl(None)
    assert not locked and pnl is not None and abs(pnl - p.realized()) < 1e-9


def test_unhedged_is_plain_mark_to_market():
    p = pos(is_hedged=False, hedge_price=None)
    pnl, pct, locked = p.pnl(0.70)
    assert not locked and abs(pnl - 0.6) < 1e-9 and abs(pct - (0.70 / 0.64 - 1)) < 1e-9


def test_held_leg_without_trade_row_is_listed_as_open_position():
    import os
    import sqlite3
    import tempfile
    from pathlib import Path

    from src.tui.data import load_snapshot

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE paper_account (id INTEGER PRIMARY KEY, cash REAL, start_cash REAL, created_at TEXT)")
    c.execute("INSERT INTO paper_account VALUES (1, 900, 1000, 't')")
    c.execute("CREATE TABLE paper_orders (id TEXT PRIMARY KEY, token_id TEXT, side TEXT, price REAL, size REAL, filled REAL, status TEXT, order_type TEXT, post_only INT, created_at TEXT, updated_at TEXT, slug TEXT, outcome TEXT)")
    c.execute("CREATE TABLE paper_positions (token_id TEXT PRIMARY KEY, size REAL, avg_price REAL, slug TEXT, opened_at TEXT, outcome TEXT)")
    c.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT, slug TEXT, side TEXT, size REAL, entry_price REAL, bet_usd REAL, token_id TEXT, is_hedged INT, hedge_order_price REAL, settled INT DEFAULT 0, window_end TEXT, limit_sell_order_id TEXT, scaled_in INT, is_reversal INT, exit_price REAL, pnl_usd REAL, roi_pct REAL, final_outcome TEXT, exited_early INT, hedge_exited_early INT, hedge_exit_price REAL, settled_at TEXT, timestamp TEXT)")
    # hedged pair on btc (both legs held, one trade row) + a lone ETH leg with no row
    c.execute("INSERT INTO trades (id,symbol,slug,side,size,entry_price,bet_usd,token_id,is_hedged,hedge_order_price) VALUES (1,'BTC-5M','btc-updown-5m-1793144400','UP',10,0.5,5,'t1',1,0.49)")
    c.executemany("INSERT INTO paper_positions VALUES (?,?,?,?,?,?)", [
        ("t1", 10, 0.5, "btc-updown-5m-1793144400", "x", "Up"),
        ("t2", 10, 0.49, "btc-updown-5m-1793144400", "x", "Down"),
        ("t3", 20, 0.4, "eth-updown-5m-1793144400", "x", "Down"),
    ])
    c.commit()
    c.close()
    snap = load_snapshot(Path(path))
    lone = [p for p in snap.open_positions if p.unpaired]
    assert [(p.symbol, p.side, p.size) for p in lone] == [("ETH-5M", "DOWN", 20)]
    assert len(snap.open_positions) == 2  # the hedge leg of the BTC pair is not listed twice
    os.unlink(path)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
