"""Logging utilities"""

import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from src.config.settings import LOG_FILE, DISCORD_WEBHOOK


def log(text: str) -> None:
    """Log message to console and file"""
    try:
        line = (
            f"[{datetime.now(tz=ZoneInfo('UTC')).strftime('%Y-%m-%d %H:%M:%S UTC')}] {text}"
        )
        try:
            print(line, flush=True)
        except Exception:
            try:
                # Fallback for consoles that don't support emojis/unicode or other print errors
                print(line.encode("ascii", "replace").decode("ascii"), flush=True)
            except Exception:
                pass

        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
    except Exception:
        # Final safety net to prevent recursive logging failures from crashing the bot
        pass


def send_discord(msg: str) -> None:
    """Send notification to Discord webhook"""
    if not DISCORD_WEBHOOK:
        return
    try:
        requests.post(DISCORD_WEBHOOK, json={"content": msg}, timeout=5)
    except Exception:
        pass
