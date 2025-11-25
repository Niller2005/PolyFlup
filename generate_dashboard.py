#!/usr/bin/env python3
"""
PolyAstra Dashboard Generator
Creates interactive HTML dashboard for viewing trading statistics
Run: python3 generate_dashboard.py
View: http://your-server-ip:8000/dashboard.html
"""

import sqlite3
import json
from datetime import datetime

DB_FILE = "/home/ubuntu/polyastra/trades.db"
OUTPUT_FILE = "/home/ubuntu/polyastra/dashboard.html"

def get_stats():
    """Fetch all statistics from database"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # Summary statistics
    c.execute('''
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN settled = 1 THEN 1 ELSE 0 END) as settled,
            SUM(CASE WHEN settled = 1 AND pnl_usd > 0 THEN 1 ELSE 0 END) as wins,
            SUM(bet_usd) as invested,
            SUM(CASE WHEN settled = 1 THEN pnl_usd ELSE 0 END) as total_pnl,
            AVG(CASE WHEN settled = 1 THEN roi_pct ELSE NULL END) as avg_roi
        FROM trades
    ''')
    summary = c.fetchone()

    # Per symbol statistics
    c.execute('''
        SELECT symbol,
            COUNT(*) as trades,
            SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins,
            SUM(pnl_usd) as pnl,
            AVG(roi_pct) as avg_roi
        FROM trades
        WHERE settled = 1
        GROUP BY symbol
        ORDER BY pnl DESC
    ''')
    per_symbol = c.fetchall()

    # Per side statistics
    c.execute('''
        SELECT side,
            COUNT(*) as trades,
            SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins,
            SUM(pnl_usd) as pnl,
            AVG(roi_pct) as avg_roi
        FROM trades
        WHERE settled = 1
        GROUP BY side
    ''')
    per_side = c.fetchall()

    # Recent trades
    c.execute('''
        SELECT id, timestamp, symbol, side, edge, entry_price,
               pnl_usd, roi_pct, settled, order_status
        FROM trades
        ORDER BY id DESC
        LIMIT 50
    ''')
    recent_trades = c.fetchall()

    # PnL history for chart
    c.execute('''
        SELECT timestamp, pnl_usd
        FROM trades
        WHERE settled = 1
        ORDER BY timestamp ASC
    ''')
    pnl_history = c.fetchall()

    conn.close()

    return {
        'summary': summary,
        'per_symbol': per_symbol,
        'per_side': per_side,
        'recent_trades': recent_trades,
        'pnl_history': pnl_history
    }

def generate_html(stats):
    """Generate HTML dashboard"""

    total, settled, wins, invested, total_pnl, avg_roi = stats['summary']
    win_rate = (wins / settled * 100) if settled > 0 else 0
    total_roi = (total_pnl / invested * 100) if invested > 0 else 0

    # Prepare chart data
    cumulative_pnl = []
    cumsum = 0
    for timestamp, pnl in stats['pnl_history']:
        cumsum += pnl
        cumulative_pnl.append({'time': timestamp, 'pnl': cumsum})

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PolyAstra Trading Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0"></script>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
            color: #333;
        }}

        .container {{
            max-width: 1400px;
            margin: 0 auto;
        }}

        .header {{
            background: white;
            padding: 30px;
            border-radius: 15px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            margin-bottom: 30px;
            text-align: center;
        }}

        .header h1 {{
            font-size: 2.5em;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 10px;
        }}

        .last-update {{
            color: #666;
            font-size: 0.9em;
        }}

        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}

        .stat-card {{
            background: white;
            padding: 25px;
            border-radius: 15px;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
            transition: transform 0.3s;
        }}

        .stat-card:hover {{
            transform: translateY(-5px);
            box-shadow: 0 10px 25px rgba(0,0,0,0.15);
        }}

        .stat-label {{
            font-size: 0.9em;
            color: #666;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 10px;
        }}

        .stat-value {{
            font-size: 2em;
            font-weight: bold;
            color: #333;
        }}

        .stat-value.positive {{
            color: #10b981;
        }}

        .stat-value.negative {{
            color: #ef4444;
        }}

        .chart-container {{
            background: white;
            padding: 30px;
            border-radius: 15px;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
            margin-bottom: 30px;
        }}

        .chart-title {{
            font-size: 1.5em;
            font-weight: bold;
            margin-bottom: 20px;
            color: #333;
        }}

        .table-container {{
            background: white;
            padding: 30px;
            border-radius: 15px;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
            overflow-x: auto;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
        }}

        th {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 15px;
            text-align: left;
            font-weight: 600;
            text-transform: uppercase;
            font-size: 0.85em;
            letter-spacing: 1px;
        }}

        td {{
            padding: 12px 15px;
            border-bottom: 1px solid #eee;
        }}

        tr:hover {{
            background: #f9fafb;
        }}

        .badge {{
            display: inline-block;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 0.85em;
            font-weight: 600;
        }}

        .badge.yes {{
            background: #dcfce7;
            color: #15803d;
        }}

        .badge.no {{
            background: #fee2e2;
            color: #991b1b;
        }}

        .badge.settled {{
            background: #dbeafe;
            color: #1e40af;
        }}

        .badge.pending {{
            background: #fef3c7;
            color: #92400e;
        }}

        .refresh-btn {{
            position: fixed;
            bottom: 30px;
            right: 30px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            padding: 15px 30px;
            border-radius: 50px;
            font-size: 1em;
            font-weight: 600;
            cursor: pointer;
            box-shadow: 0 5px 15px rgba(0,0,0,0.2);
            transition: transform 0.3s;
        }}

        .refresh-btn:hover {{
            transform: scale(1.05);
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🤖 PolyAstra Trading Dashboard</h1>
            <div class="last-update">Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}</div>
        </div>

        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-label">Total Trades</div>
                <div class="stat-value">{total}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Settled</div>
                <div class="stat-value">{settled}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Win Rate</div>
                <div class="stat-value {'positive' if win_rate >= 50 else 'negative'}">{win_rate:.1f}%</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Total PnL</div>
                <div class="stat-value {'positive' if total_pnl >= 0 else 'negative'}">${total_pnl:.2f}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Total ROI</div>
                <div class="stat-value {'positive' if total_roi >= 0 else 'negative'}">{total_roi:.1f}%</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Avg ROI</div>
                <div class="stat-value">{avg_roi:.1f}%</div>
            </div>
        </div>

        <div class="chart-container">
            <div class="chart-title">📈 Cumulative PnL</div>
            <canvas id="pnlChart"></canvas>
        </div>

        <div class="chart-container">
            <div class="chart-title">💰 Performance by Symbol</div>
            <canvas id="symbolChart"></canvas>
        </div>

        <div class="table-container">
            <div class="chart-title">📊 Symbol Statistics</div>
            <table>
                <thead>
                    <tr>
                        <th>Symbol</th>
                        <th>Trades</th>
                        <th>Wins</th>
                        <th>Win Rate</th>
                        <th>Total PnL</th>
                        <th>Avg ROI</th>
                    </tr>
                </thead>
                <tbody>
'''

    # Add per-symbol statistics
    for symbol, trades, wins, pnl, roi in stats['per_symbol']:
        wr = (wins/trades*100) if trades > 0 else 0
        pnl_class = 'positive' if pnl >= 0 else 'negative'
        html += f'''
                    <tr>
                        <td><strong>{symbol}</strong></td>
                        <td>{trades}</td>
                        <td>{wins}</td>
                        <td>{wr:.1f}%</td>
                        <td class="{pnl_class}">${pnl:.2f}</td>
                        <td>{roi:.1f}%</td>
                    </tr>
'''

    html += '''
                </tbody>
            </table>
        </div>

        <div class="table-container" style="margin-top: 30px;">
            <div class="chart-title">🕐 Recent Trades</div>
            <table>
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>Time</th>
                        <th>Symbol</th>
                        <th>Side</th>
                        <th>Edge</th>
                        <th>Price</th>
                        <th>PnL</th>
                        <th>ROI</th>
                        <th>Status</th>
                    </tr>
                </thead>
                <tbody>
'''

    # Add recent trades
    for trade_id, timestamp, symbol, side, edge, price, pnl, roi, settled, status in stats['recent_trades']:
        pnl_display = f"${pnl:.2f}" if settled else "-"
        roi_display = f"{roi:.1f}%" if settled else "-"
        pnl_class = 'positive' if (pnl and pnl > 0) else 'negative'
        status_badge = 'settled' if settled else 'pending'
        time_str = timestamp.split('T')[1][:8] if 'T' in timestamp else timestamp

        html += f'''
                    <tr>
                        <td>#{trade_id}</td>
                        <td>{time_str}</td>
                        <td><strong>{symbol}</strong></td>
                        <td><span class="badge {side.lower()}">{side}</span></td>
                        <td>{edge*100:.1f}%</td>
                        <td>${price:.4f}</td>
                        <td class="{pnl_class}">{pnl_display}</td>
                        <td>{roi_display}</td>
                        <td><span class="badge {status_badge}">{'✓' if settled else '⏳'}</span></td>
                    </tr>
'''

    # Prepare chart data
    cumulative_labels = [item['time'].split('T')[0] + ' ' + item['time'].split('T')[1][:5] for item in cumulative_pnl]
    cumulative_data = [item['pnl'] for item in cumulative_pnl]

    symbol_labels = [row[0] for row in stats['per_symbol']]
    symbol_pnl = [row[3] for row in stats['per_symbol']]

    html += f'''
                </tbody>
            </table>
        </div>
    </div>

    <button class="refresh-btn" onclick="location.reload()">🔄 Refresh</button>

    <script>
        // Cumulative PnL Chart
        const pnlCtx = document.getElementById('pnlChart').getContext('2d');
        new Chart(pnlCtx, {{
            type: 'line',
            data: {{
                labels: {json.dumps(cumulative_labels[-50:])},
                datasets: [{{
                    label: 'Cumulative PnL ($)',
                    data: {json.dumps(cumulative_data[-50:])},
                    borderColor: 'rgb(102, 126, 234)',
                    backgroundColor: 'rgba(102, 126, 234, 0.1)',
                    tension: 0.4,
                    fill: true
                }}]
            }},
            options: {{
                responsive: true,
                plugins: {{
                    legend: {{
                        display: false
                    }}
                }},
                scales: {{
                    y: {{
                        beginAtZero: true
                    }}
                }}
            }}
        }});

        // Symbol Performance Chart
        const symbolCtx = document.getElementById('symbolChart').getContext('2d');
        new Chart(symbolCtx, {{
            type: 'bar',
            data: {{
                labels: {json.dumps(symbol_labels)},
                datasets: [{{
                    label: 'PnL ($)',
                    data: {json.dumps(symbol_pnl)},
                    backgroundColor: [
                        'rgba(102, 126, 234, 0.8)',
                        'rgba(118, 75, 162, 0.8)',
                        'rgba(16, 185, 129, 0.8)',
                        'rgba(239, 68, 68, 0.8)'
                    ]
                }}]
            }},
            options: {{
                responsive: true,
                plugins: {{
                    legend: {{
                        display: false
                    }}
                }}
            }}
        }});
    </script>
</body>
</html>'''

    return html

def main():
    print("🚀 Generating PolyAstra dashboard...")
    try:
        stats = get_stats()
        html = generate_html(stats)

        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(html)

        print(f"✅ Dashboard generated: {OUTPUT_FILE}")
        print("\n📊 To view:")
        print("1. cd /home/ubuntu/polyastra")
        print("2. python3 -m http.server 8000")
        print("3. Open: http://YOUR_SERVER_IP:8000/dashboard.html")
        print("\n🔄 Auto-refresh: Add to crontab:")
        print("*/5 * * * * cd /home/ubuntu/polyastra && python3 generate_dashboard.py")

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
