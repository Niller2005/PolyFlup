"""Late-window conviction mode.

Near the close of a 5m/15m up/down window the side that is ahead of the strike usually
wins, and taker fees are smallest at extreme prices (0.07*p*(1-p)). Each evaluation:

    z       = ln(spot/strike) / (sigma_1m * sqrt(seconds_left/60))
    p_use   = min(Phi(|z|), empirical hold rate for that |z| bucket)
    EV/sh   = p_use - ask - 0.07*ask*(1-ask)

The strike and spot come from Polymarket's PolyBolt Chainlink feed (the source the
market resolves on) when available, otherwise Binance (flagged and held to a
stricter distance filter because of basis risk).

Every evaluation is written to `shadow_signals` and resolved against the real market
outcome later, so the strategy can be judged on data before `LATE_WINDOW_LIVE=YES`.
"""

import math
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

from src.config.settings import (
    BINANCE_FUNDING_MAP,
    LATE_WINDOW_BET_USD,
    LATE_WINDOW_ENABLED,
    LATE_WINDOW_EVAL_INTERVAL,
    LATE_WINDOW_LIVE,
    LATE_WINDOW_MIN_DISTANCE_PCT,
    LATE_WINDOW_MAX_DISAGREEMENT,
    LATE_WINDOW_MIN_EV,
    LATE_WINDOW_SECONDS,
    MARKETS,
    TAKER_FEE_RATE,
    KELLY_FRACTION,
    KELLY_MAX_FRACTION,
    KELLY_PROB_HAIRCUT,
    KELLY_SIZING,
)
from src.data.db_connection import db_connection
from src.data.market_data import get_token_ids
from src.data.market_data.binance import get_current_spot_price, get_window_start_price
from src.data.market_data.polybolt import PolyBoltFeed
from src.markets import asset_of, parse_market, window_bounds
from src.trading.edge import (
    kelly_binary,
    late_window_ev,
    late_window_probability,
    late_window_z,
    scaled_kelly,
    taker_fee,
)
from src.utils.logger import log, log_error

BASIS_MULTIPLIER = 2.0  # Binance fallback must clear the distance filter 2x

_feed: Optional[PolyBoltFeed] = None
_sigma_cache: Dict[str, Tuple[float, float]] = {}  # asset -> (fetched_at, sigma_1m)
_last_eval: Dict[str, float] = {}
_live_done: set = set()


def sigma_1m(asset: str) -> Optional[float]:
    """Stdev of 1-minute log returns over the last 3 hours of Binance data (cached 60s)."""
    cached = _sigma_cache.get(asset)
    if cached and time.time() - cached[0] < 60:
        return cached[1]
    pair = BINANCE_FUNDING_MAP.get(asset)
    if not pair:
        return None
    try:
        rows = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": pair, "interval": "1m", "limit": 180},
            timeout=5,
        ).json()
        closes = [float(r[4]) for r in rows]
        rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
        mean = sum(rets) / len(rets)
        sigma = math.sqrt(sum((r - mean) ** 2 for r in rets) / len(rets))
        _sigma_cache[asset] = (time.time(), sigma)
        return sigma
    except Exception:
        return cached[1] if cached else None


def best_ask(token_id: str) -> Optional[float]:
    from src.utils.websocket_manager import ws_manager

    _, ask = ws_manager.get_bid_ask(token_id)
    if ask and ask > 0.01:
        return float(ask)
    try:
        from src.trading.orders import get_clob_client

        book = get_clob_client().get_order_book(token_id)
        asks = [float(x["price"]) for x in (book.get("asks") or [])]
        return min(asks) if asks else None
    except Exception:
        return None


def _prices(symbol: str, window_start: datetime) -> Optional[Tuple[float, float, str]]:
    """(strike, spot, source) preferring the Chainlink feed that resolves the market."""
    asset = asset_of(symbol)
    if _feed is not None and _feed.connected:
        strike = _feed.strike(asset, window_start.timestamp())
        spot = _feed.current_twap(asset) or _feed.current_spot(asset)
        if strike and spot:
            return strike, spot, "chainlink"
    strike = get_window_start_price(symbol)
    spot = get_current_spot_price(symbol)
    if strike > 0 and spot > 0:
        return strike, spot, "binance"
    return None


def market_disagrees(p_use: float, ask: float) -> bool:
    """True when the ask is far below our probability; there the market has been right, not us."""
    return p_use - ask > LATE_WINDOW_MAX_DISAGREEMENT


