"""Logging utilities"""

import os
import requests
import traceback
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo
from src.config.settings import LOG_FILE, ERROR_LOG_FILE, DISCORD_WEBHOOK, BASE_DIR


_current_log_file: str = LOG_FILE
_current_master_log: str = LOG_FILE
_last_rotation_date: str = ""


def _format_window_filename(window_id: str, window_range: str = "") -> str:
    """Convert window_id and optional range to user-friendly filename

    window_id: ISO datetime like "2026-01-14T15:30:00+00:00" or simple "2026-01-14 15:30:00"
    window_range: Human-readable range like "January 14, 3:30-3:45PM ET"

    Output examples:
    - With range: "window_Jan14_3:30-3:45PM.log"
    - Without range: "window_Jan14_3:30PM.log"
    """
    if not window_id:
        return window_id

    # Normalize: replace 'T' with ' ' and remove timezone offset
    normalized = window_id.replace("T", " ").split("+")[0]
    parts = normalized.split()

    if len(parts) < 2:
        return window_id

    date_part, time_part = parts[0], parts[1]

    # Parse date "2026-01-14"
    try:
        date_obj = datetime.strptime(date_part, "%Y-%m-%d")
        month_name = date_obj.strftime("%b")  # "Jan", "Feb", etc.
        day_num = date_obj.strftime("%d")  # "14"
        month_day = f"{month_name}{day_num}"  # "Jan14"
    except Exception:
        return window_id

    # Parse time "15:30:00" to "3:30PM" format
    time_parts = time_part.split(":")
    if len(time_parts) >= 2:
        hour = int(time_parts[0])
        minute = (
            time_parts[1].split(":")[0]
            if len(time_parts[1].split(":")) > 1
            else time_parts[1]
        )

        # Convert to 12-hour format with AM/PM
        if hour == 0:
            hour_12 = 12
            ampm = "AM"
        elif hour < 12:
            hour_12 = hour
            ampm = "AM"
        elif hour == 12:
            hour_12 = 12
            ampm = "PM"
        else:
            hour_12 = hour - 12
            ampm = "PM"

        hour_min = f"{hour_12}:{minute}{ampm}"
    else:
        return window_id

    # Append range if provided
    if window_range:
        return f"{month_day}_{hour_min}-{window_range}"
    else:
        return f"{month_day}_{hour_min}"


def _rotate_logs_if_needed():
    """Check if log file should be rotated to a new day"""
    global _current_log_file, _current_master_log, _last_rotation_date

    # Get current date in YYYY-MM-DD format
    current_date = datetime.now(tz=ZoneInfo("UTC")).strftime("%Y-%m-%d")

    # Check if master log file exists and has content
    if not os.path.exists(_current_master_log):
        return None

    # Check if already rotated today
    if _last_rotation_date == current_date:
        return None

    # Rotate daily: archive current log and start fresh
    archive_dir = os.path.join(BASE_DIR, "logs", "archive")
    os.makedirs(archive_dir, exist_ok=True)

    # Archive current log with date stamp
    archive_name = f"trades_{current_date}.log"
    archive_path = os.path.join(archive_dir, archive_name)

    if os.path.getsize(_current_master_log) > 100:
        try:
            with open(_current_master_log, "r", encoding="utf-8") as f:
                content = f.read()
            with open(archive_path, "w", encoding="utf-8") as f:
                f.write(content)
            # Truncate original and write header
            with open(_current_master_log, "w", encoding="utf-8") as f:
                f.write(
                    f"[{datetime.now(tz=ZoneInfo('UTC')).strftime('%Y-%m-%d %H:%M:%S UTC')}] 🔄 Log rotated to archive\n"
                )
            # Mark as rotated
            _last_rotation_date = current_date
        except Exception:
            pass

    # Update window log to point to fresh master log
    _current_log_file = _current_master_log

    return None


def set_log_window(window_id: str = "", window_range: str = "") -> None:
    """Set the log file for a specific trading window"""
    global _current_log_file

    if not window_id:
        _current_log_file = _current_master_log
        return

    # Convert window_id and optional range to user-friendly filename
    # Input: "2026-01-14 15:30:00" and "January 14, 3:30-3:45PM ET"
    # Output: "window_Jan14_3:30-3:45PM.log"
    window_name = _format_window_filename(window_id, window_range)

    # Ensure windows folder exists
    windows_dir = os.path.join(BASE_DIR, "logs", "windows")
    os.makedirs(windows_dir, exist_ok=True)

    _current_log_file = os.path.join(windows_dir, f"window_{window_name}.log")


def log(text: str) -> None:
    """Log message to console and file"""
    global _current_log_file, _current_master_log

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

        # Check if log rotation is needed
        rotated_path = _rotate_logs_if_needed()
        master_log_path = rotated_path if rotated_path else _current_master_log

        try:
            # Always log to master log file
            with open(master_log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

            # Also log to window-specific file if it's different
            if _current_log_file != master_log_path:
                try:
                    with open(_current_log_file, "a", encoding="utf-8") as f:
                        f.write(line + "\n")
                except Exception:
                    pass
        except Exception:
            pass
    except Exception:
        # Final safety net to prevent recursive logging failures from crashing bot
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
