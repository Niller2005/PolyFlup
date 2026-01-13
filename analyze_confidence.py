#!/usr/bin/env python3
"""Analyze confidence vs win rate to calibrate formula parameters"""

import sqlite3
from typing import List, Tuple

DB_FILE = "/mnt/d/dev/polyastra/trades.db"


def analyze_confidence_performance():
    """Analyze how confidence correlates with win rates"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Get all settled trades with outcome data
    query = """
        SELECT edge, pnl_usd, roi_pct, final_outcome, side
        FROM trades
        WHERE settled=1
        AND final_outcome IN ('RESOLVED', 'STOP_LOSS', 'STOP_LOSS_GHOST_FILL', 'REVERSAL_STOP_LOSS')
        AND edge IS NOT NULL
        ORDER BY edge DESC
    """
    cursor.execute(query)
    trades = cursor.fetchall()

    if not trades:
        print("No settled trades found in database")
        conn.close()
        return

    # Analyze by confidence buckets (percentiles)
    edges = [t[0] for t in trades if t[0] is not None]
    n_buckets = 10

    # Create percentile buckets
    min_edge = min(edges)
    max_edge = max(edges)
    bucket_size = (max_edge - min_edge) / n_buckets

    bucket_stats = []

    for i in range(n_buckets):
        lower = min_edge + i * bucket_size
        upper = lower + bucket_size

        bucket_trades = [
            t
            for t in trades
            if lower <= t[0] < upper or (i == n_buckets - 1 and t[0] <= upper)
        ]

        if not bucket_trades:
            continue

        wins = sum(1 for t in bucket_trades if t[2] > 0)  # roi_pct > 0
        losses = sum(1 for t in bucket_trades if t[2] <= 0)
        avg_roi = sum(t[2] for t in bucket_trades) / len(bucket_trades)
        avg_pnl = sum(t[1] for t in bucket_trades) / len(bucket_trades)
        avg_edge = sum(t[0] for t in bucket_trades) / len(bucket_trades)

        win_rate = wins / len(bucket_trades) if bucket_trades else 0

        bucket_stats.append(
            {
                "bucket": i + 1,
                "lower": lower,
                "upper": upper,
                "count": len(bucket_trades),
                "wins": wins,
                "losses": losses,
                "win_rate": win_rate,
                "avg_roi": avg_roi,
                "avg_pnl": avg_pnl,
                "avg_edge": avg_edge,
            }
        )

    conn.close()
    return bucket_stats, trades


def analyze_by_edge_threshold(trades: List[Tuple], thresholds: List[float]):
    """Test different MIN_EDGE thresholds"""
    results = []

    for threshold in thresholds:
        # Filter by edge >= threshold (higher edge = more selective)
        filtered_trades = [t for t in trades if t[0] >= threshold]

        if not filtered_trades:
            continue

        wins = sum(1 for t in filtered_trades if t[2] > 0)
        total = len(filtered_trades)
        avg_roi = sum(t[2] for t in filtered_trades) / total
        avg_pnl = sum(t[1] for t in filtered_trades) / total

        results.append(
            {
                "threshold": threshold,
                "total": total,
                "wins": wins,
                "win_rate": wins / total,
                "avg_roi": avg_roi,
                "avg_pnl": avg_pnl,
            }
        )

    return results


def main():
    print("=" * 80)
    print("CONFIDENCE VS WIN RATE ANALYSIS")
    print("=" * 80)

    bucket_stats, trades = analyze_confidence_performance()

    if not bucket_stats:
        print("No data to analyze")
        return

    print(f"\nTotal trades analyzed: {len(trades)}")
    print(f"\nCONFIDENCE BUCKET ANALYSIS:")
    print("-" * 80)
    print(
        f"{'Bucket':<8} {'Edge Range':<20} {'Count':<8} {'Wins':<8} {'Losses':<8} {'Win Rate':<10} {'Avg ROI':<12} {'Avg PnL':<12} {'Avg Edge':<12}"
    )
    print("-" * 80)

    for stat in bucket_stats:
        edge_range = f"{stat['lower']:.3f} - {stat['upper']:.3f}"
        print(
            f"{stat['bucket']:<8} {edge_range:<20} {stat['count']:<8} "
            f"{stat['wins']:<8} {stat['losses']:<8} {stat['win_rate']:.1%}    "
            f"{stat['avg_roi']:+7.1f}%   ${stat['avg_pnl']:+7.2f}  {stat['avg_edge']:.2f}%"
        )

    # Test different MIN_EDGE thresholds
    print("\n" + "=" * 80)
    print("MIN_EDGE THRESHOLD ANALYSIS")
    print("=" * 80)
    print("Testing: What MIN_EDGE threshold maximizes win rate and ROI?")
    print("-" * 80)

    thresholds = [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]
    threshold_results = analyze_by_edge_threshold(trades, thresholds)

    print(
        f"{'Threshold':<12} {'Trades':<10} {'Wins':<10} {'Win Rate':<12} {'Avg ROI':<12} {'Avg PnL':<12}"
    )
    print("-" * 80)

    for r in threshold_results:
        print(
            f"{r['threshold']:.2f}        {r['total']:<10} {r['wins']:<10} "
            f"{r['win_rate']:.1%}      {r['avg_roi']:+7.1f}%      ${r['avg_pnl']:+7.2f}"
        )

    # Find optimal threshold
    print("\n" + "=" * 80)
    print("RECOMMENDATIONS")
    print("=" * 80)

    if threshold_results:
        best_roi = max(threshold_results, key=lambda x: x["avg_roi"])
        best_winrate = max(threshold_results, key=lambda x: x["win_rate"])
        best_balance = max(threshold_results, key=lambda x: x["win_rate"] * len(x))

        print(
            f"\nBest ROI:        {best_roi['threshold']:.2f} (avg ROI: {best_roi['avg_roi']:+.1f}%, {best_roi['total']} trades)"
        )
        print(
            f"Best Win Rate:   {best_winrate['threshold']:.2f} (win rate: {best_winrate['win_rate']:.1%}, {best_winrate['total']} trades)"
        )
        print(
            f"Best Balance:    {best_balance['threshold']:.2f} (balance of win rate & volume)"
        )

        current_min_edge = 0.35
        current_results = [
            r for r in threshold_results if r["threshold"] == current_min_edge
        ]
        if current_results:
            current = current_results[0]
            print(
                f"\nCurrent MIN_EDGE ({current_min_edge:.2f}): {current['win_rate']:.1%} win rate, {current['avg_roi']:+.1f}% avg ROI"
            )

    print("\n" + "=" * 80)
    print("RECOMMENDATION: Start logging raw signal scores")
    print("=" * 80)
    print("\nTo properly calibrate confidence formula with different variants:")
    print("1. Modify calculate_confidence() to return and log raw signal scores")
    print("2. Store up_total, down_total, momentum_score, flow_score, etc. in database")
    print("3. After collecting 100+ trades, re-run this analysis with formula variants")
    print("\nFormula variants to test:")
    print("  - Current: (up_total - down_total * 0.2) * lead_lag_bonus")
    print("  - Pure ratio: up_total / (up_total + down_total)")
    print("  - No discount: up_total")
    print("  - Various k1 values: 0.0, 0.1, 0.2, 0.3, 0.4")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    main()
