#!/usr/bin/env python3
"""
PolyFlup Trading Bot
Entry point for the modular trading system.
"""

import sys
import os

# Add src to python path if needed
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.bot import main
from src.utils.logger import log

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("⛔ Bot stopped by user")
    except Exception as e:
        log(f"❌ FATAL CRASH: {e}")
        import traceback

        log(traceback.format_exc())
