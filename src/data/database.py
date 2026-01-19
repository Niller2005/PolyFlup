"""
Database operations

NOTE: This module maintains the legacy trades table for backward compatibility.
For new code, prefer using the normalized schema via src.data.normalized_db:
- windows: Trading window metadata
- positions: Position tracking
- orders: Order tracking

The trades table will be deprecated in a future version.
"""

import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
from src.config.settings import DB_FILE, REPORTS_DIR
from src.utils.logger import log, send_discord
from src.data.db_connection import db_connection
from src.data.migrations import run_migrations
from src.data.normalized_db import (
    get_or_create_window,
    create_position,
    create_order,
    get_open_positions,
    get_performance_stats,
    get_total_exposure as get_total_exposure_normalized,
)


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
    Save trade to database (LEGACY - uses trades table)

    For new code, use the normalized schema:
    - window_id = get_or_create_window(cursor, symbol, window_start, window_end, ...)
    - position_id = create_position(cursor, window_id, side, entry_price, size, ...)
    - order_id = create_order(cursor, position_id, 'ENTRY', order_id=..., ...)

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
            divergence_score, divergence_dir, vwm_score, vwm_dir, pm_mom_score, pm_mom_dir, adx_score, adx_dir, lead_lag_bonus,
            additive_confidence, additive_bias, bayesian_confidence, bayesian_bias, market_prior_p_up, condition_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                kwargs.get("additive_confidence"),
                kwargs.get("additive_bias"),
                kwargs.get("bayesian_confidence"),
                kwargs.get("bayesian_bias"),
                kwargs.get("market_prior_p_up"),
                kwargs.get("condition_id"),
            ),
        )
        trade_id = cursor.lastrowid

        # Also save to normalized tables
        if trade_id:
            _save_to_normalized_schema(cursor, trade_id, **kwargs)

        return trade_id
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
                divergence_score, divergence_dir, vwm_score, vwm_dir, pm_mom_score, pm_mom_dir, adx_score, adx_dir, lead_lag_bonus,
                additive_confidence, additive_bias, bayesian_confidence, bayesian_bias, market_prior_p_up, condition_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    kwargs.get("additive_confidence"),
                    kwargs.get("additive_bias"),
                    kwargs.get("bayesian_confidence"),
                    kwargs.get("bayesian_bias"),
                    kwargs.get("market_prior_p_up"),
                    kwargs.get("condition_id"),
                ),
            )
            trade_id = c.lastrowid

            # Also save to normalized tables
            if trade_id:
                _save_to_normalized_schema(c, trade_id, **kwargs)

            return trade_id


def _save_to_normalized_schema(cursor, trade_id: int, **kwargs):
    """
    Internal helper to save trade data to normalized schema.
    This ensures both legacy and new schemas stay in sync during transition.
    """
    # Skip if required fields are missing
    symbol = kwargs.get("symbol")
    window_start = kwargs.get("window_start")
    window_end = kwargs.get("window_end")
    side = kwargs.get("side")

    if not all([symbol, window_start, window_end, side]):
        log(f"⚠️  Skipping normalized schema save - missing required fields")
        return

    # Type assertions for the checker (we know these are strings now)
    assert isinstance(symbol, str)
    assert isinstance(window_start, str)
    assert isinstance(window_end, str)
    assert isinstance(side, str)

    # Create or get window
    window_id = get_or_create_window(
        cursor,
        symbol=symbol,
        window_start=window_start,
        window_end=window_end,
        slug=kwargs.get("slug"),
        token_id=kwargs.get("token_id"),
        condition_id=kwargs.get("condition_id"),
        p_yes=kwargs.get("p_yes"),
        best_bid=kwargs.get("best_bid"),
        best_ask=kwargs.get("best_ask"),
        imbalance=kwargs.get("imbalance"),
        funding_bias=kwargs.get("funding_bias"),
        market_prior_p_up=kwargs.get("market_prior_p_up"),
        up_total=kwargs.get("up_total"),
        down_total=kwargs.get("down_total"),
        momentum_score=kwargs.get("momentum_score"),
        momentum_dir=kwargs.get("momentum_dir"),
        flow_score=kwargs.get("flow_score"),
        flow_dir=kwargs.get("flow_dir"),
        divergence_score=kwargs.get("divergence_score"),
        divergence_dir=kwargs.get("divergence_dir"),
        vwm_score=kwargs.get("vwm_score"),
        vwm_dir=kwargs.get("vwm_dir"),
        pm_mom_score=kwargs.get("pm_mom_score"),
        pm_mom_dir=kwargs.get("pm_mom_dir"),
        adx_score=kwargs.get("adx_score"),
        adx_dir=kwargs.get("adx_dir"),
        lead_lag_bonus=kwargs.get("lead_lag_bonus"),
    )

    # Create position
    position_id = create_position(
        cursor,
        window_id=window_id,
        side=side,
        entry_price=kwargs.get("price", 0.0),
        size=kwargs.get("size", 0.0),
        bet_usd=kwargs.get("bet_usd", 0.0),
        edge=kwargs.get("edge", 0.0),
        additive_confidence=kwargs.get("additive_confidence"),
        additive_bias=kwargs.get("additive_bias"),
        bayesian_confidence=kwargs.get("bayesian_confidence"),
        bayesian_bias=kwargs.get("bayesian_bias"),
        is_reversal=kwargs.get("is_reversal", False),
        target_price=kwargs.get("target_price"),
    )

    # Create entry order if order_id provided
    if kwargs.get("order_id") and kwargs.get("order_id") != "N/A":
        create_order(
            cursor,
            position_id=position_id,
            order_type="ENTRY",
            order_id=kwargs.get("order_id"),
            order_status=kwargs.get("order_status", "UNKNOWN"),
            price=kwargs.get("price", 0.0),
            size=kwargs.get("size", 0.0),
        )

    # Create limit sell order if provided
    if kwargs.get("limit_sell_order_id"):
        create_order(
            cursor,
            position_id=position_id,
            order_type="LIMIT_SELL",
            order_id=kwargs.get("limit_sell_order_id"),
            order_status="OPEN",
            price=kwargs.get("target_price"),
            size=kwargs.get("size", 0.0),
        )


