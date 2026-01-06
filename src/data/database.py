"""Database operations"""

import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
from src.config.settings import DB_FILE, REPORTS_DIR
from src.utils.logger import log, send_discord
from src.data.db_connection import db_connection
from src.data.migrations import run_migrations


def init_database():
    """Initialize SQLite database and run migrations"""
    with db_connection() as conn:
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
                limit_sell_order_id TEXT, scale_in_order_id TEXT,
                final_outcome TEXT, exit_price REAL, pnl_usd REAL, roi_pct REAL,
                settled BOOLEAN DEFAULT 0, settled_at TEXT, exited_early BOOLEAN DEFAULT 0,
                scaled_in BOOLEAN DEFAULT 0, is_reversal BOOLEAN DEFAULT 0, target_price REAL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_symbol ON trades(symbol)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_settled ON trades(settled)")

    log("✓ Database initialized")

    # Run migrations after initial schema is created
    run_migrations()


def save_trade(cursor=None, **kwargs):
    """
    Save trade to database

    Args:
        cursor: Optional cursor from existing connection. If None, opens new connection.
        **kwargs: Trade parameters
    """
    # If cursor provided, use it (already inside a transaction)
    if cursor:
        cursor.execute(
            """
            INSERT INTO trades (timestamp, symbol, window_start, window_end, slug, token_id,
            side, edge, entry_price, size, bet_usd, p_yes, best_bid, best_ask,
            imbalance, funding_bias, order_status, order_id, limit_sell_order_id, is_reversal, target_price)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                datetime.now(tz=ZoneInfo("UTC")).isoformat(),
                kwargs.get("symbol"),
                kwargs.get("window_start"),
                kwargs.get("window_end"),
                kwargs.get("slug"),
                kwargs.get("token_id"),
                kwargs.get("side"),
                kwargs.get("edge", 0.0),
                kwargs.get("price", 0.0),
                kwargs.get("size", 0.0),
                kwargs.get("bet_usd", 0.0),
                kwargs.get("p_yes", 0.5),
                kwargs.get("best_bid"),
                kwargs.get("best_ask"),
                kwargs.get("imbalance", 0.5),
                kwargs.get("funding_bias", 0.0),
                kwargs.get("order_status", "UNKNOWN"),
                kwargs.get("order_id", "N/A"),
                kwargs.get("limit_sell_order_id"),
                kwargs.get("is_reversal", False),
                kwargs.get("target_price"),
            ),
        )
        return cursor.lastrowid
    else:
        # No cursor provided, open new connection (for backward compatibility)
        with db_connection() as conn:
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
                    kwargs.get("symbol"),
                    kwargs.get("window_start"),
                    kwargs.get("window_end"),
                    kwargs.get("slug"),
                    kwargs.get("token_id"),
                    kwargs.get("side"),
                    kwargs.get("edge", 0.0),
                    kwargs.get("price", 0.0),
                    kwargs.get("size", 0.0),
                    kwargs.get("bet_usd", 0.0),
                    kwargs.get("p_yes", 0.5),
                    kwargs.get("best_bid"),
                    kwargs.get("best_ask"),
                    kwargs.get("imbalance", 0.5),
                    kwargs.get("funding_bias", 0.0),
                    kwargs.get("order_status", "UNKNOWN"),
                    kwargs.get("order_id", "N/A"),
                    kwargs.get("limit_sell_order_id"),
                    kwargs.get("is_reversal", False),
                    kwargs.get("target_price"),
                ),
            )
            trade_id = c.lastrowid
            return trade_id


def has_side_for_window(symbol: str, window_start: str, side: str) -> bool:
    """Check if a trade already exists for the given symbol, window and side"""
    with db_connection() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT id FROM trades WHERE symbol = ? AND window_start = ? AND side = ? AND settled = 0",
            (symbol, window_start, side),
        )
        return c.fetchone() is not None


def has_trade_for_window(symbol: str, window_start: str) -> bool:
    """Check if a trade already exists for the given symbol and window"""
    with db_connection() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT id FROM trades WHERE symbol = ? AND window_start = ?",
            (symbol, window_start),
        )
        return c.fetchone() is not None


def generate_statistics():
    """Generate performance statistics report"""
    with db_connection() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT COUNT(*), SUM(bet_usd), SUM(pnl_usd), AVG(roi_pct) FROM trades WHERE settled = 1"
        )
        result = c.fetchone()
        total_trades = result[0] or 0

        if not total_trades:
            log("ℹ No settled trades for analysis")
            return

        total_invested, total_pnl, avg_roi = (
            result[1] or 0,
            result[2] or 0,
            result[3] or 0,
        )
        c.execute("SELECT COUNT(*) FROM trades WHERE settled = 1 AND pnl_usd > 0")
        winning_trades = c.fetchone()[0]
        win_rate = (winning_trades / total_trades) * 100

        # Outcome Breakdown
        c.execute(
            "SELECT final_outcome, COUNT(*) FROM trades WHERE settled = 1 GROUP BY final_outcome"
        )
        outcomes = c.fetchall()

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

    if outcomes:
        report.append("-" * 40)
        report.append("OUTCOME BREAKDOWN:")
        for outcome, count in outcomes:
            report.append(f"  {str(outcome or 'UNKNOWN'):<20}: {count}")

    report.append("=" * 80)

    report_text = "\n".join(report)
    log(report_text)

    report_file = f"{REPORTS_DIR}/report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    with open(report_file, "w") as f:
        f.write(report_text)

    send_discord(f"📊 **PERFORMANCE REPORT**\n```\n{report_text}\n```")


def get_total_exposure() -> float:
    """Get total USD exposure of open trades"""
    with db_connection() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT SUM(bet_usd) FROM trades WHERE settled = 0 AND exited_early = 0"
        )
        result = c.fetchone()
        return result[0] or 0.0
