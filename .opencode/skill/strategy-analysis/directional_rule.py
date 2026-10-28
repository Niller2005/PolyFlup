"""Pre-registered test of an UNHEDGED directional entry (position-adding / "high confidence play").

Rule D1 (frozen 2026-10-28, chosen after looking at the data up to 04:05 UTC - so only data AFTER that
counts as out-of-sample; use --since):
  one entry per market window = the FIRST evaluation with bias UP/DOWN and confidence >= 0.40
  whose ask for the biased side is between 0.30 and 0.80; buy that side at its ask (+ taker fee),
  hold to resolution.

Reports per-share EV, PnL at a fixed size, and the result per window start (assets in a window are
highly correlated: a window is ONE bet repeated across assets, so judge the number of window starts,
not the number of entries).

Usage: uv run python .opencode/skill/strategy-analysis/directional_rule.py [db] [--since ISO_TS] [--conf 0.4] [--lo 0.3] [--hi 0.8] [--size 10]
"""

import random
import sqlite3
import sys

argv = sys.argv[1:]


def opt(name, default):
    if name in argv:
        i = argv.index(name)
        v = argv[i + 1]
        del argv[i : i + 2]
        return type(default)(v)
    return default


since = opt("--since", "")
conf, lo, hi, size = opt("--conf", 0.40), opt("--lo", 0.30), opt("--hi", 0.80), opt("--size", 10.0)
db = argv[0] if argv else "trades_paper.db"
c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
rows = c.execute(
    """SELECT e.slug, e.symbol, e.ts, e.confidence, e.bias,
              CASE e.bias WHEN 'UP' THEN e.best_ask ELSE 1 - e.best_bid END AS ask,
              (e.bias = 'UP') = w.up_won AS win, w.start_ts
       FROM evaluations e JOIN market_windows w ON w.slug = e.slug
       WHERE e.bias IN ('UP','DOWN') AND e.confidence >= ? AND e.best_ask IS NOT NULL AND e.best_bid IS NOT NULL
         AND e.ts >= ? ORDER BY e.ts""",
    (conf, since),
).fetchall()
entries, seen = [], set()
for slug, sym, ts, cf, bias, ask, win, start in rows:
    if slug in seen or not (lo <= ask <= hi):
        continue
    seen.add(slug)
    entries.append((slug, sym, start, ask, int(win), int(win) - ask - 0.07 * ask * (1 - ask)))
if not entries:
    sys.exit("no qualifying entries yet (need resolved windows; try again later)")

n = len(entries)
net = [e[5] for e in entries]
by = {}
for e in entries:
    by.setdefault(e[2], []).append(e[5])
print(f"rule D1: conf>={conf} ask in [{lo},{hi}] first qualifying evaluation per window  since={since or 'ever'}")
print(f"entries {n} | window starts {len(by)} | hit {sum(e[4] for e in entries) / n:.3f} | avg ask {sum(e[3] for e in entries) / n:.3f} | net EV/share {sum(net) / n:+.3f} | PnL at {size:g} sh: {sum(net) * size:+.1f}")
keys = list(by)
rng = random.Random(7)
def boot_mean():
    pick = [rng.choice(keys) for _ in keys]
    return sum(sum(by[k]) for k in pick) / sum(len(by[k]) for k in pick)


means = sorted(boot_mean() for _ in range(2000))
print(f"cluster-bootstrap 95% CI of net EV/share: [{means[50]:+.3f}, {means[1949]:+.3f}]  ({'edge' if means[50] > 0 and len(keys) >= 30 else 'not established'}: needs lower bound > 0 and >= 30 window starts)")
print("\nwindow start (UTC)   entries  wins   net EV/sh   worst single-window loss at size")
worst = 0.0
for k in sorted(by):
    xs = by[k]
    pnl = sum(xs) * size
    worst = min(worst, pnl)
    print(f"{str(k)[:16]:<20}{len(xs):>7}{sum(1 for x in xs if x > 0):>6}{sum(xs) / len(xs):>+12.3f}   {pnl:+.1f}")
print(f"\nworst window start: {worst:+.1f} (all assets in a window tend to lose together - cap total exposure per window start)")
