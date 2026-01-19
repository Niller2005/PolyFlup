"""Database migrations system"""

from typing import List, Callable, Any
from src.utils.logger import log, log_error
from src.data.db_connection import db_connection


def get_schema_version(conn: Any) -> int:
    """Get current schema version from database"""
    c = conn.cursor()

    # Create schema_version table if it doesn't exist
    c.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
    """)

    c.execute("SELECT MAX(version) FROM schema_version")
    result = c.fetchone()
    current_version = result[0] if result[0] is not None else 0

    return current_version


def set_schema_version(conn: Any, version: int) -> None:
    """Set schema version in database"""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    c = conn.cursor()
    c.execute(
        "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
        (version, datetime.now(tz=ZoneInfo("UTC")).isoformat()),
    )


def migration_001_add_scale_in_order_id(conn: Any) -> None:
    """Add scale_in_order_id column to track pending scale-in orders"""
    c = conn.cursor()

    # Check if column already exists
    c.execute("PRAGMA table_info(trades)")
    columns = [row[1] for row in c.fetchall()]

    if "scale_in_order_id" not in columns:
        log("  - Adding scale_in_order_id column...")
        c.execute("ALTER TABLE trades ADD COLUMN scale_in_order_id TEXT")
        log("    ✓ Column added")
    else:
        log("    ✓ scale_in_order_id column already exists")


def migration_002_add_created_at_column(conn: Any) -> None:
    """Add created_at as alias for timestamp for better clarity"""
    c = conn.cursor()

    # Note: SQLite doesn't support renaming columns easily
    # We'll just ensure timestamp exists and document it should be used
    c.execute("PRAGMA table_info(trades)")
    columns = [row[1] for row in c.fetchall()]

    if "timestamp" in columns:
        log("    ✓ timestamp column exists (serves as created_at)")
    else:
        log("    ⚠️  timestamp column missing - schema needs rebuild")


def migration_003_add_reversal_triggered_column(conn: Any) -> None:
    """Add reversal_triggered column to track if a reversal has been initiated for a trade"""
    c = conn.cursor()

    c.execute("PRAGMA table_info(trades)")
    columns = [row[1] for row in c.fetchall()]

    if "reversal_triggered" not in columns:
        log("  - Adding reversal_triggered column...")
        c.execute("ALTER TABLE trades ADD COLUMN reversal_triggered BOOLEAN DEFAULT 0")
        log("    ✓ Column added")
    else:
        log("    ✓ reversal_triggered column already exists")


def migration_004_add_reversal_triggered_at_column(conn: Any) -> None:
    """Add reversal_triggered_at column to track when a reversal was initiated"""
    c = conn.cursor()

    c.execute("PRAGMA table_info(trades)")
    columns = [row[1] for row in c.fetchall()]

    if "reversal_triggered_at" not in columns:
        log("  - Adding reversal_triggered_at column...")
        c.execute("ALTER TABLE trades ADD COLUMN reversal_triggered_at TEXT")
        log("    ✓ Column added")
    else:
        log("    ✓ reversal_triggered_at column already exists")


def migration_005_add_last_scale_in_at_column(conn: Any) -> None:
    """Add last_scale_in_at column to track when last scale-in occurred"""
    c = conn.cursor()

    c.execute("PRAGMA table_info(trades)")
    columns = [row[1] for row in c.fetchall()]

    if "last_scale_in_at" not in columns:
        log("  - Adding last_scale_in_at column...")
        c.execute("ALTER TABLE trades ADD COLUMN last_scale_in_at TEXT")
        log("    ✓ Column added")
    else:
        log("    ✓ last_scale_in_at column already exists")


def migration_006_add_signal_score_columns(conn: Any) -> None:
    """Add raw signal score columns for confidence formula calibration"""
    c = conn.cursor()

    c.execute("PRAGMA table_info(trades)")
    columns = [row[1] for row in c.fetchall()]

    new_columns = [
        ("up_total", "REAL"),
        ("down_total", "REAL"),
        ("momentum_score", "REAL"),
        ("momentum_dir", "TEXT"),
        ("flow_score", "REAL"),
        ("flow_dir", "TEXT"),
        ("divergence_score", "REAL"),
        ("divergence_dir", "TEXT"),
        ("vwm_score", "REAL"),
        ("vwm_dir", "TEXT"),
        ("pm_mom_score", "REAL"),
        ("pm_mom_dir", "TEXT"),
        ("adx_score", "REAL"),
        ("adx_dir", "TEXT"),
        ("lead_lag_bonus", "REAL"),
    ]

    for col_name, col_type in new_columns:
        if col_name not in columns:
            log(f"  - Adding {col_name} column...")
            c.execute(f"ALTER TABLE trades ADD COLUMN {col_name} {col_type}")
            log(f"    ✓ {col_name} added")
        else:
            log(f"    ✓ {col_name} already exists")


def migration_007_add_bayesian_comparison_columns(conn: Any) -> None:
    """Add Bayesian confidence comparison columns for A/B testing against additive method"""
    c = conn.cursor()

    c.execute("PRAGMA table_info(trades)")
    columns = [row[1] for row in c.fetchall()]

    new_columns = [
        ("additive_confidence", "REAL"),
        ("additive_bias", "TEXT"),
        ("bayesian_confidence", "REAL"),
        ("bayesian_bias", "TEXT"),
        ("market_prior_p_up", "REAL"),
    ]

    for col_name, col_type in new_columns:
        if col_name not in columns:
            log(f"  - Adding {col_name} column...")
            c.execute(f"ALTER TABLE trades ADD COLUMN {col_name} {col_type}")
            log(f"    ✓ {col_name} added")
        else:
            log(f"    ✓ {col_name} already exists")


def migration_008_add_hedge_order_columns(conn: Any) -> None:
    """Add hedge order tracking columns for guaranteed profit strategy"""
    c = conn.cursor()

    c.execute("PRAGMA table_info(trades)")
    columns = [row[1] for row in c.fetchall()]

    new_columns = [
        ("hedge_order_id", "TEXT"),
        ("hedge_order_price", "REAL"),
        ("is_hedged", "BOOLEAN DEFAULT 0"),
    ]

    for col_name, col_type in new_columns:
        if col_name not in columns:
            log(f"  - Adding {col_name} column...")
            c.execute(f"ALTER TABLE trades ADD COLUMN {col_name} {col_type}")
            log(f"    ✓ {col_name} added")
        else:
            log(f"    ✓ {col_name} already exists")


def migration_009_add_ctf_merge_columns(conn: Any) -> None:
    """Add CTF merge tracking columns for immediate capital recovery"""
    c = conn.cursor()

    c.execute("PRAGMA table_info(trades)")
    columns = [row[1] for row in c.fetchall()]

    new_columns = [
        ("condition_id", "TEXT"),  # Store condition_id from position data
        ("merge_tx_hash", "TEXT"),  # Track CTF merge transaction
    ]

    for col_name, col_type in new_columns:
        if col_name not in columns:
            log(f"  - Adding {col_name} column...")
            c.execute(f"ALTER TABLE trades ADD COLUMN {col_name} {col_type}")
            log(f"    ✓ {col_name} added")
        else:
            log(f"    ✓ {col_name} already exists")


def migration_010_add_redeem_tx_hash_column(conn: Any) -> None:
    """Add redeem transaction hash column for post-resolution redemption tracking"""
    c = conn.cursor()

    c.execute("PRAGMA table_info(trades)")
    columns = [row[1] for row in c.fetchall()]

    if "redeem_tx_hash" not in columns:
        log("  - Adding redeem_tx_hash column...")
        c.execute("ALTER TABLE trades ADD COLUMN redeem_tx_hash TEXT")
        log("    ✓ redeem_tx_hash added")
    else:
        log("    ✓ redeem_tx_hash column already exists")


def migration_011_add_hedge_exit_tracking_columns(conn: Any) -> None:
    """Add hedge exit price tracking for accurate P&L on hedged positions"""
    c = conn.cursor()

    c.execute("PRAGMA table_info(trades)")
    columns = [row[1] for row in c.fetchall()]

    new_columns = [
        ("hedge_exit_price", "REAL"),
        ("hedge_exited_early", "BOOLEAN DEFAULT 0"),
    ]

    for col_name, col_type in new_columns:
        if col_name not in columns:
            log(f"  - Adding {col_name} column...")
            c.execute(f"ALTER TABLE trades ADD COLUMN {col_name} {col_type}")
            log(f"    ✓ {col_name} added")
        else:
            log(f"    ✓ {col_name} already exists")


def migration_012_normalize_schema(conn: Any) -> None:
    """
    Normalize database schema into windows, positions, and orders tables.

    This migration:
    1. Creates new normalized tables
    2. Migrates existing data from trades table
    3. Keeps trades table for backward compatibility (will be deprecated later)
    """
    c = conn.cursor()

    log("  - Creating windows table...")
    c.execute("""
        CREATE TABLE IF NOT EXISTS windows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            window_start TEXT NOT NULL,
            window_end TEXT NOT NULL,
            slug TEXT,
            token_id TEXT,
            condition_id TEXT,
            
            -- Market data at window start
            p_yes REAL,
            best_bid REAL,
            best_ask REAL,
            imbalance REAL,
            funding_bias REAL,
            market_prior_p_up REAL,
            
            -- Signal scores (for analysis)
            up_total REAL,
            down_total REAL,
            momentum_score REAL,
            momentum_dir TEXT,
            flow_score REAL,
            flow_dir TEXT,
            divergence_score REAL,
            divergence_dir TEXT,
            vwm_score REAL,
            vwm_dir TEXT,
            pm_mom_score REAL,
            pm_mom_dir TEXT,
            adx_score REAL,
            adx_dir TEXT,
            lead_lag_bonus REAL,
            
            -- Settlement
            final_outcome TEXT,
            
            UNIQUE(symbol, window_start)
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_windows_symbol ON windows(symbol)")
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_windows_time ON windows(window_start, window_end)"
    )
    log("    ✓ windows table created")

    log("  - Creating positions table...")
    c.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            window_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            
            -- Position details
            side TEXT NOT NULL,
            entry_price REAL NOT NULL,
            size REAL NOT NULL,
            bet_usd REAL NOT NULL,
            edge REAL,
            
            -- Confidence metrics
            additive_confidence REAL,
            additive_bias TEXT,
            bayesian_confidence REAL,
            bayesian_bias TEXT,
            
            -- Position type
            is_reversal BOOLEAN DEFAULT 0,
            is_hedged BOOLEAN DEFAULT 0,
            target_price REAL,
            
            -- Scale-in tracking
            scaled_in BOOLEAN DEFAULT 0,
            last_scale_in_at TEXT,
            
            -- Exit tracking
            settled BOOLEAN DEFAULT 0,
            settled_at TEXT,
            exited_early BOOLEAN DEFAULT 0,
            exit_price REAL,
            pnl_usd REAL,
            roi_pct REAL,
            
            -- Hedge exit tracking
            hedge_exit_price REAL,
            hedge_exited_early BOOLEAN DEFAULT 0,
            
            -- CTF merge tracking
            merge_tx_hash TEXT,
            redeem_tx_hash TEXT,
            
            -- Reversal tracking
            reversal_triggered BOOLEAN DEFAULT 0,
            reversal_triggered_at TEXT,
            
            FOREIGN KEY (window_id) REFERENCES windows(id)
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_positions_window ON positions(window_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_positions_settled ON positions(settled)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_positions_side ON positions(side)")
    log("    ✓ positions table created")

    log("  - Creating orders table...")
    c.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            
            -- Order details
            order_id TEXT,
            order_type TEXT NOT NULL,
            order_status TEXT,
            price REAL,
            size REAL,
            
            -- Tracking
            filled_at TEXT,
            cancelled_at TEXT,
            
            FOREIGN KEY (position_id) REFERENCES positions(id)
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_orders_position ON orders(position_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_orders_order_id ON orders(order_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_orders_type ON orders(order_type)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(order_status)")
    log("    ✓ orders table created")

    # Migrate existing data from trades table
    log("  - Migrating existing data from trades table...")

    # Step 1: Migrate windows (unique combinations of symbol + window_start)
    log("    - Migrating windows...")
    c.execute("""
        INSERT OR IGNORE INTO windows (
            symbol, window_start, window_end, slug, token_id, condition_id,
            p_yes, best_bid, best_ask, imbalance, funding_bias, market_prior_p_up,
            up_total, down_total, momentum_score, momentum_dir, flow_score, flow_dir,
            divergence_score, divergence_dir, vwm_score, vwm_dir, pm_mom_score, pm_mom_dir,
            adx_score, adx_dir, lead_lag_bonus, final_outcome
        )
        SELECT DISTINCT
            symbol, window_start, window_end, slug, token_id, condition_id,
            p_yes, best_bid, best_ask, imbalance, funding_bias, market_prior_p_up,
            up_total, down_total, momentum_score, momentum_dir, flow_score, flow_dir,
            divergence_score, divergence_dir, vwm_score, vwm_dir, pm_mom_score, pm_mom_dir,
            adx_score, adx_dir, lead_lag_bonus, final_outcome
        FROM trades
        ORDER BY timestamp ASC
    """)
    window_count = c.rowcount
    log(f"      ✓ Migrated {window_count} windows")

    # Step 2: Migrate positions (each trade becomes a position)
    log("    - Migrating positions...")
    c.execute("""
        INSERT INTO positions (
            window_id, created_at, side, entry_price, size, bet_usd, edge,
            additive_confidence, additive_bias, bayesian_confidence, bayesian_bias,
            is_reversal, is_hedged, target_price, scaled_in, last_scale_in_at,
            settled, settled_at, exited_early, exit_price, pnl_usd, roi_pct,
            hedge_exit_price, hedge_exited_early, merge_tx_hash, redeem_tx_hash,
            reversal_triggered, reversal_triggered_at
        )
        SELECT
            w.id, t.timestamp, t.side, t.entry_price, t.size, t.bet_usd, t.edge,
            t.additive_confidence, t.additive_bias, t.bayesian_confidence, t.bayesian_bias,
            t.is_reversal, t.is_hedged, t.target_price, t.scaled_in, t.last_scale_in_at,
            t.settled, t.settled_at, t.exited_early, t.exit_price, t.pnl_usd, t.roi_pct,
            t.hedge_exit_price, t.hedge_exited_early, t.merge_tx_hash, t.redeem_tx_hash,
            t.reversal_triggered, t.reversal_triggered_at
        FROM trades t
        JOIN windows w ON t.symbol = w.symbol AND t.window_start = w.window_start
        ORDER BY t.id ASC
    """)
    position_count = c.rowcount
    log(f"      ✓ Migrated {position_count} positions")

    # Step 3: Migrate orders (extract from trades table)
    log("    - Migrating orders...")

    # Create a temporary mapping table for trades.id -> positions.id
    c.execute("""
        CREATE TEMPORARY TABLE trade_position_map AS
        SELECT t.id as trade_id, p.id as position_id, t.timestamp
        FROM trades t
        JOIN windows w ON t.symbol = w.symbol AND t.window_start = w.window_start
        JOIN positions p ON p.window_id = w.id AND p.created_at = t.timestamp
    """)

    # Entry orders (order_id)
    c.execute("""
        INSERT INTO orders (position_id, created_at, order_id, order_type, order_status, price, size)
        SELECT m.position_id, t.timestamp, t.order_id, 'ENTRY', t.order_status, t.entry_price, t.size
        FROM trades t
        JOIN trade_position_map m ON t.id = m.trade_id
        WHERE t.order_id IS NOT NULL AND t.order_id != 'N/A'
    """)
    entry_orders = c.rowcount

    # Limit sell orders
    c.execute("""
        INSERT INTO orders (position_id, created_at, order_id, order_type, order_status, price, size)
        SELECT m.position_id, t.timestamp, t.limit_sell_order_id, 'LIMIT_SELL', 'OPEN', t.target_price, t.size
        FROM trades t
        JOIN trade_position_map m ON t.id = m.trade_id
        WHERE t.limit_sell_order_id IS NOT NULL
    """)
    limit_sell_orders = c.rowcount

    # Scale-in orders
    c.execute("""
        INSERT INTO orders (position_id, created_at, order_id, order_type, order_status, price, size)
        SELECT m.position_id, t.timestamp, t.scale_in_order_id, 'SCALE_IN', 'OPEN', NULL, NULL
        FROM trades t
        JOIN trade_position_map m ON t.id = m.trade_id
        WHERE t.scale_in_order_id IS NOT NULL
    """)
    scale_in_orders = c.rowcount

    # Hedge orders
    c.execute("""
        INSERT INTO orders (position_id, created_at, order_id, order_type, order_status, price, size)
        SELECT m.position_id, t.timestamp, t.hedge_order_id, 'HEDGE', t.order_status, t.hedge_order_price, t.size
        FROM trades t
        JOIN trade_position_map m ON t.id = m.trade_id
        WHERE t.hedge_order_id IS NOT NULL
    """)
    hedge_orders = c.rowcount

    total_orders = entry_orders + limit_sell_orders + scale_in_orders + hedge_orders
    log(
        f"      ✓ Migrated {total_orders} orders ({entry_orders} entry, {limit_sell_orders} limit_sell, {scale_in_orders} scale_in, {hedge_orders} hedge)"
    )

    log("    ✓ Data migration complete")
    log("  NOTE: trades table retained for backward compatibility")


# Migration registry: version -> migration function
MIGRATIONS: List[tuple[int, str, Callable]] = [
    (1, "Add scale_in_order_id column", migration_001_add_scale_in_order_id),
    (2, "Verify timestamp column", migration_002_add_created_at_column),
    (3, "Add reversal_triggered column", migration_003_add_reversal_triggered_column),
    (
        4,
        "Add reversal_triggered_at column",
        migration_004_add_reversal_triggered_at_column,
    ),
    (
        5,
        "Add last_scale_in_at column",
        migration_005_add_last_scale_in_at_column,
    ),
    (
        6,
        "Add signal score columns for calibration",
        migration_006_add_signal_score_columns,
    ),
    (
        7,
        "Add Bayesian comparison columns for A/B testing",
        migration_007_add_bayesian_comparison_columns,
    ),
    (
        8,
        "Add hedge order tracking columns for guaranteed profit strategy",
        migration_008_add_hedge_order_columns,
    ),
    (
        9,
        "Add CTF merge tracking columns for immediate capital recovery",
        migration_009_add_ctf_merge_columns,
    ),
    (
        10,
        "Add redeem transaction hash column for post-resolution redemption",
        migration_010_add_redeem_tx_hash_column,
    ),
    (
        11,
        "Add hedge exit tracking columns for accurate P&L",
        migration_011_add_hedge_exit_tracking_columns,
    ),
    (
        12,
        "Normalize schema into windows, positions, and orders tables",
        migration_012_normalize_schema,
    ),
]


def run_migrations() -> None:
    """Run all pending database migrations"""
    with db_connection() as conn:
        try:
            current_version = get_schema_version(conn)
            log(f"📊 Database schema version: {current_version}")

            # Find pending migrations
            pending = [m for m in MIGRATIONS if m[0] > current_version]

            if not pending:
                log("✓ Database schema is up to date")
                return

            log(f"🔄 Running {len(pending)} pending migrations...")

            latest_version = current_version
            for version, description, migration_func in pending:
                log(f"  Migration {version}: {description}")
                try:
                    migration_func(conn)
                    set_schema_version(conn, version)
                    latest_version = version
                    log(f"    ✓ Migration {version} completed")
                except Exception as e:
                    log_error(f"Migration {version} failed: {e}")
                    conn.rollback()
                    raise

            log(f"✓ All migrations completed. Schema version: {latest_version}")

        except Exception as e:
            log_error(f"Migration error: {e}")
            raise


def add_migration(description: str, migration_func: Callable) -> None:
    """
    Helper to add a new migration (for future use)

    Usage:
        def migration_003_add_my_column(conn):
            c = conn.cursor()
            c.execute("ALTER TABLE trades ADD COLUMN my_column TEXT")

        # Add to MIGRATIONS list manually
    """
    next_version = max([m[0] for m in MIGRATIONS]) + 1 if MIGRATIONS else 1
    MIGRATIONS.append((next_version, description, migration_func))
    log(f"Migration {next_version} registered: {description}")
