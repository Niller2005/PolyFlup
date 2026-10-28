"""Paper trading: a simulated CLOB account driven by REAL order books.

With `PAPER_TRADING=YES` the order layer (`src/trading/orders/*`, `web3_utils.get_balance`,
`ctf_operations`) delegates here instead of signing and posting real orders. Everything
above that layer (strategy, hedge monitoring, stop loss, pre-settlement exit,
settlement, TUI) runs unchanged against the paper account.

Realism:
  * Prices/depth come from the live public order book at the moment of each action.
  * Marketable orders walk the book and pay the CLOB V2 taker fee 0.07*p*(1-p) in pUSD.
  * POST_ONLY orders that would cross are rejected ("order crosses book"), FOK/FAK
    behave like the exchange, balances are checked.
  * Resting (maker) orders fill in a background matcher. `PAPER_MAKER_FILL_MODE`:
      cross  (default, conservative) fill only when the opposite best price crosses ours
      touch  (optimistic bound) also fill when the midpoint reaches our price
    Queue position and partial maker fills are not modelled, so maker fill rates are
    approximate; treat `cross` as a floor and `touch` as a ceiling.
  * Positions are redeemed at 1.00/0.00 once Gamma reports the market resolved.

State lives in the paper database (`trades_paper.db`): paper_account, paper_orders,
paper_positions, paper_fills. Delete that file to reset.
"""

import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import requests

from src.config.settings import (
    GAMMA_API_BASE,
    PAPER_MAKER_FILL_MODE,
    PAPER_STARTING_BALANCE,
    TAKER_FEE_RATE,
)
from src.data.db_connection import db_connection
from src.markets import parse_slug
from src.utils.logger import log, log_error

EPS = 1e-9
_lock = threading.RLock()
_started = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fee(price: float, size: float) -> float:
    return round(size * TAKER_FEE_RATE * price * (1.0 - price), 5)


# ---- storage ---------------------------------------------------------------
def init() -> None:
    with _lock, db_connection() as conn:
        c = conn.cursor()
        c.execute("CREATE TABLE IF NOT EXISTS paper_account (id INTEGER PRIMARY KEY CHECK (id=1), cash REAL, start_cash REAL, created_at TEXT)")
        c.execute(
            """CREATE TABLE IF NOT EXISTS paper_orders (
                id TEXT PRIMARY KEY, token_id TEXT, side TEXT, price REAL, size REAL, filled REAL DEFAULT 0,
                status TEXT, order_type TEXT, post_only INTEGER, created_at TEXT, updated_at TEXT,
                slug TEXT, outcome TEXT)"""
        )
        order_cols = [r[1] for r in c.execute("PRAGMA table_info(paper_orders)")]
        for col in ("slug", "outcome"):  # databases created before order labels
            if col not in order_cols:
                c.execute(f"ALTER TABLE paper_orders ADD COLUMN {col} TEXT")
        c.execute("CREATE TABLE IF NOT EXISTS paper_positions (token_id TEXT PRIMARY KEY, size REAL, avg_price REAL, slug TEXT, opened_at TEXT, outcome TEXT)")
        cols = [r[1] for r in c.execute("PRAGMA table_info(paper_positions)")]
        if "outcome" not in cols:  # databases created before outcome tracking
            c.execute("ALTER TABLE paper_positions ADD COLUMN outcome TEXT")
        c.execute(
            """CREATE TABLE IF NOT EXISTS paper_fills (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, order_id TEXT, token_id TEXT, side TEXT,
                price REAL, size REAL, fee REAL, liquidity TEXT)"""
        )
        if not c.execute("SELECT 1 FROM paper_account").fetchone():
            c.execute(
                "INSERT INTO paper_account (id, cash, start_cash, created_at) VALUES (1, ?, ?, ?)",
                (PAPER_STARTING_BALANCE, PAPER_STARTING_BALANCE, _now()),
            )
            log(f"📝 PAPER account created with ${PAPER_STARTING_BALANCE:,.2f} pUSD")


