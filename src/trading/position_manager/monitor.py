"""Simplified position monitoring - display only, no actions"""

import time
from datetime import datetime
from zoneinfo import ZoneInfo
from src.data.db_connection import db_connection
from src.utils.logger import log, log_error
from src.trading.orders import get_multiple_market_prices
from src.utils.websocket_manager import ws_manager
from src.trading.settlement import force_settle_trade
from .shared import _position_check_lock
from .pnl import _get_position_pnl

_failed_pnl_checks = {}
_last_no_positions_log = 0.0  # Rate-limit "No open positions" message
_NO_POSITIONS_LOG_COOLDOWN = 30  # Log once per 30 seconds

# Monitor health tracking
_last_monitor_check = time.time()
_last_health_warning = 0


def check_monitor_health():
    """
    Check if monitor is running properly and alert if stalled.
    Called from main loop to detect situations where monitor stops checking positions.
    """
    global _last_monitor_check, _last_health_warning

    now = time.time()
    time_since_last_check = now - _last_monitor_check

    # If monitor hasn't run in 5 seconds, something is wrong
    if time_since_last_check > 5:
        # Only log warning every 30 seconds to avoid spam
        if now - _last_health_warning > 30:
            log_error(
                f"⚠️  MONITOR HEALTH WARNING: Position check hasn't run in {time_since_last_check:.1f}s (expected: 1s)"
            )
            _last_health_warning = now
        return False

    return True


def check_open_positions(verbose=True, check_orders=False, user_address=None):
    """
    Monitor and display open positions (DISPLAY ONLY - NO ACTIONS)

    Simplified to only:
    - Fetch open positions
    - Display PnL
    - Force settle if price unavailable for 3 cycles
    """
    global _last_monitor_check
    _last_monitor_check = time.time()  # Update health tracking

    if not _position_check_lock.acquire(blocking=False):
        return

    global _failed_pnl_checks
    try:
        with db_connection() as conn:
            c = conn.cursor()
            now = datetime.now(tz=ZoneInfo("UTC"))

            # Fetch open positions
            c.execute(
                """SELECT id, symbol, slug, token_id, side, entry_price, size, bet_usd, window_end, 
                   is_hedged, order_status, timestamp 
                   FROM trades 
                   WHERE settled = 0 
                     AND exited_early = 0 
                     AND merge_tx_hash IS NULL 
                     AND datetime(window_end) > datetime(?)""",
                (now.isoformat(),),
            )
            open_positions = c.fetchall()

            if not open_positions:
                if verbose:
                    # Rate-limit this log message (once per 30 seconds)
                    global _last_no_positions_log
                    current_time = time.time()
                    if (
                        current_time - _last_no_positions_log
                        >= _NO_POSITIONS_LOG_COOLDOWN
                    ):
                        log("💤 No open positions. Monitoring markets...")
                        _last_no_positions_log = current_time
                return

            # Batch price fetching
            token_ids = list(set([str(p[3]) for p in open_positions if p[3]]))

            # Try to get prices from WS cache first
            cached_prices = {}
            missing_tokens = []
            for tid in token_ids:
                price = ws_manager.get_price(tid)
                if price is not None:
                    cached_prices[tid] = price
                else:
                    missing_tokens.append(tid)

            # Fetch missing prices from API
            if missing_tokens:
                try:
                    fetched_prices = get_multiple_market_prices(missing_tokens)
                    for tid, price in fetched_prices.items():
                        cached_prices[tid] = price
                except Exception as e:
                    log_error(f"Error fetching batch prices: {e}")

            # Display positions
            for pos in open_positions:
                (
                    tid,
                    sym,
                    slug,
                    tok,
                    side,
                    entry,
                    size,
                    bet,
                    w_end,
                    is_hed,
                    b_status,
                    ts,
                ) = pos

                # Skip if not filled
                if b_status not in ["FILLED", "MATCHED", "HEDGED"]:
                    continue

                # Get PnL info
                pnl_i = _get_position_pnl(tok, entry, size, cached_prices)
                if not pnl_i:
                    # Increment failure counter
                    _failed_pnl_checks[tid] = _failed_pnl_checks.get(tid, 0) + 1
                    if _failed_pnl_checks[tid] >= 3:
                        log(
                            f"🧟 [{sym}] #{tid} price unavailable for 3 cycles - attempting force settlement..."
                        )
                        force_settle_trade(tid)
                        _failed_pnl_checks[tid] = 0  # Reset
                    continue

                # Reset failure counter on success
                _failed_pnl_checks[tid] = 0

                cur_p = pnl_i["current_price"]
                p_pct_val = pnl_i["pnl_pct"]
                p_usd_val = pnl_i["pnl_usd"]
                p_chg_val = pnl_i["price_change_pct"]

                # Calculate time remaining
                try:
                    w_dt = (
                        datetime.fromisoformat(w_end)
                        if isinstance(w_end, str)
                        else w_end
                    )
                    t_left = (w_dt - now).total_seconds()
                    t_left_str = f"{int(t_left // 60)}m{int(t_left % 60)}s"
                except:
                    t_left = 0
                    t_left_str = "0s"

                # Display position (only log if verbose or significant change)
                if verbose:
                    hedge_status = "🛡️ HEDGED" if is_hed else "⚠️  UNHEDGED"
                    log(
                        f"📊 [{sym}] #{tid} {side} {size:.1f} @ ${entry:.2f} → ${cur_p:.2f} ({p_chg_val:+.1f}%) | "
                        f"PnL: {p_usd_val:+.2f}$ ({p_pct_val:+.1f}%) | {hedge_status} | {t_left_str} left"
                    )

    except Exception as e:
        log_error(f"Error in check_open_positions: {e}")
    finally:
        _position_check_lock.release()
