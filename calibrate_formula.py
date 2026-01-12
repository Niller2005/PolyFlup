#!/usr/bin/env python3
"""Calibrate confidence formula using historical raw signal scores

This script should be run after collecting 100+ trades with raw signal data.
It tests multiple formula variants to find optimal parameters.
"""

import sqlite3
from typing import Dict, List, Tuple
from collections import defaultdict

DB_FILE = "trades.db"


def get_trades_with_raw_signals() -> List[Dict]:
    """Load trades that have raw signal scores"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    query = """
        SELECT
            id, symbol, side, edge, pnl_usd, roi_pct, final_outcome,
            up_total, down_total, momentum_score, momentum_dir,
            flow_score, flow_dir, divergence_score, divergence_dir,
            vwm_score, vwm_dir, pm_mom_score, pm_mom_dir,
            adx_score, adx_dir, lead_lag_bonus
        FROM trades
        WHERE settled=1
        AND up_total IS NOT NULL
        AND down_total IS NOT NULL
        ORDER BY timestamp DESC
    """

    cursor.execute(query)
    rows = cursor.fetchall()

    columns = [
        "id",
        "symbol",
        "side",
        "edge",
        "pnl_usd",
        "roi_pct",
        "final_outcome",
        "up_total",
        "down_total",
        "momentum_score",
        "momentum_dir",
        "flow_score",
        "flow_dir",
        "divergence_score",
        "divergence_dir",
        "vwm_score",
        "vwm_dir",
        "pm_mom_score",
        "pm_mom_dir",
        "adx_score",
        "adx_dir",
        "lead_lag_bonus",
    ]

    trades = [dict(zip(columns, row)) for row in rows]
    conn.close()

    return trades


def calculate_confidence_variant(
    up_total: float,
    down_total: float,
    lead_lag_bonus: float,
    variant: str,
    k1: float = 0.2,
    k2: float = 1.0,
) -> float:
    """Calculate confidence using different formula variants"""

    if variant == "current":
        # Current formula: (up - down * 0.2) * lead_lag_bonus
        if up_total > down_total:
            confidence = (up_total - down_total * k1) * lead_lag_bonus
        elif down_total > up_total:
            confidence = (down_total - up_total * k1) * lead_lag_bonus
        else:
            confidence = 0.0
    elif variant == "pure_ratio":
        # Pure ratio: up / (up + down)
        total = up_total + down_total
        confidence = up_total / total if total > 0 else 0.0
    elif variant == "no_discount":
        # No opposition discount
        confidence = up_total
    elif variant == "k1_variant":
        # Test different k1 values
        if up_total > down_total:
            confidence = (up_total - down_total * k1) * lead_lag_bonus
        elif down_total > up_total:
            confidence = (down_total - up_total * k1) * lead_lag_bonus
        else:
            confidence = 0.0
    else:
        confidence = 0.0

    # Normalize to 0-1
    confidence = max(0.0, min(1.0, confidence))

    return confidence


def test_formula_variant(
    trades: List[Dict], variant: str, k1: float = 0.2, min_edge: float = 0.35
) -> Dict:
    """Test a specific formula variant"""

    results = []

    for trade in trades:
        up_total = trade["up_total"]
        down_total = trade["down_total"]
        lead_lag_bonus = trade["lead_lag_bonus"] or 1.0
        actual_roi = trade["roi_pct"]

        # Calculate confidence using the variant
        calculated_confidence = calculate_confidence_variant(
            up_total, down_total, lead_lag_bonus, variant, k1
        )

        # Only include trades that would have been taken with this formula
        if calculated_confidence >= min_edge:
            results.append(
                {
                    "confidence": calculated_confidence,
                    "roi_pct": actual_roi,
                    "win": actual_roi > 0,
                    "side": trade["side"],
                }
            )

    if not results:
        return {
            "variant": variant,
            "k1": k1,
            "trades": 0,
            "win_rate": 0.0,
            "avg_roi": 0.0,
            "avg_pnl": 0.0,
        }

    wins = sum(1 for r in results if r["win"])
    total = len(results)
    avg_roi = sum(r["roi_pct"] for r in results) / total
    avg_pnl = sum(trade["pnl_usd"] for trade in trades[:total]) / total

    return {
        "variant": variant,
        "k1": k1,
        "trades": total,
        "wins": wins,
        "win_rate": wins / total,
        "avg_roi": avg_roi,
        "avg_pnl": avg_pnl,
    }


def analyze_by_confidence_buckets(
    trades: List[Dict], variant: str, k1: float = 0.2
) -> List[Dict]:
    """Analyze win rate by confidence buckets"""

    bucket_stats = []

    for trade in trades:
        up_total = trade["up_total"]
        down_total = trade["down_total"]
        lead_lag_bonus = trade["lead_lag_bonus"] or 1.0

        confidence = calculate_confidence_variant(
            up_total, down_total, lead_lag_bonus, variant, k1
        )

        bucket_stats.append(
            {
                "confidence": confidence,
                "roi_pct": trade["roi_pct"],
                "win": trade["roi_pct"] > 0,
            }
        )

    # Create percentile buckets
    n_buckets = 10
    bucket_stats.sort(key=lambda x: x["confidence"])

    if not bucket_stats:
        return []

    min_conf = bucket_stats[0]["confidence"]
    max_conf = bucket_stats[-1]["confidence"]
    bucket_size = (max_conf - min_conf) / n_buckets if max_conf > min_conf else 0.01

    results = []

    for i in range(n_buckets):
        lower = min_conf + i * bucket_size
        upper = lower + bucket_size

        bucket = [
            b
            for b in bucket_stats
            if lower <= b["confidence"] < upper
            or (i == n_buckets - 1 and b["confidence"] <= upper)
        ]

        if not bucket:
            continue

        wins = sum(1 for b in bucket if b["win"])
        avg_roi = sum(b["roi_pct"] for b in bucket) / len(bucket)
        avg_conf = sum(b["confidence"] for b in bucket) / len(bucket)

        results.append(
            {
                "bucket": i + 1,
                "lower": lower,
                "upper": upper,
                "count": len(bucket),
                "wins": wins,
                "win_rate": wins / len(bucket),
                "avg_roi": avg_roi,
                "avg_confidence": avg_conf,
            }
        )

    return results


def main():
    print("=" * 80)
    print("CONFIDENCE FORMULA CALIBRATION")
    print("=" * 80)

    trades = get_trades_with_raw_signals()

    if not trades:
        print("\n❌ No trades with raw signal data found.")
        print(
            "   Please run the bot and collect 100+ trades before running this calibration."
        )
        print("\n   The following raw signal scores are now being logged:")
        print("   - up_total, down_total")
        print("   - momentum_score, momentum_dir")
        print("   - flow_score, flow_dir")
        print("   - divergence_score, divergence_dir")
        print("   - vwm_score, vwm_dir")
        print("   - pm_mom_score, pm_mom_dir")
        print("   - adx_score, adx_dir")
        print("   - lead_lag_bonus")
        return

    print(f"\n✓ Loaded {len(trades)} trades with raw signal data")

    print("\n" + "=" * 80)
    print("FORMULA VARIANT TESTING (MIN_EDGE = 0.35)")
    print("=" * 80)

    variants = [
        ("current", 0.2),
        ("pure_ratio", 0.2),
        ("no_discount", 0.2),
        ("k1_variant", 0.0),
        ("k1_variant", 0.1),
        ("k1_variant", 0.2),
        ("k1_variant", 0.3),
        ("k1_variant", 0.4),
    ]

    results = []

    for variant, k1 in variants:
        result = test_formula_variant(trades, variant, k1)
        results.append(result)

    print(
        f"\n{'Variant':<20} {'k1':<6} {'Trades':<8} {'Wins':<8} {'Win Rate':<12} {'Avg ROI':<12}"
    )
    print("-" * 80)

    for r in results:
        variant_name = f"{r['variant']}"
        if r["variant"] == "k1_variant":
            variant_name = f"k1_variant"
        print(
            f"{variant_name:<20} {r['k1']:.1f}    {r['trades']:<8} "
            f"{r.get('wins', 0):<8} {r['win_rate']:.1%}        {r['avg_roi']:+8.1f}%"
        )

    # Find best variants
    print("\n" + "=" * 80)
    print("TOP PERFORMING VARIANTS")
    print("=" * 80)

    best_roi = max(results, key=lambda x: x["avg_roi"])
    best_winrate = max(results, key=lambda x: x["win_rate"])
    best_balance = max(results, key=lambda x: x["win_rate"] * x["trades"])

    print(f"\nBest ROI:         {best_roi['variant']} (k1={best_roi['k1']})")
    print(
        f"                    Avg ROI: {best_roi['avg_roi']:+.1f}%, {best_roi['trades']} trades, {best_roi['win_rate']:.1%} win rate"
    )

    print(f"\nBest Win Rate:    {best_winrate['variant']} (k1={best_winrate['k1']})")
    print(
        f"                    Win Rate: {best_winrate['win_rate']:.1%}, {best_winrate['trades']} trades, {best_winrate['avg_roi']:+.1f}% avg ROI"
    )

    print(f"\nBest Balance:     {best_balance['variant']} (k1={best_balance['k1']})")
    print(
        f"                    Balance: {best_balance['win_rate']:.1%} × {best_balance['trades']} = {best_balance['win_rate'] * best_balance['trades']:.1f}"
    )

    # Compare with actual performance
    print("\n" + "=" * 80)
    print("COMPARISON: ACTUAL vs SIMULATED")
    print("=" * 80)

    actual_results = test_formula_variant(trades, "current", 0.2, min_edge=0.0)
    print(
        f"\nActual (MIN_EDGE=0): {actual_results['trades']} trades, {actual_results['win_rate']:.1%} win rate, {actual_results['avg_roi']:+.1f}% avg ROI"
    )

    # Show confidence bucket analysis for best variant
    print("\n" + "=" * 80)
    print(f"CONFIDENCE BUCKET ANALYSIS: {best_roi['variant']} (k1={best_roi['k1']})")
    print("=" * 80)

    buckets = analyze_by_confidence_buckets(trades, best_roi["variant"], best_roi["k1"])

    print(
        f"\n{'Bucket':<8} {'Conf Range':<18} {'Count':<8} {'Wins':<8} {'Win Rate':<12} {'Avg ROI':<12}"
    )
    print("-" * 80)

    for b in buckets:
        conf_range = f"{b['lower']:.2f} - {b['upper']:.2f}"
        print(
            f"{b['bucket']:<8} {conf_range:<18} {b['count']:<8} "
            f"{b['wins']:<8} {b['win_rate']:.1%}        {b['avg_roi']:+8.1f}%"
        )

    print("\n" + "=" * 80)
    print("RECOMMENDATIONS")
    print("=" * 80)

    if best_roi["variant"] == "current" and best_roi["k1"] == 0.2:
        print("\n✓ Current formula is optimal for ROI based on historical data")
        print("  No changes recommended.")
    else:
        print(
            f"\n💡 Consider switching to: {best_roi['variant']} with k1={best_roi['k1']}"
        )
        print(
            f"  Expected improvement: {best_roi['avg_roi'] - actual_results['avg_roi']:+.1f}% ROI"
        )

    print(
        f"\n📊 Collect {max(0, 100 - len(trades))} more trades with raw signals for more reliable calibration"
    )
    print("\nTo apply changes:")
    print("  1. Update k1 in src/trading/strategy.py (line ~193, ~199)")
    print("  2. Or modify the formula itself if switching to pure_ratio or no_discount")


if __name__ == "__main__":
    main()