def _cash(c) -> float:
    return float(c.execute("SELECT cash FROM paper_account WHERE id=1").fetchone()[0])


def _reserved_cash(c) -> float:
    r = c.execute("SELECT COALESCE(SUM((size-filled)*price),0) FROM paper_orders WHERE status='LIVE' AND side='BUY'").fetchone()
    return float(r[0])


def _reserved_shares(c, token_id: str) -> float:
    r = c.execute("SELECT COALESCE(SUM(size-filled),0) FROM paper_orders WHERE status='LIVE' AND side='SELL' AND token_id=?", (token_id,)).fetchone()
    return float(r[0])


def cash_balance() -> float:
    with _lock:
        init()
        with db_connection() as conn:
            return _cash(conn.cursor())


def free_cash() -> float:
    with _lock:
        init()
        with db_connection() as conn:
            c = conn.cursor()
            return max(0.0, _cash(c) - _reserved_cash(c))


def position_size(token_id: str) -> float:
    with _lock:
        init()
        with db_connection() as conn:
            r = conn.execute("SELECT size FROM paper_positions WHERE token_id=?", (token_id,)).fetchone()
            return float(r[0]) if r else 0.0


def balance_allowance(token_id: Optional[str]) -> dict:
    """Shape of ClobClient.get_balance_allowance(), already in whole units."""
    bal = position_size(token_id) if token_id else free_cash()
    return {"balance": bal, "allowance": 1e12}


def positions() -> List[dict]:
    with _lock:
        init()
        with db_connection() as conn:
            rows = conn.execute("SELECT token_id, size, avg_price, slug, outcome FROM paper_positions WHERE size > 0.001").fetchall()
    return [
        {"token_id": t, "condition_id": "", "current_size": s, "avg_price": a, "slug": slug or "", "title": slug or "", "outcome": outcome or ""}
        for t, s, a, slug, outcome in rows
    ]


# ---- order book helpers ------------------------------------------------------
def _book(token_id: str) -> Tuple[List[Tuple[float, float]], List[Tuple[float, float]]]:
    """(bids best-first, asks best-first) as (price, size)."""
    from src.trading.orders.client import client

    b = client.get_order_book(token_id)
    bids = sorted(((float(x["price"]), float(x["size"])) for x in (b.get("bids") or [])), reverse=True)
    asks = sorted((float(x["price"]), float(x["size"])) for x in (b.get("asks") or []))
    return bids, asks


def _market_for(token_id: str) -> Tuple[str, str]:
    """(slug, outcome label such as "Up"/"Down") for a token via Gamma; ("", "") if unknown."""
    try:
        import json as _json

        r = requests.get(f"{GAMMA_API_BASE}/markets", params={"clob_token_ids": token_id}, timeout=8).json()
        if not r:
            return "", ""
        m = r[0]
        ids = m.get("clobTokenIds")
        outs = m.get("outcomes")
        ids = _json.loads(ids) if isinstance(ids, str) else ids
        outs = _json.loads(outs) if isinstance(outs, str) else outs
        return m.get("slug", ""), (outs[ids.index(token_id)] if ids and outs else "")
    except Exception:
        return "", ""


_market_cache: Dict[str, Tuple[str, str]] = {}


def _market_cached(token_id: str) -> Tuple[str, str]:
    if token_id not in _market_cache:
        found = _market_for(token_id)
        if found[0]:  # don't cache failures
            _market_cache[token_id] = found
        return found
    return _market_cache[token_id]


