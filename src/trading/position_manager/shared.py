import threading

# Threading lock to prevent concurrent position checks (prevents database locks)
_position_check_lock = threading.Lock()

# Module-level tracking for exit plan attempts to prevent spamming on errors (e.g. balance errors)
_last_exit_attempt = {}
