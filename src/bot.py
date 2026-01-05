"""Main bot loop"""

import time
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
    ENABLE_STOP_LOSS,
    STOP_LOSS_PERCENT,
    ENABLE_TAKE_PROFIT,
    TAKE_PROFIT_PERCENT,
    ENABLE_REVERSAL,
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

from src.utils.logger import log, send_discord
from src.utils.web3_utils import get_balance
from src.data.database import (
    init_database,
    save_trade,
    generate_statistics,
    get_total_exposure,
    has_trade_for_window,
)
from src.data.market_data import (
    get_token_ids,
    get_current_slug,
    get_window_times,
    get_funding_bias,
    get_window_start_price,
    get_current_spot_price,
)
from src.trading.strategy import (
    calculate_confidence,
    bfxd_allows_trade,
)


from src.trading.orders import (
    setup_api_creds,
    place_order,
    place_batch_orders,
    get_clob_client,
    get_bulk_spreads,
    get_spread,
    check_liquidity,
    BUY,
    SELL,
)
from src.trading.position_manager import (
    check_open_positions,
    get_exit_plan_stats,
    recover_open_positions,
    sync_positions_with_exchange,
)
from src.utils.notifications import process_notifications, init_ws_callbacks
from src.trading.settlement import check_and_settle_trades
from src.utils.websocket_manager import ws_manager


def _determine_trade_side(bias: str, confidence: float) -> tuple[str, float]:
    """
    Determine actual trading side and confidence for sizing.
    Tiered logic: 
    - High Confidence: Follow Trend
    - Low Confidence: Contrarian (expect flip)
    - Medium: Wait (Neutral)
    """
    from src.config.settings import MIN_EDGE, CONTRARIAN_THRESHOLD
    
    if confidence >= MIN_EDGE:
        # Strong trend confirmed
        actual_side = bias
        sizing_confidence = confidence
    elif confidence <= CONTRARIAN_THRESHOLD:
        # Very low confidence - high chance of side flipping (Contrarian)
        actual_side = "DOWN" if bias == "UP" else "UP"
        sizing_confidence = 0.25 # Fixed lower sizing for contrarian plays
    else:
        # "No man's land" - wait for higher confidence or clear flip
        actual_side = "NEUTRAL"
        sizing_confidence = 0.0

    return actual_side, sizing_confidence


def _check_target_price_alignment(
    symbol: str,
    side: str,
    confidence: float,
    current_spot: float,
    target_price: float,
) -> bool:
    """Check if target price alignment allows trading"""
    if target_price > 0 and current_spot > 0:
        from src.config.settings import WINDOW_START_PRICE_BUFFER_PCT

        buffer = target_price * (WINDOW_START_PRICE_BUFFER_PCT / 100.0)

        is_winning_side = False
        if side == "UP":
            is_winning_side = current_spot >= (target_price - buffer)
        elif side == "DOWN":
            is_winning_side = current_spot <= (target_price + buffer)

        if not is_winning_side:
            LOSING_SIDE_BYPASS_CONFIDENCE = 0.45
            if confidence < LOSING_SIDE_BYPASS_CONFIDENCE:
                log(
                    f"[{symbol}] ⚠️ {side} is on LOSING side (Spot ${current_spot:,.2f} vs Target ${target_price:,.2f}) and confidence {confidence:.1%} < {LOSING_SIDE_BYPASS_CONFIDENCE:.0%}. SKIPPING."
                )
                return False
            else:
                log(
                    f"[{symbol}] 🔥 HIGH CONFIDENCE BYPASS: Entering {side} on losing side (Confidence: {confidence:.1%})"
                )

    return True


