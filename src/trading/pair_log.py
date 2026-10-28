"""Measurement records for the pair strategy: attempts, order events and position marks.

Nothing here may affect trading: every function swallows its own errors. Tables (migration 14):

- pair_attempts  one row per entry+hedge attempt (or skipped opportunity) with prices, books at
                 placement, which leg filled first and when, how it ended, what the sale/hold cost,
                 and (filled in after the market resolves) which side won.
- order_events   every order placed/cancelled/sold, linked to the attempt of the calling thread.
- position_marks periodic mark of every open leg (bid/ask/mid, seconds left) for path analysis.
"""

import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from src.data.db_connection import db_connection
from src.utils.logger import log_error

_ctx = threading.local()
_last_mark = 0.0
_ATTEMPT_COLS = {
    "mode", "profile", "entry_bid", "entry_ask", "hedge_bid", "hedge_ask", "outcome", "first_leg",
    "first_fill_s", "second_fill_s", "other_bid_at_first", "other_ask_at_first", "trail_moves",
    "final_entry_price", "final_hedge_price", "entry_filled", "hedge_filled", "resolution",
    "sell_price", "sell_size", "sell_fee", "realized_pnl", "winner_side", "filled_leg_won",
    "trade_id", "closed_at", "note", "entry_order_id", "hedge_order_id", "net_edge", "secs_into_window",
    "confidence", "p_up", "spot", "strike", "dist_pct", "entry_depth", "hedge_depth", "cash", "exposure",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(fn):
    try:
        with db_connection() as conn:
            return fn(conn)
    except Exception as e:  # measurement must never break trading
        log_error(f"pair_log: {e}")
        return None


def current() -> Optional[Dict[str, Any]]:
    return getattr(_ctx, "attempt", None)


def end() -> None:
    _ctx.attempt = None


def begin(
    symbol: str,
    slug: str,
    mode: str,
    entry_side: str,
    size: float,
    entry_price: float,
    hedge_price: float,
    secs_left: Optional[float] = None,
    timeout_s: Optional[float] = None,
    **extra: Any,
) -> Optional[int]:
    """Insert an attempt row and make it the calling thread's current attempt."""
    cols = {
        "ts": _now(), "symbol": symbol, "slug": slug, "mode": mode, "entry_side": entry_side,
        "size": size, "entry_price": entry_price, "hedge_price": hedge_price,
        "combined": entry_price + hedge_price, "secs_left": secs_left, "timeout_s": timeout_s,
    }
    cols.update({k: v for k, v in extra.items() if k in _ATTEMPT_COLS})

    def run(conn):
        cur = conn.execute(
            f"INSERT INTO pair_attempts ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            list(cols.values()),
        )
        return cur.lastrowid

    attempt_id = _safe(run)
    tokens = extra.get("tokens")
    _ctx.attempt = {"id": attempt_id, "symbol": symbol, "slug": slug, "tokens": tokens} if attempt_id else None
    return attempt_id


def update(**cols: Any) -> None:
    """Update the calling thread's current attempt."""
    a = current()
    if not a:
        return
    cols = {k: v for k, v in cols.items() if k in _ATTEMPT_COLS}
    if not cols:
        return
    _safe(
        lambda conn: conn.execute(
            f"UPDATE pair_attempts SET {', '.join(k + '=?' for k in cols)} WHERE id=?",
            list(cols.values()) + [a["id"]],
        )
    )


def skipped(symbol: str, reason: str, **extra: Any) -> None:
    """Record an opportunity that was evaluated but not placed."""
    from src.markets import slug_for

    try:
        slug = slug_for(symbol)
    except Exception:
        slug = None
    cols = {"ts": _now(), "symbol": symbol, "slug": slug, "outcome": "skipped", "note": reason[:200]}
    cols.update({k: v for k, v in extra.items() if k in _ATTEMPT_COLS or k in ("size", "entry_side", "entry_price", "hedge_price", "secs_left")})
    _safe(
        lambda conn: conn.execute(
            f"INSERT INTO pair_attempts ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            list(cols.values()),
        )
    )


def tick(t_s: float, phase: str = "wait", entry_filled: float = 0.0, hedge_filled: float = 0.0) -> None:
    """Top of book of both legs (and spot) at one poll of the current attempt."""
    a = current()
    if not a or not a.get("tokens"):
        return
    e_tok, h_tok = a["tokens"]
    eb, ea = _top(e_tok)
    hb, ha = _top(h_tok)
    _safe(
        lambda conn: conn.execute(
            "INSERT INTO attempt_ticks (attempt_id, ts, t_s, phase, entry_bid, entry_ask, hedge_bid, hedge_ask, entry_filled, hedge_filled, spot) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (a["id"], _now(), t_s, phase, eb, ea, hb, ha, entry_filled, hedge_filled, _spot(a["symbol"])),
        )
    )


def _top(token_id: str):
    """(bid, ask): websocket cache, else the CLOB book (the cache is empty for tokens we never subscribed)."""
    try:
        from src.trading.execution import _book_top

        b, a = _book_top(token_id)
        return b, a
    except Exception:
        return None, None


def _spot(symbol: str) -> Optional[float]:
    try:
        from src.trading.late_window import _prices
        from src.markets import window_bounds
        from datetime import datetime
        from zoneinfo import ZoneInfo

        start, _ = window_bounds(symbol, datetime.now(tz=ZoneInfo("America/New_York")))
        prices = _prices(symbol, start)
        return prices[1] if prices else None
    except Exception:
        return None


def event(name: str, **f: Any) -> None:
    """Append an order event (linked to the thread's current attempt when there is one)."""
    a = current()
    cols = {
        "ts": _now(), "attempt_id": a["id"] if a else None, "symbol": f.pop("symbol", a["symbol"] if a else None),
        "event": name,
    }
    cols.update({k: v for k, v in f.items() if k in ("token_id", "order_id", "side", "price", "size", "filled", "order_type", "post_only", "success", "error", "purpose", "note")})
    _safe(
        lambda conn: conn.execute(
            f"INSERT INTO order_events ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            list(cols.values()),
        )
    )


def record_sale(symbol: str, size: float, price: float, reason: str) -> None:
    """An emergency/pre-settlement sale completed: log it and fold it into the current attempt."""
    try:
        from src.config.settings import TAKER_FEE_RATE
        from src.trading.edge import taker_fee

        fee = taker_fee(price, TAKER_FEE_RATE) * size
    except Exception:
        fee = 0.0
    event("sell", symbol=symbol, side="SELL", price=price, size=size, success=1, purpose=reason[:80])
    a = current()
    if not a or not a.get("id"):
        return

    def run(conn):
        row = conn.execute("SELECT sell_price, sell_size, sell_fee, first_leg, entry_side, entry_price, hedge_price FROM pair_attempts WHERE id=?", (a["id"],)).fetchone()
        if not row:
            return
        old_p, old_s, old_f, first_leg, entry_side, ep, hp = row
        old_s = old_s or 0.0
        tot = old_s + size
        avg = ((old_p or 0.0) * old_s + price * size) / tot if tot else price
        conn.execute(
            "UPDATE pair_attempts SET sell_price=?, sell_size=?, sell_fee=?, resolution='sell' WHERE id=?",
            (avg, tot, (old_f or 0.0) + fee, a["id"]),
        )

    _safe(run)


def event_order_batch(orders, results, order_type: str = "GTC") -> None:
    for op, r in zip(orders, results):
        event(
            "place", token_id=op.get("token_id"), order_id=(r or {}).get("order_id"), side=op.get("side"),
            price=op.get("price"), size=op.get("size"), order_type=order_type,
            post_only=int(bool(op.get("post_only"))), success=int(bool((r or {}).get("success"))),
            error=(r or {}).get("error") or None,
        )


# --- periodic marks of open legs ---------------------------------------------------------------
def snapshot_positions(every: float = 30.0) -> int:
    """Record bid/ask/mid of every open leg (trades and paper holdings); throttled to `every` s."""
    global _last_mark
    now = time.time()
    if now - _last_mark < every:
        return 0
    _last_mark = now
    def run(conn):
        rows = []
        for tid, symbol, slug, side, tok, size, entry, hedged, hedge_price, win_end in conn.execute(
            "SELECT id, symbol, slug, side, token_id, size, entry_price, is_hedged, hedge_order_price, window_end "
            "FROM trades WHERE settled = 0 AND COALESCE(exited_early,0)=0"
        ):
            rows.append((tid, symbol, slug, side, tok, size, entry, win_end))
        try:
            for tok, size, avg, slug, outcome in conn.execute(
                "SELECT token_id, size, avg_price, slug, outcome FROM paper_positions WHERE size > 0.001"
            ):
                rows.append((None, None, slug, (outcome or "").upper(), tok, size, avg, None))
        except Exception:
            pass  # not a paper database
        n = 0
        # path after we stopped waiting: both legs of recent attempts, to see what the market did next
        for aid, t0, e_tok, h_tok in conn.execute(
            "SELECT a.id, a.ts, "
            "(SELECT token_id FROM order_events WHERE attempt_id=a.id AND event='place' ORDER BY id LIMIT 1), "
            "(SELECT token_id FROM order_events WHERE attempt_id=a.id AND event='place' ORDER BY id LIMIT 1 OFFSET 1) "
            "FROM pair_attempts a WHERE a.outcome IN ('timeout','both') AND a.winner_side IS NULL "
            "AND datetime(a.ts) > datetime('now', '-16 minutes')"
        ).fetchall():
            if not e_tok or not h_tok:
                continue
            eb, ea = _top(e_tok)
            hb, ha = _top(h_tok)
            try:
                t_s = (datetime.now(timezone.utc) - datetime.fromisoformat(t0)).total_seconds()
            except Exception:
                t_s = None
            conn.execute(
                "INSERT INTO attempt_ticks (attempt_id, ts, t_s, phase, entry_bid, entry_ask, hedge_bid, hedge_ask) VALUES (?,?,?,?,?,?,?,?)",
                (aid, _now(), t_s, "after", eb, ea, hb, ha),
            )
        for tid, symbol, slug, side, tok, size, avg, win_end in rows:
            bid, ask = _top(tok)
            if not bid and not ask:
                continue
            mid = (bid + ask) / 2 if bid and ask else (bid or ask)
            left = None
            try:
                if win_end:
                    left = (datetime.fromisoformat(win_end) - datetime.now(timezone.utc)).total_seconds()
            except Exception:
                pass
            conn.execute(
                "INSERT INTO position_marks (ts, trade_id, symbol, slug, side, token_id, size, avg_price, bid, ask, mid, secs_left) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (_now(), tid, symbol, slug, side, tok, size, avg, bid, ask, mid, left),
            )
            n += 1
        return n

    return _safe(run) or 0


# --- outcome back-fill -------------------------------------------------------------------------
def resolve_attempts(limit: int = 30) -> int:
    """Fill winner_side / filled_leg_won / realized_pnl for attempts whose market has resolved."""
    from src.trading.settlement import get_market_resolution

    done_n = 0

    def pending(conn):
        return conn.execute(
            "SELECT DISTINCT slug FROM pair_attempts WHERE winner_side IS NULL AND slug IS NOT NULL "
            "AND datetime(ts) < datetime('now', '-10 minutes') LIMIT ?",
            (limit,),
        ).fetchall()

    for (slug,) in _safe(pending) or []:
        try:
            done, prices = get_market_resolution(slug)
        except Exception:
            continue
        if not done or not prices:
            continue
        winner = "UP" if prices[0] >= 0.99 else "DOWN"

        def run(conn):
            conn.execute("UPDATE pair_attempts SET winner_side=? WHERE slug=? AND winner_side IS NULL", (winner, slug))
            for aid, first_leg, entry_side, size, ep, hp, sp, ss, fee, filled, outcome in conn.execute(
                "SELECT id, first_leg, entry_side, size, final_entry_price, final_hedge_price, sell_price, sell_size, sell_fee, entry_filled, outcome "
                "FROM pair_attempts WHERE slug=? AND outcome IN ('timeout','both') AND filled_leg_won IS NULL",
                (slug,),
            ).fetchall():
                if first_leg not in ("entry", "hedge"):
                    continue
                leg_side = entry_side if first_leg == "entry" else ("DOWN" if entry_side == "UP" else "UP")
                paid = ep if first_leg == "entry" else hp
                won = 1 if leg_side == winner else 0
                pnl = None
                if outcome == "timeout" and paid is not None:
                    qty = size or 0.0
                    pnl = ((sp - paid) * (ss or qty) - (fee or 0.0)) if sp is not None else (won - paid) * qty
                conn.execute(
                    "UPDATE pair_attempts SET filled_leg_won=?, realized_pnl=COALESCE(?, realized_pnl) WHERE id=?",
                    (won, pnl, aid),
                )

        _safe(run)
        done_n += 1
    return done_n


# --- order-wrapper decorators (every order path is recorded without touching its callers) -------
def logged_batch(fn):
    def wrapper(orders, order_type="GTC"):
        results = fn(orders, order_type)
        try:
            event_order_batch(orders, results, order_type)
        except Exception:
            pass
        return results

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


def logged_single(purpose: str):
    """Wrap place_limit_order / place_market_order (token_id, [price,] size, side, ...)."""

    def deco(fn):
        def wrapper(*args, **kwargs):
            result = fn(*args, **kwargs)
            try:
                names = fn.__code__.co_varnames[: fn.__code__.co_argcount]
                a = dict(zip(names, args))
                a.update(kwargs)
                event(
                    "place", token_id=a.get("token_id"), order_id=(result or {}).get("order_id"),
                    side=a.get("side"), price=a.get("price"), size=a.get("size"),
                    order_type=a.get("order_type"), success=int(bool((result or {}).get("success"))),
                    error=(result or {}).get("error") or None, purpose=purpose,
                )
            except Exception:
                pass
            return result

        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper

    return deco


def logged_cancel(fn):
    def wrapper(order_id):
        ok = fn(order_id)
        try:
            event("cancel", order_id=order_id, success=int(bool(ok)))
        except Exception:
            pass
        return ok

    wrapper.__name__ = fn.__name__
    return wrapper


_last_resolve = 0.0


def maintenance() -> None:
    """Called from the main loop: throttled position marks (30s) and outcome back-fill (90s)."""
    global _last_resolve
    try:
        snapshot_positions(30.0)
        if time.time() - _last_resolve >= 90.0:
            _last_resolve = time.time()
            resolve_attempts()
    except Exception as e:
        log_error(f"pair_log maintenance: {e}")