# ---- fills -------------------------------------------------------------------
def _apply_fill(c, order_id: str, token_id: str, side: str, price: float, size: float, liquidity: str) -> float:
    """Update cash/positions for one fill; returns the fee paid."""
    fee = _fee(price, size) if liquidity == "TAKER" else 0.0
    if side == "BUY":
        c.execute("UPDATE paper_account SET cash = cash - ? WHERE id=1", (price * size + fee,))
        row = c.execute("SELECT size, avg_price FROM paper_positions WHERE token_id=?", (token_id,)).fetchone()
        if row:
            new = row[0] + size
            avg = (row[0] * row[1] + size * price) / new
            c.execute("UPDATE paper_positions SET size=?, avg_price=? WHERE token_id=?", (new, avg, token_id))
        else:
            slug, outcome = _market_cached(token_id)
            c.execute("INSERT INTO paper_positions (token_id, size, avg_price, slug, opened_at, outcome) VALUES (?,?,?,?,?,?)", (token_id, size, price, slug, _now(), outcome))
    else:
        c.execute("UPDATE paper_account SET cash = cash + ? WHERE id=1", (price * size - fee,))
        c.execute("UPDATE paper_positions SET size = MAX(0, size - ?) WHERE token_id=?", (size, token_id))
        c.execute("DELETE FROM paper_positions WHERE token_id=? AND size < 0.0001", (token_id,))
    c.execute(
        "INSERT INTO paper_fills (ts, order_id, token_id, side, price, size, fee, liquidity) VALUES (?,?,?,?,?,?,?,?)",
        (_now(), order_id, token_id, side, price, size, fee, liquidity),
    )
    return fee


def _walk(levels: List[Tuple[float, float]], side: str, limit: float, size: float) -> List[Tuple[float, float]]:
    """Fills from the book for a marketable order: [(price, size)]."""
    out, left = [], size
    for price, avail in levels:
        if (side == "BUY" and price > limit + EPS) or (side == "SELL" and price < limit - EPS):
            break
        take = min(avail, left)
        if take > EPS:
            out.append((price, take))
            left -= take
        if left <= EPS:
            break
    return out


def place_order(token_id: str, price: float, size: float, side: str, order_type: str = "GTC", post_only: bool = False) -> dict:
    """Simulate posting an order. Result mirrors the real wrappers' raw response."""
    side = side.upper()
    order_type = order_type.upper()
    with _lock:
        init()
        oid = "0xpaper" + uuid.uuid4().hex[:24]
        try:
            bids, asks = _book(token_id)
        except Exception as e:
            return {"success": False, "status": "ERROR", "errorMsg": f"paper: order book unavailable ({e})"}

        with db_connection() as conn:
            c = conn.cursor()
            best_ask = asks[0][0] if asks else None
            best_bid = bids[0][0] if bids else None
            crosses = (side == "BUY" and best_ask is not None and price >= best_ask - EPS) or (
                side == "SELL" and best_bid is not None and price <= best_bid + EPS
            )
            if post_only and crosses:
                return {"success": False, "status": "ERROR", "errorMsg": "order crosses book (post-only)"}

            # funds / inventory check against what is free after resting orders
            if side == "BUY":
                need = price * size * (1 + 0.02)  # slack for the taker fee
                if need > _cash(c) - _reserved_cash(c) + EPS:
                    return {"success": False, "status": "ERROR", "errorMsg": "not enough balance / allowance"}
            else:
                row = c.execute("SELECT size FROM paper_positions WHERE token_id=?", (token_id,)).fetchone()
                if (row[0] if row else 0.0) - _reserved_shares(c, token_id) < size - EPS:
                    return {"success": False, "status": "ERROR", "errorMsg": "not enough balance / allowance"}

            fills = _walk(asks if side == "BUY" else bids, side, price, size) if crosses else []
            got = sum(s for _, s in fills)
            if order_type == "FOK" and got < size - EPS:
                return {"success": False, "status": "ERROR", "errorMsg": "FOK order not fully filled error"}
            if order_type in ("FAK", "FOK") and got <= EPS:
                return {"success": False, "status": "ERROR", "errorMsg": "no orders found to match with FAK order"}

            slug, outcome = _market_cached(token_id)
            c.execute(
                "INSERT INTO paper_orders (id, token_id, side, price, size, filled, status, order_type, post_only, created_at, updated_at, slug, outcome) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (oid, token_id, side, price, size, 0.0, "LIVE", order_type, int(post_only), _now(), _now(), slug, outcome),
            )
            total_fee = 0.0
            for p, s in fills:
                total_fee += _apply_fill(c, oid, token_id, side, p, s, "TAKER")
            status = "LIVE"
            if got >= size - EPS:
                status = "MATCHED"
            elif order_type in ("FAK", "FOK"):
                status = "MATCHED"  # remainder killed
            c.execute("UPDATE paper_orders SET filled=?, status=?, updated_at=? WHERE id=?", (got, status, _now(), oid))
            if fills:
                avg = sum(p * s for p, s in fills) / got
                log(f"   📝 PAPER {side} {got:.2f} @ ${avg:.3f} (taker, fee ${total_fee:.3f}) {oid[:12]}")
            else:
                log(f"   📝 PAPER {side} {size:.2f} @ ${price:.2f} resting {'POST_ONLY ' if post_only else ''}{oid[:12]}")
            return {"success": True, "status": status.lower() if status == "LIVE" else "matched", "orderID": oid, "errorMsg": ""}


