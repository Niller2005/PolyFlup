"""
Normalized database operations for windows, positions, and orders tables.

This module provides functions for working with the normalized schema introduced
in migration 012. The legacy trades table is still supported but deprecated.
"""

from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, Dict, List, Any, Tuple
from src.data.db_connection import db_connection
from src.utils.logger import log


# ============================================================================
# WINDOW OPERATIONS
# ============================================================================


def get_or_create_window(
    cursor, symbol: str, window_start: str, window_end: str, **kwargs
) -> int:
    """
    Get existing window ID or create new window.

    Args:
        cursor: Active database cursor
        symbol: Trading symbol (e.g., "ETHUSDT")
        window_start: ISO format window start time
        window_end: ISO format window end time
        **kwargs: Optional window attributes (slug, token_id, p_yes, etc.)

    Returns:
        Window ID
    """
    # Try to get existing window
    cursor.execute(
        "SELECT id FROM windows WHERE symbol = ? AND window_start = ?",
        (symbol, window_start),
    )
    result = cursor.fetchone()

    if result:
        return result[0]

    # Create new window
    cursor.execute(
        """
        INSERT INTO windows (
            symbol, window_start, window_end, slug, token_id, condition_id,
            p_yes, best_bid, best_ask, imbalance, funding_bias, market_prior_p_up,
            up_total, down_total, momentum_score, momentum_dir, flow_score, flow_dir,
            divergence_score, divergence_dir, vwm_score, vwm_dir, pm_mom_score, pm_mom_dir,
            adx_score, adx_dir, lead_lag_bonus
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            symbol,
            window_start,
            window_end,
            kwargs.get("slug"),
            kwargs.get("token_id"),
            kwargs.get("condition_id"),
            kwargs.get("p_yes"),
            kwargs.get("best_bid"),
            kwargs.get("best_ask"),
            kwargs.get("imbalance"),
            kwargs.get("funding_bias"),
            kwargs.get("market_prior_p_up"),
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


def update_window_outcome(cursor, window_id: int, final_outcome: str) -> None:
    """Update window with final outcome after settlement."""
    cursor.execute(
        "UPDATE windows SET final_outcome = ? WHERE id = ?", (final_outcome, window_id)
    )


def get_window_by_symbol_and_time(
    cursor, symbol: str, window_start: str
) -> Optional[Dict]:
    """Get window by symbol and start time."""
    cursor.execute(
        "SELECT * FROM windows WHERE symbol = ? AND window_start = ?",
        (symbol, window_start),
    )
    row = cursor.fetchone()
    if not row:
        return None

    columns = [col[0] for col in cursor.description]
    return dict(zip(columns, row))


# ============================================================================
# POSITION OPERATIONS
# ============================================================================


def create_position(cursor, window_id: int, **kwargs) -> int:
    """
    Create a new position.

    Args:
        cursor: Active database cursor
        window_id: ID of the trading window
        **kwargs: Position attributes (side, entry_price, size, etc.)

    Returns:
        Position ID
    """
    now = datetime.now(tz=ZoneInfo("UTC")).isoformat()

    cursor.execute(
        """
        INSERT INTO positions (
            window_id, created_at, side, entry_price, size, bet_usd, edge,
            additive_confidence, additive_bias, bayesian_confidence, bayesian_bias,
            is_reversal, is_hedged, target_price
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            window_id,
            now,
            kwargs.get("side"),
            kwargs.get("entry_price"),
            kwargs.get("size"),
            kwargs.get("bet_usd"),
            kwargs.get("edge"),
            kwargs.get("additive_confidence"),
            kwargs.get("additive_bias"),
            kwargs.get("bayesian_confidence"),
            kwargs.get("bayesian_bias"),
            kwargs.get("is_reversal", False),
            kwargs.get("is_hedged", False),
            kwargs.get("target_price"),
        ),
    )

    return cursor.lastrowid


def get_open_positions(cursor, symbol: Optional[str] = None) -> List[Dict]:
    """
    Get all open (unsettled) positions.

    Args:
        cursor: Active database cursor
        symbol: Optional symbol filter

    Returns:
        List of position dictionaries with window data joined
    """
    query = """
        SELECT 
            p.*, 
            w.symbol, w.window_start, w.window_end, w.slug, w.token_id, w.condition_id
        FROM positions p
        JOIN windows w ON p.window_id = w.id
        WHERE p.settled = 0 AND p.exited_early = 0
    """
    params = []

    if symbol:
        query += " AND w.symbol = ?"
        params.append(symbol)

    cursor.execute(query, params)
    rows = cursor.fetchall()

    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in rows]


