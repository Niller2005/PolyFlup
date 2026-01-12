"""Bayesian vs Additive Confidence A/B Testing Script

Analyzes historical trades to compare performance of additive vs Bayesian confidence methods.
Requires migration 007 columns (additive_confidence, bayesian_confidence, etc.)

IMPORTANT: Only NEW trades (after migration 007) will have both confidence values.
Old trades will have NULL for these columns and will be excluded from analysis.

Usage:
    # 1. Run bot to collect ~100+ trades with migration 007 data
    uv run polyflup.py

    # 2. Wait for trades to settle (15-minute windows)

    # 3. Run comparison analysis
    uv run python compare_bayesian_additive.py

Expected sample size:
- Minimum: 50 trades for preliminary comparison
- Good: 100+ trades for reliable analysis
- Best: 200+ trades for statistical significance
"""

import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, List, Tuple


def get_connection():
    """Get database connection"""
    return sqlite3.connect("trades.db")


def get_comparison_data(conn: sqlite3.Connection) -> List[Dict]:
    """Fetch trades with both additive and Bayesian confidence data"""
    c = conn.cursor()

    c.execute("""
        SELECT
            id,
            symbol,
            timestamp,
            side,
            edge,
            pnl_usd,
            settled,
            additive_confidence,
            additive_bias,
            bayesian_confidence,
            bayesian_bias,
            market_prior_p_up
        FROM trades
        WHERE settled = 1
          AND additive_confidence IS NOT NULL
          AND bayesian_confidence IS NOT NULL
        ORDER BY timestamp DESC
    """)

    columns = [desc[0] for desc in c.description]
    trades = []
    for row in c.fetchall():
        trades.append(dict(zip(columns, row)))

    return trades


def calculate_metrics(trades: List[Dict], method: str) -> Dict:
    """Calculate performance metrics for a given confidence method"""
    method_conf = f"{method}_confidence"
    method_bias = f"{method}_bias"

    results = {
        "total": 0,
        "wins": 0,
        "losses": 0,
        "avg_edge": 0.0,
        "avg_pnl": 0.0,
        "win_rate": 0.0,
        "by_confidence": {},
    }

    filtered = []
    for trade in trades:
        conf = trade[method_conf]
        if conf and conf > 0:
            filtered.append(trade)

    results["total"] = len(filtered)

    if results["total"] == 0:
        return results

    # Win/loss counts
    results["wins"] = sum(1 for t in filtered if t["pnl_usd"] > 0)
    results["losses"] = sum(1 for t in filtered if t["pnl_usd"] <= 0)
    results["win_rate"] = (
        results["wins"] / results["total"] if results["total"] > 0 else 0
    )

    # Average edge and PnL
    results["avg_edge"] = sum(t["edge"] for t in filtered) / results["total"]
    results["avg_pnl"] = sum(t["pnl_usd"] for t in filtered) / results["total"]

    # By confidence buckets
    buckets = [
        (0.0, 0.25, "Very Low (0-25%)"),
        (0.25, 0.35, "Partial (25-35%)"),
        (0.35, 0.50, "Moderate (35-50%)"),
        (0.50, 0.65, "High (50-65%)"),
        (0.65, 1.0, "Very High (65-100%)"),
    ]

    for min_conf, max_conf, label in buckets:
        bucket_trades = [t for t in filtered if min_conf < t[method_conf] <= max_conf]

        if bucket_trades:
            bucket_wins = sum(1 for t in bucket_trades if t["pnl_usd"] > 0)
            bucket_total = len(bucket_trades)

            results["by_confidence"][label] = {
                "count": bucket_total,
                "wins": bucket_wins,
                "win_rate": bucket_wins / bucket_total if bucket_total > 0 else 0,
                "avg_edge": sum(t["edge"] for t in bucket_trades) / bucket_total,
            }

    return results


