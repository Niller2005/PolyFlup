"""Does the signal beat the market after costs? (position-adding / directional-add analysis)

Uses `evaluations` + `market_windows` (v0.7.6+; Bayesian values only from v0.8.1 on are valid).
For every evaluation with a bias, buying the biased side costs its ASK plus the taker fee, and wins
if that side won the window. `net edge` = hit - ask - fee. Assets move together, so rows are
clustered by window start (the slug's timestamp) and the 95% interval comes from a cluster bootstrap:
an edge is only real if the interval excludes 0 AND there are enough clusters.

Usage: uv run python .opencode/skill/strategy-analysis/signal_edge.py [trades_paper.db] [--min-conf 0.4]
"""

import random
import sqlite3
import sys
from collections import defaultdict

args = [a for a in sys.argv[1:] if not a.startswith("--")]
db = args[0] if args else "trades_paper.db"
min_conf = float(sys.argv[sys.argv.index("--min-conf") + 1]) if "--min-conf" in sys.argv else 0.0
c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)

rows = c.execute(
    """SELECT e.method, e.confidence, e.secs_into_window, e.slug, e.bias, e.best_bid, e.best_ask,
              (e.bias = 'UP') = w.up_won
       FROM evaluations e JOIN market_windows w ON w.slug = e.slug
       WHERE e.bias IN ('UP','DOWN') AND e.best_bid IS NOT NULL AND e.best_ask IS NOT NULL AND e.confidence >= ?""",
    (min_conf,),
).fetchall()
data = []
for method, conf, t, slug, bias, bid, ask, win in rows:
    cost = ask if bias == "UP" else 1.0 - bid  # the ask of the token we would buy
    fee = 0.07 * cost * (1 - cost)
    data.append(dict(method=method, conf=conf, t=t or 0, cluster=slug.rsplit("-", 1)[-1], cost=cost, fee=fee, win=int(win)))
print(f"{len(data)} evaluations in {len({d['cluster'] for d in data})} distinct window starts (assets are clustered)")


def summarise(sel):
    if not sel:
        return None
    net = [d["win"] - d["cost"] - d["fee"] for d in sel]
    by = defaultdict(list)
    for d, x in zip(sel, net):
        by[d["cluster"]].append(x)
    keys = list(by)
    rng = random.Random(1)
    means = []
    for _ in range(1000):
        pick = [x for k in (rng.choice(keys) for _ in keys) for x in by[k]]
        means.append(sum(pick) / len(pick))
    means.sort()
    return len(sel), len(keys), sum(d["win"] for d in sel) / len(sel), sum(d["cost"] for d in sel) / len(sel), sum(net) / len(net), means[25], means[974]


def table(title, key, order=None):
    print(f"\n{title}\n{'bucket':<12}{'rows':>5}{'clust':>6}{'hit':>7}{'ask':>7}{'net edge':>10}   95% CI")
    groups = defaultdict(list)
    for d in data:
        groups[key(d)].append(d)
    for k in sorted(groups) if order is None else order:
        s = summarise(groups.get(k, []))
        if s:
            n, kc, hit, cost, net, lo, hi = s
            flag = "  <- edge" if lo > 0 and kc >= 30 else ""
            print(f"{str(k):<12}{n:>5}{kc:>6}{hit:>7.3f}{cost:>7.3f}{net:>+10.3f}   [{lo:+.3f}, {hi:+.3f}]{flag}")


table("ALL", lambda d: "all")
table("by confidence", lambda d: "1 <.2" if d["conf"] < .2 else "2 .2-.4" if d["conf"] < .4 else "3 .4-.6" if d["conf"] < .6 else "4 .6-.8" if d["conf"] < .8 else "5 >=.8")
table("by method", lambda d: d["method"])
table("by minute into window", lambda d: int(d["t"] // 60))
table("by ask of the biased side", lambda d: "1 <.35" if d["cost"] < .35 else "2 .35-.5" if d["cost"] < .5 else "3 .5-.65" if d["cost"] < .65 else "4 .65-.8" if d["cost"] < .8 else "5 >=.8")
