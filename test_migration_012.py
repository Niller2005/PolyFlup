#!/usr/bin/env python3
"""
Test migration 012 - Normalize schema into windows, positions, and orders tables.

This script:
1. Initializes the database (runs all migrations including 012)
2. Queries the new tables to verify data was migrated correctly
3. Prints statistics about the migration
"""

import sys

sys.path.insert(0, "/mnt/d/dev/polyastra")

from src.data.database import init_database
from src.data.db_connection import db_connection


def test_migration():
    print("=" * 80)
    print("Testing Migration 012: Normalize Database Schema")
    print("=" * 80)

    # Initialize database (will run migration 012 if not already run)
    print("\n1. Initializing database and running migrations...")
    init_database()

    # Query the new tables
    with db_connection() as conn:
        c = conn.cursor()

        # Check windows table
        print("\n2. Checking windows table...")
        c.execute("SELECT COUNT(*) FROM windows")
        window_count = c.fetchone()[0]
        print(f"   ✓ Found {window_count} windows")

        if window_count > 0:
            c.execute("SELECT * FROM windows LIMIT 1")
            cols = [desc[0] for desc in c.description]
            print(f"   ✓ Columns: {', '.join(cols)}")

        # Check positions table
        print("\n3. Checking positions table...")
        c.execute("SELECT COUNT(*) FROM positions")
        position_count = c.fetchone()[0]
        print(f"   ✓ Found {position_count} positions")

        c.execute("SELECT COUNT(*) FROM positions WHERE settled = 0")
        open_positions = c.fetchone()[0]
        print(f"   ✓ Open positions: {open_positions}")

        c.execute("SELECT COUNT(*) FROM positions WHERE settled = 1")
        settled_positions = c.fetchone()[0]
        print(f"   ✓ Settled positions: {settled_positions}")

        # Check orders table
        print("\n4. Checking orders table...")
        c.execute("SELECT COUNT(*) FROM orders")
        order_count = c.fetchone()[0]
        print(f"   ✓ Found {order_count} orders")

        c.execute("SELECT order_type, COUNT(*) FROM orders GROUP BY order_type")
        for row in c.fetchall():
            print(f"     - {row[0]}: {row[1]}")

        # Compare with trades table
        print("\n5. Comparing with legacy trades table...")
        c.execute("SELECT COUNT(*) FROM trades")
        trade_count = c.fetchone()[0]
        print(f"   ✓ Legacy trades table has {trade_count} records")

        # Verify data consistency
        print("\n6. Verifying data consistency...")
        if position_count == trade_count:
            print(f"   ✓ Position count matches trade count ({position_count})")
        else:
            print(
                f"   ⚠️  Position count ({position_count}) != trade count ({trade_count})"
            )

        # Sample query: Get open positions with window data
        print("\n7. Sample query: Get open positions with window data...")
        c.execute("""
            SELECT 
                p.id, p.side, p.size, p.bet_usd,
                w.symbol, w.window_start, w.window_end
            FROM positions p
            JOIN windows w ON p.window_id = w.id
            WHERE p.settled = 0
            LIMIT 5
        """)
        results = c.fetchall()
        if results:
            print(f"   ✓ Found {len(results)} open positions:")
            for row in results:
                print(
                    f"     - Position {row[0]}: {row[1]} {row[2]} shares @ ${row[3]:.2f} ({row[4]} {row[5]})"
                )
        else:
            print("   ℹ  No open positions")

        # Sample query: Get position with all orders
        print("\n8. Sample query: Get position with all orders...")
        c.execute("""
            SELECT 
                p.id as position_id, p.side, p.size,
                o.order_type, o.order_id, o.order_status
            FROM positions p
            LEFT JOIN orders o ON o.position_id = p.id
            WHERE p.settled = 0
            LIMIT 10
        """)
        results = c.fetchall()
        if results:
            print(f"   ✓ Sample positions with orders:")
            current_pos_id = None
            for row in results:
                if row[0] != current_pos_id:
                    print(f"     Position {row[0]} ({row[1]} {row[2]} shares):")
                    current_pos_id = row[0]
                if row[3]:  # If there's an order
                    print(f"       - {row[3]}: {row[4]} ({row[5]})")
        else:
            print("   ℹ  No positions with orders found")

    print("\n" + "=" * 80)
    print("Migration test complete!")
    print("=" * 80)


if __name__ == "__main__":
    test_migration()
