"""Does the signal beat the market? Hit rate of the biased side vs the price paid for it, from the
`evaluations` and `market_windows` tables (v0.7.6+).

Usage: uv run python .opencode/skill/strategy-analysis/signal_edge.py [trades_paper.db]
A scale-in / directional add is only worth building where `edge` is clearly positive with a real n
(number of DISTINCT windows matters more than rows: evaluations repeat every 20s inside a window).
"""

import sqlite3
import sys

db = sys.argv[1] if len(sys.argv) > 1 else "trades_paper.db"
c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
base = """
SELECT e.method, e.confidence, e.secs_into_window, e.slug,
       CASE e.bias WHEN 'UP' THEN (e.best_bid + e.best_ask) / 2 ELSE 1 - (e.best_bid + e.best_ask) / 2 END AS price,
       (e.bias = 'UP') = w.up_won AS win
FROM evaluations e JOIN market_windows w ON w.slug = e.slug
WHERE e.bias IN ('UP', 'DOWN') AND e.best_bid IS NOT NULL AND e.best_ask IS NOT NULL
"""


def table(title, bucket_sql):
    print(f"\n{title}")
    print(f"{'bucket':<12}{'rows':>6}{'windows':>9}{'hit':>7}{'price':>7}{'edge':>8}")
    for r in c.execute(
        f"SELECT {bucket_sql} b, COUNT(*), COUNT(DISTINCT slug), AVG(win), AVG(price) FROM ({base}) GROUP BY b ORDER BY b"
    ):
        print(f"{str(r[0]):<12}{r[1]:>6}{r[2]:>9}{r[3]:>7.3f}{r[4]:>7.3f}{r[3] - r[4]:>+8.3f}")


table("by confidence", "CASE WHEN confidence<0.2 THEN '1 <.2' WHEN confidence<0.4 THEN '2 .2-.4' WHEN confidence<0.6 THEN '3 .4-.6' WHEN confidence<0.8 THEN '4 .6-.8' ELSE '5 >=.8' END")
table("by method", "method")
table("by minute into the window", "CAST(secs_into_window/60 AS INT)")
table("by price of the biased side", "CASE WHEN price<0.35 THEN '1 <.35' WHEN price<0.5 THEN '2 .35-.5' WHEN price<0.65 THEN '3 .5-.65' WHEN price<0.8 THEN '4 .65-.8' ELSE '5 >=.8' END")
