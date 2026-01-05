"""Database connection context manager for SQLite"""

import sqlite3
from contextlib import contextmanager
from src.config.settings import DB_FILE


@contextmanager
def db_connection():
    """
    Context manager for database connections with automatic cleanup.
    Uses local SQLite file.

    Usage:
        with db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trades WHERE settled = 0")
    """
    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
