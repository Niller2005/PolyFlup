"""Position sizing against available cash and a maximum portfolio exposure.

    available   = wallet cash - cash reserved by entries still being placed
    exposure    = cost of open positions (entry + hedge for hedged pairs)
    equity      = wallet cash + exposure
    headroom    = MAX_EXPOSURE_PERCENT% of equity - exposure - reserved
    allocation  = min(BET_PERCENT% of available, headroom)

Each new entry is therefore smaller as cash is used up, and no entry may push total
exposure above the cap.
"""

import threading
from typing import Dict

from src.data.db_connection import db_connection
from src.trading import inflight

_local = threading.local()


def open_exposure_usd() -> float:
    """Cost basis of unsettled positions: entry + hedge for hedged pairs, entry otherwise."""
    with db_connection() as conn:
        row = conn.execute(
            """SELECT COALESCE(SUM(
                   CASE WHEN COALESCE(is_hedged, 0) = 1 AND COALESCE(hedge_order_price, 0) > 0
                             AND COALESCE(exited_early, 0) = 0 AND COALESCE(hedge_exited_early, 0) = 0
                        THEN size * (entry_price + hedge_order_price)
                        WHEN COALESCE(exited_early, 0) = 1 THEN 0
                        ELSE COALESCE(bet_usd, 0) END), 0)
               FROM trades WHERE settled = 0"""
        ).fetchone()
    return float(row[0] or 0.0)


def sizing_state(wallet_cash: float) -> Dict[str, float]:
    from src.config.settings import (
        BET_PERCENT,
        KELLY_MAX_FRACTION,
        KELLY_SIZING,
        MAX_EXPOSURE_PERCENT,
        SIZE_FROM_AVAILABLE_CASH,
    )

    # Kelly stakes never exceed KELLY_MAX_FRACTION, so that is the most an entry can reserve
    pct = KELLY_MAX_FRACTION * 100.0 if KELLY_SIZING else BET_PERCENT

    reserved = inflight.reserved_usd()
    exposure = open_exposure_usd()
    available = max(0.0, wallet_cash - reserved)
    equity = wallet_cash + exposure
    cap = equity * MAX_EXPOSURE_PERCENT / 100.0
    headroom = max(0.0, cap - exposure - reserved)
    base = available if SIZE_FROM_AVAILABLE_CASH else wallet_cash
    allocation = min(base * pct / 100.0, headroom)
    return {
        "wallet": wallet_cash,
        "reserved": reserved,
        "available": available,
        "exposure": exposure,
        "equity": equity,
        "cap": cap,
        "headroom": headroom,
        "allocation": allocation,
        "exposure_pct": (exposure + reserved) / equity * 100.0 if equity > 0 else 0.0,
    }


# Per-thread ceiling on the entry being sized (set by the entry worker, read by sizing code)
def set_allocation_cap(usd) -> None:
    _local.cap = usd


def allocation_cap():
    return getattr(_local, "cap", None)
