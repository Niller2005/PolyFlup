"""Tests for fee-aware edge math, late-window probabilities and the PolyBolt parser.

Run: uv run python test_edge.py   (also collected by pytest)
"""

import os

os.environ.setdefault("PROXY_PK", "0x" + "1" * 64)

from src.trading.edge import (
    empirical_hold,
    flatten_ev,
    late_window_ev,
    late_window_probability,
    late_window_z,
    maker_pair_prices,
    pair_net_edge,
    taker_fee,
    taker_hedge_ev,
)


def test_fee_matches_polymarket_table():
    # docs: 100 crypto shares at 0.50 -> $1.75, at 0.30 -> $1.47, at 0.90 -> $0.63
    assert abs(100 * taker_fee(0.50) - 1.75) < 1e-9
    assert abs(100 * taker_fee(0.30) - 1.47) < 1e-9
    assert abs(100 * taker_fee(0.90) - 0.63) < 1e-9


def test_taker_pair_near_fifty_fifty_loses_despite_097_combined():
    net = pair_net_edge(0.49, 0.48, True, True)
    assert abs(net - (-0.0050)) < 5e-4
    assert pair_net_edge(0.90, 0.07, True, True) > 0.019  # skewed pairs pay


def test_maker_pair_has_full_edge_and_no_fees():
    assert abs(pair_net_edge(0.50, 0.49, False, False) - 0.01) < 1e-12
    assert maker_pair_prices(0.50, 0.49) == (0.50, 0.49)
    assert maker_pair_prices(0.50, 0.49, improve_ticks=1) == (0.51, 0.50)
    assert maker_pair_prices(None, 0.49) is None


def test_leg_risk_choices():
    # entry 0.50 maker; hedge ask 0.51 -> locking in costs ~2.7c; flattening at bid 0.49 costs the same
    assert abs(taker_hedge_ev(0.50, 0.51) - (-0.0275)) < 1e-3
    assert abs(flatten_ev(0.50, 0.49) - (-0.0275)) < 1e-3
    # cheap hedge ask still profitable
    assert taker_hedge_ev(0.50, 0.40) > 0.0


def test_late_window_z_and_probability():
    # +0.052% over strike, sigma 0.0428%/min, 70s left -> z ~ 1.1
    z = late_window_z(80641.8, 80600.0, 0.000428, 70)
    assert 1.0 < z < 1.3
    assert late_window_z(80600.0, 80641.8, 0.000428, 70) < 0  # below the strike -> DOWN leads
    assert late_window_z(80000, 80000, 0.0004, 60) == 0.0
    assert late_window_z(0, 80000, 0.0004, 60) == 0.0
    p_model, p_emp, p_use = late_window_probability(z)
    assert p_use == min(p_model, p_emp) and 0.5 < p_use < 1.0


def test_empirical_buckets_monotonic_and_capped():
    vals = [empirical_hold(z) for z in (0.0, 0.6, 1.2, 1.7, 2.2, 3.0, 9.0)]
    assert vals == sorted(vals) and vals[-1] <= 0.995


def test_late_window_ev_uses_fee():
    assert abs(late_window_ev(0.97, 0.95) - (0.97 - 0.95 - taker_fee(0.95))) < 1e-12
    assert late_window_ev(0.869, 0.88) < 0  # the log example: skip


def test_polybolt_ingest_snapshot_and_update():
    from src.data.market_data.polybolt import PolyBoltFeed

    feed = PolyBoltFeed(["BTC"])
    # shapes from the migration docs: snapshot carries payload.data[], update a single value
    feed.ingest(
        {
            "channel": "price.crypto.twap",
            "snapshot": True,
            "payload": {
                "symbol": "btcusd",
                "data": [
                    {"timestamp": 1788886057000, "value": "78788.5"},
                    {"timestamp": 1788886058000, "value": "78788.8"},
                ],
                "windowSeconds": 60,
            },
        }
    )
    feed.ingest(
        {
            "channel": "price.crypto.twap",
            "payload": {"symbol": "btcusd", "timestamp": 1788886059000, "value": "78789.1"},
        }
    )
    feed.ingest(
        {
            "channel": "price.crypto",
            "payload": {"symbol": "btcusd", "timestamp": 1788886059000, "value": 78790.0, "source": "chainlink"},
        }
    )
    feed.ingest({"channel": "price.crypto", "payload": {"symbol": "ethusd", "value": 1.0}})  # unsubscribed
    feed.ingest({"op": "ack"})  # acks are ignored
    assert feed.strike("BTC", 1788886058.0) == 78788.8
    assert feed.strike("BTC", 1788880000.0) is None  # feed wasn't connected at that time
    assert feed.spot["BTC"][1] == 78790.0
    assert list(feed.twap) == ["BTC"]


