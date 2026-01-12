"""Check trades from 2:45PM ET window"""

import sqlite3
from datetime import datetime

conn = sqlite3.connect("trades.db")
c = conn.cursor()

c.execute("""
    SELECT id, symbol, timestamp, side, edge, size, bet_usd, order_status
    FROM trades
    WHERE timestamp >= '2026-01-12T19:45:00'
    ORDER BY id DESC
    LIMIT 20
""")

print("Trades from 2:45PM ET window:")
print("=" * 110)
for row in c.fetchall():
    id_, symbol, ts, side, edge, size, bet_usd, status = row
    ts_str = datetime.fromisoformat(ts).strftime("%H:%M:%S") if ts else "N/A"
    print(
        f"ID: {id_:>4} | {symbol:<4} {ts_str:<10} {side:<5} | Edge: {edge:>5.1%} | Size: {size:>6.1f} | ${bet_usd:>5.2f} | {status}"
    )

conn.close()