def _calculate_bet_size(
    balance: float, price: float, sizing_confidence: float
) -> tuple[float, float]:
    """Calculate position size and effective bet amount"""
    base_bet = balance * (BET_PERCENT / 100.0)
    confidence_multiplier = 0.5 + (sizing_confidence * 3.5)
    target_bet = base_bet * confidence_multiplier

    size = round(target_bet / price, 4)

    MIN_SIZE = 5.0
    bet_usd_effective = target_bet

    if size < MIN_SIZE:
        size = MIN_SIZE
        bet_usd_effective = round(size * price, 4)

    return size, bet_usd_effective


def _prepare_trade_params(
    symbol: str, balance: float, add_spacing: bool = True
) -> Optional[dict]:
    """
    Prepare trade parameters without executing the order

    Returns:
        Dict with trade parameters or None if no trade should be made
    """
    up_id, down_id = get_token_ids(symbol)
    if not up_id or not down_id:
        log(f"[{symbol}] ❌ Market not found")
        if add_spacing:
            log("")  # Add blank line
        return

    client = get_clob_client()
    confidence, bias, p_up, best_bid, best_ask, signals = calculate_confidence(
        symbol, up_id, client
    )

    if bias == "NEUTRAL":
        log(f"[{symbol}] ⚪ Confidence: {confidence:.1%} ({bias}) - NO TRADE")
        if add_spacing:
            log("")  # Add blank line
        return

    actual_side, sizing_confidence = _determine_trade_side(bias, confidence)

    if actual_side == "NEUTRAL":
        # Check if this is an "Empty Book" scenario or just low confidence
        if confidence == 0 and bias == "NEUTRAL":
            log(f"[{symbol}] ⚪ Neutral / No Signal")
        else:
            log(f"[{symbol}] ⏳ WAIT ZONE: {bias} ({confidence:.1%}) | {CONTRARIAN_THRESHOLD} < x < {MIN_EDGE}")
        
        if add_spacing:
            log("")
        return

    if actual_side == bias:
        log(f"[{symbol}] ✅ Trend Following: {bias} (Confidence: {confidence:.1%})")
    else:
        log(
            f"[{symbol}] 🔄 Contrarian Entry: {actual_side} (Bias flipping from {bias} @ {confidence:.1%})"
        )

    if actual_side == "UP":
        token_id, side, price = up_id, "UP", p_up
    else:
        token_id, side, price = down_id, "DOWN", 1.0 - p_up

    # Return trade parameters
    window_start, window_end = get_window_times(symbol)

    # Check lateness
    now_et = datetime.now(tz=ZoneInfo("America/New_York"))
    lateness = (now_et - window_start).total_seconds()
    time_left = (window_end - now_et).total_seconds()

    if lateness > MAX_ENTRY_LATENESS_SEC:
        log(
            f"[{symbol}] ⚠️  Cycle is TOO LATE ({lateness:.0f}s into window, {time_left:.0f}s left). SKIPPING."
        )
        if add_spacing:
            log("")
        return
    elif lateness > 60:
        log(
            f"[{symbol}] ⏳ Cycle is LATE ({lateness:.0f}s into window, {time_left:.0f}s left)"
        )

    target_price = float(get_window_start_price(symbol))

    current_spot = 0.0
    if isinstance(signals, dict):
        current_spot = float(signals.get("current_spot", 0))

    if not _check_target_price_alignment(
        symbol, side, confidence, current_spot, target_price
    ):
        if add_spacing:
            log("")  # Add blank line
        return

    # Check filters
    bfxd_ok, bfxd_trend = bfxd_allows_trade(symbol, side)

    # Signal details
    rsi = 50.0
    imbalance_val = 0.5
    adx_val = 0.0
    if isinstance(signals, dict):
        if "momentum" in signals and isinstance(signals["momentum"], dict):
            rsi = signals["momentum"].get("rsi", 50.0)

        if "order_flow" in signals and isinstance(signals["order_flow"], dict):
            imbalance_val = signals["order_flow"].get("buy_pressure", 0.5)

        if "adx" in signals and isinstance(signals["adx"], dict):
            adx_val = signals["adx"].get("value", 0.0)

    filter_text = f"ADX: {adx_val:.1f}"

    if ENABLE_BFXD and symbol == "BTC":
        filter_text += f" | BFXD: {bfxd_trend} {'✅' if bfxd_ok else '❌'}"

    core_summary = (
        f"Confidence: {confidence:.1%} ({bias}) | {filter_text} | RSI: {rsi:.1f}"
    )

    if not bfxd_ok:
        log(f"[{symbol}] ⛔ {core_summary} | status: BLOCKED")
        if add_spacing:
            log("")  # Add blank line
        return

    log(f"[{symbol}] ✅ {core_summary} | status: ENTERING TRADE")

    if price <= 0:
        log(f"[{symbol}] ERROR: Invalid price {price}")
        if add_spacing:
            log("")  # Add blank line
        return

    # Clamp and round to minimum tick size (0.01)
    price = max(0.01, min(0.99, price))
    price = round(price, 2)

    size, bet_usd_effective = _calculate_bet_size(balance, price, sizing_confidence)

    return {
        "symbol": symbol,
        "token_id": token_id,
        "side": side,
        "price": price,
        "size": size,
        "bet_usd": bet_usd_effective,
        "confidence": confidence,
        "p_up": p_up,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "imbalance": imbalance_val,
        "funding_bias": get_funding_bias(symbol),
        "target_price": target_price if target_price > 0 else None,
        "window_start": window_start,
        "window_end": window_end,
        "slug": get_current_slug(symbol),
    }


