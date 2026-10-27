"""PolyFlup terminal dashboard.

Usage:
    uv run tui.py                       # live dashboard (q quit, r refresh, p pause)
    uv run tui.py --db prod/trades.db   # inspect a database downloaded from production
    uv run tui.py --once                # render a single frame and exit
"""

import argparse
import sys
from pathlib import Path

from src.tui.app import run

BASE_DIR = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description="PolyFlup terminal dashboard (read-only)")
    parser.add_argument("--db", type=Path, default=BASE_DIR / "trades.db", help="path to trades.db")
    parser.add_argument(
        "--log", type=Path, default=BASE_DIR / "logs" / "trades_2025.log", help="path to the master log"
    )
    parser.add_argument(
        "--paper", action="store_true", help="show the paper-trading account (trades_paper.db / trades_paper.log)"
    )
    parser.add_argument("--refresh", type=float, default=1.0, help="database refresh interval in seconds")
    parser.add_argument("--once", action="store_true", help="render one frame and exit")
    parser.add_argument(
        "--no-wallet", action="store_true", help="skip wallet balance lookup (no .env / RPC needed)"
    )
    args = parser.parse_args()
    if args.paper:
        if args.db == BASE_DIR / "trades.db":
            args.db = BASE_DIR / "trades_paper.db"
        if args.log == BASE_DIR / "logs" / "trades_2025.log":
            args.log = BASE_DIR / "logs" / "trades_paper.log"
        args.no_wallet = True  # paper cash comes from the database
    return run(args.db, args.log, max(0.25, args.refresh), args.once, not args.no_wallet)


if __name__ == "__main__":
    sys.exit(main())
