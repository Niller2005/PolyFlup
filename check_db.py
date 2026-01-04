import sqlite3

conn = sqlite3.connect("trades.db")
c = conn.cursor()

print(f"Total trades: {c.execute('SELECT COUNT(*) FROM trades').fetchone()[0]}")
print("\nRecent 5 trades:")
for row in c.execute(
    "SELECT id, timestamp, symbol, settled FROM trades ORDER BY id DESC LIMIT 5"
).fetchall():
    print(row)

# Check if WAL mode is active and if there are uncommitted changes
print("\n=== Journal Mode ===")
print(c.execute("PRAGMA journal_mode").fetchone())

# Check if there's a WAL file
import os

if os.path.exists("trades.db-wal"):
    print(
        f"\nWAL file exists: trades.db-wal (size: {os.path.getsize('trades.db-wal')} bytes)"
    )
else:
    print("\nNo WAL file found")

if os.path.exists("trades.db-shm"):
    print(
        f"SHM file exists: trades.db-shm (size: {os.path.getsize('trades.db-shm')} bytes)"
    )
else:
    print("No SHM file found")

conn.close()
