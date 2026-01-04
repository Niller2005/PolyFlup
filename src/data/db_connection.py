"""Database connection context manager"""

import sqlite3
from contextlib import contextmanager
from src.config.settings import DB_FILE


@contextmanager
def db_connection():
    """Context manager for database connections with automatic cleanup"""
    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