def cancel(order_id: str) -> bool:
    with _lock:
        init()
        with db_connection() as conn:
            row = conn.execute("SELECT status FROM paper_orders WHERE id=?", (order_id,)).fetchone()
            if not row:
                return True
            if row[0] == "LIVE":
                conn.execute("UPDATE paper_orders SET status='CANCELED', updated_at=? WHERE id=?", (_now(), order_id))
            return True


def cancel_all(token_id: Optional[str] = None) -> List[str]:
    with _lock:
        init()
        with db_connection() as conn:
            q = "SELECT id FROM paper_orders WHERE status='LIVE'" + (" AND token_id=?" if token_id else "")
            ids = [r[0] for r in conn.execute(q, (token_id,) if token_id else ())]
            for i in ids:
                conn.execute("UPDATE paper_orders SET status='CANCELED', updated_at=? WHERE id=?", (_now(), i))
            return ids


def _order_dict(r) -> dict:
    return {
        "id": r[0], "asset_id": r[1], "side": r[2], "price": str(r[3]), "original_size": str(r[4]),
        "size_matched": str(r[5]), "status": r[6], "order_type": r[7],
    }


def get_order(order_id: str) -> Optional[dict]:
    with _lock:
        init()
        with db_connection() as conn:
            r = conn.execute("SELECT id, token_id, side, price, size, filled, status, order_type FROM paper_orders WHERE id=?", (order_id,)).fetchone()
    return _order_dict(r) if r else None


def open_orders(asset_id: Optional[str] = None) -> List[dict]:
    with _lock:
        init()
        with db_connection() as conn:
            q = "SELECT id, token_id, side, price, size, filled, status, order_type FROM paper_orders WHERE status='LIVE'"
            rows = conn.execute(q + (" AND token_id=?" if asset_id else ""), (asset_id,) if asset_id else ()).fetchall()
    return [_order_dict(r) for r in rows]


# ---- background matcher -------------------------------------------------------
def match_resting() -> int:
    """Fill resting orders against the current book. Returns number of orders filled."""
    filled = 0
    with _lock:
        init()
        with db_connection() as conn:
            live = conn.execute("SELECT id, token_id, side, price, size, filled FROM paper_orders WHERE status='LIVE'").fetchall()
        books: Dict[str, tuple] = {}
        for oid, token_id, side, price, size, done in live:
            if token_id not in books:
                try:
                    books[token_id] = _book(token_id)
                except Exception as e:
                    # a resolved/expired market has no book any more: its resting orders can never fill, and
                    # polling it every cycle only produces 404s. (Transient errors keep the order alive.)
                    if "no orderbook" in str(e).lower() or "404" in str(e):
                        with db_connection() as conn:
                            conn.execute(
                                "UPDATE paper_orders SET status='CANCELED', updated_at=? WHERE token_id=? AND status='LIVE'",
                                (_now(), token_id),
                            )
                        log(f"   🧹 PAPER: market of {token_id[:10]}… has no order book (ended) - resting orders cancelled")
                        books[token_id] = ([], [])
                    continue
            bids, asks = books[token_id]
            bid = bids[0][0] if bids else None
            ask = asks[0][0] if asks else None
            hit = False
            if side == "BUY" and ask is not None:
                hit = ask <= price + EPS or (PAPER_MAKER_FILL_MODE == "touch" and bid is not None and (bid + ask) / 2 <= price + EPS)
            elif side == "SELL" and bid is not None:
                hit = bid >= price - EPS or (PAPER_MAKER_FILL_MODE == "touch" and ask is not None and (bid + ask) / 2 >= price - EPS)
            if not hit:
                continue
            qty = size - done
            with db_connection() as conn:
                c = conn.cursor()
                if side == "BUY" and price * qty > _cash(c) + EPS:
                    continue  # no longer affordable
                _apply_fill(c, oid, token_id, side, price, qty, "MAKER")
                c.execute("UPDATE paper_orders SET filled=?, status='MATCHED', updated_at=? WHERE id=?", (size, _now(), oid))
            log(f"   📝 PAPER maker fill: {side} {qty:.2f} @ ${price:.2f} (no fee) {oid[:12]}")
            filled += 1
    return filled


