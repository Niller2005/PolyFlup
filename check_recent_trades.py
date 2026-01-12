"""Check recent trades from window 2:45PM"""

import sqlite3

conn = sqlite3.connect("trades.db")
c = conn.cursor()

c.execute("""
    SELECT id, symbol, side, edge, order_status, settled, pnl_usd
    FROM trades
    WHERE id >= 550
    ORDER BY id DESC
    LIMIT 15
""")

print("Recent trades (ID #550+):")
print("=" * 110)
for row in c.fetchall():
    id_, symbol, side, edge, status, settled, pnl = row
    result = "SETTLED" if settled else "OPEN"
    print(
        f"ID {id_:>4} | {symbol:<4} {side:<5} | Edge: {edge:>5.1%} | Status: {status:<15} | PnL: {pnl:6.2f} | {result}"
    )

conn.close()
