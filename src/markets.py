"""Market keys and window math for Polymarket up/down markets.

A *market key* is the string the whole bot uses as `symbol` (DB column, logs, caches):

    BTC      15-minute market on BTC   (default; unchanged from before 5m support)
    BTC-5M   5-minute market on BTC

`.env` accepts `BTC`, `BTC-15M`, `BTC-5M`, `BTC:5m` and `BTC5M`; all are normalised to the
key above. Polymarket's slug is `{asset}-updown-{N}m-{window_start_unix}`.

This module must stay free of `src.config` imports (which require a `.env`), so the
read-only TUI can use it against a downloaded database.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
DEFAULT_MINUTES = 15
SUPPORTED_MINUTES = (5, 15)

_KEY_RE = re.compile(r"^([A-Z0-9]+?)(?:[-:_ ]?(\d+)M)?$")
_SLUG_RE = re.compile(r"^([a-z0-9]+)-updown-(\d+)m-(\d+)$")


@dataclass(frozen=True)
class MarketSpec:
    key: str  # what the bot uses as `symbol`
    asset: str  # Binance/Gamma asset, e.g. "BTC"
    minutes: int  # window length

    @property
    def seconds(self) -> int:
        return self.minutes * 60

    @property
    def slug_interval(self) -> str:
        return f"{self.minutes}m"


def make_key(asset: str, minutes: int = DEFAULT_MINUTES) -> str:
    asset = asset.upper()
    return asset if minutes == DEFAULT_MINUTES else f"{asset}-{minutes}M"


def parse_market(value: str) -> MarketSpec:
    """Parse a market key / .env entry into a MarketSpec (raises ValueError if invalid)."""
    m = _KEY_RE.match(value.strip().upper())
    if not m or not m.group(1):
        raise ValueError(f"Invalid market '{value}' (expected e.g. BTC, BTC-5M)")
    asset = m.group(1)
    minutes = int(m.group(2)) if m.group(2) else DEFAULT_MINUTES
    if minutes not in SUPPORTED_MINUTES:
        raise ValueError(
            f"Unsupported window '{minutes}m' in '{value}' (supported: "
            f"{', '.join(f'{x}m' for x in SUPPORTED_MINUTES)})"
        )
    return MarketSpec(make_key(asset, minutes), asset, minutes)


def asset_of(symbol: str) -> str:
    """Asset for a market key; tolerant of plain assets and unparsable strings."""
    try:
        return parse_market(symbol).asset
    except ValueError:
        return symbol.upper()


def window_minutes(symbol: str) -> int:
    try:
        return parse_market(symbol).minutes
    except ValueError:
        return DEFAULT_MINUTES


def window_seconds(symbol: str) -> int:
    return window_minutes(symbol) * 60


def scale_seconds(seconds_at_15m: float, symbol: str, floor: float = 1.0) -> float:
    """Scale a timing tuned for 15m windows to `symbol`'s window length."""
    return max(floor, seconds_at_15m * window_minutes(symbol) / DEFAULT_MINUTES)


def window_bounds(
    symbol: str, now: Optional[datetime] = None
) -> Tuple[datetime, datetime]:
    """(start, end) of the current window in ET. 5m/15m slots align with unix time."""
    minutes = window_minutes(symbol)
    now_et = (now or datetime.now(tz=ET)).astimezone(ET)
    start = now_et.replace(
        minute=(now_et.minute // minutes) * minutes, second=0, microsecond=0
    )
    return start, start + timedelta(minutes=minutes)


def slug_for(symbol: str, now: Optional[datetime] = None) -> str:
    spec = parse_market(symbol)
    start, _ = window_bounds(symbol, now)
    return f"{spec.asset.lower()}-updown-{spec.slug_interval}-{int(start.timestamp())}"


def parse_slug(slug: str) -> Optional[Tuple[str, datetime, datetime]]:
    """`btc-updown-5m-1793126700` -> ("BTC-5M", window_start_et, window_end_et)."""
    m = _SLUG_RE.match(slug or "")
    if not m:
        return None
    asset, minutes, ts = m.group(1).upper(), int(m.group(2)), int(m.group(3))
    if minutes not in SUPPORTED_MINUTES:
        return None
    start = datetime.fromtimestamp(ts, tz=ET)
    return make_key(asset, minutes), start, start + timedelta(minutes=minutes)