def trade_symbol(symbol: str, balance: float) -> int:
    """
    Execute trading logic for a symbol (single order)
    Returns the number of successful trades placed (0 or 1).
    """
    trade_params = _prepare_trade_params(symbol, balance, add_spacing=True)

    if not trade_params:
        return 0

    # Place order
    result = place_order(
        trade_params["token_id"], trade_params["price"], trade_params["size"]
    )

    # Only proceed if order was successful
    if not result["success"]:
        log(f"[{trade_params['symbol']}] ❌ Order failed, skipping trade tracking")
        return 0

    send_discord(
        f"**[{trade_params['symbol']}] {trade_params['side']} ${trade_params['bet_usd']:.2f}** | Confidence {trade_params['confidence']:.1%} | Price {trade_params['price']:.4f}"
    )

    try:
        trade_id = save_trade(
            symbol=trade_params["symbol"],
            window_start=trade_params["window_start"].isoformat(),
            window_end=trade_params["window_end"].isoformat(),
            slug=trade_params["slug"],
            token_id=trade_params["token_id"],
            side=trade_params["side"],
            edge=trade_params["confidence"],
            price=trade_params["price"],
            size=trade_params["size"],
            bet_usd=trade_params["bet_usd"],
            p_yes=trade_params["p_up"],
            best_bid=trade_params["best_bid"],
            best_ask=trade_params["best_ask"],
            imbalance=trade_params["imbalance"],
            funding_bias=trade_params["funding_bias"],
            order_status=result["status"],
            order_id=result["order_id"],
            limit_sell_order_id=None,
            target_price=trade_params["target_price"],
        )
        log("")
        log(
            f"[{trade_params['symbol']}] 🚀 #{trade_id} {trade_params['side']} ${trade_params['bet_usd']:.2f} @ {trade_params['price']:.4f} | {result['status']} | ID: {result['order_id'][:10] if result['order_id'] else 'N/A'}"
        )
        return 1
    except Exception as e:
        log(f"[{trade_params['symbol']}] Trade completion error: {e}")
        return 0


