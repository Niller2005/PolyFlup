"""Main bot loop"""

import time
import os
import sys
import fcntl
import subprocess
from typing import Optional, List, Tuple
from datetime import datetime
from zoneinfo import ZoneInfo
from eth_account import Account
from src.config.settings import (
    MARKETS,
    PROXY_PK,
    FUNDER_PROXY,
    MIN_EDGE,
    CONTRARIAN_THRESHOLD,
    MAX_SPREAD,
    WINDOW_DELAY_SEC,
    MAX_ENTRY_LATENESS_SEC,
    ADX_ENABLED,
    ADX_PERIOD,
    ADX_INTERVAL,
    BET_PERCENT,
    CONFIDENCE_SCALING_FACTOR,
    ENABLE_HEDGED_REVERSAL,
    ENABLE_MOMENTUM_FILTER,
    ENABLE_ORDER_FLOW,
    ENABLE_DIVERGENCE,
    ENABLE_VWM,
    ENABLE_BFXD,
)

from src.utils.logger import log, log_error, send_discord, set_log_window
from src.utils.web3_utils import get_balance
from src.data.database import (
    init_database,
    save_trade,
    generate_statistics,
    get_total_exposure,
    has_trade_for_window,
    has_side_for_window,
)
from src.data.market_data import (
    get_token_ids,
    get_current_slug,
    get_window_times,
    format_window_range,
    get_funding_bias,
    get_window_start_price,
    get_current_spot_price,
)
from src.trading import (
    calculate_confidence,
    bfxd_allows_trade,
    execute_trade,
    _determine_trade_side,
    _calculate_bet_size,
    _prepare_trade_params,
    log_skipped_symbols_summary,
)


from src.trading.orders import (
    setup_api_creds,
    place_order,
    place_batch_orders,
    get_clob_client,
    get_bulk_spreads,
    get_spread,
    get_order,
    check_liquidity,
    BUY,
    SELL,
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
    """Get current git commit hash and branch for debugging"""
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


def trade_symbol(symbol: str, balance: float, verbose: bool = True) -> int:
    """Execute trading logic for a symbol"""
    trade_id = execute_first_entry(symbol, balance, verbose=verbose)

    if trade_id:
        # Check if this was a hedged reversal and mark the original trade
        from src.data.db_connection import db_connection

        with db_connection() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT is_reversal, side, window_start FROM trades WHERE id = ?",
                (trade_id,),
            )
            row = c.fetchone()
            if row and row[0]:
                is_reversal = row[0]
                side = row[1]
                window_start = row[2]

                now_iso = datetime.now(tz=ZoneInfo("UTC")).isoformat()
                c.execute(
                    "UPDATE trades SET reversal_triggered = 1, reversal_triggered_at = ? WHERE symbol = ? AND window_start = ? AND side != ? AND settled = 0",
                    (
                        now_iso,
                        symbol,
                        window_start,
                        side,
                    ),
                )
                if c.rowcount > 0:
                    log(f"   🛡️  [{symbol}] Original trade marked as reversal_triggered")

    return 1 if trade_id else 0


def trade_symbols_batch(symbols: list, balance: float, verbose: bool = True) -> int:
    """
    Execute trading logic for multiple symbols using ATOMIC entry+hedge for each.

    Each symbol uses BET_PERCENT of the INITIAL balance (not remaining balance).
    Example: $100 balance, BET_PERCENT=20%, 4 symbols
      → Each symbol gets $20 allocation (20 shares)
      → Total needed: $80 for all 4 symbols

    Symbols are processed sequentially. If balance drops below the fixed allocation
    needed for a symbol, that symbol and all remaining symbols are skipped.
    """
    placed_count = 0

    # Calculate FIXED allocation per symbol based on INITIAL balance
    # This is the same for all symbols, regardless of how many have traded
    fixed_allocation_per_symbol = balance * (BET_PERCENT / 100.0)
    approximate_cost_per_symbol = (
        fixed_allocation_per_symbol * 1.0
    )  # Worst case: $1.00 combined price

    log(
        f"📋 Batch processing {len(symbols)} symbols sequentially | Fixed allocation per symbol: ${fixed_allocation_per_symbol:.2f}"
    )

    for idx, symbol in enumerate(symbols, 1):
        # Check current balance before attempting trade
        from src.trading.orders import get_balance_allowance

        bal_info = get_balance_allowance()
        current_balance = bal_info.get("balance", 0) if bal_info else 0

        # Check if we have enough for the FIXED allocation
        if current_balance < approximate_cost_per_symbol:
            log(
                f"⏭️  [{symbol}] ({idx}/{len(symbols)}) SKIPPED: Insufficient balance (${current_balance:.2f} < ${approximate_cost_per_symbol:.2f} needed)"
            )
            log(
                f"   ⚠️  Skipping remaining {len(symbols) - idx + 1} symbol(s) - balance too low for fixed ${fixed_allocation_per_symbol:.2f} allocation"
            )
            break  # Skip this and all remaining symbols

        log(
            f"🎯 [{symbol}] ({idx}/{len(symbols)}) Processing with ${fixed_allocation_per_symbol:.2f} allocation (balance: ${current_balance:.2f})"
        )

        # Pass INITIAL balance so _calculate_bet_size() uses the fixed allocation
        # This ensures all symbols use BET_PERCENT of the initial balance, not remaining
        trade_id = execute_first_entry(symbol, balance, verbose=verbose)

        if trade_id:
            placed_count += 1
            log(
                f"✅ [{symbol}] ({idx}/{len(symbols)}) Trade #{trade_id} placed successfully"
            )
        else:
            log(
                f"⏭️  [{symbol}] ({idx}/{len(symbols)}) No trade placed (filters/conditions not met)"
            )

        # Small delay between trades to avoid rate limits
        if symbol != symbols[-1]:
            import time

            time.sleep(0.5)

    log(f"📊 Batch complete: {placed_count}/{len(symbols)} trades placed")
    return placed_count


