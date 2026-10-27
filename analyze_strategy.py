"""Strategy diagnostics on trades.db (read-only).

    uv run analyze_strategy.py [--db trades.db]

Sections:
  1. Early exits (stop loss / pre-settlement) vs. held to resolution
  2. Confidence calibration for the additive and Bayesian methods
  3. PnL by market (5m vs 15m) and hedged vs unhedged
  4. Late-window shadow signals: is the model calibrated, and would it have made money?
"""

import argparse
import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent


def rows(conn, sql, params=()):
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.Error as e:
        print(f"   (unavailable: {e})")
        return []


def table(headers, data):
    if not data:
        print("   (no data)")
        return
    cells = [[("" if v is None else v) for v in r] for r in data]
    widths = [max(len(str(h)), *(len(str(r[i])) for r in cells)) for i, h in enumerate(headers)]
    print("   " + "  ".join(str(h).ljust(w) for h, w in zip(headers, widths)))
    for r in cells:
        print("   " + "  ".join(str(v).ljust(w) for v, w in zip(r, widths)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=BASE / "trades.db")
    args = ap.parse_args()
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)

    print("\n1. Early exits vs held to resolution (settled trades)")
    table(
        ["exited_early", "trades", "total_pnl", "avg_pnl", "win_rate"],
        rows(
            conn,
            """SELECT exited_early, COUNT(*), ROUND(SUM(pnl_usd),2), ROUND(AVG(pnl_usd),3),
                      ROUND(AVG(pnl_usd > 0),3)
               FROM trades WHERE settled=1 GROUP BY exited_early""",
        ),
    )

    for col in ("bayesian_confidence", "additive_confidence"):
        print(f"\n2. Calibration: {col} (win rate should rise with confidence)")
        table(
            ["bucket", "trades", "win_rate", "avg_pnl"],
            rows(
                conn,
                f"""SELECT ROUND({col},1), COUNT(*), ROUND(AVG(pnl_usd > 0),3), ROUND(AVG(pnl_usd),3)
                    FROM trades WHERE settled=1 AND {col} IS NOT NULL
                    GROUP BY ROUND({col},1) ORDER BY 1""",
            ),
        )

    print("\n3. PnL by market key (BTC = 15m, BTC-5M = 5m) and hedge status")
    table(
        ["symbol", "is_hedged", "trades", "total_pnl", "avg_pnl", "win_rate"],
        rows(
            conn,
            """SELECT symbol, COALESCE(is_hedged,0), COUNT(*), ROUND(SUM(pnl_usd),2),
                      ROUND(AVG(pnl_usd),3), ROUND(AVG(pnl_usd > 0),3)
               FROM trades WHERE settled=1 GROUP BY symbol, COALESCE(is_hedged,0) ORDER BY 4 DESC""",
        ),
    )

    print("\n4a. Late-window shadow signals by |z| bucket (resolved only)")
    table(
        ["|z| bucket", "n", "won", "p_use avg", "avg ask", "realized EV/sh"],
        rows(
            conn,
            """SELECT CAST(ABS(z)/0.5 AS INT)*0.5, COUNT(*), ROUND(AVG(won),3), ROUND(AVG(p_use),3),
                      ROUND(AVG(ask),3), ROUND(AVG(won - ask - 0.07*ask*(1-ask)),4)
               FROM shadow_signals WHERE won IS NOT NULL AND ask IS NOT NULL
               GROUP BY 1 ORDER BY 1""",
        ),
    )
    print("\n4b. Late-window decisions: would ENTER have paid? (resolved only)")
    table(
        ["decision", "source", "n", "win_rate", "avg_pred_EV", "realized_EV/sh"],
        rows(
            conn,
            """SELECT decision, source, COUNT(*), ROUND(AVG(won),3), ROUND(AVG(ev),4),
                      ROUND(AVG(won - ask - 0.07*ask*(1-ask)),4)
               FROM shadow_signals WHERE won IS NOT NULL AND ask IS NOT NULL
               GROUP BY decision, source ORDER BY decision, source""",
        ),
    )
    print("\n4c. Coverage")
    table(
        ["total signals", "resolved", "windows"],
        rows(
            conn,
            "SELECT COUNT(*), SUM(won IS NOT NULL), COUNT(DISTINCT slug) FROM shadow_signals",
        ),
    )
    print(
        "\nRule of thumb: enable LATE_WINDOW_LIVE only when 4b shows ENTER with >=100 resolved"
        "\nwindows, realized EV/sh > 0 and p_use not materially above the realized win rate.\n"
    )


if __name__ == "__main__":
    main()
