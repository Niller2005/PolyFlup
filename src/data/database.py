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
                scaled_in BOOLEAN DEFAULT 0, is_reversal BOOLEAN DEFAULT 0, target_price REAL,
                reversal_triggered BOOLEAN DEFAULT 0, reversal_triggered_at TEXT,
                last_scale_in_at TEXT,
                up_total REAL, down_total REAL, momentum_score REAL, momentum_dir TEXT,
                flow_score REAL, flow_dir TEXT, divergence_score REAL, divergence_dir TEXT,
                vwm_score REAL, vwm_dir TEXT, pm_mom_score REAL, pm_mom_dir TEXT,
                adx_score REAL, adx_dir TEXT, lead_lag_bonus REAL
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
            imbalance, funding_bias, order_status, order_id, limit_sell_order_id, is_reversal, target_price, reversal_triggered, reversal_triggered_at,
            up_total, down_total, momentum_score, momentum_dir, flow_score, flow_dir,
            divergence_score, divergence_dir, vwm_score, vwm_dir, pm_mom_score, pm_mom_dir, adx_score, adx_dir, lead_lag_bonus)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                kwargs.get("reversal_triggered", False),
                kwargs.get("reversal_triggered_at"),
                kwargs.get("up_total"),
                kwargs.get("down_total"),
                kwargs.get("momentum_score"),
                kwargs.get("momentum_dir"),
                kwargs.get("flow_score"),
                kwargs.get("flow_dir"),
                kwargs.get("divergence_score"),
                kwargs.get("divergence_dir"),
                kwargs.get("vwm_score"),
                kwargs.get("vwm_dir"),
                kwargs.get("pm_mom_score"),
                kwargs.get("pm_mom_dir"),
                kwargs.get("adx_score"),
                kwargs.get("adx_dir"),
                kwargs.get("lead_lag_bonus"),
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
                imbalance, funding_bias, order_status, order_id, limit_sell_order_id, is_reversal, target_price, reversal_triggered, reversal_triggered_at,
                up_total, down_total, momentum_score, momentum_dir, flow_score, flow_dir,
                divergence_score, divergence_dir, vwm_score, vwm_dir, pm_mom_score, pm_mom_dir, adx_score, adx_dir, lead_lag_bonus)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    kwargs.get("reversal_triggered", False),
                    kwargs.get("reversal_triggered_at"),
                    kwargs.get("up_total"),
                    kwargs.get("down_total"),
                    kwargs.get("momentum_score"),
                    kwargs.get("momentum_dir"),
                    kwargs.get("flow_score"),
                    kwargs.get("flow_dir"),
                    kwargs.get("divergence_score"),
                    kwargs.get("divergence_dir"),
                    kwargs.get("vwm_score"),
                    kwargs.get("vwm_dir"),
                    kwargs.get("pm_mom_score"),
                    kwargs.get("pm_mom_dir"),
                    kwargs.get("adx_score"),
                    kwargs.get("adx_dir"),
                    kwargs.get("lead_lag_bonus"),
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

    # SIMPLIFIED: Only show basic stats instead of verbose performance reports
    # The detailed reports had incorrect $12M+ numbers that cluttered logs

    if total_trades > 0:
        log(f"📊 Stats: {total_trades} trades, {win_rate:.1f}% win rate")

    # Skip file creation and Discord notifications for now to reduce clutter
    # The detailed reports can be enabled again if needed for debugging


def get_total_exposure() -> float:
    """Get total USD exposure of open trades"""
    with db_connection() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT SUM(bet_usd) FROM trades WHERE settled = 0 AND exited_early = 0"
        )
        result = c.fetchone()
        return result[0] or 0.0
