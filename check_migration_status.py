#!/usr/bin/env python3
"""Check database migration status"""

import sqlite3
from src.config.settings import DB_FILE
from src.data.migrations import get_schema_version, MIGRATIONS


def main():
    print("=" * 80)
    print("DATABASE MIGRATION STATUS")
    print("=" * 80)

    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    c = conn.cursor()

    # Get current version
    current_version = get_schema_version(conn)
    print(f"\nDatabase file: {DB_FILE}")
    print(f"Current schema version: {current_version}")
    print(f"Latest available version: {max(m[0] for m in MIGRATIONS)}")

    # Check if migrations are pending
    pending = [m for m in MIGRATIONS if m[0] > current_version]

    if pending:
        print(f"\n⚠️   {len(pending)} pending migrations:")
        for version, description, _ in pending:
            print(f"  - v{version}: {description}")
    else:
        print("\n✅ Database schema is up to date")

    # Show applied migrations
    c.execute("SELECT version, applied_at FROM schema_version ORDER BY version")
    applied = c.fetchall()

    if applied:
        print(f"\nApplied migrations ({len(applied)}):")
        for version, applied_at in applied:
            migration_name = next(
                (m[1] for m in MIGRATIONS if m[0] == version), "Unknown"
            )
            print(f"  v{version}: {migration_name}")
            print(f"         Applied: {applied_at}")

    # Show database stats
    c.execute("SELECT COUNT(*) FROM trades")
    total_trades = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM trades WHERE settled = 0")
    open_trades = c.fetchone()[0]

    c.execute("SELECT MAX(id) FROM trades")
    max_id = c.fetchone()[0]

    print(f"\nDatabase statistics:")
    print(f"  Total trades: {total_trades}")
    print(f"  Latest trade ID: {max_id}")
    print(f"  Open positions: {open_trades}")

    # Check for scale_in_order_id column
    c.execute("PRAGMA table_info(trades)")
    columns = [row[1] for row in c.fetchall()]

    print(f"\nSchema columns: {len(columns)}")
    print(f"  - scale_in_order_id: {'✅' if 'scale_in_order_id' in columns else '❌'}")
    print(
        f"  - limit_sell_order_id: {'✅' if 'limit_sell_order_id' in columns else '❌'}"
    )
    print(f"  - target_price: {'✅' if 'target_price' in columns else '❌'}")

    conn.close()
    print("\n" + "=" * 80)


if __name__ == "__main__":
    main()
