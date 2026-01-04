"""Database connection context manager - supports both SQLite and Turso"""

import sqlite3
from contextlib import contextmanager
from src.config.settings import DB_FILE, USE_TURSO, TURSO_DATABASE_URL, TURSO_AUTH_TOKEN


class TursoConnection:
    """Wrapper to make Turso client compatible with sqlite3 interface"""

    def __init__(self, client):
        self.client = client
        self._cursor = None

    def cursor(self):
        """Return a cursor-like object"""
        if self._cursor is None:
            self._cursor = TursoCursor(self.client)
        return self._cursor

    def commit(self):
        """Commit is automatic in Turso, but keep for compatibility"""
        pass

    def rollback(self):
        """Rollback - Turso doesn't support transactions the same way"""
        # Note: Turso auto-commits each statement
        # For complex transactions, we'd need to use BEGIN/COMMIT explicitly
        pass

    def close(self):
        """Close the connection"""
        self.client.close()


class TursoCursor:
    """Cursor-like wrapper for Turso client"""

    def __init__(self, client):
        self.client = client
        self.lastrowid = None
        self._result = None

    def execute(self, query, params=None):
        """Execute a query"""
        try:
            if params:
                # Convert ? placeholders to Turso format if needed
                result = self.client.execute(query, params)
            else:
                result = self.client.execute(query)

            # Store result for fetchone/fetchall
            self._result = result

            # Extract lastrowid if it's an INSERT
            if result and hasattr(result, "last_insert_rowid"):
                self.lastrowid = result.last_insert_rowid
            elif (
                result
                and hasattr(result, "rows")
                and query.strip().upper().startswith("INSERT")
            ):
                # For Turso, we might need to get the last inserted id differently
                self.lastrowid = getattr(result, "last_insert_rowid", None)

        except Exception as e:
            # Re-raise with more context
            raise Exception(f"Query failed: {query[:100]}... Error: {e}")

    def fetchone(self):
        """Fetch one row"""
        if self._result and hasattr(self._result, "rows") and self._result.rows:
            return self._result.rows[0] if self._result.rows else None
        return None

    def fetchall(self):
        """Fetch all rows"""
        if self._result and hasattr(self._result, "rows"):
            return self._result.rows
        return []


@contextmanager
def db_connection():
    """
    Context manager for database connections with automatic cleanup.
    Supports both local SQLite (for dev) and Turso (for production).

    Set USE_TURSO=YES in .env to use Turso.
    """
    if USE_TURSO:
        # Use Turso
        try:
            import libsql_client
        except ImportError:
            raise ImportError(
                "libsql-client is required for Turso. Install with: pip install libsql-client"
            )

        if not TURSO_DATABASE_URL or not TURSO_AUTH_TOKEN:
            raise ValueError(
                "TURSO_DATABASE_URL and TURSO_AUTH_TOKEN must be set when USE_TURSO=YES"
            )

        client = libsql_client.create_client(
            url=TURSO_DATABASE_URL, auth_token=TURSO_AUTH_TOKEN
        )
        conn = TursoConnection(client)

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