def evaluate(symbol: str, now: Optional[datetime] = None) -> Optional[dict]:
    """One evaluation for a market key; None when outside the evaluation window."""
    now = now or datetime.now(tz=ZoneInfo("America/New_York"))
    start, end = window_bounds(symbol, now)
    left = (end - now).total_seconds()
    if left > LATE_WINDOW_SECONDS or left < 5:
        return None

    prices = _prices(symbol, start)
    sigma = sigma_1m(asset_of(symbol))
    if not prices or not sigma:
        return {"symbol": symbol, "decision": "SKIP", "reason": "no price/vol data", "left": left}
    strike, spot, source = prices

    z = late_window_z(spot, strike, sigma, left)
    side = "UP" if z > 0 else "DOWN"
    p_model, p_emp, p_use = late_window_probability(z)
    up_id, down_id = get_token_ids(symbol)
    token_id = up_id if side == "UP" else down_id
    ask = best_ask(token_id) if token_id else None
    dist_pct = abs(spot - strike) / strike * 100.0
    min_dist = LATE_WINDOW_MIN_DISTANCE_PCT * (1.0 if source == "chainlink" else BASIS_MULTIPLIER)

    row = {
        "symbol": symbol, "slug": f"{parse_market(symbol).asset.lower()}-updown-{parse_market(symbol).slug_interval}-{int(start.timestamp())}",
        "window_end": end.isoformat(), "left": left, "side": side, "token_id": token_id,
        "strike": strike, "spot": spot, "source": source, "z": z, "dist_pct": dist_pct,
        "p_model": p_model, "p_emp": p_emp, "p_use": p_use, "ask": ask,
        "fee": taker_fee(ask, TAKER_FEE_RATE) if ask else None,
        "ev": late_window_ev(p_use, ask) if ask else None,
    }
    row["kelly"] = (
        scaled_kelly(
            kelly_binary(p_use - KELLY_PROB_HAIRCUT, ask + taker_fee(ask, TAKER_FEE_RATE)),
            KELLY_FRACTION,
            KELLY_MAX_FRACTION,
        )
        if ask
        else None
    )
    if ask is None or ask >= 0.99:
        row.update(decision="SKIP", reason="no ask / priced out")
    elif dist_pct < min_dist:
        row.update(decision="SKIP", reason=f"too close to strike ({dist_pct:.3f}% < {min_dist:.3f}%, {source})")
    elif market_disagrees(p_use, ask):
        row.update(decision="SKIP", reason=f"market disagrees (ask {ask:.2f} << p {p_use:.2f})")
    elif row["ev"] < LATE_WINDOW_MIN_EV:
        row.update(decision="SKIP", reason=f"EV {row['ev'] * 100:+.1f}¢ < {LATE_WINDOW_MIN_EV * 100:.1f}¢")
    else:
        row.update(decision="ENTER", reason=f"EV {row['ev'] * 100:+.1f}¢")
    return row


def _log_row(r: dict) -> None:
    if "z" not in r:
        log(f"   🕐 [{r['symbol']}] late-window skip: {r['reason']}")
        return
    tag = "LIVE" if LATE_WINDOW_LIVE else "SHADOW"
    log(
        f"   🕐 [{r['symbol']}] T-{r['left']:.0f}s | strike {r['strike']:,.2f} | spot {r['spot']:,.2f} "
        f"({(r['spot'] / r['strike'] - 1) * 100:+.3f}%, {r['source']}) | z={r['z']:+.2f}"
    )
    ask = f"{r['ask']:.2f}" if r["ask"] else "n/a"
    ev = f"{r['ev'] * 100:+.1f}¢" if r["ev"] is not None else "n/a"
    fee = f"{r['fee'] * 100:.2f}¢" if r["fee"] is not None else "n/a"
    log(
        f"   🧮 [{r['symbol']}] {r['side']} p_model={r['p_model']:.3f} p_emp={r['p_emp']:.3f} → p_use={r['p_use']:.3f} "
        f"| ask {ask} fee {fee} → EV {ev} | Kelly stake {(r.get('kelly') or 0) * 100:.1f}% → {r['decision']} [{tag}] ({r['reason']})"
    )


