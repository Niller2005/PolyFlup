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
  lock_loss(default)      tolerated certain loss to complete a pair: randomised arms compared on realised
                          results per share (bandit), recorded per attempt in pair_attempts.lock_arm
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


def choose_arm(stats_by_arm: Dict[float, Tuple[int, float]], arms: Sequence[float], min_n: int, epsilon: float, rng) -> float:
    """Pick a tolerance arm: uniformly at random until every arm has `min_n` results, then the arm with the
    best measured mean result per share (explore uniformly with probability `epsilon`)."""
    if rng.random() < epsilon or any(stats_by_arm.get(a, (0, 0.0))[0] < min_n for a in arms):
        return rng.choice(list(arms))
    return max(arms, key=lambda a: stats_by_arm[a][1])


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


def _arm_stats() -> Dict[float, Tuple[int, float]]:
    """{tolerance: (n, mean result per share)} from attempts that recorded their `lock_arm`."""
    from src.config.settings import ADAPTIVE_WINDOW

    out: Dict[float, List[float]] = {}
    with db_connection() as conn:
        for arm, outcome, ep, hp, pnl, size in conn.execute(
            """SELECT lock_arm, outcome, final_entry_price, final_hedge_price, realized_pnl, size FROM pair_attempts
               WHERE lock_arm IS NOT NULL AND outcome IN ('both','timeout') ORDER BY id DESC LIMIT ?""",
            (ADAPTIVE_WINDOW,),
        ):
            if outcome == "both" and ep is not None and hp is not None:
                res = 1.0 - ep - hp
            elif outcome == "timeout" and pnl is not None and size:
                res = pnl / size  # sold or resolved result per share (unresolved held legs are skipped)
            else:
                continue
            out.setdefault(round(arm, 4), []).append(res)
    return {a: (len(v), sum(v) / len(v)) for a, v in out.items()}


def lock_loss(default: float) -> float:
    """Tolerated certain loss for ONE attempt: a randomised arm (A/B/bandit), recorded in pair_attempts.lock_arm.

    The earlier replay optimiser always pushed the tolerance to its maximum (it never charged the cost of
    completing at worse locks) and in the live data 8c produced no fewer leg-outs than 3c, only 2c worse
    locks. Arms are therefore compared on realised results per share instead.
    """
    import random

    from src.config.settings import ADAPTIVE_EPSILON, ADAPTIVE_LOCK_ARMS, ADAPTIVE_MIN_N

    if not _enabled() or not ADAPTIVE_LOCK_ARMS:
        return default
    stats_by_arm = _cached("arms", _arm_stats) or {}
    return choose_arm(stats_by_arm, ADAPTIVE_LOCK_ARMS, ADAPTIVE_MIN_N, ADAPTIVE_EPSILON, random)


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
