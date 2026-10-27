"""PolyBolt live price feed: the Chainlink prices Polymarket actually resolves on.

Up/down markets resolve on a Chainlink 60-second TWAP ("price to beat" at the window
start vs. the TWAP at the end), not on Binance. This feed streams both:

    price.crypto       Chainlink spot, e.g. symbol "btcusd"
    price.crypto.twap  Chainlink 60s TWAP (window_seconds=60)

Protocol: https://docs.polymarket.com/api-reference/wss/polybolt.md
Reference-price channels need CLOB API credentials (`{"op":"auth"}`), the same
API_KEY / API_SECRET / API_PASSPHRASE the bot already uses for trading.
"""

import asyncio
import json
import os
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple

import websockets

from src.utils.logger import log, log_error

POLYBOLT_URL = "wss://ws-live-v2.polymarket.com/ws"
HISTORY_SECONDS = 1800  # keep 30 min of TWAP samples (covers 15m windows + margin)


def to_pair(asset: str) -> str:
    """BTC -> btcusd (PolyBolt wants lowercase symbols ending in usd, not usdt)."""
    return f"{asset.lower()}usd"


class PolyBoltFeed:
    def __init__(self, assets: Iterable[str]):
        self.assets = sorted({a.upper() for a in assets})
        self.twap: Dict[str, Deque[Tuple[float, float]]] = {
            a: deque(maxlen=HISTORY_SECONDS) for a in self.assets
        }  # asset -> (ts_seconds, value)
        self.spot: Dict[str, Tuple[float, float]] = {}  # asset -> (ts_seconds, value)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.connected = False
        self.fatal: Optional[str] = None

    # ---- public ----------------------------------------------------------
    def start(self) -> bool:
        creds = [os.getenv(k) for k in ("API_KEY", "API_SECRET", "API_PASSPHRASE")]
        if not all(creds):
            self.fatal = "missing API_KEY/API_SECRET/API_PASSPHRASE"
            return False
        self._creds = creds
        self._thread = threading.Thread(target=self._run, daemon=True, name="PolyBolt")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def current_twap(self, asset: str, max_age: float = 15.0) -> Optional[float]:
        with self._lock:
            hist = self.twap.get(asset.upper())
            if not hist:
                return None
            ts, val = hist[-1]
        return val if time.time() - ts <= max_age else None

    def current_spot(self, asset: str, max_age: float = 15.0) -> Optional[float]:
        with self._lock:
            item = self.spot.get(asset.upper())
        if not item or time.time() - item[0] > max_age:
            return None
        return item[1]

    def strike(self, asset: str, window_start_ts: float, tolerance: float = 5.0):
        """TWAP sample closest to the window start (the market's "price to beat").

        Returns None unless a sample within `tolerance` seconds exists, i.e. the
        feed was connected when the window opened.
        """
        with self._lock:
            hist = list(self.twap.get(asset.upper(), ()))
        best = None
        for ts, val in hist:
            d = abs(ts - window_start_ts)
            if d <= tolerance and (best is None or d < best[0]):
                best = (d, val)
        return best[1] if best else None

    # ---- ingestion (pure, unit-testable) ----------------------------------
    def ingest(self, msg: Dict[str, Any]) -> None:
        channel = msg.get("channel", "")
        payload = msg.get("payload")
        if channel not in ("price.crypto", "price.crypto.twap") or payload is None:
            return
        rows = payload.get("data") if isinstance(payload, dict) else None
        if rows is None:
            rows = [payload]
        symbol = (payload.get("symbol") if isinstance(payload, dict) else None) or ""
        asset = symbol.lower().removesuffix("usd").upper() if symbol else ""
        for row in rows:
            try:
                value = float(row.get("full_accuracy_value") or row["value"])
                ts_raw = float(row.get("timestamp") or msg.get("ts") or 0)
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
            ts = ts_raw / 1000.0 if ts_raw > 1e11 else ts_raw  # ms -> s
            if not asset or asset not in self.twap:
                continue
            with self._lock:
                if channel == "price.crypto.twap":
                    hist = self.twap[asset]
                    if not hist or ts > hist[-1][0]:
                        hist.append((ts, value))
                else:
                    self.spot[asset] = (ts, value)

    # ---- connection loop --------------------------------------------------
    def _run(self) -> None:
        asyncio.run(self._main())

    async def _main(self) -> None:
        backoff = 1.0
        while not self._stop.is_set() and not self.fatal:
            try:
                async with websockets.connect(POLYBOLT_URL, ping_interval=None) as ws:
                    await ws.send(
                        json.dumps(
                            {
                                "op": "auth",
                                "auth": {
                                    "apiKey": self._creds[0],
                                    "secret": self._creds[1],
                                    "passphrase": self._creds[2],
                                },
                            }
                        )
                    )
                    subs: List[dict] = []
                    for a in self.assets:
                        pair = to_pair(a)
                        subs.append({"channel": "price.crypto", "filter": {"symbol": pair}})
                        subs.append(
                            {
                                "channel": "price.crypto.twap",
                                "filter": {"symbol": pair, "window_seconds": 60},
                            }
                        )
                    await ws.send(json.dumps({"op": "subscribe", "rid": "s1", "subscriptions": subs}))
                    self.connected = True
                    backoff = 1.0
                    log(f"📡 PolyBolt connected: Chainlink prices for {', '.join(self.assets)}")
                    async for raw in ws:
                        if self._stop.is_set():
                            return
                        try:
                            self.ingest(json.loads(raw))
                        except ValueError:
                            continue
            except websockets.ConnectionClosed as e:
                code = getattr(e, "code", None)
                if code in (4001, 4008):  # auth failed / policy: reconnecting won't help
                    self.fatal = f"closed {code}: {getattr(e, 'reason', '')}"
                    log_error(f"PolyBolt feed disabled ({self.fatal})", include_traceback=False)
                    return
            except Exception as e:
                log(f"⚠️  PolyBolt error: {e}")
            finally:
                self.connected = False
            await asyncio.sleep(min(30.0, backoff))
            backoff *= 2