def _record(r: dict) -> None:
    if "z" not in r:
        return
    try:
        with db_connection() as conn:
            conn.execute(
                """INSERT INTO shadow_signals (ts, symbol, slug, window_end, seconds_left, side, token_id,
                   strike, spot, source, z, dist_pct, p_model, p_emp, p_use, ask, fee, ev, decision, reason)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    datetime.now(timezone.utc).isoformat(), r["symbol"], r["slug"], r["window_end"],
                    r["left"], r["side"], r["token_id"], r["strike"], r["spot"], r["source"], r["z"],
                    r["dist_pct"], r["p_model"], r["p_emp"], r["p_use"], r["ask"], r["fee"], r["ev"],
                    r["decision"], r["reason"],
                ),
            )
    except Exception as e:
        log_error(f"shadow_signals insert failed: {e}")


def _enter_live(r: dict) -> None:
    """Buy the leading side as a FAK taker order and record it as a normal trade."""
    from src.data.database import has_trade_for_window, save_trade
    from src.trading.orders import get_order, place_limit_order

    symbol, slug = r["symbol"], r["slug"]
    if slug in _live_done:
        return
    _live_done.add(slug)
    start, end = window_bounds(symbol)
    if has_trade_for_window(symbol, start.isoformat()):
        log(f"   ⏭️  [{symbol}] late-window: window already has a trade - skipping live entry")
        return
    bet_usd = LATE_WINDOW_BET_USD
    if KELLY_SIZING:
        from src.trading.orders import get_balance_allowance

        info = get_balance_allowance() or {}
        bankroll = float(info.get("balance", 0.0))
        cost = r["ask"] + r["fee"]
        full = kelly_binary(r["p_use"] - KELLY_PROB_HAIRCUT, cost)
        frac = scaled_kelly(full, KELLY_FRACTION, KELLY_MAX_FRACTION)
        bet_usd = min(bankroll * frac, LATE_WINDOW_BET_USD)  # LATE_WINDOW_BET_USD stays a hard cap
        log(
            f"   🎲 [{symbol}] Kelly: p={r['p_use']:.3f}-{KELLY_PROB_HAIRCUT:.2f} cost={cost:.3f} → full={full:.2f} × {KELLY_FRACTION:g} "
            f"(cap {KELLY_MAX_FRACTION:.0%}) → stake ${bet_usd:.2f} of ${bankroll:.2f}"
        )
    size = math.floor(bet_usd / r["ask"] * 100) / 100
    if size < 5.0:
        log(f"   ⏭️  [{symbol}] late-window: size {size:.2f} < 5 share minimum (raise LATE_WINDOW_BET_USD)")
        return
    log(f"   🚀 [{symbol}] LATE-WINDOW ENTRY: BUY {r['side']} {size:.2f} @ ${r['ask']:.2f} (FAK, EV {r['ev'] * 100:+.1f}¢)")
    res = place_limit_order(r["token_id"], r["ask"], size, "BUY", order_type="FAK")
    if not res.get("success") or not res.get("order_id"):
        log(f"   ❌ [{symbol}] late-window order failed: {res.get('error')}")
        return
    time.sleep(1.0)
    info = get_order(res["order_id"]) or {}
    matched = float(info.get("size_matched") or 0)
    if matched <= 0:
        log(f"   ⚠️  [{symbol}] late-window order did not fill (FAK killed)")
        return
    trade_id = save_trade(
        symbol=symbol, window_start=start.isoformat(), window_end=end.isoformat(), slug=slug,
        token_id=r["token_id"], side=r["side"], edge=r["ev"], price=r["ask"], size=matched,
        bet_usd=matched * r["ask"], p_yes=r["p_use"] if r["side"] == "UP" else 1 - r["p_use"],
        order_status="FILLED", order_id=res["order_id"],
    )
    log(f"   ✅ [{symbol}] late-window trade #{trade_id}: {r['side']} {matched:.2f} @ ${r['ask']:.2f}")


def resolve_shadow_signals() -> int:
    """Fill in `won` for finished windows from the real market resolution."""
    from src.trading.settlement import get_market_resolution

    resolved = 0
    with db_connection() as conn:
        rows = conn.execute(
            """SELECT DISTINCT slug FROM shadow_signals WHERE won IS NULL
               AND datetime(window_end) < datetime('now', '-120 seconds') LIMIT 40"""
        ).fetchall()
        for (slug,) in rows:
            done, prices = get_market_resolution(slug)
            if not done or not prices:
                continue
            up_won = prices[0] >= 0.99
            conn.execute(
                """UPDATE shadow_signals SET won = CASE WHEN (side='UP') = ? THEN 1 ELSE 0 END,
                   resolved_at = ? WHERE slug = ? AND won IS NULL""",
                (1 if up_won else 0, datetime.now(timezone.utc).isoformat(), slug),
            )
            resolved += 1
    return resolved


def _loop(markets: List[str]) -> None:
    last_resolve = 0.0
    while True:
        try:
            for m in markets:
                r = evaluate(m)
                if r is None:
                    continue
                key = r.get("slug") or m
                if time.time() - _last_eval.get(key, 0) < LATE_WINDOW_EVAL_INTERVAL:
                    continue
                _last_eval[key] = time.time()
                _log_row(r)
                _record(r)
                if r["decision"] == "ENTER" and LATE_WINDOW_LIVE:
                    _enter_live(r)
            if time.time() - last_resolve > 60:
                last_resolve = time.time()
                n = resolve_shadow_signals()
                if n:
                    log(f"   🧾 Resolved shadow signals for {n} window(s)")
        except Exception as e:
            log_error(f"late-window loop error: {e}")
        time.sleep(2)


def start_late_window_monitor() -> None:
    """Start the PolyBolt feed (if credentials exist) and the evaluation thread."""
    global _feed
    if not LATE_WINDOW_ENABLED:
        log("   ⏭️  Late-window mode disabled")
        return
    assets = {asset_of(m) for m in MARKETS}
    feed = PolyBoltFeed(assets)
    if feed.start():
        _feed = feed
    else:
        log(f"   ⚠️  PolyBolt Chainlink feed unavailable ({feed.fatal}) - using Binance with {BASIS_MULTIPLIER:.0f}x distance filter")
    threading.Thread(target=_loop, args=(list(MARKETS),), daemon=True, name="LateWindow").start()
    mode = "LIVE orders" if LATE_WINDOW_LIVE else "SHADOW (logging only)"
    log(f"   🕐 Late-window monitor started: last {LATE_WINDOW_SECONDS}s, min EV {LATE_WINDOW_MIN_EV * 100:.1f}¢, {mode}")