def trade_symbols_batch(symbols: list, balance: float) -> int:
    """
    Execute trading logic for multiple symbols using batch orders
    Returns the number of successful trades placed. 
    Returns -1 if all markets were skipped specifically due to spread == 1.0 (empty book).
    """
    # 1. Bulk get tokens and check spreads to pre-filter
    market_tokens = {}
    all_token_ids = []

    for symbol in symbols:
        up_id, down_id = get_token_ids(symbol)
        if up_id and down_id:
            market_tokens[symbol] = (up_id, down_id)
            all_token_ids.extend([up_id, down_id])
    
    # Register all tokens at once with WebSocket manager
    if all_token_ids:
        ws_manager.subscribe_to_prices(all_token_ids)

    # Bulk check spreads
    spreads = get_bulk_spreads(all_token_ids)

    # Filter symbols where either side has wide spread
    valid_symbols = []
    skipped_due_to_empty_book = 0
    for symbol in symbols:
        if symbol not in market_tokens:
            continue

        up_id, down_id = market_tokens[symbol]

        # Check liquidity from bulk spreads - default to single check if bulk missing
        up_spread = spreads.get(str(up_id))
        if up_spread is None:
            up_spread = get_spread(up_id)

        down_spread = spreads.get(str(down_id))
        if down_spread is None:
            down_spread = get_spread(down_id)

        # Fallback to 0 if still None (assume liquid rather than skip)
        up_spread = float(up_spread) if up_spread is not None else 0.0
        down_spread = float(down_spread) if down_spread is not None else 0.0

        if up_spread >= 1.0 or down_spread >= 1.0:
            # Spread of 1.0 means NO orders on one side - typical at window start
            log(f"[{symbol}] ⏳ No liquidity yet (Spread: 1.0). Waiting for market makers...")
            skipped_due_to_empty_book += 1
            continue

        if up_spread > MAX_SPREAD or down_spread > MAX_SPREAD:
            log(
                f"[{symbol}] ⚠️ Spread too wide (UP: {up_spread:.3f}, DOWN: {down_spread:.3f}). SKIPPING."
            )
            continue
        valid_symbols.append(symbol)

    if not valid_symbols:
        if skipped_due_to_empty_book > 0 and skipped_due_to_empty_book == len(market_tokens):
            return -1
        return 0

    # 2. Prepare trades for remaining symbols
    trade_params_list = []
    for i, symbol in enumerate(valid_symbols):
        params = _prepare_trade_params(symbol, balance, add_spacing=False)
        if params:
            trade_params_list.append(params)

        # Add spacing between symbols
        if i < len(valid_symbols) - 1:
            log("")

    if not trade_params_list:
        return 0

    # 3. Execute batch placement
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

    # 4. Save successful trades
    placed_count = 0
    for i, result in enumerate(results):
        if i < len(trade_params_list) and result["success"]:
            placed_count += 1
            p = trade_params_list[i]
            send_discord(
                f"**[{p['symbol']}] {p['side']} ${p['bet_usd']:.2f}** | Confidence {p['confidence']:.1%} | Price {p['price']:.4f}"
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
                    price=p["price"],
                    size=p["size"],
                    bet_usd=p["bet_usd"],
                    p_yes=p["p_up"],
                    best_bid=p["best_bid"],
                    best_ask=p["best_ask"],
                    imbalance=p["imbalance"],
                    funding_bias=p["funding_bias"],
                    order_status=result["status"],
                    order_id=result["order_id"],
                    limit_sell_order_id=None,
                    target_price=p["target_price"],
                )
                log(
                    f"[{p['symbol']}] 🚀 #{trade_id} {p['side']} ${p['bet_usd']:.2f} @ {p['price']:.4f} | {result['status']} | ID: {result['order_id'][:10] if result['order_id'] else 'N/A'}"
                )
            except Exception as e:
                log(f"[{p['symbol']}] Trade completion error: {e}")
        elif i < len(trade_params_list):
            p = trade_params_list[i]
            log(f"[{p['symbol']}] ❌ Batch order failed: {result.get('error')}")
    return placed_count


