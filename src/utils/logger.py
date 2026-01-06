"""Logging utilities"""

import os
import requests
import traceback
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo
from src.config.settings import LOG_FILE, ERROR_LOG_FILE, DISCORD_WEBHOOK, BASE_DIR


_current_log_file: str = LOG_FILE


def set_log_window(window_id: str = "") -> None:
    """Set the log file for the current window"""
    global _current_log_file
    if not window_id:
        _current_log_file = LOG_FILE
    else:
        # Create a filename from window_id, e.g., 2026-01-06_15-45
        # If window_id is like '2026-01-06 15:45:00', it will be '2026-01-06_15-45'
        # We replace T, spaces and colons with underscores/dashes for filesystem safety
        safe_id = window_id.replace(" ", "_").replace(":", "-").replace("T", "_")

        # Remove any timezone offset or microseconds to keep filename clean and stable
        # Handles both +00:00 and -05:00 formats
        if "+" in safe_id:
            safe_id = safe_id.split("+")[0]
        elif "-" in safe_id and len(safe_id.split("-")) > 3:
            # Only split on the last dash if it looks like an offset (e.g. 2026-01-06_13-15-00-05-00)
            # A better way is to split on the last 3-4 characters if they match an offset pattern,
            # but let's just use a more surgical approach.
            parts = safe_id.split("-")
            # Reconstruct date (first 3 parts) and time (next 3 parts)
            # window_2026-01-06_13-15-00-05-00
            if len(parts) >= 6:
                safe_id = "-".join(parts[:5])  # 2026-01-06_13-15-00

        _current_log_file = os.path.join(BASE_DIR, "logs", f"window_{safe_id}.log")


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
            # Always log to master log file
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")

            # Also log to window-specific file if it's different
            if _current_log_file != LOG_FILE:
                try:
                    with open(_current_log_file, "a", encoding="utf-8") as f:
                        f.write(line + "\n")
                except Exception:
                    pass
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
