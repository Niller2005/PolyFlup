"""Main bot loop"""

import time
import os
import sys
import fcntl
import subprocess
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
from eth_account import Account
from src.config.settings import (
    MARKETS,
    PROXY_PK,
    FUNDER_PROXY,
    MIN_EDGE,
    PAPER_TRADING,
    PRIMARY_MARKET,
    max_entry_lateness,
    ADX_ENABLED,
    ADX_PERIOD,
    ADX_INTERVAL,
    BET_PERCENT,
    ENABLE_HEDGED_REVERSAL,
)

from src.utils.logger import log, log_error, send_discord, set_log_window
from src.utils.web3_utils import get_balance
from src.data.database import (
    init_database,
    generate_statistics,
    has_trade_for_window,
)
from src.data.market_data import (
    get_window_times,
    format_window_range,
)


from src.trading.orders import (
    setup_api_creds,
)
from src.trading.position_manager import (
    check_open_positions,
    check_monitor_health,
    recover_open_positions,
    sync_positions_with_exchange,
    sync_with_exchange,
    execute_first_entry,
)
from src.utils.notifications import process_notifications, init_ws_callbacks
from src.trading.settlement import check_and_settle_trades
from src.utils.websocket_manager import ws_manager


def get_git_commit_info() -> str:
    """Get current git commit hash and branch for debugging

    In Docker, reads from GIT_VERSION environment variable (set at build time).
    Outside Docker, runs git commands to get current commit info.
    """
    # Check if running in Docker with build-time version
    docker_version = os.environ.get("GIT_VERSION")
    if docker_version and docker_version != "unknown":
        return docker_version

    # Fall back to git commands (for local development)
    try:
        # Get commit hash (short)
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()

        # Get branch name
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()

        # Check if there are uncommitted changes
        status = subprocess.check_output(
            ["git", "status", "--porcelain"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()

        dirty = " (uncommitted changes)" if status else ""

        return f"{branch}@{commit}{dirty}"
    except Exception:
        return "unknown"


_entry_threads: dict = {}
_entry_lock = threading.Lock()


def _entry_worker(symbol: str, balance: float, verbose: bool) -> None:
    from src.trading import inflight

    inflight.mark(symbol)
    try:
        trade_id = execute_first_entry(symbol, balance, verbose=verbose)
        if trade_id:
            log(f"✅ [{symbol}] Trade #{trade_id} placed successfully")
        else:
            log(f"⏭️  [{symbol}] No trade placed (filters/conditions not met)")
    except Exception as e:
        log_error(f"[{symbol}] Entry worker failed: {e}")
    finally:
        inflight.clear(symbol)


def launch_entries(symbols: list, balance: float, verbose: bool = True) -> int:
    """
    Start one entry worker thread per market so a slow fill monitor (up to 90s on 15m,
    30s on 5m windows) never blocks position monitoring, notifications, settlement or
    the other markets.

    Each market uses BET_PERCENT of `balance` as a FIXED allocation. Funds reserved by
    entries still in flight are subtracted from the wallet balance before launching
    another one. A market with an entry already in flight is skipped.
    """
    from src.trading.orders import get_balance_allowance

    from src.config.settings import KELLY_MAX_FRACTION, KELLY_SIZING

    # Kelly stakes never exceed KELLY_MAX_FRACTION, so that is what an in-flight entry reserves
    allocation = balance * (KELLY_MAX_FRACTION if KELLY_SIZING else BET_PERCENT / 100.0)
    launched = 0
    for symbol in symbols:
        with _entry_lock:
            running = [s for s, t in _entry_threads.items() if t.is_alive()]
            if symbol in running:
                continue
            bal_info = get_balance_allowance()
            wallet = bal_info.get("balance", 0) if bal_info else 0
            available = wallet - allocation * len(running)
            if available < allocation:
                log(
                    f"⏭️  [{symbol}] SKIPPED: insufficient free balance (${available:.2f} < ${allocation:.2f}; {len(running)} entr{'y' if len(running) == 1 else 'ies'} in flight)"
                )
                break
            log(
                f"🎯 [{symbol}] Launching entry with ${allocation:.2f} allocation (free: ${available:.2f})"
            )
            thread = threading.Thread(
                target=_entry_worker,
                args=(symbol, balance, verbose),
                name=f"Entry-{symbol}",
                daemon=True,
            )
            _entry_threads[symbol] = thread
            thread.start()
            launched += 1
    return launched


def main():
    """Main bot loop"""
    # PID lock to prevent duplicate processes
    lock_file = "/tmp/polyflup_paper.lock" if PAPER_TRADING else "/tmp/polyflup.lock"
    try:
        f = open(lock_file, "w")
        fcntl.lockf(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        print("❌ Another instance of PolyFlup is already running. Exiting.")
        sys.exit(1)

    setup_api_creds()
    init_database()
    if PAPER_TRADING:
        from src import paper

        paper.start()

    # Get git commit info for debugging
    git_info = get_git_commit_info()

    # Set initial log window if markets are defined
    if MARKETS:
        try:
            w_start, w_end = get_window_times(PRIMARY_MARKET)
            set_log_window(w_start.isoformat())
            range_str = format_window_range(w_start, w_end)
            log(f"🚀 Starting PolyFlup Trading Bot | Window: {range_str}")
        except Exception as e:
            log("🚀 Starting PolyFlup Trading Bot (Modular Version)...")
            log_error(f"Error setting initial log window: {e}")
    else:
        log("🚀 Starting PolyFlup Trading Bot (Modular Version)...")

    log(f"🔧 Version: {git_info}")
    log(
        f"📊 ADX System: {'INTEGRATED' if ADX_ENABLED else 'DISABLED'} (period={ADX_PERIOD}, interval={ADX_INTERVAL})"
    )

    ws_manager.start()
    init_ws_callbacks()

    # Clear any stale notifications from previous runs
    try:
        from src.trading.orders import get_notifications, drop_notifications

        stale_notifs = get_notifications()
        if stale_notifs:
            stale_ids = [str(n.get("id")) for n in stale_notifs if n.get("id")]
            if stale_ids:
                drop_notifications(stale_ids)
                log(
                    f"🧹 Cleared {len(stale_ids)} stale notification(s) from previous session"
                )
    except Exception as e:
        log_error(f"Failed to clear stale notifications: {e}")

    if FUNDER_PROXY and FUNDER_PROXY.startswith("0x"):
        addr = FUNDER_PROXY
    else:
        addr = Account.from_key(PROXY_PK).address

    log("=" * 90)
    log(
        f"🤖 POLYFLUP{' [PAPER]' if PAPER_TRADING else ''} | Wallet: {addr[:10]}...{addr[-8:]} | Balance: {get_balance(addr):.2f} pUSD"
    )
    log(f"⚙️  MIN_EDGE: {MIN_EDGE:.1%} | BET: {BET_PERCENT}%")
    log(f"⚙️  HEDGED REVERSAL: {'ENABLED' if ENABLE_HEDGED_REVERSAL else 'DISABLED'}")
    log("=" * 90)

    recover_open_positions()
    sync_with_exchange(addr)

    log("🔍 Performing initial position check...")
    check_open_positions(user_address=addr, verbose=True, check_orders=True)

    # Launch background task to monitor pre-settlement exit opportunities
    from src.trading.pre_settlement_exit import start_pre_settlement_monitor

    start_pre_settlement_monitor()

    from src.trading.late_window import start_late_window_monitor

    start_late_window_monitor()

    last_position_check = time.time()
    last_order_check = time.time()
    last_verbose_log = time.time()
    last_entry_check = 0
    last_settle_check = time.time()
    last_position_sync = (
        time.time()
    )  # Track last time we synced positions with exchange

    log("🏁 Bot initialized. Entering continuous monitoring loop...")

    # Initialize with current window so we don't log a duplicate "NEW WINDOW" immediately
    last_window_logged = None
    if MARKETS:
        try:
            last_window_logged, _ = get_window_times(PRIMARY_MARKET)
        except Exception:
            # Silently ignore window time fetch errors on startup
            pass

    while True:
        try:
            now_ts = time.time()
            now_et = datetime.now(tz=ZoneInfo("America/New_York"))

            # Log new window start
            if MARKETS:
                w_start, w_end = get_window_times(PRIMARY_MARKET)
                if last_window_logged != w_start:
                    range_str = format_window_range(w_start, w_end)
                    # Update logger to use a new file for this window
                    set_log_window(w_start.isoformat())
                    log("")
                    log(f"🪟  NEW WINDOW: {range_str}")
                    last_window_logged = w_start

            is_verbose_cycle = now_ts - last_verbose_log >= 60
            is_order_check_cycle = now_ts - last_order_check >= 10

            # Health check: Verify monitor is running properly
            if now_ts % 30 < 1:  # Check every 30 seconds
                check_monitor_health()

            if now_ts - last_position_check >= 1:
                check_open_positions(
                    user_address=addr,
                    verbose=is_verbose_cycle,
                    check_orders=is_order_check_cycle,
                )
                last_position_check = now_ts

            if now_ts - last_entry_check >= 20:
                last_entry_check = now_ts
                current_balance = get_balance(addr)

                eligible_markets = []
                for m in MARKETS:
                    w_start_et, _ = get_window_times(m)
                    lateness = (now_et - w_start_et).total_seconds()

                    if 0 <= lateness <= max_entry_lateness(m):
                        if ENABLE_HEDGED_REVERSAL or not has_trade_for_window(
                            m, w_start_et.isoformat()
                        ):
                            eligible_markets.append(m)

                if eligible_markets:
                    current_balance = get_balance(addr)
                    if current_balance < 5.0:
                        if is_verbose_cycle:
                            log(
                                f"💰 Balance too low ({current_balance:.2f} pUSD). Minimum $5.00 required. Skipping trade evaluation."
                            )
                    else:
                        launch_entries(
                            eligible_markets,
                            current_balance,
                            verbose=is_verbose_cycle,
                        )

            if is_order_check_cycle:
                process_notifications()
                last_order_check = now_ts

            if now_ts - last_settle_check >= 60:
                check_and_settle_trades()
                last_settle_check = now_ts

            # Periodic position sync to detect external merges/closes
            if now_ts - last_position_sync >= 60:
                sync_positions_with_exchange(addr)
                last_position_sync = now_ts

            if is_verbose_cycle:
                last_verbose_log = now_ts
                if int(now_ts) % 14400 < 60:
                    generate_statistics()

            time.sleep(0.5)

        except KeyboardInterrupt:
            log("\n⛔ Bot stopped by user")
            generate_statistics()
            break
        except (
            ConnectionError,
            TimeoutError,
            OSError,
        ) as e:
            # Recoverable network/connection errors - retry after delay
            log_error(f"Recoverable error: {e}")
            send_discord(f"⚠️  Warning: {e} (will retry)")
            time.sleep(60)
        except Exception as e:
            # Unexpected critical error - log and crash for visibility
            log_error(f"FATAL ERROR: {e}")
            send_discord(f"🚨 FATAL: {e} - Bot crashed!")
            generate_statistics()
            raise  # Re-raise to crash the bot


if __name__ == "__main__":
    main()
