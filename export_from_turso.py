#!/usr/bin/env python3
"""Export data from Turso to local SQLite backup"""

import sqlite3
from src.data.db_connection import db_connection

print("🔄 Exporting data from Turso...")

# Export from Turso
with db_connection() as turso_conn:
    c = turso_conn.cursor()

    # Get all trades
    c.execute("SELECT * FROM trades ORDER BY id")
    trades = c.fetchall()

    # Get column names
    c.execute("PRAGMA table_info(trades)")
    columns_info = c.fetchall()
    column_names = [col[1] for col in columns_info]

    # Get schema version
    try:
        c.execute("SELECT * FROM schema_version")
        schema_versions = c.fetchall()
    except:
        schema_versions = [(2,)]  # Default to version 2

    print(f"✓ Exported {len(trades)} trades")
    print(f"✓ Columns: {len(column_names)}")
    print(f"✓ Schema version: {schema_versions[0][0] if schema_versions else 2}")

# Save to backup file
backup_file = "trades_backup_from_turso.db"
backup_conn = sqlite3.connect(backup_file)
bc = backup_conn.cursor()

# Create trades table
bc.execute("""
    CREATE TABLE trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT, symbol TEXT, window_start TEXT, window_end TEXT,
        slug TEXT, token_id TEXT, side TEXT, edge REAL, entry_price REAL,
        size REAL, bet_usd REAL, p_yes REAL, best_bid REAL, best_ask REAL,
        imbalance REAL, funding_bias REAL, order_status TEXT, order_id TEXT,
        limit_sell_order_id TEXT, scale_in_order_id TEXT,
        final_outcome TEXT, exit_price REAL, pnl_usd REAL, roi_pct REAL,
        settled BOOLEAN DEFAULT 0, settled_at TEXT, exited_early BOOLEAN DEFAULT 0,
        scaled_in BOOLEAN DEFAULT 0, is_reversal BOOLEAN DEFAULT 0, target_price REAL
    )
""")

# Create indexes
bc.execute("CREATE INDEX idx_symbol ON trades(symbol)")
bc.execute("CREATE INDEX idx_settled ON trades(settled)")

# Insert all trades
placeholders = ",".join(["?"] * len(column_names))
for trade in trades:
    bc.execute(f"INSERT INTO trades VALUES ({placeholders})", trade)

# Create schema_version table
bc.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
for row in schema_versions:
    bc.execute("INSERT INTO schema_version VALUES (?)", row)

backup_conn.commit()
backup_conn.close()

print(f"✓ Backup saved to {backup_file}")
print(f"\nNext steps:")
print(f"1. Update .env: USE_TURSO=NO, USE_EMBEDDED_REPLICA=NO")
print(f"2. cp {backup_file} trades.db")
print(f"3. python polyastra.py")
