"""Fee-aware edge math (pure functions, no I/O).

Polymarket CLOB V2 taker fee, charged in pUSD at match time, makers pay nothing:

    fee = shares * rate * p * (1 - p)        (rate = 0.07 for crypto markets)

Everything here is per share unless stated otherwise.
"""

import math
from typing import Optional, Tuple

TAKER_FEE_RATE = 0.07
TICK = 0.01

# Empirical "leader at T-60s still wins" rates by |z| bucket (lower edge of bucket ->
# hold rate). Measured on 7 days of Binance 1m data, BTC+ETH 5m windows pooled
# (~4000 windows); rounded down and capped at 0.995 because the top buckets are
# thin. Re-measure with analyze_strategy.py once shadow data exists.
EMPIRICAL_HOLD_BY_Z = [
    (0.0, 0.69),
    (0.5, 0.855),
    (1.0, 0.93),
    (1.5, 0.97),
    (2.0, 0.99),
    (2.5, 0.995),
]


def taker_fee(price: float, rate: float = TAKER_FEE_RATE) -> float:
    """Taker fee per share at `price`."""
    return rate * price * (1.0 - price)


def pair_net_edge(
    entry_price: float,
    hedge_price: float,
    entry_taker: bool,
    hedge_taker: bool,
    rate: float = TAKER_FEE_RATE,
) -> float:
    """Guaranteed profit per share of holding UP+DOWN to resolution ($1 payout)."""
    fees = (taker_fee(entry_price, rate) if entry_taker else 0.0) + (
        taker_fee(hedge_price, rate) if hedge_taker else 0.0
    )
    return 1.0 - entry_price - hedge_price - fees


def maker_pair_prices(
    up_bid: Optional[float],
    down_bid: Optional[float],
    improve_ticks: int = 0,
) -> Optional[Tuple[float, float]]:
    """Resting bid prices for a maker pair, or None if a side has no bid."""
    if not up_bid or not down_bid or up_bid <= 0 or down_bid <= 0:
        return None
    step = improve_ticks * TICK
    return round(up_bid + step, 2), round(down_bid + step, 2)


def taker_hedge_ev(
    entry_price: float, hedge_ask: float, entry_taker: bool = False
) -> float:
    """Net profit per share if the missing leg is bought now as a taker."""
    return pair_net_edge(entry_price, hedge_ask, entry_taker, hedge_taker=True)


def flatten_ev(entry_price: float, entry_bid: float) -> float:
    """Net result per share of selling the filled leg now as a taker."""
    return entry_bid - entry_price - taker_fee(entry_bid)


def hold_ev(entry_price: float, win_prob: float) -> float:
    """Expected result per share of holding one leg unhedged to resolution."""
    return win_prob - entry_price


def norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def late_window_z(
    spot: float, strike: float, sigma_1m: float, seconds_left: float
) -> float:
    """Signed distance from the strike in standard deviations of the remaining move.

    `sigma_1m` is the stdev of 1-minute log returns; the remaining move scales with
    sqrt(time left). Positive z means UP is currently ahead.
    """
    if spot <= 0 or strike <= 0 or sigma_1m <= 0 or seconds_left <= 0:
        return 0.0
    move = math.log(spot / strike)
    return move / (sigma_1m * math.sqrt(seconds_left / 60.0))


def empirical_hold(abs_z: float) -> float:
    rate = EMPIRICAL_HOLD_BY_Z[0][1]
    for lo, hold in EMPIRICAL_HOLD_BY_Z:
        if abs_z >= lo:
            rate = hold
    return rate


def late_window_probability(z: float) -> Tuple[float, float, float]:
    """(p_model, p_empirical, p_use) that the side currently ahead wins.

    p_use is the smaller of the two, to stay conservative.
    """
    az = abs(z)
    p_model = norm_cdf(az)
    p_emp = empirical_hold(az)
    return p_model, p_emp, min(p_model, p_emp)


def late_window_ev(p_win: float, ask: float) -> float:
    """Net EV per share of buying the leading side at `ask` as a taker."""
    return p_win - ask - taker_fee(ask)


# ---- Kelly sizing ----------------------------------------------------------
def kelly_binary(p_win: float, cost: float) -> float:
    """Kelly fraction of bankroll for a share bought at `cost` (price + fee) that pays 1 on a win.

    f* = (p - cost) / (1 - cost); 0 when there is no edge.
    """
    if cost <= 0.0 or cost >= 1.0 or p_win <= cost:
        return 0.0
    return (p_win - cost) / (1.0 - cost)


def kelly_two_outcome(p_gain: float, gain: float, loss: float) -> float:
    """Kelly fraction of the stake when it grows by `gain` with prob p_gain, else shrinks by `loss`.

    f* = p/loss - (1-p)/gain. Can exceed 1 for near-riskless bets; callers cap it.
    """
    if gain <= 0.0 or loss <= 0.0 or not 0.0 <= p_gain <= 1.0:
        return 0.0
    return max(0.0, p_gain / loss - (1.0 - p_gain) / gain)


def pair_kelly_fraction(net_edge: float, leg_fail_rate: float, leg_loss: float) -> float:
    """Kelly fraction of bankroll for an entry+hedge pair.

    Both legs fill (prob 1-q): lock in `net_edge` per share on a stake of ~(1 - net_edge).
    One leg fills and the other can't be hedged (prob q): lose `leg_loss` per share.
    """
    stake = 1.0 - net_edge
    return kelly_two_outcome(1.0 - leg_fail_rate, net_edge / stake, leg_loss / stake)


def scaled_kelly(full_kelly: float, fraction: float, cap: float) -> float:
    """Fractional Kelly, capped at `cap` of bankroll (never negative)."""
    return min(max(full_kelly, 0.0) * fraction, cap)
