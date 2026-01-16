"""Core position monitoring loop with comprehensive audit trail"""

import time
from datetime import datetime
from zoneinfo import ZoneInfo
from src.data.db_connection import db_connection
from src.utils.logger import log, log_error
from src.config.settings import EXIT_PRICE_TARGET
from src.trading.orders import (
    get_order_status,
    cancel_order,
    get_order,
    get_multiple_market_prices,
    check_orders_scoring,
    get_balance_allowance,
)
from src.utils.websocket_manager import ws_manager
from src.trading.settlement import force_settle_trade
from src.trading.logic import MIN_SIZE
from .shared import _position_check_lock
from .shared import _scale_in_order_lock
from .shared import _recent_fills
from .reconciliation import safe_cancel_order, is_recently_filled, track_recent_fill
from .pnl import _get_position_pnl
from .stop_loss import _check_stop_loss
from .scale import _check_scale_in
from .exit import _check_exit_plan

_failed_pnl_checks = {}


def check_open_positions(verbose=True, check_orders=False, user_address=None):
    if not _position_check_lock.acquire(blocking=False):
        return
    global _failed_pnl_checks
    try:
        with db_connection() as conn:
            c = conn.cursor()
            now = datetime.now(tz=ZoneInfo("UTC"))
            c.execute(
                "SELECT id, symbol, slug, token_id, side, entry_price, size, bet_usd, window_end, scaled_in, is_reversal, target_price, limit_sell_order_id, order_id, order_status, timestamp, scale_in_order_id, reversal_triggered, reversal_triggered_at, edge, last_scale_in_at, is_hedged FROM trades WHERE settled = 0 AND exited_early = 0 AND datetime(window_end) > datetime(?)",
                (now.isoformat(),),
            )
            open_positions = c.fetchall()
            if not open_positions:
                if verbose:
                    log("💤 No open positions. Monitoring markets...")
                return

            # PRIORITY 1: Batch price fetching
            token_ids = list(set([str(p[3]) for p in open_positions if p[3]]))

            # Try to get prices from WS cache first
            cached_prices = {}
            missing_tokens = []
            for tid in token_ids:
                price = ws_manager.get_price(tid)
                if price:
                    cached_prices[tid] = price
                else:
                    missing_tokens.append(tid)

            # Fetch missing prices in batch
            if missing_tokens:
                batch_prices = get_multiple_market_prices(missing_tokens)
                cached_prices.update(batch_prices)

            # PRIORITY 2: Batch reward scoring check
            sell_order_ids = [p[12] for p in open_positions if p[12]]
            scoring_map = {}
            if sell_order_ids and (verbose or check_orders):
                try:
                    scoring_map = check_orders_scoring(sell_order_ids)
                except Exception as e:
                    log(f"⚠️  Error in batch scoring check: {e}")

            if verbose:
                bal_info = get_balance_allowance()
                usdc_balance = bal_info.get("balance", 0) if bal_info else 0
                log(
                    f"👀 Monitoring {len(open_positions)} positions... | 💰 USDC: ${usdc_balance:.2f}"
                )
                if usdc_balance < 10.0:
                    log("   ⚠️  WARNING: USDC balance below $10. Scale-ins may fail!")

                # Detailed position report every minute - each position on its own line with full details
                if len(open_positions) > 0:
                    # Group positions by symbol and side for clean display
                    positions_by_symbol = {}
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
                            sc_in,
                            rev,
                            target,
                            l_sell,
                            b_id,
                            b_status,
                            ts,
                            sc_id,
                            rev_trig,
                            rev_trig_at,
                            edge,
                            last_sc_at,
                            is_hedged,
                        ) = pos

                        if sym not in positions_by_symbol:
                            positions_by_symbol[sym] = {
                                "UP": {"filled": [], "waiting": []},
                                "DOWN": {"filled": [], "waiting": []},
                            }

                        if b_status in ["FILLED", "MATCHED"]:
                            # Filled/Matched positions with PnL
                            pnl_pct = _get_position_pnl(tok, entry, size)

                            # Build aligned position details with trade ID, scaled-in and exit plan status
                            # Use fixed width formatting for consistent alignment
                            pnl_result = _get_position_pnl(tok, entry, size)
                            if (
                                pnl_result
                                and isinstance(pnl_result, dict)
                                and "pnl_pct" in pnl_result
                            ):
                                pnl_pct_val = pnl_result["pnl_pct"]
                                position_details = f"#{str(tid):<6} 📦{size:>5.1f} 🧮{pnl_pct_val:+6.1f}%"
                            else:
                                # Fallback if PnL calculation fails
                                position_details = (
                                    f"#{str(tid):<6} 📦{size:>5.1f} 🧮  +0.0%"
                                )

                        elif b_status in ["LIVE", "OPEN", "PENDING"] and b_id:
                            # Waiting for fill positions with trade ID - aligned format
                            waiting_details = (
                                f"#{str(tid):<6} 📦{size:>5.1f} | ⏳ Waiting for fill"
                            )
                            positions_by_symbol[sym][side]["waiting"].append(
                                waiting_details
                            )

                # Clean position report with status indicators - matches desired format
                if len(open_positions) > 0:
                    for pos in open_positions[:10]:  # Show max 10 positions
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
                            sc_in,
                            rev,
                            target,
                            l_sell,
                            b_id,
                            b_status,
                            ts,
                            sc_id,
                            rev_trig,
                            rev_trig_at,
                            edge,
                            last_sc_at,
                            is_hedged,
                        ) = pos

                        if b_status in ["FILLED", "MATCHED"]:
                            # Get PnL data
                            pnl_result = _get_position_pnl(tok, entry, size)
                            if (
                                pnl_result
                                and isinstance(pnl_result, dict)
                                and "pnl_pct" in pnl_result
                            ):
                                pnl_pct_val = pnl_result["pnl_pct"]
                                # Build status indicators
                                status_parts = []
                                if is_hedged:
                                    status_parts.append("🛡️ Hedged")
                                if sc_in:
                                    status_parts.append("📊 Scaled in")
                                if l_sell:
                                    status_parts.append("⏰ Exit active")
                                else:
                                    # Check if position is below MIN_SIZE threshold
                                    if size < MIN_SIZE:
                                        status_parts.append("⏭️ Exit skipped")
                                    elif not is_hedged:
                                        # Only show "Exit pending" if not hedged (hedged positions don't need exit plan)
                                        status_parts.append("⏳ Exit pending")

                                status_str = (
                                    " | ".join(status_parts) if status_parts else ""
                                )
                                # Add directional emoji based on side and PnL
                                direction_emoji = (
                                    "📈"
                                    if (side == "UP" and pnl_pct_val >= 0)
                                    or (side == "DOWN" and pnl_pct_val <= 0)
                                    else "📉"
                                )
                                position_line = f"  {direction_emoji} [{sym}] {side:<4} #{str(tid):<6}  📦{size:>5.1f}  🧮{pnl_pct_val:+6.1f}%"
                                if status_str:
                                    position_line += f" | {status_str}"
                                log(position_line)
                            else:
                                # Fallback if PnL calculation fails - use directional emoji
                                direction_emoji = "📈" if side == "UP" else "📉"
                                log(
                                    f"  {direction_emoji} [{sym}] {side:<4} #{str(tid):<6}  📦{size:>5.1f}  🧮  +0.0%"
                                )

                        elif b_status.upper() in ["LIVE", "OPEN", "PENDING"] and b_id:
                            # Waiting for fill positions - clean format with just ⏳ emoji
                            log(
                                f"  ⏳ [{sym}] {side:<4} #{str(tid):<6}  📦{size:>5.1f}"
                            )
            for (
                tid,
                sym,
                slug,
                tok,
                side,
                entry,
                size,
                bet,
                w_end,
                sc_in,
                rev,
                target,
                l_sell,
                b_id,
                b_status,
                ts,
                sc_id,
                rev_trig,
                rev_trig_at,
                edge,
                last_sc_at,
                is_hed,
            ) in open_positions:
                bet = bet or 0.0
                edge = edge or 0.0
                try:
                    c.execute("SELECT settled FROM trades WHERE id = ?", (tid,))
                    chk_res = c.fetchone()
                    if chk_res and chk_res[0] == 1:
                        continue
                    curr_b_status = b_status
                    if b_id and (b_status != "FILLED" or entry == 0):
                        curr_b_status = get_order_status(b_id)
                        if curr_b_status in ["FILLED", "MATCHED"]:
                            o_data = get_order(b_id)
                            if o_data:
                                sz = float(o_data.get("size_matched", size))
                                pr = float(o_data.get("price", entry))
                                if (abs(sz - size) > 0.001 and sz > 0) or abs(
                                    pr - entry
                                ) > 0.0001:
                                    size, entry, bet = sz, pr, sz * pr
                                    c.execute(
                                        "UPDATE trades SET size = ?, entry_price = ?, bet_usd = ? WHERE id = ?",
                                        (size, entry, bet, tid),
                                    )
                            c.execute(
                                "UPDATE trades SET order_status = 'FILLED' WHERE id = ?",
                                (tid,),
                            )
                            curr_b_status = "FILLED"
                            # Track recent fill for balance API cooldown
                            _recent_fills[tid] = now.timestamp()

                    if curr_b_status not in ["FILLED", "MATCHED"]:
                        continue

                    if (
                        check_orders or b_status == "EXIT_PLAN_PENDING_SETTLEMENT"
                    ) and l_sell:
                        o_data = get_order(l_sell)
                        if o_data:
                            o_status = o_data.get("status", "").upper()
                            if (
                                o_status in ["FILLED", "MATCHED"]
                                or b_status == "EXIT_PLAN_PENDING_SETTLEMENT"
                            ):
                                ex_p = float(o_data.get("price", EXIT_PRICE_TARGET))
                                sz_m = float(o_data.get("size_matched", size))
                                if sz_m == 0:
                                    sz_m = size  # Fallback if size_matched is missing
                                pnl_val_f = (ex_p * sz_m) - bet
                                roi_val_f = (pnl_val_f / bet) * 100 if bet > 0 else 0
                                log(
                                    f"💰 [{sym}] #{tid} {side} EXIT SUCCESS: MATCHED at {ex_p}! (size: {sz_m:.2f}) | {pnl_val_f:+.2f}$ ({roi_val_f:+.1f}%)"
                                )
                                # CANCEL ANY PENDING SCALE-IN ORDER - WITH COMPREHENSIVE AUDIT TRAIL
                                if sc_id:
                                    # AUDIT: Starting scale-in cancellation process
                                    log(
                                        f"   🧹 [{sym}] #{tid} EXIT AUDIT: Starting scale-in order cancellation process for order {sc_id[:10]}"
                                    )

                                    # CRITICAL FIX: Check if scale-in order is actually unfilled before cancelling
                                    sc_status = get_order_status(sc_id)

                                    # AUDIT: Scale-in order status check
                                    log(
                                        f"   🔍 [{sym}] #{tid} EXIT AUDIT: Scale-in order {sc_id[:10]} status: {sc_status}"
                                    )

                                    if sc_status not in ["FILLED", "MATCHED"]:
                                        # Check for race condition - track this as a recent fill attempt
                                        if is_recently_filled(sc_id):
                                            # AUDIT: Race condition detected - order was recently filled
                                            fill_data = get_order(sc_id)
                                            fill_size = (
                                                fill_data.get("size_matched", 0)
                                                if fill_data
                                                else "unknown"
                                            )
                                            log(
                                                f"   ⚠️  [{sym}] #{tid} EXIT AUDIT: RACE CONDITION DETECTED! Order {sc_id[:10]} was recently filled (size: {fill_size}), skipping cancellation"
                                            )
                                        else:
                                            # AUDIT: Safe to cancel - order confirmed unfilled
                                            log(
                                                f"   🧹 [{sym}] #{tid} EXIT AUDIT: Confirmed scale-in order {sc_id[:10]} unfilled (Status: {sc_status}), proceeding with cancellation"
                                            )
                                            cancel_result = cancel_order(sc_id)
                                            if cancel_result:
                                                # AUDIT: Scale-in cancellation successful
                                                log(
                                                    f"   ✅ [{sym}] #{tid} EXIT AUDIT: Successfully cancelled scale-in order {sc_id[:10]}"
                                                )
                                            else:
                                                # AUDIT: Scale-in cancellation failed
                                                log(
                                                    f"   ❌ [{sym}] #{tid} EXIT AUDIT: Failed to cancel scale-in order {sc_id[:10]}"
                                                )
                                    else:
                                        # AUDIT: Scale-in order already filled, update database
                                        log(
                                            f"   ✅ [{sym}] #{tid} EXIT AUDIT: Scale-in order {sc_id[:10]} already filled (Status: {sc_status}), updating database and skipping cancellation"
                                        )
                                        # Track this fill to prevent future race conditions
                                        track_recent_fill(sc_id)

                                c.execute(
                                    "UPDATE trades SET order_status = 'EXIT_PLAN_FILLED', settled=1, exited_early=1, exit_price=?, pnl_usd=?, roi_pct=?, settled_at=?, scale_in_order_id=NULL WHERE id=?",
                                    (ex_p, pnl_val_f, roi_val_f, now.isoformat(), tid),
                                )
                                continue
                        else:
                            pass

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
                    cur_p, p_pct_val, p_usd_val, p_chg_val = (
                        pnl_i["current_price"],
                        pnl_i["pnl_pct"],
                        pnl_i["pnl_usd"],
                        pnl_i["price_change_pct"],
                    )
                    if _check_stop_loss(
                        user_address,
                        sym,
                        tid,
                        tok,
                        side,
                        entry,
                        size,
                        p_pct_val,
                        p_usd_val,
                        cur_p,
                        target,
                        l_sell,
                        rev,
                        c,
                        conn,
                        now,
                        curr_b_status,
                        sc_id,
                        rev_trig,
                        rev_trig_at,
                        w_end,
                        is_hedged=is_hed,
                    ):
                        continue

                    try:
                        w_dt = (
                            datetime.fromisoformat(w_end)
                            if isinstance(w_end, str)
                            else w_end
                        )
                        t_left = (w_dt - now).total_seconds()
                    except:
                        t_left = 0

                    _check_scale_in(
                        sym,
                        tid,
                        tok,
                        entry,
                        size,
                        bet,
                        sc_in,
                        sc_id,
                        t_left,
                        cur_p,
                        check_orders,
                        c,
                        conn,
                        side,
                        p_chg_val,
                        curr_b_status,
                        confidence=edge,
                        target_price=target,
                        verbose=verbose,
                    )

                    # Refresh data after scale-in check
                    c.execute(
                        "SELECT size, entry_price, bet_usd, scaled_in, limit_sell_order_id, scale_in_order_id, last_scale_in_at FROM trades WHERE id = ?",
                        (tid,),
                    )
                    row_data_f = c.fetchone()
                    if row_data_f:
                        size, entry, bet, sc_in, l_sell, sc_id, last_sc_at = row_data_f

                    _check_exit_plan(
                        user_address,
                        sym,
                        tid,
                        tok,
                        size,
                        curr_b_status,
                        l_sell,
                        ts,
                        c,
                        conn,
                        now,
                        verbose,
                        side,
                        p_pct_val,
                        p_chg_val,
                        sc_in,
                        sc_id,
                        entry,
                        cur_p,
                        check_orders=check_orders,
                        is_scoring=scoring_map.get(l_sell) if l_sell else None,
                        last_scale_in_at=last_sc_at,
                    )
                except Exception as e:
                    log_error(f"[{sym}] #{tid} Position monitoring error: {e}")
    finally:
        _position_check_lock.release()