def test_shadow_table_migration_and_resolution_sql():
    import sqlite3
    from src.data.migrations import migration_012_create_shadow_signals_table

    conn = sqlite3.connect(":memory:")
    migration_012_create_shadow_signals_table(conn)
    conn.execute(
        "INSERT INTO shadow_signals (slug, side, window_end, ask) VALUES ('s', 'UP', '2026-01-01T00:00:00', 0.9)"
    )
    # same statement resolve_shadow_signals() runs: UP signal, DOWN won -> lost
    conn.execute(
        "UPDATE shadow_signals SET won = CASE WHEN (side='UP') = ? THEN 1 ELSE 0 END WHERE slug='s'", (0,)
    )
    assert conn.execute("SELECT won FROM shadow_signals").fetchone()[0] == 0


def test_trail_target_follows_bid_within_cap():
    from src.trading.edge import trail_target

    # UP filled at 0.72 -> DOWN may cost at most 1-0.72-0.01 = 0.27
    assert trail_target(0.72, 0.25, 0.26, 0, 0.01) == (0.25, 0.27)  # join best bid: best lock
    assert trail_target(0.72, 0.25, 0.30, 2, 0.01) == (0.27, 0.27)  # escalates, capped at 0.27
    assert trail_target(0.72, 0.30, 0.31, 0, 0.01) == (0.27, 0.27)  # market ran away: rest at the cap
    assert trail_target(0.72, 0.25, 0.26, 2, 0.01) == (0.25, 0.27)  # never crosses the ask (0.26-0.01)
    assert trail_target(0.995, 0.01, 0.02, 0, 0.01)[0] is None  # nothing left to pay


def test_meets_edge_tolerates_float_noise():
    from src.trading.edge import meets_edge

    net = pair_net_edge(0.55, 0.44, False, False)
    assert net < 0.01  # 0.00999999...: a plain >= comparison wrongly rejected this 1c pair
    assert meets_edge(net, 0.01)
    assert not meets_edge(0.009, 0.01)


def test_kelly_binary_matches_closed_form():
    from src.trading.edge import kelly_binary, scaled_kelly

    # p=0.97 at cost 0.95: f = (0.97-0.95)/(1-0.95) = 0.4
    assert abs(kelly_binary(0.97, 0.95) - 0.4) < 1e-12
    assert kelly_binary(0.90, 0.95) == 0.0  # negative edge -> no bet
    assert kelly_binary(0.97, 1.0) == 0.0
    assert scaled_kelly(0.4, 0.25, 0.08) == 0.08  # cap binds
    assert abs(scaled_kelly(0.2, 0.25, 0.08) - 0.05) < 1e-12
    assert scaled_kelly(-1.0, 0.25, 0.08) == 0.0


def test_kelly_maximises_log_growth():
    import math
    from src.trading.edge import kelly_binary

    p, cost = 0.97, 0.95
    f = kelly_binary(p, cost)
    growth = lambda x: p * math.log(1 + x * (1 - cost) / cost) + (1 - p) * math.log(1 - x)
    assert growth(f) > growth(f * 0.7) and growth(f) > growth(min(0.99, f * 1.3))


def test_pair_kelly_depends_on_leg_fail_rate():
    from src.trading.edge import pair_kelly_fraction

    # 1c edge, 4c loss when legging out: breakeven leg-fail rate ~ 20%
    assert pair_kelly_fraction(0.01, 0.30, 0.04) == 0.0  # 30% leg failures: no edge, don't size
    assert pair_kelly_fraction(0.01, 0.10, 0.04) > 5.0  # near-riskless: huge full Kelly (always capped)
    assert pair_kelly_fraction(0.01, 0.0, 0.04) > pair_kelly_fraction(0.01, 0.10, 0.04)


def test_late_window_skips_when_ask_is_far_below_our_probability():
    from src.trading.late_window import market_disagrees

    assert market_disagrees(0.80, 0.30)  # 23 of 24 such entries lost
    assert not market_disagrees(0.80, 0.70)
    assert not market_disagrees(0.60, 0.93)  # market more confident than us: not a disagreement


def test_hedged_pairs_are_not_exited_early_by_default():
    from src.config import settings

    assert settings.PRE_SETTLEMENT_EXIT_HEDGED is False


def test_trail_may_rest_above_break_even_by_the_allowed_lock_loss():
    from src.trading.edge import trail_target

    # DOWN filled @0.56, UP bid ran to 0.47: break-even cap is 0.44, a 3c allowed loss lifts it to 0.47
    assert trail_target(0.56, 0.47, 0.48, 0, -0.03) == (0.47, 0.47)
    assert trail_target(0.56, 0.47, 0.48, 0, 0.01) == (0.43, 0.43)  # old behaviour: stuck 4c below the market


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
