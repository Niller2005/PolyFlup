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
