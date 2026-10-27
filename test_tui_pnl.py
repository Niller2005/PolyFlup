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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