def main():
    """Main bot loop"""
    # PID lock to prevent duplicate processes
    lock_file = "/tmp/polyflup.lock"
    try:
        f = open(lock_file, "w")
        fcntl.lockf(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        print("❌ Another instance of PolyFlup is already running. Exiting.")
        sys.exit(1)

    setup_api_creds()
    init_database()

    # Get git commit info for debugging
    git_info = get_git_commit_info()

    # Set initial log window if markets are defined
    if MARKETS:
        try:
            w_start, w_end = get_window_times(MARKETS[0])
            set_log_window(w_start.isoformat())
            range_str = format_window_range(w_start, w_end)
            log(f"🚀 Starting PolyFlup Trading Bot | Window: {range_str}")
        except Exception as e:
            log(f"🚀 Starting PolyFlup Trading Bot (Modular Version)...")
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
        f"🤖 POLYFLUP | Wallet: {addr[:10]}...{addr[-8:]} | Balance: {get_balance(addr):.2f} USDC"
    )
    log(f"⚙️  MIN_EDGE: {MIN_EDGE:.1%} | BET: {BET_PERCENT}%")
    log(f"⚙️  HEDGED REVERSAL: {'ENABLED' if ENABLE_HEDGED_REVERSAL else 'DISABLED'}")
    log("=" * 90)

    recover_open_positions()
    sync_with_exchange(addr)

    log("🔍 Performing initial position check...")
    check_open_positions(user_address=addr, verbose=True, check_orders=True)

    # Launch background task to redeem old trades
    from src.trading.background_redemption import start_background_redemption

    start_background_redemption()

    # Launch background task to monitor pre-settlement exit opportunities
    from src.trading.pre_settlement_exit import start_pre_settlement_monitor

    start_pre_settlement_monitor()

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
            last_window_logged, _ = get_window_times(MARKETS[0])
        except:
            pass

    while True:
        try:
            now_ts = time.time()
            now_et = datetime.now(tz=ZoneInfo("America/New_York"))

            # Log new window start
            if MARKETS:
                w_start, w_end = get_window_times(MARKETS[0])
                if last_window_logged != w_start:
                    range_str = format_window_range(w_start, w_end)
                    # Update logger to use a new file for this window
                    set_log_window(w_start.isoformat())
                    log("")
                    log(f"🪟  NEW WINDOW: {range_str}")
                    last_window_logged = w_start

                    # Auto-redeem winning tokens from recently settled trades
                    # Run ONLY at window boundaries (every 15 minutes) to avoid spam
                    from src.trading.settlement import redeem_recent_settled_trades

                    redeem_recent_settled_trades()

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

                    if 0 <= lateness <= MAX_ENTRY_LATENESS_SEC:
                        if ENABLE_HEDGED_REVERSAL or not has_trade_for_window(
                            m, w_start_et.isoformat()
                        ):
                            eligible_markets.append(m)

                if eligible_markets:
                    current_balance = get_balance(addr)
                    if current_balance < 5.0:
                        if is_verbose_cycle:
                            log(
                                f"💰 Balance too low ({current_balance:.2f} USDC). Minimum $5.00 required. Skipping trade evaluation."
                            )
                    else:
                        if len(eligible_markets) > 1:
                            trade_symbols_batch(
                                eligible_markets,
                                current_balance,
                                verbose=is_verbose_cycle,
                            )
                        else:
                            trade_symbol(
                                eligible_markets[0],
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
        except Exception as e:
            log_error(f"Critical error: {e}")
            send_discord(f"❌ Error: {e}")
            time.sleep(60)


if __name__ == "__main__":
    main()
