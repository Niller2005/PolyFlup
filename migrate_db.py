"""Database migration script to add missing columns"""

import sqlite3
from src.config.settings import DB_FILE


def migrate_database():
    """Add missing columns to trades table"""
    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    c = conn.cursor()

    # Get existing columns
    c.execute("PRAGMA table_info(trades)")
    existing_columns = {row[1] for row in c.fetchall()}

    print(f"Existing columns: {existing_columns}")

    # Add missing columns
    columns_to_add = {
        "exited_early": "BOOLEAN DEFAULT 0",
        "scaled_in": "BOOLEAN DEFAULT 0",
        "is_reversal": "BOOLEAN DEFAULT 0",
        "target_price": "REAL",
        "limit_sell_order_id": "TEXT",
    }

    for column_name, column_type in columns_to_add.items():
        if column_name not in existing_columns:
            try:
                c.execute(f"ALTER TABLE trades ADD COLUMN {column_name} {column_type}")
                print(f"[OK] Added column: {column_name}")
            except sqlite3.OperationalError as e:
                print(f"[WARN] Column {column_name} may already exist: {e}")

    conn.commit()

    # Verify final schema
    c.execute("PRAGMA table_info(trades)")
    final_columns = [row[1] for row in c.fetchall()]
    print(f"\n[OK] Migration complete. Total columns: {len(final_columns)}")
    print(f"Columns: {', '.join(final_columns)}")

    conn.close()


if __name__ == "__main__":
    migrate_database()
