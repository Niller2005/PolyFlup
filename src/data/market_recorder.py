"""Raw market data recorder: everything needed to research strategies offline.

Independent of trading and never raises into it. Every MARKET_RECORD_INTERVAL seconds it stores, for
each traded market, the top of book of both tokens plus spot/strike (`market_ticks`); it drains the
websocket trade tape into `market_trades` (queue-fill / adverse-selection modelling needs the tape);
and once a window has ended it stores the real result in `market_windows` - for every market, not just
the ones we traded. Together with `pair_attempts` this allows replaying any pair/maker idea.
"""

import threading
import time
from datetime import datetime, timezone
from typing import Dict, Iterable, Optional

from src.data.db_connection import db_connection
from src.utils.logger import log, log_error

_thread: Optional[threading.Thread] = None


def _iso(t: Optional[float] = None) -> str:
    return datetime.fromtimestamp(t if t is not None else time.time(), tz=timezone.utc).isoformat()


class _State:
    def __init__(self) -> None:
        self.tokens: Dict[str, tuple] = {}  # slug -> (symbol, up_token, down_token)
        self.token_info: Dict[str, tuple] = {}  # token -> (symbol, slug, outcome)
        self.last: Dict[str, dict] = {}  # slug -> last tick (strike/spot) for the window result
        self.pending: Dict[str, tuple] = {}  # slug -> (symbol, start_iso, end_iso, end_epoch)
        self.done: set = set()


def _tick_once(markets: Iterable[str], st: _State) -> None:
    from datetime import datetime as dt
    from zoneinfo import ZoneInfo

    from src.data.market_data import get_token_ids
    from src.markets import slug_for, window_bounds
    from src.trading.late_window import _prices
    from src.utils.websocket_manager import ws_manager

    now_et = dt.now(tz=ZoneInfo("America/New_York"))
    rows = []
    for m in markets:
        try:
            start, end = window_bounds(m, now_et)
            slug = slug_for(m, now_et)
            if slug not in st.tokens:
                up, down = get_token_ids(m)
                if not up or not down:
                    continue
                st.tokens[slug] = (m, up, down)
                st.token_info[str(up)] = (m, slug, "UP")
                st.token_info[str(down)] = (m, slug, "DOWN")
                ws_manager.subscribe_to_prices([str(up), str(down)])
                st.pending[slug] = (m, start.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat(), end.timestamp())
            _, up, down = st.tokens[slug]
            ub, ua = ws_manager.get_bid_ask(str(up))
            db_, da = ws_manager.get_bid_ask(str(down))
            from src.trading import late_window

            feed = getattr(late_window, "_feed", None)
            # only the in-memory Chainlink feed: the Binance fallback would be an API call per market per tick
            prices = _prices(m, start) if feed is not None and feed.connected else None
            strike, spot = (prices[0], prices[1]) if prices else (None, None)
            rows.append((_iso(), m, slug, (end - now_et).total_seconds(), ub, ua, db_, da, spot, strike))
            st.last[slug] = {"strike": strike, "spot": spot}
        except Exception as e:
            log_error(f"market_recorder tick {m}: {e}")

    tape = []
    while ws_manager.trade_tape:
        try:
            ts, token, price, size, side = ws_manager.trade_tape.popleft()
        except IndexError:
            break
        info = st.token_info.get(token)
        if info:
            tape.append((_iso(ts), info[0], info[1], token, info[2], price, size, side))

    results = []
    from src.trading.settlement import get_market_resolution

    for slug, (sym, s_iso, e_iso, e_epoch) in list(st.pending.items()):
        if slug in st.done or time.time() < e_epoch + 150:
            continue
        try:
            done, prices = get_market_resolution(slug)
        except Exception:
            continue
        if done and prices:
            last = st.last.get(slug, {})
            results.append((slug, sym, s_iso, e_iso, last.get("strike"), last.get("spot"), 1 if prices[0] >= 0.99 else 0, _iso()))
            st.done.add(slug)
            st.pending.pop(slug, None)

    if not (rows or tape or results):
        return
    with db_connection() as conn:
        if rows:
            conn.executemany(
                "INSERT INTO market_ticks (ts, symbol, slug, secs_left, up_bid, up_ask, down_bid, down_ask, spot, strike) VALUES (?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
        if tape:
            conn.executemany(
                "INSERT INTO market_trades (ts, symbol, slug, token_id, outcome, price, size, side) VALUES (?,?,?,?,?,?,?,?)",
                tape,
            )
        if results:
            conn.executemany(
                "INSERT OR REPLACE INTO market_windows (slug, symbol, start_ts, end_ts, strike, final_spot, up_won, recorded_at) VALUES (?,?,?,?,?,?,?,?)",
                results,
            )


def _loop(markets, interval: float) -> None:
    st = _State()
    while True:
        try:
            _tick_once(markets, st)
        except Exception as e:
            log_error(f"market_recorder: {e}")
        time.sleep(interval)


def start(markets) -> None:
    """Start the recorder thread (MARKET_RECORD_INTERVAL seconds, 0 disables)."""
    global _thread
    from src.config.settings import MARKET_RECORD_INTERVAL

    if MARKET_RECORD_INTERVAL <= 0 or _thread is not None:
        return
    _thread = threading.Thread(target=_loop, args=(list(markets), MARKET_RECORD_INTERVAL), daemon=True, name="MarketRecorder")
    _thread.start()
    log(f"   🗄️  Market data recorder started: {len(list(markets))} markets every {MARKET_RECORD_INTERVAL:g}s (ticks, trade tape, window results)")
