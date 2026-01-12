"""Main bot loop"""

import time
import os
import sys
import fcntl
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
    ENABLE_STOP_LOSS,
    STOP_LOSS_PERCENT,
    ENABLE_TAKE_PROFIT,
    TAKE_PROFIT_PERCENT,
    ENABLE_REVERSAL,
    ENABLE_HEDGED_REVERSAL,
    LOSING_SIDE_MIN_CONFIDENCE,
    STOP_LOSS_PRICE,
    ENABLE_SCALE_IN,
    SCALE_IN_MIN_PRICE,
    SCALE_IN_MAX_PRICE,
    SCALE_IN_TIME_LEFT,
    SCALE_IN_MULTIPLIER,
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
    get_exit_plan_stats,
    recover_open_positions,
    sync_positions_with_exchange,
    sync_with_exchange,
    execute_first_entry,
)
from src.utils.notifications import process_notifications, init_ws_callbacks
from src.trading.settlement import check_and_settle_trades
from src.utils.websocket_manager import ws_manager


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
    """Execute trading logic for multiple symbols using batch orders"""
    market_tokens = {}
    all_token_ids = []

    for symbol in symbols:
        up_id, down_id = get_token_ids(symbol)
        if up_id and down_id:
            market_tokens[symbol] = (up_id, down_id)
            all_token_ids.extend([up_id, down_id])

    if all_token_ids:
        ws_manager.subscribe_to_prices(all_token_ids)

    spreads = get_bulk_spreads(all_token_ids)

    valid_symbols = []
    skipped_due_to_empty_book = 0
    for symbol in symbols:
        if symbol not in market_tokens:
            continue

        up_id, down_id = market_tokens[symbol]
        up_spread = spreads.get(str(up_id))
        if up_spread is None:
            up_spread = get_spread(up_id)

        down_spread = spreads.get(str(down_id))
        if down_spread is None:
            down_spread = get_spread(down_id)

        up_spread = float(up_spread) if up_spread is not None else 0.0
        down_spread = float(down_spread) if down_spread is not None else 0.0

        if up_spread >= 1.0 or down_spread >= 1.0:
            if verbose:
                log(
                    f"[{symbol}] ⏳ No liquidity yet (Spread: 1.0). Waiting for market makers..."
                )
            skipped_due_to_empty_book += 1
            continue

        if up_spread > MAX_SPREAD or down_spread > MAX_SPREAD:
            if verbose:
                log(
                    f"[{symbol}] ⚠️  Spread too wide (UP: {up_spread:.3f}, DOWN: {down_spread:.3f}). SKIPPING."
                )
            continue
        valid_symbols.append(symbol)

    if not valid_symbols:
        if skipped_due_to_empty_book > 0 and skipped_due_to_empty_book == len(
            market_tokens
        ):
            return -1
        return 0

    trade_params_list = []
    last_symbol_logged = False
    for i, symbol in enumerate(valid_symbols):
        if last_symbol_logged and verbose:
            log("")
            last_symbol_logged = False

        params = _prepare_trade_params(
            symbol, balance, add_spacing=False, verbose=verbose
        )
        if params:
            trade_params_list.append(params)
            last_symbol_logged = True
        elif verbose:
            pass

    if not trade_params_list:
        return 0

    orders = [
        {
            "token_id": p["token_id"],
            "price": p["price"],
            "size": p["size"],
            "side": BUY,
        }
        for p in trade_params_list
    ]

    results = place_batch_orders(orders)

    placed_count = 0
    from src.data.db_connection import db_connection

    for i, result in enumerate(results):
        if i < len(trade_params_list) and result["success"]:
            placed_count += 1
            p = trade_params_list[i]

            actual_size = p["size"]
            actual_price = p["price"]
            actual_status = result["status"]
            is_reversal = "HEDGED REVERSAL" in str(p.get("core_summary", ""))

            if actual_status.upper() in ["FILLED", "MATCHED"]:
                try:
                    o_data = get_order(result["order_id"])
                    if o_data:
                        sz_m = float(o_data.get("size_matched", 0))
                        pr_m = float(o_data.get("price", 0))
                        if sz_m > 0:
                            actual_size = sz_m
                            if pr_m > 0:
                                actual_price = pr_m
                            p["bet_usd"] = actual_size * actual_price
                except:
                    pass

            send_discord(
                f"**{'🔄 ' if is_reversal else ''}[{p['symbol']}] {p['side']} ${p['bet_usd']:.2f}** | Confidence {p['confidence']:.1%} | Price {actual_price:.4f}"
            )
            try:
                trade_id = save_trade(
                    symbol=p["symbol"],
                    window_start=p["window_start"].isoformat(),
                    window_end=p["window_end"].isoformat(),
                    slug=p["slug"],
                    token_id=p["token_id"],
                    side=p["side"],
                    edge=p["confidence"],
                    price=actual_price,
                    size=actual_size,
                    bet_usd=p["bet_usd"],
                    p_yes=p["p_up"],
                    best_bid=p["best_bid"],
                    best_ask=p["best_ask"],
                    imbalance=p["imbalance"],
                    funding_bias=p["funding_bias"],
                    order_status=actual_status,
                    order_id=result["order_id"],
                    limit_sell_order_id=None,
                    is_reversal=is_reversal,
                    target_price=p["target_price"],
                    up_total=p["raw_scores"].get("up_total"),
                    down_total=p["raw_scores"].get("down_total"),
                    momentum_score=p["raw_scores"].get("momentum_score"),
                    momentum_dir=p["raw_scores"].get("momentum_dir"),
                    flow_score=p["raw_scores"].get("flow_score"),
                    flow_dir=p["raw_scores"].get("flow_dir"),
                    divergence_score=p["raw_scores"].get("divergence_score"),
                    divergence_dir=p["raw_scores"].get("divergence_dir"),
                    vwm_score=p["raw_scores"].get("vwm_score"),
                    vwm_dir=p["raw_scores"].get("vwm_dir"),
                    pm_mom_score=p["raw_scores"].get("pm_mom_score"),
                    pm_mom_dir=p["raw_scores"].get("pm_mom_dir"),
                    adx_score=p["raw_scores"].get("adx_score"),
                    adx_dir=p["raw_scores"].get("adx_dir"),
                    lead_lag_bonus=p["raw_scores"].get("lead_lag_bonus"),
                )

                emoji = p.get("emoji", "🚀")
                entry_type = p.get("entry_type", "Trade")
                log(
                    f"{emoji} [{p['symbol']}] {entry_type}: {p.get('core_summary', '')} | #{trade_id} {p['side']} ${p['bet_usd']:.2f} @ {actual_price:.4f} | ID: {result['order_id'][:10] if result['order_id'] else 'N/A'}"
                )

                if is_reversal:
                    with db_connection() as conn:
                        c = conn.cursor()
                        now_iso = datetime.now(tz=ZoneInfo("UTC")).isoformat()
                        c.execute(
                            "UPDATE trades SET reversal_triggered = 1, reversal_triggered_at = ? WHERE symbol = ? AND window_start = ? AND side != ? AND settled = 0",
                            (
                                now_iso,
                                p["symbol"],
                                p["window_start"].isoformat(),
                                p["side"],
                            ),
                        )
                        if c.rowcount > 0:
                            log(
                                f"   🛡️  [{p['symbol']}] Original trade marked as reversal_triggered"
                            )
            except Exception as e:
                log_error(f"[{p['symbol']}] Trade completion error: {e}")
        elif i < len(trade_params_list):
            p = trade_params_list[i]
            log_error(
                f"[{p['symbol']}] ❌ Batch order failed: {result.get('error')}",
                include_traceback=False,
            )
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

    log(
        f"📊 ADX System: {'INTEGRATED' if ADX_ENABLED else 'DISABLED'} (period={ADX_PERIOD}, interval={ADX_INTERVAL})"
    )

    ws_manager.start()
    init_ws_callbacks()

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
    log(f"⚙️  STOP LOSS: Midpoint <= ${STOP_LOSS_PRICE:.2f}")
    log("=" * 90)

    recover_open_positions()
    sync_with_exchange(addr)

    log("🔍 Performing initial position check...")
    check_open_positions(user_address=addr, verbose=True, check_orders=True)

    last_position_check = time.time()
    last_order_check = time.time()
    last_verbose_log = time.time()
    last_exit_stats_log = time.time()
    last_entry_check = 0
    last_settle_check = time.time()

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

            is_verbose_cycle = now_ts - last_verbose_log >= 60
            is_order_check_cycle = now_ts - last_order_check >= 10

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
                    if current_balance < 1.0:
                        if is_verbose_cycle:
                            log(
                                f"💰 Balance too low ({current_balance:.2f} USDC). Skipping trade evaluation."
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

            if is_verbose_cycle:
                last_verbose_log = now_ts
                if now_ts - last_exit_stats_log >= 900:
                    exit_stats = get_exit_plan_stats()
                    last_exit_stats_log = now_ts
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
