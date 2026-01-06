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
        sizing_confidence = 0.25  # Fixed lower sizing for contrarian plays
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
    current_price: float,
    verbose: bool = True,
) -> bool:
    """Check if target price alignment allows trading"""

    # 1. Underdog Filter: Require higher confidence if we are entering the losing side
    # A side is considered "losing" if its midpoint price is < $0.50
    is_underdog = current_price < 0.50

    if is_underdog:
        # PROTECT AGAINST IMMEDIATE STOP LOSS: Don't enter if already below/at stop loss threshold
        # We require at least 5 cents of cushion above the standard floor ($0.30 + $0.05 = $0.35)
        if ENABLE_STOP_LOSS and current_price <= (STOP_LOSS_PRICE + 0.05):
            if verbose:
                log(
                    f"[{symbol}] ⚠️ {side} is too close to stop loss zone (${current_price:.2f} <= ${STOP_LOSS_PRICE + 0.05:.2f}). SKIPPING."
                )
            return False

        if confidence < LOSING_SIDE_MIN_CONFIDENCE:
            if verbose:
                log(
                    f"[{symbol}] ⚠️ {side} is UNDERDOG (${current_price:.2f}) and confidence {confidence:.1%} < {LOSING_SIDE_MIN_CONFIDENCE:.0%}. SKIPPING."
                )
            return False
        else:
            log(
                f"[{symbol}] 🔥 HIGH CONFIDENCE UNDERDOG: Entering {side} at ${current_price:.2f} (Confidence: {confidence:.1%})"
            )

    # 2. Spot-vs-Target Alignment (Safety Layer)
    if target_price > 0 and current_spot > 0:
        from src.config.settings import WINDOW_START_PRICE_BUFFER_PCT

        buffer = target_price * (WINDOW_START_PRICE_BUFFER_PCT / 100.0)

        is_winning_side_on_spot = False
        if side == "UP":
            is_winning_side_on_spot = current_spot >= (target_price - buffer)
        elif side == "DOWN":
            is_winning_side_on_spot = current_spot <= (target_price + buffer)

        if not is_winning_side_on_spot:
            if confidence < LOSING_SIDE_MIN_CONFIDENCE:
                if verbose:
                    log(
                        f"[{symbol}] ⚠️ {side} is losing on SPOT (${current_spot:,.2f} vs Target ${target_price:,.2f}) and confidence {confidence:.1%} < {LOSING_SIDE_MIN_CONFIDENCE:.0%}. SKIPPING."
                    )
                return False
            else:
                log(
                    f"[{symbol}] 🔥 HIGH CONFIDENCE REVERSAL: Entering {side} against spot direction (Confidence: {confidence:.1%})"
                )

    return True


def _calculate_bet_size(
    balance: float, price: float, sizing_confidence: float
) -> tuple[float, float]:
    """Calculate position size and effective bet amount"""
    base_bet = balance * (BET_PERCENT / 100.0)
    # Scaled bet based on confidence
    confidence_multiplier = sizing_confidence * CONFIDENCE_SCALING_FACTOR
    target_bet = base_bet * confidence_multiplier

    # Ensure at least some minimum multiplier if confidence is very low but valid
    if target_bet < base_bet * 0.5:
        target_bet = base_bet * 0.5

    size = round(target_bet / price, 4)

    MIN_SIZE = 5.0
    bet_usd_effective = target_bet

    if size < MIN_SIZE:
        size = MIN_SIZE
        bet_usd_effective = round(size * price, 4)

    return size, bet_usd_effective


