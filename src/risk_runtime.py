"""Apply a risk profile to the running bot (hot, no restart).

Settings are module-level constants that many modules imported by value, so applying a
profile sets the new value on `src.config.settings` and on every loaded `src.*` module
that holds the same name with the old value. Code that imports lazily inside functions
picks the change up from `settings` directly. Entries already in flight keep the
parameters they started with.
"""

import sys
from typing import Any, Dict

from src import profiles
from src.config import settings
from src.data.db_connection import db_connection
from src.utils.logger import log

_base: Dict[str, Any] = {}  # the .env values, restored by `custom`
_active = None


def _snapshot_base() -> None:
    if not _base:
        for k in profiles.KEYS:
            _base[k] = getattr(settings, k)


def _set(key: str, value: Any) -> None:
    old = getattr(settings, key)
    setattr(settings, key, value)
    for name, mod in list(sys.modules.items()):
        if mod is None or mod is settings or not (name == "src" or name.startswith("src.")):
            continue
        try:
            if key in vars(mod) and vars(mod)[key] == old and type(vars(mod)[key]) is type(old):
                setattr(mod, key, value)
        except Exception:
            continue


def apply_profile(name: str) -> str:
    """Make `name` the active profile; returns the normalised name."""
    global _active
    _snapshot_base()
    name = profiles.normalize(name)
    values = _base if name == profiles.CUSTOM else {**_base, **profiles.PROFILES[name]}
    for k in profiles.KEYS:
        v = values[k]
        cur = getattr(settings, k)
        if isinstance(cur, float) and v is not None:
            v = float(v)
        _set(k, v)
    _active = name
    return name


def active_profile() -> str:
    return _active or profiles.CUSTOM


def init() -> None:
    """At startup: the DB request wins (the user may have switched while the bot was down),
    else RISK_PROFILE from .env."""
    with db_connection() as conn:
        profiles.ensure(conn)
        name = profiles.get(conn, "risk_profile") or settings.RISK_PROFILE
        name = profiles.normalize(name)
        apply_profile(name)
        profiles.put(conn, "risk_profile", name)
        profiles.put(conn, "risk_profile_applied", name)
    log(f"🎚️  Risk profile: {name.upper()} ({profiles.describe(name)})")


def poll() -> None:
    """Called from the main loop: switch when the TUI (or anything) requested a new profile."""
    with db_connection() as conn:
        wanted = profiles.normalize(profiles.get(conn, "risk_profile"))
        if wanted == active_profile():
            return
        apply_profile(wanted)
        profiles.put(conn, "risk_profile_applied", wanted)
    log(f"🎚️  Risk profile switched to {wanted.upper()} ({profiles.describe(wanted)})")