def get_position_with_window(cursor, position_id: int) -> Optional[Dict]:
    """Get position by ID with window data joined."""
    cursor.execute(
        """
        SELECT 
            p.*, 
            w.symbol, w.window_start, w.window_end, w.slug, w.token_id, w.condition_id
        FROM positions p
        JOIN windows w ON p.window_id = w.id
        WHERE p.id = ?
    """,
        (position_id,),
    )

    row = cursor.fetchone()
    if not row:
        return None

    columns = [col[0] for col in cursor.description]
    return dict(zip(columns, row))


def get_position_by_id(cursor, position_id: int) -> Optional[Dict]:
    """
    Get position by ID without joins (faster for simple lookups).
    For position with window data, use get_position_with_window().
    """
    cursor.execute("SELECT * FROM positions WHERE id = ?", (position_id,))
    row = cursor.fetchone()
    if not row:
        return None

    columns = [col[0] for col in cursor.description]
    return dict(zip(columns, row))


def update_position_size(
    cursor, position_id: int, new_size: float, new_bet_usd: float
) -> None:
    """Update position size after scale-in."""
    now = datetime.now(tz=ZoneInfo("UTC")).isoformat()
    cursor.execute(
        """
        UPDATE positions 
        SET size = ?, bet_usd = ?, scaled_in = 1, last_scale_in_at = ?
        WHERE id = ?
    """,
        (new_size, new_bet_usd, now, position_id),
    )


def settle_position(cursor, position_id: int, **kwargs) -> None:
    """
    Settle a position with exit data.

    Args:
        cursor: Active database cursor
        position_id: Position ID
        **kwargs: Settlement data (exit_price, pnl_usd, roi_pct, exited_early, etc.)
    """
    now = datetime.now(tz=ZoneInfo("UTC")).isoformat()

    cursor.execute(
        """
        UPDATE positions 
        SET settled = 1, settled_at = ?, 
            exited_early = ?, exit_price = ?, pnl_usd = ?, roi_pct = ?,
            hedge_exit_price = ?, hedge_exited_early = ?,
            merge_tx_hash = ?, redeem_tx_hash = ?
        WHERE id = ?
    """,
        (
            now,
            kwargs.get("exited_early", False),
            kwargs.get("exit_price"),
            kwargs.get("pnl_usd"),
            kwargs.get("roi_pct"),
            kwargs.get("hedge_exit_price"),
            kwargs.get("hedge_exited_early", False),
            kwargs.get("merge_tx_hash"),
            kwargs.get("redeem_tx_hash"),
            position_id,
        ),
    )


def trigger_reversal(cursor, position_id: int) -> None:
    """Mark position as reversal triggered."""
    now = datetime.now(tz=ZoneInfo("UTC")).isoformat()
    cursor.execute(
        """
        UPDATE positions 
        SET reversal_triggered = 1, reversal_triggered_at = ?
        WHERE id = ?
    """,
        (now, position_id),
    )


def update_position_hedge_status(
    cursor, position_id: int, is_hedged: bool = True
) -> None:
    """Mark position as hedged or unhedged."""
    cursor.execute(
        "UPDATE positions SET is_hedged = ? WHERE id = ?", (is_hedged, position_id)
    )


def has_position_for_window(cursor, window_id: int, side: Optional[str] = None) -> bool:
    """Check if a position exists for a window (optionally filtered by side)."""
    if side:
        cursor.execute(
            "SELECT id FROM positions WHERE window_id = ? AND side = ? AND settled = 0",
            (window_id, side),
        )
    else:
        cursor.execute(
            "SELECT id FROM positions WHERE window_id = ? AND settled = 0", (window_id,)
        )
    return cursor.fetchone() is not None


def get_positions_by_window(
    cursor, window_id: int, settled: Optional[bool] = None
) -> List[Dict]:
    """
    Get all positions for a window.

    Args:
        cursor: Active database cursor
        window_id: Window ID
        settled: Optional filter - True for settled only, False for open only, None for all

    Returns:
        List of position dictionaries
    """
    if settled is None:
        cursor.execute("SELECT * FROM positions WHERE window_id = ?", (window_id,))
    elif settled:
        cursor.execute(
            "SELECT * FROM positions WHERE window_id = ? AND settled = 1", (window_id,)
        )
    else:
        cursor.execute(
            "SELECT * FROM positions WHERE window_id = ? AND settled = 0", (window_id,)
        )

    rows = cursor.fetchall()
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in rows]


