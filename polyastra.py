#!/usr/bin/env python3
"""
PolyAstra Trading Bot
Entry point for the modular trading system.
"""

import sys
import os

# Add src to python path if needed
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.bot import main

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⛔ Bot stopped by user")
    except Exception as e:
        print(f"\n❌ Critical error: {e}")
        import traceback

        traceback.print_exc()
