"""Run database migrations manually

Usage:
    uv run python run_migrations.py
"""

from src.data.migrations import run_migrations


if __name__ == "__main__":
    print("🔄 Running database migrations...\n")
    run_migrations()
    print("\n✅ Migrations completed")
