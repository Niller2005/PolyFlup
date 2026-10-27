"""Risk profiles: named bundles of settings, switchable at runtime.

Pure data + tiny state helpers (sqlite only, no `src.config` import) so the read-only
TUI can show and request profiles without a `.env`. The bot applies them live, see
`src/risk_runtime.py`.

State lives in the `bot_state` table of the bot's own database (so paper and real
accounts keep separate profiles):
    risk_profile          what the user asked for (written by the TUI or at startup)
    risk_profile_applied  what the running bot has actually applied
"""

import sqlite3
from datetime import datetime, timezone
from typing import Dict, Optional

# `custom` applies nothing: the values from .env stay exactly as written.
CUSTOM = "custom"

PROFILES: Dict[str, Dict[str, float]] = {
    "conservative": {
        "BET_PERCENT": 3.0,  # share of free cash per entry
        "MAX_EXPOSURE_PERCENT": 25.0,  # cap on open positions + entries in flight, % of equity
        "MAX_SIZE": 50.0,  # shares per entry
        "MIN_EDGE": 0.45,  # signal confidence needed to enter
        "MAX_SPREAD": 0.12,
        "MIN_NET_EDGE": 0.02,  # guaranteed profit per share after fees (2c needs a 0.98 pair)
        "MAKER_PAIR_PRICE_BAND": 0.30,  # only quote while both bids are in [0.30, 0.70]
        "TRAIL_MAX_TICKS": 1,
        "HEDGE_FILL_TIMEOUT_SECONDS": 60,  # leave a half-filled pair sooner
        "HEDGE_GTC_FALLBACK_SECONDS": 20,
        "LIQUIDITY_MAX_SPREAD_PCT": 5.0,
        "LATE_WINDOW_MIN_EV": 0.03,
        "KELLY_FRACTION": 0.15,
        "KELLY_MAX_FRACTION": 0.04,
    },
    "balanced": {
        "BET_PERCENT": 6.0,
        "MAX_EXPOSURE_PERCENT": 45.0,
        "MAX_SIZE": 150.0,
        "MIN_EDGE": 0.40,
        "MAX_SPREAD": 0.15,
        "MIN_NET_EDGE": 0.01,
        "MAKER_PAIR_PRICE_BAND": 0.20,
        "TRAIL_MAX_TICKS": 2,
        "HEDGE_FILL_TIMEOUT_SECONDS": 90,
        "HEDGE_GTC_FALLBACK_SECONDS": 30,
        "LIQUIDITY_MAX_SPREAD_PCT": 8.0,
        "LATE_WINDOW_MIN_EV": 0.015,
        "KELLY_FRACTION": 0.25,
        "KELLY_MAX_FRACTION": 0.06,
    },
    "aggressive": {
        "BET_PERCENT": 10.0,
        "MAX_EXPOSURE_PERCENT": 70.0,
        "MAX_SIZE": 500.0,
        "MIN_EDGE": 0.32,
        "MAX_SPREAD": 0.18,
        "MIN_NET_EDGE": 0.01,
        "MAKER_PAIR_PRICE_BAND": 0.12,
        "TRAIL_MAX_TICKS": 3,
        "HEDGE_FILL_TIMEOUT_SECONDS": 120,
        "HEDGE_GTC_FALLBACK_SECONDS": 45,
        "LIQUIDITY_MAX_SPREAD_PCT": 12.0,
        "LATE_WINDOW_MIN_EV": 0.01,
        "KELLY_FRACTION": 0.35,
        "KELLY_MAX_FRACTION": 0.10,
    },
}
ORDER = ["conservative", "balanced", "aggressive", CUSTOM]
KEYS = sorted({k for p in PROFILES.values() for k in p})
HOTKEYS = {"1": "conservative", "2": "balanced", "3": "aggressive", "0": CUSTOM}


def normalize(name: Optional[str]) -> str:
    n = (name or CUSTOM).strip().lower()
    return n if n in PROFILES or n == CUSTOM else CUSTOM


def describe(name: str) -> str:
    """One-line summary for the TUI."""
    p = PROFILES.get(name)
    if not p:
        return "values from .env"
    return (
        f"bet {p['BET_PERCENT']:g}% of cash · exposure ≤{p['MAX_EXPOSURE_PERCENT']:g}% · "
        f"≤{p['MAX_SIZE']:g} sh · net edge ≥{p['MIN_NET_EDGE'] * 100:g}¢ · timeout {p['HEDGE_FILL_TIMEOUT_SECONDS']}s"
    )


# ---- state in the bot database ------------------------------------------------
def ensure(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS bot_state (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")


def get(conn: sqlite3.Connection, key: str) -> Optional[str]:
    try:
        row = conn.execute("SELECT value FROM bot_state WHERE key=?", (key,)).fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def put(conn: sqlite3.Connection, key: str, value: str) -> None:
    ensure(conn)
    conn.execute(
        "INSERT INTO bot_state (key, value, updated_at) VALUES (?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, value, datetime.now(timezone.utc).isoformat()),
    )


def request_profile(db_path: str, name: str) -> None:
    """Ask the running bot to switch (used by the TUI; short-lived read-write connection)."""
    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        put(conn, "risk_profile", normalize(name))
        conn.commit()
    finally:
        conn.close()