def has_side_for_window(symbol: str, window_start: str, side: str) -> bool:
    """
    Check if a position already exists for the given symbol, window and side.
    Uses normalized schema (positions + windows tables).
    """
    with db_connection() as conn:
        c = conn.cursor()
        c.execute(
            """
            SELECT p.id 
            FROM positions p
            JOIN windows w ON p.window_id = w.id
            WHERE w.symbol = ? AND w.window_start = ? AND p.side = ? AND p.settled = 0
        """,
            (symbol, window_start, side),
        )
        return c.fetchone() is not None


def has_trade_for_window(symbol: str, window_start: str) -> bool:
    """
    Check if an active (non-settled) position already exists for the given symbol and window.
    Uses normalized schema (positions + windows tables).
    """
    with db_connection() as conn:
        c = conn.cursor()
        c.execute(
            """
            SELECT p.id
            FROM positions p
            JOIN windows w ON p.window_id = w.id
            WHERE w.symbol = ? AND w.window_start = ? AND p.settled = 0
        """,
            (symbol, window_start),
        )
        return c.fetchone() is not None


def generate_statistics():
    """
    Generate performance statistics report.
    Uses normalized schema (positions table).
    """
    with db_connection() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT COUNT(*), SUM(bet_usd), SUM(pnl_usd), AVG(roi_pct) FROM positions WHERE settled = 1"
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
        c.execute("SELECT COUNT(*) FROM positions WHERE settled = 1 AND pnl_usd > 0")
        winning_trades = c.fetchone()[0]
        win_rate = (winning_trades / total_trades) * 100

        # Outcome Breakdown - Query windows joined with positions
        c.execute("""
            SELECT w.final_outcome, COUNT(p.id) 
            FROM positions p
            JOIN windows w ON p.window_id = w.id
            WHERE p.settled = 1 
            GROUP BY w.final_outcome
        """)
        outcomes = c.fetchall()

    # SIMPLIFIED: Only show basic stats instead of verbose performance reports
    # The detailed reports had incorrect $12M+ numbers that cluttered logs

    if total_trades > 0:
        log(f"📊 Stats: {total_trades} trades, {win_rate:.1f}% win rate")

    # Skip file creation and Discord notifications for now to reduce clutter
    # The detailed reports can be enabled again if needed for debugging


def get_total_exposure() -> float:
    """
    Get total USD exposure of open positions.
    Uses normalized schema (positions table).
    """
    with db_connection() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT SUM(bet_usd) FROM positions WHERE settled = 0 AND exited_early = 0"
        )
        result = c.fetchone()
        return result[0] or 0.0