def main():
    """Main bot loop"""
    log("🚀 Starting PolyAstra Trading Bot (Modular Version)...")
    log(
        f"📊 ADX System: {'INTEGRATED' if ADX_ENABLED else 'DISABLED'} (period={ADX_PERIOD}, interval={ADX_INTERVAL})"
    )
    setup_api_creds()
    init_database()

    # Start WebSocket Manager
    ws_manager.start()
    init_ws_callbacks()

    if FUNDER_PROXY and FUNDER_PROXY.startswith("0x"):
        addr = FUNDER_PROXY
        log_addr_type = "Funder"
    else:
        addr = Account.from_key(PROXY_PK).address
        log_addr_type = "Proxy"

    log("=" * 90)
    log(
        f"🤖 POLYASTRA | Wallet: {addr[:10]}...{addr[-8:]} | Balance: {get_balance(addr):.2f} USDC"
    )
    log(f"⚙️  MIN_EDGE: {MIN_EDGE:.1%} | BET: {BET_PERCENT}%")
    log("=" * 90)

    # Recover and start monitoring any existing open positions
    recover_open_positions()
    sync_positions_with_exchange(addr)

    # Immediately check positions to ensure stop loss/take profit monitoring is active
    log("🔍 Performing initial position check...")
    check_open_positions(verbose=True, check_orders=True)

    cycle = 0
    last_position_check = time.time()
    last_order_check = time.time()
    last_verbose_log = time.time()
    last_exit_stats_log = time.time()
    
    # Check if we should trade immediately (if we haven't traded for current window yet)
    now_utc = datetime.utcnow()
    window_start_min = (now_utc.minute // 15) * 15
    # Since market_data.get_window_times uses ET, we should match that for consistency
    # but here we just need to know if we are in the window.
    
    log("🏁 Bot initialized. Checking for immediate trading opportunities...")
    
    # First, run a cycle immediately if we are within lateness limits
    current_balance = get_balance(addr)
    
    # Filter markets that haven't been traded this window
    eligible_markets = []
    for m in MARKETS:
        w_start_et, _ = get_window_times(m)
        w_start_iso = w_start_et.isoformat()
        if not has_trade_for_window(m, w_start_iso):
            eligible_markets.append(m)
        else:
            log(f"[{m}] ⏭️ Already traded for current window ({w_start_et.strftime('%H:%M')}).")

    if eligible_markets:
        log(f"🔍 Evaluating {len(eligible_markets)} eligible markets: {', '.join(eligible_markets)}")
        # Check lateness for the first market in the list (assuming same windows)
        w_start_et, _ = get_window_times(eligible_markets[0])
        now_et = datetime.now(tz=ZoneInfo("America/New_York"))
        lateness = (now_et - w_start_et).total_seconds()
        
        if lateness <= MAX_ENTRY_LATENESS_SEC:
            if len(eligible_markets) > 1:
                trade_symbols_batch(eligible_markets, current_balance)
            else:
                trade_symbol(eligible_markets[0], current_balance)
        else:
            log(f"⏳ Too late in current window ({lateness:.0f}s > {MAX_ENTRY_LATENESS_SEC}s). Waiting for next window.")

    while True:
        try:
            # Check positions every 1 second (verbose log every 60 seconds)
            now_ts = time.time()
            if now_ts - last_position_check >= 1:
                is_verbose_cycle = now_ts - last_verbose_log >= 60
                is_order_check_cycle = now_ts - last_order_check >= 30

                check_open_positions(
                    verbose=is_verbose_cycle, check_orders=is_order_check_cycle
                )

                last_position_check = now_ts
                if is_verbose_cycle:
                    last_verbose_log = now_ts
                if is_order_check_cycle:
                    last_order_check = now_ts

                # Process notifications every 30 seconds (when checking orders)
                if is_order_check_cycle:
                    process_notifications()

                # Log exit plan stats every 15 minutes (every 15th verbose cycle)
                if (
                    is_verbose_cycle and now_ts - last_exit_stats_log >= 900
                ):  # 15 minutes
                    exit_stats = get_exit_plan_stats()
                    if exit_stats:
                        log(
                            f"📈 Exit Plan Performance: {exit_stats['exit_success_rate']:.1f}% success rate "
                            f"({exit_stats['exit_plan_successes']}/{exit_stats['exit_plan_successes'] + exit_stats['natural_settlements'] + exit_stats['legacy_limit_sells']}) "
                            f"| Avg ROI: Exit {exit_stats['avg_exit_plan_roi']:.1f}%, Natural {exit_stats['avg_natural_roi']:.1f}%"
                        )
                    last_exit_stats_log = now_ts

            now = datetime.utcnow()
            wait = 900 - ((now.minute % 15) * 60 + now.second)
            if wait <= 0:
                wait += 900

            # Wait in 1-second chunks so we can check positions
            if cycle == 0 or wait > 30:
                log(f"⏱️  Waiting {wait}s until next window...")
            elif wait <= 5:
                log(f"⏳ Window starting in {wait}s...")

            remaining = wait + WINDOW_DELAY_SEC
            while remaining > 0:
                sleep_time = min(1, remaining)
                time.sleep(sleep_time)
                remaining -= sleep_time

                # Check positions during wait (silent unless it's been 60s)
                if remaining > 0:
                    now_ts = time.time()
                    if now_ts - last_position_check >= 1:
                        is_verbose_cycle = now_ts - last_verbose_log >= 60
                        is_order_check_cycle = now_ts - last_order_check >= 30

                        check_open_positions(
                            verbose=is_verbose_cycle, check_orders=is_order_check_cycle
                        )

                        last_position_check = now_ts
                        if is_verbose_cycle:
                            last_verbose_log = now_ts
                        if is_order_check_cycle:
                            last_order_check = now_ts

            log(
                f"\n{'=' * 90}\n🔄 CYCLE #{cycle + 1} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}\n{'=' * 90}\n"
            )

            # Fetch balance once for the cycle
            current_balance = get_balance(addr)
            log(f"💰 Balance: {current_balance:.2f} USDC")
            log(f"🔍 Evaluating {len(MARKETS)} markets: {', '.join(MARKETS)}")

            # Filter markets that haven't been traded this window
            current_window_markets = []
            for m in MARKETS:
                w_start_et, _ = get_window_times(m)
                if not has_trade_for_window(m, w_start_et.isoformat()):
                    current_window_markets.append(m)
                else:
                    log(f"[{m}] ⏭️ Already traded for this window. Skipping.")

            if current_window_markets:
                # Use batch orders for multiple markets (more efficient)
                # Try up to 3 times if we skip due to zero liquidity (Polymarket warm-up)
                for attempt in range(3):
                    if len(current_window_markets) > 1:
                        placed = trade_symbols_batch(current_window_markets, current_balance)
                    elif len(current_window_markets) == 1:
                        placed = trade_symbol(current_window_markets[0], current_balance)
                    else:
                        placed = 0
                    
                    # If we placed a trade, or if we evaluated without hitting the "empty book" state (-1), we're done
                    if placed != -1:
                        break
                    
                    if attempt < 2:
                        log(f"⏳ All markets skipped due to zero liquidity (warm-up). Retrying in 10s... (Attempt {attempt+2}/3)")
                        time.sleep(10)
            else:
                log("ℹ️ No eligible markets to trade for this window (all already traded).")

            check_and_settle_trades()
            cycle += 1

            if cycle % 16 == 0:
                log("\n📊 Generating performance report...")
                generate_statistics()

        except KeyboardInterrupt:
            log("\n⛔ Bot stopped by user")
            log("📊 Generating final report...")
            generate_statistics()
            break

        except Exception as e:
            log(f"❌ Critical error: {e}")
            import traceback

            log(traceback.format_exc())
            send_discord(f"❌ Error: {e}")
            time.sleep(60)
