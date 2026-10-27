"""Tests for the paper exchange (stubbed order book, temporary database).

Run: uv run python test_paper.py   (also collected by pytest)
"""

import os
import tempfile

os.environ["PAPER_TRADING"] = "YES"
os.environ.setdefault("PAPER_STARTING_BALANCE", "1000")

import src.data.db_connection as dbc
from src import paper

TOKEN = "111"
BOOK = {"bids": [(0.48, 100.0), (0.47, 100.0)], "asks": [(0.50, 6.0), (0.52, 100.0)]}


def fresh(book=None):
    """New empty paper account in a temp DB with a stubbed book."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    dbc.DB_FILE = path
    paper._book = lambda token: (list(BOOK["bids"]), list(BOOK["asks"])) if book is None else book
    paper._market_for = lambda token: ("btc-updown-5m-1", "Up")
    return path


def test_taker_buy_walks_book_and_pays_fee():
    fresh()
    r = paper.place_order(TOKEN, 0.52, 10.0, "BUY", "FAK")
    assert r["success"] and r["status"] == "matched"
    # 6 @ 0.50 + 4 @ 0.52 ; fee = sum(size*0.07*p*(1-p))
    cost = 6 * 0.50 + 4 * 0.52
    fee = round(6 * 0.07 * 0.5 * 0.5, 5) + round(4 * 0.07 * 0.52 * 0.48, 5)
    assert abs(paper.cash_balance() - (1000 - cost - fee)) < 1e-6
    assert paper.position_size(TOKEN) == 10.0


def test_post_only_crossing_is_rejected_and_fak_without_liquidity_fails():
    fresh()
    r = paper.place_order(TOKEN, 0.50, 5.0, "BUY", "GTC", post_only=True)
    assert not r["success"] and "crosses book" in r["errorMsg"]
    r = paper.place_order(TOKEN, 0.40, 5.0, "BUY", "FAK")  # limit below the ask: nothing to match
    assert not r["success"]


def test_fok_requires_full_fill():
    fresh()
    r = paper.place_order(TOKEN, 0.50, 10.0, "BUY", "FOK")  # only 6 available at <= 0.50
    assert not r["success"] and paper.position_size(TOKEN) == 0.0
    assert paper.cash_balance() == 1000.0


def test_resting_bid_reserves_cash_cancel_releases():
    fresh()
    r = paper.place_order(TOKEN, 0.40, 10.0, "BUY", "GTC", post_only=True)
    assert r["success"] and r["status"] == "live"
    assert abs(paper.free_cash() - (1000 - 4.0)) < 1e-9 and paper.cash_balance() == 1000.0
    assert paper.cancel(r["orderID"]) and paper.free_cash() == 1000.0
    assert paper.get_order(r["orderID"])["status"] == "CANCELED"


def test_maker_fill_cross_mode_only_when_book_crosses_and_is_fee_free():
    fresh()
    r = paper.place_order(TOKEN, 0.45, 10.0, "BUY", "GTC", post_only=True)
    assert paper.match_resting() == 0  # ask 0.50 > 0.45: not filled
    paper._book = lambda token: ([(0.43, 50.0)], [(0.45, 50.0)])  # ask came down to our bid
    assert paper.match_resting() == 1
    assert paper.get_order(r["orderID"])["status"] == "MATCHED"
    assert abs(paper.cash_balance() - (1000 - 4.5)) < 1e-9  # no fee for makers
    assert paper.position_size(TOKEN) == 10.0


def test_funds_and_inventory_checks():
    fresh()
    assert not paper.place_order(TOKEN, 0.50, 5000.0, "BUY", "GTC", post_only=True)["success"]
    r = paper.place_order(TOKEN, 0.50, 5.0, "SELL", "GTC")
    assert not r["success"] and "balance" in r["errorMsg"]  # nothing to sell


def test_sell_and_round_trip_pnl():
    fresh()
    paper.place_order(TOKEN, 0.50, 5.0, "BUY", "FAK")
    paper.place_order(TOKEN, 0.48, 5.0, "SELL", "FAK")
    assert paper.position_size(TOKEN) == 0.0
    # bought at 0.50, sold at 0.48, two taker fees
    expected = 1000 - 5 * 0.50 + 5 * 0.48 - round(5 * 0.07 * 0.25, 5) - round(5 * 0.07 * 0.48 * 0.52, 5)
    assert abs(paper.cash_balance() - expected) < 1e-6


def test_settlement_pays_one_or_zero():
    fresh()
    paper.place_order(TOKEN, 0.50, 5.0, "BUY", "FAK")
    before = paper.cash_balance()

    class R:
        def __init__(self, prices):
            self.prices = prices

        def json(self):
            return {"clobTokenIds": f'["{TOKEN}", "222"]', "outcomePrices": self.prices}

    paper.requests.get = lambda *a, **k: R('["0.4", "0.6"]')  # unresolved
    assert paper.settle_resolved() == 0 and paper.position_size(TOKEN) == 5.0
    paper.requests.get = lambda *a, **k: R('["1", "0"]')
    assert paper.settle_resolved() == 1
    assert abs(paper.cash_balance() - (before + 5.0)) < 1e-9 and paper.position_size(TOKEN) == 0.0


def test_extreme_price_before_window_end_is_not_a_resolution():
    import time

    fresh()
    paper._market_for = lambda token: (f"btc-updown-5m-{int(time.time()) + 600}", "Up")  # window still open
    paper.place_order(TOKEN, 0.50, 5.0, "BUY", "FAK")

    class R:
        def json(self):
            return {"clobTokenIds": f'["{TOKEN}", "222"]', "outcomePrices": '["1", "0"]'}  # trading at 0.99+

    paper.requests.get = lambda *a, **k: R()
    assert paper.settle_resolved() == 0 and paper.position_size(TOKEN) == 5.0


def test_fmt_num_handles_missing_values():
    from src.utils.logger import fmt_num

    assert fmt_num(None) == "n/a" and fmt_num(0.0123) == "0.012" and fmt_num(14.26, 1) == "14.3"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
