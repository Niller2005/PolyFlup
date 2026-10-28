"""Parameters measured from our own recorded pair attempts instead of fixed constants.

Everything here is derived from `pair_attempts` / `attempt_ticks` of the CURRENT database (so a real
and a paper database learn separately) and falls back to the configured value until there is enough
data. Estimates are shrunk towards a prior with `ADAPTIVE_PRIOR_N` pseudo-observations so a handful of
attempts cannot swing a parameter. All functions are cheap (60s cache) and never raise.

What is measured
  leg_fail_rate(symbol)   P(only one leg fills): market-level estimate shrunk to the global one
  legout_loss(symbol)     mean loss per share of a leg-out (taker sale incl. fee)
  pair_ev(symbol)         (1-r) * edge_when_completed - r * legout_loss, per share
  timeout_seconds(...)    p90 of the time completed pairs took (waiting longer rarely pays)
  lock_loss(default)      the certain loss we should tolerate to complete a pair, found by replaying
                          recorded leg-outs against the book ticks recorded while they waited
  pair_size(symbol, n)    full size only while pair_ev >= 0, minimum size ("explore") otherwise, so a
                          losing market keeps producing data at minimal cost instead of being switched off
"""

import sqlite3
import time
from typing import Dict, List, Optional, Sequence, Tuple

from src.data.db_connection import db_connection
from src.utils.logger import log_error

_CACHE: Dict[str, Tuple[float, object]] = {}
_TTL = 60.0


def _enabled() -> bool:
    from src.config.settings import ADAPTIVE

    return ADAPTIVE


def _cached(key: str, fn):
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    try:
        val = fn()
    except Exception as e:
        log_error(f"adaptive {key}: {e}")
        val = None
    _CACHE[key] = (now, val)
    return val


def clear_cache() -> None:
    _CACHE.clear()


# ---- pure statistics (unit-tested) ---------------------------------------------------------------
def shrink(successes: float, n: float, prior: float, prior_n: float) -> float:
    """Posterior mean of a rate with `prior_n` pseudo-observations at `prior`."""
    return (successes + prior * prior_n) / (n + prior_n) if (n + prior_n) > 0 else prior


def p_quantile(values: Sequence[float], q: float) -> Optional[float]:
    if not values:
        return None
    xs = sorted(values)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def best_lock_loss(
    cases: Sequence[Tuple[float, float, float, List[Tuple[float, float]]]],
    grid: Sequence[float],
) -> Optional[float]:
    """Pick the tolerated certain loss that would have made the most money on recorded leg-outs.

    Each case is (paid, size, actual_pnl, [(t_s, other_ask), ...]) with only the ticks after the first
    fill. Resting the second leg at `1 - paid + A` completes the pair at a loss of A whenever the other
    leg's ask touched that price; otherwise the leg-out result stands. Returns the best A or None.
    """
    if not cases:
        return None
    best, best_pnl = None, None
    for a in grid:
        total = 0.0
        for paid, size, actual, ticks in cases:
            cap = 1.0 - paid + a
            total += (-a * size) if any(ask <= cap + 1e-9 for _, ask in ticks) else actual
        if best_pnl is None or total > best_pnl + 1e-9:  # ties go to the smaller tolerance
            best, best_pnl = a, total
    return best


# ---- data access ---------------------------------------------------------------------------------
def _attempts(limit: int) -> List[dict]:
    with db_connection() as conn:
        conn.row_factory = sqlite3.Row
        return [
            dict(r)
            for r in conn.execute(
                """SELECT id, symbol, outcome, first_leg, size, final_entry_price, final_hedge_price,
                          second_fill_s, sell_price, sell_size, sell_fee, resolution
                   FROM pair_attempts WHERE outcome IN ('both', 'timeout') AND first_leg IS NOT NULL
                   ORDER BY id DESC LIMIT ?""",
                (limit,),
            )
        ]


def _loss_per_share(a: dict) -> Optional[float]:
    """Loss (positive) per share of a SOLD leg-out; held ones are excluded (their result is not yet known)."""
    if a["outcome"] != "timeout" or a["sell_price"] is None or (a["resolution"] or "").startswith("hold"):
        return None
    paid = a["final_entry_price"] if a["first_leg"] == "entry" else a["final_hedge_price"]
    qty = a["sell_size"] or a["size"] or 0.0
    if paid is None or not qty:
        return None
    return (paid - a["sell_price"]) + (a["sell_fee"] or 0.0) / qty


def _edge(a: dict) -> Optional[float]:
    if a["outcome"] == "both" and a["final_entry_price"] and a["final_hedge_price"]:
        return 1.0 - a["final_entry_price"] - a["final_hedge_price"]
    return None