def _prepare_trade_params(
    symbol: str, balance: float, add_spacing: bool = True, verbose: bool = True
) -> Optional[dict]:
    """
    Prepare trade parameters without executing the order
    """
    up_id, down_id = get_token_ids(symbol)
    if not up_id or not down_id:
        if verbose:
            log(f"[{symbol}] ❌ Market not found")
            if add_spacing:
                log("")
        return

    # if verbose:
    #     log(f"[{symbol}] 🔍 Token IDs: UP={up_id}, DOWN={down_id}")

    client = get_clob_client()
    confidence, bias, p_up, best_bid, best_ask, signals = calculate_confidence(
        symbol, up_id, client
    )

    if bias == "NEUTRAL":
        if verbose:
            log(f"[{symbol}] ⚪ Confidence: {confidence:.1%} ({bias}) - NO TRADE")
            if add_spacing:
                log("")
        return

    actual_side, sizing_confidence = _determine_trade_side(bias, confidence)

    if actual_side == "NEUTRAL":
        if verbose:
            if confidence == 0 and bias == "NEUTRAL":
                log(f"[{symbol}] ⚪ Neutral / No Signal")
            else:
                log(
                    f"[{symbol}] ⏳ WAIT ZONE: {bias} ({confidence:.1%}) | {CONTRARIAN_THRESHOLD} < x < {MIN_EDGE}"
                )

            if add_spacing:
                log("")
        return

    if actual_side == "UP":
        token_id, side, price = up_id, "UP", p_up
    else:
        token_id, side, price = down_id, "DOWN", 1.0 - p_up

    # NEW: Check if we already have a trade for THIS SIDE in this window
    window_start, window_end = get_window_times(symbol)

    # Check if ANY trade exists for this window
    other_side_exists = False
    if has_trade_for_window(symbol, window_start.isoformat()):
        other_side_exists = True

    if has_side_for_window(symbol, window_start.isoformat(), side):
        if verbose:
            log(
                f"[{symbol}] ℹ️ Already have an open {side} position for this window. Skipping duplicate entry."
            )
            if add_spacing:
                log("")
        return None

    if actual_side == bias:
        entry_type = "HEDGED REVERSAL" if other_side_exists else "Trend Following"
        if verbose:
            log(f"[{symbol}] ✅ {entry_type}: {bias} (Confidence: {confidence:.1%})")
    else:
        entry_type = (
            "HEDGED REVERSAL (Contrarian)" if other_side_exists else "Contrarian Entry"
        )
        if verbose:
            log(
                f"[{symbol}] 🔄 {entry_type}: {actual_side} (Bias flipping from {bias} @ {confidence:.1%})"
            )

    # Check lateness
    now_et = datetime.now(tz=ZoneInfo("America/New_York"))
    lateness = (now_et - window_start).total_seconds()
    time_left = (window_end - now_et).total_seconds()

    if lateness > MAX_ENTRY_LATENESS_SEC:
        if verbose:
            log(
                f"[{symbol}] ⚠️  Cycle is TOO LATE ({lateness:.0f}s into window, {time_left:.0f}s left). SKIPPING."
            )
            if add_spacing:
                log("")
        return
    elif lateness > 60:
        if verbose:
            log(
                f"[{symbol}] ⏳ Cycle is LATE ({lateness:.0f}s into window, {time_left:.0f}s left)"
            )

    target_price = float(get_window_start_price(symbol))

    current_spot = 0.0
    if isinstance(signals, dict):
        current_spot = float(signals.get("current_spot", 0))

    if not _check_target_price_alignment(
        symbol, side, confidence, current_spot, target_price, price, verbose=verbose
    ):
        if add_spacing and verbose:
            log("")
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
            log("")
        return

    log(f"[{symbol}] ✅ {core_summary} | status: ENTERING TRADE")

    if price <= 0:
        log(f"[{symbol}] ERROR: Invalid price {price}")
        if add_spacing:
            log("")
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


