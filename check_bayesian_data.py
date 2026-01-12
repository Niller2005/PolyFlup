"""Check if Bayesian comparison data is populated after bot restart"""

import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

conn = sqlite3.connect("trades.db")
c = conn.cursor()

c.execute("""
    SELECT id, symbol, timestamp, side, edge,
           additive_confidence, bayesian_confidence,
           additive_bias, bayesian_bias, market_prior_p_up
    FROM trades
    ORDER BY id DESC
    LIMIT 15
""")

print("Recent trades with Bayesian comparison data:")
print("=" * 120)
print(
    f"{'ID':<5} {'Symbol':<8} {'Time':<20} {'Side':<5} {'Edge':<7} {'Add Conf':<10} {'Bay Conf':<10} {'Add Bias':<10} {'Bay Bias':<10}"
)
print("=" * 120)

for row in c.fetchall():
    id_, symbol, ts, side, edge, add_conf, bay_conf, add_bias, bay_bias, prior = row
    ts_str = datetime.fromisoformat(ts).strftime("%m-%d %H:%M:%S") if ts else "N/A"
    add_str = f"{add_conf:.1%}" if add_conf else "NULL"
    bay_str = f"{bay_conf:.1%}" if bay_conf else "NULL"
    add_ok = "✅" if add_conf else "❌"
    bay_ok = "✅" if bay_conf else "❌"
    print(
        f"{id_:<5} {symbol:<8} {ts_str:<20} {side:<5} {edge:>5.1%} {add_str:>10} ({add_ok}) {bay_str:>10} ({bay_ok}) {add_bias or 'NULL':<10} {bay_bias or 'NULL':<10}"
    )

conn.close()