def update_position_order(
    cursor, position_id: int, order_type: str, order_id: str, order_status: str = "OPEN"
) -> int:
    """
    Add or update an order for a position.

    Args:
        cursor: Active database cursor
        position_id: Position ID
        order_type: Order type (ENTRY, LIMIT_SELL, SCALE_IN, HEDGE)
        order_id: Exchange order ID
        order_status: Order status (default: OPEN)

    Returns:
        Order ID (database)
    """
    # Check if order already exists
    cursor.execute(
        "SELECT id FROM orders WHERE position_id = ? AND order_type = ?",
        (position_id, order_type),
    )
    result = cursor.fetchone()

    if result:
        # Update existing order
        cursor.execute(
            "UPDATE orders SET order_id = ?, order_status = ? WHERE id = ?",
            (order_id, order_status, result[0]),
        )
        return result[0]
    else:
        # Create new order
        return create_order(
            cursor,
            position_id=position_id,
            order_type=order_type,
            order_id=order_id,
            order_status=order_status,
        )


# ============================================================================
# ORDER OPERATIONS
# ============================================================================


def create_order(cursor, position_id: int, order_type: str, **kwargs) -> int:
    """
    Create a new order.

    Args:
        cursor: Active database cursor
        position_id: Position ID
        order_type: Order type (ENTRY, LIMIT_SELL, SCALE_IN, HEDGE)
        **kwargs: Order attributes (order_id, price, size, order_status)

    Returns:
        Order ID
    """
    now = datetime.now(tz=ZoneInfo("UTC")).isoformat()

    cursor.execute(
        """
        INSERT INTO orders (
            position_id, created_at, order_id, order_type, order_status, price, size
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """,
        (
            position_id,
            now,
            kwargs.get("order_id"),
            order_type,
            kwargs.get("order_status", "PENDING"),
            kwargs.get("price"),
            kwargs.get("size"),
        ),
    )

    return cursor.lastrowid


def update_order_status(cursor, order_db_id: int, status: str) -> None:
    """Update order status."""
    cursor.execute(
        "UPDATE orders SET order_status = ? WHERE id = ?", (status, order_db_id)
    )

    if status == "FILLED":
        now = datetime.now(tz=ZoneInfo("UTC")).isoformat()
        cursor.execute(
            "UPDATE orders SET filled_at = ? WHERE id = ?", (now, order_db_id)
        )
    elif status == "CANCELLED":
        now = datetime.now(tz=ZoneInfo("UTC")).isoformat()
        cursor.execute(
            "UPDATE orders SET cancelled_at = ? WHERE id = ?", (now, order_db_id)
        )


def get_orders_for_position(
    cursor, position_id: int, order_type: Optional[str] = None
) -> List[Dict]:
    """
    Get all orders for a position.

    Args:
        cursor: Active database cursor
        position_id: Position ID
        order_type: Optional filter by order type

    Returns:
        List of order dictionaries
    """
    if order_type:
        cursor.execute(
            "SELECT * FROM orders WHERE position_id = ? AND order_type = ?",
            (position_id, order_type),
        )
    else:
        cursor.execute("SELECT * FROM orders WHERE position_id = ?", (position_id,))

    rows = cursor.fetchall()
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in rows]


def get_order_by_exchange_id(cursor, order_id: str) -> Optional[Dict]:
    """Get order by exchange order ID."""
    cursor.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,))
    row = cursor.fetchone()
    if not row:
        return None

    columns = [col[0] for col in cursor.description]
    return dict(zip(columns, row))


def get_position_by_order_id(
    cursor, order_id: str, order_type: Optional[str] = None
) -> Optional[Dict]:
    """
    Get position by exchange order ID.

    Args:
        cursor: Active database cursor
        order_id: Exchange order ID
        order_type: Optional filter by order type

    Returns:
        Position dictionary with window data joined, or None
    """
    query = """
        SELECT 
            p.*, 
            w.symbol, w.window_start, w.window_end, w.slug, w.token_id, w.condition_id,
            o.order_type, o.order_status
        FROM orders o
        JOIN positions p ON o.position_id = p.id
        JOIN windows w ON p.window_id = w.id
        WHERE o.order_id = ?
    """
    params = [order_id]

    if order_type:
        query += " AND o.order_type = ?"
        params.append(order_type)

    cursor.execute(query, params)
    row = cursor.fetchone()
    if not row:
        return None

    columns = [col[0] for col in cursor.description]
    return dict(zip(columns, row))


# ============================================================================
# STATISTICS AND REPORTING
# ============================================================================


def get_total_exposure(cursor) -> float:
    """Get total USD exposure of open positions."""
    cursor.execute(
        "SELECT SUM(bet_usd) FROM positions WHERE settled = 0 AND exited_early = 0"
    )
    result = cursor.fetchone()
    return result[0] or 0.0


