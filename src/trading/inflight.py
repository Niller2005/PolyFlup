"""Registry of entries currently being placed/monitored by worker threads.

An entry's legs fill on the exchange before the trade row is saved (the row is written
once both legs are confirmed). While any entry is in flight, position sync must not
treat those fills as untracked positions and adopt them.
"""

import threading
from typing import Set

_active: Set[str] = set()
_lock = threading.Lock()


def mark(symbol: str) -> None:
    with _lock:
        _active.add(symbol)


def clear(symbol: str) -> None:
    with _lock:
        _active.discard(symbol)


def entries_in_flight() -> Set[str]:
    with _lock:
        return set(_active)