def settle_resolved() -> int:
    """Redeem positions of resolved markets at 1.00 / 0.00."""
    settled = 0
    with _lock:
        init()
        with db_connection() as conn:
            rows = conn.execute("SELECT token_id, size, slug FROM paper_positions WHERE size > 0").fetchall()
        for token_id, size, slug in rows:
            if not slug:
                slug = _market_for(token_id)[0]
                if not slug:
                    continue
            try:
                m = requests.get(f"{GAMMA_API_BASE}/markets/slug/{slug}", timeout=8).json()
                import json as _json

                ids = m.get("clobTokenIds")
                prices = m.get("outcomePrices")
                ids = _json.loads(ids) if isinstance(ids, str) else ids
                prices = [float(x) for x in (_json.loads(prices) if isinstance(prices, str) else prices)]
                p = prices[ids.index(token_id)]
            except Exception:
                continue
            parsed = parse_slug(slug)
            if parsed and parsed[2].astimezone(timezone.utc) > datetime.now(timezone.utc):
                continue  # window still open: 0.99/0.01 is a price, not a resolution
            if not (p >= 0.99 or p <= 0.01):
                continue  # not resolved yet
            payout = 1.0 if p >= 0.99 else 0.0
            with db_connection() as conn:
                c = conn.cursor()
                c.execute("UPDATE paper_account SET cash = cash + ? WHERE id=1", (size * payout,))
                c.execute("DELETE FROM paper_positions WHERE token_id=?", (token_id,))
                c.execute(
                    "INSERT INTO paper_fills (ts, order_id, token_id, side, price, size, fee, liquidity) VALUES (?,?,?,?,?,?,?,?)",
                    (_now(), "SETTLE", token_id, "REDEEM", payout, size, 0.0, "SETTLE"),
                )
                c.execute("UPDATE paper_orders SET status='CANCELED' WHERE token_id=? AND status='LIVE'", (token_id,))
            log(f"   📝 PAPER settled {slug}: {size:.2f} shares → ${size * payout:.2f}")
            settled += 1
    return settled


def start(match_interval: float = 2.0, settle_interval: float = 30.0) -> None:
    """Start the background matcher/settler (idempotent)."""
    global _started
    if _started:
        return
    _started = True
    init()
    # orders left LIVE by a previous process (restart/crash) belong to attempts nobody monitors any more
    stale = cancel_all()
    if stale:
        log(f"   🧹 PAPER: cancelled {len(stale)} resting order(s) left over from a previous run")

    def loop():
        last_settle = 0.0
        while True:
            try:
                match_resting()
                if time.time() - last_settle >= settle_interval:
                    last_settle = time.time()
                    settle_resolved()
            except Exception as e:
                log_error(f"paper matcher error: {e}")
            time.sleep(match_interval)

    threading.Thread(target=loop, daemon=True, name="PaperExchange").start()
    log(
        f"📝 PAPER TRADING active: simulated fills on live books | cash ${cash_balance():,.2f} | maker fills: {PAPER_MAKER_FILL_MODE}"
    )
