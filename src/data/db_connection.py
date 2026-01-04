"""Database connection context manager - supports SQLite, Turso, and Embedded Replicas"""

import sqlite3
import time
from contextlib import contextmanager
from src.config.settings import (
    DB_FILE,
    USE_TURSO,
    TURSO_DATABASE_URL,
    TURSO_AUTH_TOKEN,
    USE_EMBEDDED_REPLICA,
    EMBEDDED_REPLICA_FILE,
)
from src.utils.logger import log

# Track last sync time for embedded replica (sync every 30 seconds max)
_last_sync_time = 0
_sync_interval = 30  # seconds


@contextmanager
def db_connection():
    """
    Context manager for database connections with automatic cleanup.
    Supports three modes:
    1. Local SQLite (default for dev)
    2. Turso (remote database for production)
    3. Embedded Replica (local replica synced with remote Turso - RECOMMENDED for local dev)

    Configuration:
    - USE_TURSO=YES: Connect directly to remote Turso database
    - USE_EMBEDDED_REPLICA=YES: Use local replica that syncs with Turso (recommended for dev)
    - Neither: Use local SQLite file

    Args:
        sync_on_connect: For embedded replica, sync before yielding connection (default: True)
        sync_on_close: For embedded replica, sync after commit (default: True)

    Usage:
        # For frequent reads (like position monitoring), disable syncing:
        with db_connection(sync_on_connect=False, sync_on_close=False) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trades WHERE settled = 0")

        # For writes, keep syncing enabled (default):
        with db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO trades (...) VALUES (...)")
    """
    if USE_EMBEDDED_REPLICA:
        # Use Embedded Replica (local file synced with remote Turso)
        try:
            import libsql
        except ImportError:
            raise ImportError(
                "libsql is required for Embedded Replicas. Install with: pip install libsql"
            )

        if not TURSO_DATABASE_URL or not TURSO_AUTH_TOKEN:
            raise ValueError(
                "TURSO_DATABASE_URL and TURSO_AUTH_TOKEN must be set when USE_EMBEDDED_REPLICA=YES"
            )

        global _last_sync_time

        # Connect with embedded replica configuration
        # The libsql Python SDK uses .connect() with sync_url parameter
        conn = libsql.connect(
            EMBEDDED_REPLICA_FILE,  # Local SQLite file
            sync_url=TURSO_DATABASE_URL,  # Remote Turso database to sync with
            auth_token=TURSO_AUTH_TOKEN,
        )

        try:
            # Sync periodically (every 30s) to reduce network overhead on reads
            current_time = time.time()
            if current_time - _last_sync_time >= _sync_interval:
                conn.sync()
                _last_sync_time = current_time

            yield conn
            conn.commit()

            # Sync after commit to push writes to remote (fast no-op if no changes)
            conn.sync()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    elif USE_TURSO:
        # Use Turso (direct remote connection)
        try:
            import libsql
        except ImportError:
            raise ImportError(
                "libsql is required for Turso. Install with: pip install libsql"
            )

        if not TURSO_DATABASE_URL or not TURSO_AUTH_TOKEN:
            raise ValueError(
                "TURSO_DATABASE_URL and TURSO_AUTH_TOKEN must be set when USE_TURSO=YES"
            )

        log(f"📡 Connecting to remote Turso database")

        # For remote-only connection, use the URL directly
        # Note: libsql.connect() with just URL creates a remote connection
        conn = libsql.connect(
            TURSO_DATABASE_URL,
            auth_token=TURSO_AUTH_TOKEN,
        )

        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        # Use local SQLite
        conn = sqlite3.connect(DB_FILE, timeout=30.0)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