def _stats() -> dict:
    from src.config.settings import ADAPTIVE_PRIOR_N, ADAPTIVE_WINDOW, KELLY_LEG_FAIL_RATE, KELLY_LEG_LOSS, MIN_NET_EDGE

    rows = _attempts(ADAPTIVE_WINDOW)
    out: dict = {"n": len(rows), "by": {}}
    r_global = shrink(sum(r["outcome"] == "timeout" for r in rows), len(rows), KELLY_LEG_FAIL_RATE, ADAPTIVE_PRIOR_N)
    losses = [x for x in map(_loss_per_share, rows) if x is not None]
    l_global = shrink(sum(losses), len(losses), KELLY_LEG_LOSS, ADAPTIVE_PRIOR_N / 2)
    edges = [x for x in map(_edge, rows) if x is not None]
    e_global = shrink(sum(edges), len(edges), MIN_NET_EDGE, ADAPTIVE_PRIOR_N / 2)
    out["global"] = {"r": r_global, "loss": l_global, "edge": e_global}
    by: Dict[str, List[dict]] = {}
    for r in rows:
        by.setdefault(r["symbol"], []).append(r)
    for sym, rs in by.items():
        ls = [x for x in map(_loss_per_share, rs) if x is not None]
        es = [x for x in map(_edge, rs) if x is not None]
        out["by"][sym] = {
            "n": len(rs),
            "r": shrink(sum(x["outcome"] == "timeout" for x in rs), len(rs), r_global, ADAPTIVE_PRIOR_N),
            "loss": shrink(sum(ls), len(ls), l_global, ADAPTIVE_PRIOR_N / 2),
            "edge": shrink(sum(es), len(es), e_global, ADAPTIVE_PRIOR_N / 2),
        }
    out["completion_s"] = {
        "5m": [r["second_fill_s"] for r in rows if r["outcome"] == "both" and r["second_fill_s"] and r["symbol"].endswith("-5M")],
        "15m": [r["second_fill_s"] for r in rows if r["outcome"] == "both" and r["second_fill_s"] and not r["symbol"].endswith("-5M")],
    }
    return out


def stats() -> Optional[dict]:
    return _cached("stats", _stats)


# ---- public parameters -----------------------------------------------------------------------------
def _sym(symbol: Optional[str]) -> Optional[dict]:
    s = stats()
    if not s:
        return None
    return s["by"].get(symbol) if symbol and symbol in s["by"] else s["global"]


def leg_fail_rate(symbol: Optional[str], default: float) -> float:
    s = _sym(symbol) if _enabled() else None
    return s["r"] if s else default


def legout_loss(symbol: Optional[str], default: float) -> float:
    s = _sym(symbol) if _enabled() else None
    return s["loss"] if s else default


def pair_edge(symbol: Optional[str], default: float) -> float:
    s = _sym(symbol) if _enabled() else None
    return s["edge"] if s else default


def pair_ev(symbol: Optional[str]) -> Optional[float]:
    """Expected result per share of one maker-pair attempt, or None without an estimate."""
    s = _sym(symbol) if _enabled() else None
    if not s:
        return None
    return (1.0 - s["r"]) * s["edge"] - s["r"] * s["loss"]


def timeout_seconds(symbol: str, default: float) -> float:
    """p90 of completion times of finished pairs (+5s), never above the configured/scaled default."""
    if not _enabled():
        return default
    from src.config.settings import ADAPTIVE_MIN_N

    s = stats()
    if not s:
        return default
    xs = s["completion_s"]["5m" if symbol.endswith("-5M") else "15m"]
    if len(xs) < ADAPTIVE_MIN_N:
        return default
    q = p_quantile(xs, 0.9)
    return default if q is None else max(15.0, min(default, q + 5.0))


def _lock_loss() -> Optional[float]:
    from src.config.settings import ADAPTIVE_MIN_N, ADAPTIVE_WINDOW

    cases = []
    with db_connection() as conn:
        rows = conn.execute(
            """SELECT id, first_leg, first_fill_s, size, final_entry_price, final_hedge_price, sell_price, sell_size, sell_fee
               FROM pair_attempts WHERE outcome='timeout' AND sell_price IS NOT NULL AND first_leg IS NOT NULL
               ORDER BY id DESC LIMIT ?""",
            (ADAPTIVE_WINDOW,),
        ).fetchall()
        for aid, leg, f1, size, ep, hp, sp, ss, fee in rows:
            paid = ep if leg == "entry" else hp
            if paid is None or sp is None:
                continue
            col = "hedge_ask" if leg == "entry" else "entry_ask"
            ticks = [
                (t, a)
                for t, a in conn.execute(
                    f"SELECT t_s, {col} FROM attempt_ticks WHERE attempt_id=? AND phase='wait' AND {col} IS NOT NULL", (aid,)
                )
                if f1 is None or t >= f1
            ]
            if not ticks:
                continue
            qty = ss or size
            cases.append((paid, size, (sp - paid) * qty - (fee or 0.0), ticks))
    if len(cases) < ADAPTIVE_MIN_N:
        return None
    return best_lock_loss(cases, [i / 100.0 for i in range(0, 9)])


def lock_loss(default: float) -> float:
    """Certain loss tolerated to complete a pair (replay-optimised on recorded leg-outs)."""
    if not _enabled():
        return default
    v = _cached("lock_loss", _lock_loss)
    return default if v is None else float(v)


def pair_size(symbol: str, size: float) -> Tuple[float, str]:
    """(size, note). Full size while the attempt has non-negative measured EV, minimum size otherwise."""
    from src.config.settings import ADAPTIVE_MIN_N, ADAPTIVE_SIZING

    if not (_enabled() and ADAPTIVE_SIZING):
        return size, ""
    s = stats()
    ev = pair_ev(symbol)
    if ev is None or not s:
        return size, ""
    n = s["by"].get(symbol, {}).get("n", 0)
    if n < max(5, ADAPTIVE_MIN_N // 2):  # estimates are shrunk to the prior, but a handful of attempts is not evidence
        return size, f"adaptive: n={n}, EV {ev * 100:+.1f}c/sh (too little data, full size)"
    cur = _sym(symbol)
    note = f"adaptive: leg-fail {cur['r']:.0%}, loss {cur['loss'] * 100:.1f}c, EV {ev * 100:+.1f}c/sh (n={n})"
    if ev >= 0:
        return size, note
    return min(size, 5.0), note + " -> explore at minimum size"