def compare_methods(trades: List[Dict]) -> None:
    """Compare additive vs Bayesian performance"""
    additive = calculate_metrics(trades, "additive")
    bayesian = calculate_metrics(trades, "bayesian")

    print("\n" + "=" * 80)
    print("BAYESIAN VS ADDITIVE CONFIDENCE A/B TESTING")
    print("=" * 80)
    print(
        f"Analysis Date: {datetime.now(tz=ZoneInfo('UTC')).strftime('%Y-%m-%d %H:%M:%S UTC')}"
    )
    print(f"Total Trades Analyzed: {len(trades)}")
    print()

    # Overall comparison
    print("📊 OVERALL PERFORMANCE")
    print("-" * 80)
    print(
        f"{'Method':<20} {'Trades':>8} {'Wins':>6} {'Losses':>8} "
        f"{'Win Rate':>10} {'Avg Edge':>10} {'Avg PnL':>10}"
    )
    print("-" * 80)

    for name, metrics in [("Additive", additive), ("Bayesian", bayesian)]:
        print(
            f"{name:<20} {metrics['total']:>8} {metrics['wins']:>6} "
            f"{metrics['losses']:>8} {metrics['win_rate']:>9.1%} "
            f"{metrics['avg_edge']:>9.1%} {metrics['avg_pnl']:>9.2f}"
        )

    # Improvement metrics
    win_rate_improvement = (
        (bayesian["win_rate"] - additive["win_rate"]) * 100
        if additive["win_rate"] > 0
        else 0
    )
    edge_improvement = (bayesian["avg_edge"] - additive["avg_edge"]) * 100
    pnl_improvement = bayesian["avg_pnl"] - additive["avg_pnl"]

    print("-" * 80)
    print(f"📈 Bayesian vs Additive:")
    print(f"   Win Rate: {win_rate_improvement:+.1f}% points")
    print(f"   Avg Edge: {edge_improvement:+.1f}% points")
    print(f"   Avg PnL: ${pnl_improvement:+.2f} per trade")
    print()

    # Confidence bucket comparison
    print("🎯 PERFORMANCE BY CONFIDENCE LEVEL")
    print("-" * 80)
    print(
        f"{'Bucket':<25} {'Add. Trades':>12} {'Add. Win%':>11} "
        f"{'Bay. Trades':>12} {'Bay. Win%':>11} {'Diff':>8}"
    )
    print("-" * 80)

    for label in additive["by_confidence"]:
        add_metrics = additive["by_confidence"][label]
        bay_metrics = bayesian["by_confidence"].get(label, {})

        if bay_metrics:
            diff = (bay_metrics["win_rate"] - add_metrics["win_rate"]) * 100
            diff_str = f"{diff:+.1f}%"
        else:
            diff_str = "N/A"

        print(
            f"{label:<25} {add_metrics['count']:>12} "
            f"{add_metrics['win_rate']:>10.1%} {bay_metrics.get('count', 0):>12} "
            f"{bay_metrics.get('win_rate', 0):>10.1%} {diff_str:>8}"
        )

    print()

    # Recommendation
    print("💡 RECOMMENDATION")
    print("-" * 80)

    if bayesian["total"] < 50:
        print("⚠️  WARNING: Insufficient data for reliable comparison")
        print("   Need at least 50 trades (currently: {})".format(bayesian["total"]))
        print("   Continue trading with BAYESIAN_CONFIDENCE=NO to collect more data")
    elif win_rate_improvement > 2:
        print("✅ Bayesian performs significantly better")
        print(f"   +{win_rate_improvement:.1f}% win rate improvement")
        print("   Recommendation: Set BAYESIAN_CONFIDENCE=YES in .env")
    elif win_rate_improvement < -2:
        print("❌ Additive performs significantly better")
        print(f"   -{abs(win_rate_improvement):.1f}% win rate decline with Bayesian")
        print("   Recommendation: Keep BAYESIAN_CONFIDENCE=NO (default)")
    else:
        print("⚖️  No significant difference detected")
        print(f"   Win rate difference: {win_rate_improvement:+.1f}% points")
        print("   Both methods perform similarly - continue monitoring")
        print(
            "   Consider sample size: {} trades may not be statistically significant".format(
                bayesian["total"]
            )
        )

    print()

    # Top performing trades by method
    print("🏆 TOP WINNING TRADES BY METHOD")
    print("-" * 80)

    for method, method_conf in [
        ("Additive", "additive_confidence"),
        ("Bayesian", "bayesian_confidence"),
    ]:
        top_trades = sorted(
            [t for t in trades if t.get(method_conf, 0) > 0],
            key=lambda x: x["pnl_usd"],
            reverse=True,
        )[:3]

        if top_trades:
            print(f"\n{method}:")
            for i, trade in enumerate(top_trades, 1):
                ts = datetime.fromisoformat(trade["timestamp"]).strftime("%m-%d %H:%M")
                print(
                    f"   {i}. {trade['symbol']} UP ${trade['pnl_usd']:.2f} "
                    f"| {ts} | Conf: {trade[method_conf]:.1%}"
                )

    print()


def main():
    """Main analysis function"""
    print("\n🔬 Bayesian vs Additive Confidence A/B Testing")
    print("Loading trades database...")

    try:
        conn = get_connection()
        trades = get_comparison_data(conn)
        conn.close()

        if not trades:
            print("\n❌ No trades found with migration 007 data")
            print("   Make sure you've:")
            print("   1. Run the bot after migration 007")
            print("   2. Let trades settle (result recorded)")
            print("   3. Have at least 10-20 trades for meaningful comparison")
            return

        compare_methods(trades)

    except sqlite3.Error as e:
        print(f"\n❌ Database error: {e}")
    except Exception as e:
        print(f"\n❌ Error: {e}")


if __name__ == "__main__":
    main()
