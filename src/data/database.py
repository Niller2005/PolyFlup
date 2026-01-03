"""Database operations"""

import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
from src.config.settings import DB_FILE, REPORTS_DIR
from src.utils.logger import log, send_discord


def init_database():
    """Initialize SQLite database"""
    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    c = conn.cursor()

    # Enable WAL mode for better concurrency
    c.execute("PRAGMA journal_mode=WAL")

    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT, symbol TEXT, window_start TEXT, window_end TEXT,
            slug TEXT, token_id TEXT, side TEXT, edge REAL, entry_price REAL,
            size REAL, bet_usd REAL, p_yes REAL, best_bid REAL, best_ask REAL,
            imbalance REAL, funding_bias REAL, order_status TEXT, order_id TEXT,
            limit_sell_order_id TEXT,
            final_outcome TEXT, exit_price REAL, pnl_usd REAL, roi_pct REAL,
            settled BOOLEAN DEFAULT 0, settled_at TEXT, exited_early BOOLEAN DEFAULT 0,
            scaled_in BOOLEAN DEFAULT 0, is_reversal BOOLEAN DEFAULT 0, target_price REAL
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_symbol ON trades(symbol)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_settled ON trades(settled)")
    conn.commit()
    conn.close()
    log("✓ Database initialized")


def save_trade(**kwargs):
    """Save trade to database"""
    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO trades (timestamp, symbol, window_start, window_end, slug, token_id,
        side, edge, entry_price, size, bet_usd, p_yes, best_bid, best_ask,
        imbalance, funding_bias, order_status, order_id, limit_sell_order_id, is_reversal, target_price)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            datetime.now(tz=ZoneInfo("UTC")).isoformat(),
            kwargs["symbol"],
            kwargs["window_start"],
            kwargs["window_end"],
            kwargs["slug"],
            kwargs["token_id"],
            kwargs["side"],
            kwargs["edge"],
            kwargs["price"],
            kwargs["size"],
            kwargs["bet_usd"],
            kwargs["p_yes"],
            kwargs["best_bid"],
            kwargs["best_ask"],
            kwargs["imbalance"],
            kwargs["funding_bias"],
            kwargs["order_status"],
            kwargs["order_id"],
            kwargs.get("limit_sell_order_id"),
            kwargs.get("is_reversal", False),
            kwargs.get("target_price", None),
        ),
    )
    trade_id = c.lastrowid
    conn.commit()
    conn.close()
    return trade_id


def generate_statistics():
    """Generate performance statistics report"""
    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    c = conn.cursor()
    c.execute(
        "SELECT COUNT(*), SUM(bet_usd), SUM(pnl_usd), AVG(roi_pct) FROM trades WHERE settled = 1"
    )
    result = c.fetchone()
    total_trades = result[0] or 0

    if not total_trades:
        log("ℹ No settled trades for analysis")
        conn.close()
        return

    total_invested, total_pnl, avg_roi = result[1] or 0, result[2] or 0, result[3] or 0
    c.execute("SELECT COUNT(*) FROM trades WHERE settled = 1 AND pnl_usd > 0")
    winning_trades = c.fetchone()[0]
    win_rate = (winning_trades / total_trades) * 100

    report = []
    report.append("=" * 80)
    report.append("📊 POLYASTRA TRADING PERFORMANCE REPORT")
    report.append("=" * 80)
    report.append(f"Total trades:     {total_trades}")
    report.append(f"Win rate:         {win_rate:.1f}%")
    report.append(f"Total PnL:        ${total_pnl:.2f}")
    report.append(f"Total invested:   ${total_invested:.2f}")
    report.append(f"Average ROI:      {avg_roi:.2f}%")
    report.append(f"Total ROI:        {(total_pnl / total_invested) * 100:.2f}%")
    report.append("=" * 80)

    report_text = "\n".join(report)
    log(report_text)

    report_file = f"{REPORTS_DIR}/report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    with open(report_file, "w") as f:
        f.write(report_text)

    send_discord(f"📊 **PERFORMANCE REPORT**\n```\n{report_text}\n```")
    conn.close()

def get_total_exposure() -> float:
    """Get total USD exposure of open trades"""
    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    c = conn.cursor()
    c.execute("SELECT SUM(bet_usd) FROM trades WHERE settled = 0 AND exited_early = 0")
    result = c.fetchone()
    conn.close()
    return result[0] or 0.0
