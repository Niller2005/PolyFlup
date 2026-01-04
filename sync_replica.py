#!/usr/bin/env python3
"""
Manual sync script for embedded replica.

This script manually syncs the local embedded replica with the remote Turso database.
Use this when you want to force a sync outside of the automatic sync interval.

Usage:
    python sync_replica.py
"""

import sys
from dotenv import load_dotenv

load_dotenv()

from src.config.settings import (
    USE_EMBEDDED_REPLICA,
    TURSO_DATABASE_URL,
    TURSO_AUTH_TOKEN,
    EMBEDDED_REPLICA_FILE,
)
from src.utils.logger import log


def sync_replica():
    """Manually sync the embedded replica with remote Turso database"""
    if not USE_EMBEDDED_REPLICA:
        log("❌ Embedded replica is not enabled. Set USE_EMBEDDED_REPLICA=YES in .env")
        sys.exit(1)

    if not TURSO_DATABASE_URL or not TURSO_AUTH_TOKEN:
        log("❌ TURSO_DATABASE_URL and TURSO_AUTH_TOKEN must be set in .env")
        sys.exit(1)

    try:
        import libsql
    except ImportError:
        log("❌ libsql is required. Install with: pip install libsql")
        sys.exit(1)

    log(f"🔄 Syncing embedded replica: {EMBEDDED_REPLICA_FILE}")
    log(f"📡 Remote database: {TURSO_DATABASE_URL}")

    try:
        # Connect with embedded replica
        conn = libsql.connect(
            EMBEDDED_REPLICA_FILE,
            sync_url=TURSO_DATABASE_URL,
            auth_token=TURSO_AUTH_TOKEN,
        )

        # Manually trigger sync
        conn.sync()

        log("✅ Sync completed successfully!")

        # Close connection
        conn.close()

    except Exception as e:
        log(f"❌ Sync failed: {e}")
        sys.exit(1)

    log(f"🔄 Syncing embedded replica: {EMBEDDED_REPLICA_FILE}")
    log(f"📡 Remote database: {TURSO_DATABASE_URL}")

    try:
        # Create client with embedded replica
        client = libsql_client.create_client(
            url=f"file:{EMBEDDED_REPLICA_FILE}",
            sync_url=TURSO_DATABASE_URL,
            auth_token=TURSO_AUTH_TOKEN,
        )

        # Manually trigger sync
        client.sync()

        log("✅ Sync completed successfully!")

        # Close client
        client.close()

    except Exception as e:
        log(f"❌ Sync failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    sync_replica()