def get_performance_stats(cursor) -> Dict[str, Any]:
    """Get overall performance statistics."""
    cursor.execute(
        "SELECT COUNT(*), SUM(bet_usd), SUM(pnl_usd), AVG(roi_pct) FROM positions WHERE settled = 1"
    )
    result = cursor.fetchone()
    total_trades = result[0] or 0

    if not total_trades:
        return {
            "total_trades": 0,
            "total_invested": 0,
            "total_pnl": 0,
            "avg_roi": 0,
            "win_rate": 0,
        }

    total_invested, total_pnl, avg_roi = (
        result[1] or 0,
        result[2] or 0,
        result[3] or 0,
    )

    cursor.execute("SELECT COUNT(*) FROM positions WHERE settled = 1 AND pnl_usd > 0")
    winning_trades = cursor.fetchone()[0]
    win_rate = (winning_trades / total_trades) * 100

    return {
        "total_trades": total_trades,
        "total_invested": total_invested,
        "total_pnl": total_pnl,
        "avg_roi": avg_roi,
        "win_rate": win_rate,
    }


# ============================================================================
# MIGRATION HELPERS
# ============================================================================


def get_last_settled_position_for_token(cursor, token_id: str) -> Optional[Dict]:
    """Get the most recently settled position for a given token."""
    cursor.execute(
        """
        SELECT p.*, w.symbol
        FROM positions p
        JOIN windows w ON p.window_id = w.id
        WHERE w.token_id = ? AND p.settled = 1
        ORDER BY p.settled_at DESC
        LIMIT 1
    """,
        (token_id,),
    )

    row = cursor.fetchone()
    if not row:
        return None

    columns = [col[0] for col in cursor.description]
    return dict(zip(columns, row))


def get_expired_positions(cursor, current_time_iso: str) -> List[Dict]:
    """
    Get all positions where the window has expired but position is not settled.

    Args:
        cursor: Active database cursor
        current_time_iso: Current time in ISO format

    Returns:
        List of position dictionaries with window data
    """
    cursor.execute(
        """
        SELECT 
            p.id, p.size, p.bet_usd, p.entry_price, p.side,
            w.symbol, w.slug, w.token_id, w.window_start, w.window_end
        FROM positions p
        JOIN windows w ON p.window_id = w.id
        WHERE p.settled = 0 AND datetime(w.window_end) < datetime(?)
    """,
        (current_time_iso,),
    )

    rows = cursor.fetchall()
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in rows]


def update_position_condition_id(cursor, position_id: int, condition_id: str) -> None:
    """Update position's condition_id via window."""
    # Get window_id first
    cursor.execute("SELECT window_id FROM positions WHERE id = ?", (position_id,))
    result = cursor.fetchone()
    if result:
        window_id = result[0]
        cursor.execute(
            "UPDATE windows SET condition_id = ? WHERE id = ?",
            (condition_id, window_id),
        )


def update_position_redeem_tx(cursor, position_id: int, tx_hash: str) -> None:
    """Update position's redemption transaction hash."""
    cursor.execute(
        "UPDATE positions SET redeem_tx_hash = ? WHERE id = ?", (tx_hash, position_id)
    )
    """
    Get all open positions with their associated orders.
    Useful for migration and debugging.

    Returns:
        List of dictionaries with position and order data
    """
    cursor.execute("""
        SELECT 
            p.id as position_id,
            p.created_at,
            p.side,
            p.entry_price,
            p.size,
            p.bet_usd,
            p.is_reversal,
            p.is_hedged,
            w.symbol,
            w.window_start,
            w.window_end,
            w.token_id,
            GROUP_CONCAT(o.order_type || ':' || COALESCE(o.order_id, 'NULL') || ':' || o.order_status) as orders
        FROM positions p
        JOIN windows w ON p.window_id = w.id
        LEFT JOIN orders o ON o.position_id = p.id
        WHERE p.settled = 0
        GROUP BY p.id
    """)

    rows = cursor.fetchall()
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in rows]


def get_positions_for_window_settlement(cursor, window_start: str) -> List[Dict]:
    """
    Get all positions for a specific window with settlement data.
    Used for window-level settlement reporting.
    """
    cursor.execute("""
        SELECT 
            w.symbol, p.side, p.pnl_usd, p.roi_pct, w.final_outcome, p.bet_usd
        FROM positions p
        JOIN windows w ON p.window_id = w.id
        WHERE w.window_start = ?
    """, (window_start,))
    
    rows = cursor.fetchall()
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in rows]


def count_unsettled_positions_for_window(cursor, window_start: str) -> int:
    """Count how many unsettled positions exist for a window."""
    cursor.execute("""
        SELECT COUNT(*)
        FROM positions p
        JOIN windows w ON p.window_id = w.id
        WHERE w.window_start = ? AND p.settled = 0
    """, (window_start,))
    
    result = cursor.fetchone()
    return result[0] if result else 0
