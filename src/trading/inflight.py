"""Registry of entries currently being placed/monitored by worker threads.

An entry's legs fill on the exchange before the trade row is saved (the row is written
once both legs are confirmed). While any entry is in flight, position sync must not
treat those fills as untracked positions and adopt them.
"""

import threading
from typing import Dict, Set

_active: Dict[str, float] = {}  # symbol -> cash reserved for the entry
_lock = threading.Lock()


def mark(symbol: str, reserved_usd: float = 0.0) -> None:
    with _lock:
        _active[symbol] = reserved_usd


def clear(symbol: str) -> None:
    with _lock:
        _active.pop(symbol, None)


def entries_in_flight() -> Set[str]:
    with _lock:
        return set(_active)


def reserved_usd() -> float:
    """Cash earmarked by entries that have not filled yet (still in the wallet)."""
    with _lock:
        return sum(_active.values())
