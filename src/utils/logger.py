"""Logging utilities"""

import requests
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo
from src.config.settings import LOG_FILE, ERROR_LOG_FILE, DISCORD_WEBHOOK


def log(text: str) -> None:
    """Log message to console and file"""
    try:
        line = f"[{datetime.now(tz=ZoneInfo('UTC')).strftime('%Y-%m-%d %H:%M:%S UTC')}] {text}"
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


def log_error(text: str, include_traceback: bool = True) -> None:
    """Log error message to console, main log, and dedicated error log"""
    try:
        timestamp = datetime.now(tz=ZoneInfo("UTC")).strftime("%Y-%m-%d %H:%M:%S UTC")
        error_msg = f"❌ ERROR: {text}"

        # Log to main log and console
        log(error_msg)

        # Prepare detailed error report
        report = [f"[{timestamp}] {error_msg}"]
        if include_traceback:
            report.append(traceback.format_exc())

        report_text = "\n".join(report) + "\n" + "-" * 50 + "\n"

        # Log to dedicated error log
        try:
            with open(ERROR_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(report_text)
        except Exception:
            pass

    except Exception:
        pass


def send_discord(msg: str) -> None:
    """Send notification to Discord webhook"""
    if not DISCORD_WEBHOOK:
        return
    try:
        requests.post(DISCORD_WEBHOOK, json={"content": msg}, timeout=5)
    except Exception:
        pass
