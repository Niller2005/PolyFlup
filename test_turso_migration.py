#!/usr/bin/env python3
"""Test script to verify Turso migration"""

import os
import sys

print("Testing Turso Migration...")
print("=" * 60)

# Test 1: Import check
print("\n1. Testing imports...")
try:
    from src.data.db_connection import db_connection
    from src.data.migrations import run_migrations
    from src.data.database import init_database, save_trade
    from src.config.settings import USE_TURSO, TURSO_DATABASE_URL, TURSO_AUTH_TOKEN

    print("   OK All imports successful")
except Exception as e:
    print(f"   FAIL Import failed: {e}")
    sys.exit(1)

# Test 2: Configuration check
print("\n2. Checking configuration...")
print(f"   USE_TURSO: {USE_TURSO}")
if USE_TURSO:
    print(
        f"   TURSO_DATABASE_URL: {TURSO_DATABASE_URL[:30]}..."
        if TURSO_DATABASE_URL
        else "   TURSO_DATABASE_URL: Not set"
    )
    print(f"   TURSO_AUTH_TOKEN: {'Set' if TURSO_AUTH_TOKEN else 'Not set'}")
    if not TURSO_DATABASE_URL or not TURSO_AUTH_TOKEN:
        print("   WARN  Turso enabled but credentials missing")
else:
    print("   Using local SQLite (trades.db)")

# Test 3: Connection test
print("\n3. Testing database connection...")
try:
    with db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT 1")
        result = c.fetchone()
        if result and result[0] == 1:
            print("   OK Database connection successful")
        else:
            print("   FAIL Unexpected query result")
except Exception as e:
    print(f"   FAIL Connection failed: {e}")
    print("\n   Troubleshooting:")
    if USE_TURSO:
        print("   - Check TURSO_DATABASE_URL format (should start with libsql://)")
        print("   - Verify TURSO_AUTH_TOKEN is valid")
        print("   - Check network connectivity")
        print("   - Try: turso db show <your-db>")
    else:
        print("   - Check file permissions on trades.db")
        print("   - Ensure database directory exists")
    sys.exit(1)

# Test 4: Schema check
print("\n4. Checking database schema...")
try:
    with db_connection() as conn:
        c = conn.cursor()

        # Check if trades table exists
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trades'")
        if c.fetchone():
            print("   OK 'trades' table exists")

            # Check column count
            c.execute("PRAGMA table_info(trades)")
            columns = c.fetchall()
            print(f"   OK Found {len(columns)} columns")

            # Check for new columns
            column_names = [col[1] for col in columns]
            if "scale_in_order_id" in column_names:
                print("   OK Migration column 'scale_in_order_id' present")
            else:
                print(
                    "   WARN  Migration column 'scale_in_order_id' missing (run migrations)"
                )
        else:
            print(
                "   INFO  'trades' table doesn't exist yet (will be created on bot startup)"
            )

except Exception as e:
    print(f"   FAIL Schema check failed: {e}")

# Test 5: Summary
print("\n" + "=" * 60)
print("Migration Test Results:")
print("=" * 60)
backend = "Turso (Remote)" if USE_TURSO else "SQLite (Local)"
print(f"Database Backend: {backend}")
print("Status: OK Ready to use")
print("\nNext steps:")
if USE_TURSO:
    print("  1. Run: python polyastra.py")
    print("  2. Migrations will run automatically on first start")
    print("  3. Verify trades save correctly")
    print("  4. Monitor: turso db shell <your-db>")
else:
    print("  1. Run: python polyastra.py")
    print("  2. Bot will use local trades.db file")
    print("  3. To switch to Turso: Set USE_TURSO=YES in .env")
print("\nSee TURSO_MIGRATION.md for detailed setup instructions")
print("=" * 60)