def trade_symbol(symbol: str, balance: float, verbose: bool = True) -> int:
    """Execute trading logic for a symbol"""
    trade_params = _prepare_trade_params(
        symbol, balance, add_spacing=True, verbose=verbose
    )

    if not trade_params:
        return 0

    # Place order
    result = place_order(
        trade_params["token_id"], trade_params["price"], trade_params["size"]
    )

    if not result["success"]:
        log(f"[{trade_params['symbol']}] ❌ Order failed, skipping trade tracking")
        return 0

    actual_size = trade_params["size"]
    actual_price = trade_params["price"]
    actual_status = result["status"]

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
                    trade_params["bet_usd"] = actual_size * actual_price
        except Exception as e:
            log_error(
                f"[{trade_params['symbol']}] Could not sync execution details immediately: {e}"
            )

    send_discord(
        f"**[{trade_params['symbol']}] {trade_params['side']} ${trade_params['bet_usd']:.2f}** | Confidence {trade_params['confidence']:.1%} | Price {actual_price:.4f}"
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
            price=actual_price,
            size=actual_size,
            bet_usd=trade_params["bet_usd"],
            p_yes=trade_params["p_up"],
            best_bid=trade_params["best_bid"],
            best_ask=trade_params["best_ask"],
            imbalance=trade_params["imbalance"],
            funding_bias=trade_params["funding_bias"],
            order_status=actual_status,
            order_id=result["order_id"],
            limit_sell_order_id=None,
            target_price=trade_params["target_price"],
        )
        log("")
        log(
            f"[{trade_params['symbol']}] 🚀 #{trade_id} {trade_params['side']} ${trade_params['bet_usd']:.2f} @ {actual_price:.4f} | {actual_status} | ID: {result['order_id'][:10] if result['order_id'] else 'N/A'}"
        )
        return 1
    except Exception as e:
        log_error(f"[{trade_params['symbol']}] Trade completion error: {e}")
        return 0


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
                    f"[{symbol}] ⚠️ Spread too wide (UP: {up_spread:.3f}, DOWN: {down_spread:.3f}). SKIPPING."
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
    for i, symbol in enumerate(valid_symbols):
        params = _prepare_trade_params(
            symbol, balance, add_spacing=False, verbose=verbose
        )
        if params:
            trade_params_list.append(params)

        if i < len(valid_symbols) - 1 and verbose:
            log("")

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
    for i, result in enumerate(results):
        if i < len(trade_params_list) and result["success"]:
            placed_count += 1
            p = trade_params_list[i]

            actual_size = p["size"]
            actual_price = p["price"]
            actual_status = result["status"]

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
                f"**[{p['symbol']}] {p['side']} ${p['bet_usd']:.2f}** | Confidence {p['confidence']:.1%} | Price {actual_price:.4f}"
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
                    target_price=p["target_price"],
                )
                log(
                    f"[{p['symbol']}] 🚀 #{trade_id} {p['side']} ${p['bet_usd']:.2f} @ {actual_price:.4f} | {actual_status} | ID: {result['order_id'][:10] if result['order_id'] else 'N/A'}"
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
    log("🚀 Starting PolyFlup Trading Bot (Modular Version)...")
    log(
        f"📊 ADX System: {'INTEGRATED' if ADX_ENABLED else 'DISABLED'} (period={ADX_PERIOD}, interval={ADX_INTERVAL})"
    )
    setup_api_creds()
    init_database()

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
    sync_positions_with_exchange(addr)

    log("🔍 Performing initial position check...")
    check_open_positions(verbose=True, check_orders=True)

    last_position_check = time.time()
    last_order_check = time.time()
    last_verbose_log = time.time()
    last_exit_stats_log = time.time()
    last_entry_check = 0
    last_settle_check = time.time()

    log("🏁 Bot initialized. Entering continuous monitoring loop...")
    last_window_logged = None

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
                    log(f"🪟 NEW WINDOW: {range_str}")
                    last_window_logged = w_start

            is_verbose_cycle = now_ts - last_verbose_log >= 60
            is_order_check_cycle = now_ts - last_order_check >= 10

            if now_ts - last_position_check >= 1:
                check_open_positions(
                    verbose=is_verbose_cycle, check_orders=is_order_check_cycle
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
                        # If hedged reversal is on, we allow entering even if one trade exists
                        # but _prepare_trade_params will ensure we don't double-up on the SAME side
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
