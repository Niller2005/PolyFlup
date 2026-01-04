"""Main bot loop"""

import time
from datetime import datetime
from eth_account import Account
from src.config.settings import (
    MARKETS,
    PROXY_PK,
    FUNDER_PROXY,
    MIN_EDGE,
    MAX_SPREAD,
    WINDOW_DELAY_SEC,
    ADX_ENABLED,
    ADX_THRESHOLD,
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
    CONFIDENCE_SCALING_FACTOR,
    MAX_PORTFOLIO_EXPOSURE,
    ENABLE_LATE_ENTRY_PROTECTION,
    LATE_ENTRY_THRESHOLD_PCT,
    HIGH_CONFIDENCE_EDGE_THRESHOLD,
    HIGH_CONFIDENCE_MOMENTUM_THRESHOLD,
)
from src.utils.logger import log, send_discord
from src.utils.web3_utils import get_balance
from src.data.database import (
    init_database,
    save_trade,
    generate_statistics,
    get_total_exposure,
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
    calculate_edge,
    adx_allows_trade,
    bfxd_allows_trade,
)
from src.trading.orders import (
    setup_api_creds,
    place_order,
    place_limit_order,
    get_clob_client,
    SELL,
)
from src.trading.position_manager import check_open_positions
from src.trading.settlement import check_and_settle_trades


def trade_symbol(symbol: str, balance: float):
    """Execute trading logic for a symbol"""
    up_id, down_id = get_token_ids(symbol)
    if not up_id or not down_id:
        log(f"[{symbol}] ❌ Market not found")
        return

    # Check portfolio exposure before expensive calculations
    total_exposure = get_total_exposure()
    exposure_pct = total_exposure / balance if balance > 0 else 0
    if exposure_pct >= MAX_PORTFOLIO_EXPOSURE:
        log(
            f"[{symbol}] ⚠️ Max portfolio exposure reached ({exposure_pct:.1%}) - SKIPPING"
        )
        return

    client = get_clob_client()
    edge, reason, p_up, best_bid, best_ask, imbalance, signals = calculate_edge(
        symbol, up_id, client
    )

    # Determine bias
    if edge <= (1.0 - MIN_EDGE):
        token_id, side, price, bias_text = up_id, "UP", p_up, "UP Undervalued"
    elif edge >= MIN_EDGE:
        token_id, side, price, bias_text = down_id, "DOWN", 1.0 - p_up, "UP Overvalued"
    else:
        log(f"[{symbol}] ⚪ Edge: {edge:.1%} (Neutral) - NO TRADE")
        return

    # Late Entry Protection: Don't open position if price already moved in the wrong direction
    # BUT allow switching to opposite direction if we have high confidence
    if ENABLE_LATE_ENTRY_PROTECTION:
        target_price = get_window_start_price(symbol)
        current_spot = get_current_spot_price(symbol)

        if target_price > 0 and current_spot > 0:
            price_move_pct = ((current_spot - target_price) / target_price) * 100.0

            # Check if price has already moved AGAINST our intended direction
            # For UP positions: if price already went DOWN significantly (missed lower entry)
            # For DOWN positions: if price already went UP significantly (missed higher entry)
            is_late_to_move = False
            move_reason = ""
            opposite_direction = None

            if side == "UP" and price_move_pct < -LATE_ENTRY_THRESHOLD_PCT:
                is_late_to_move = True
                opposite_direction = "DOWN"
                move_reason = (
                    f"price already down {abs(price_move_pct):.2f}%, wanted UP"
                )
            elif side == "DOWN" and price_move_pct > LATE_ENTRY_THRESHOLD_PCT:
                is_late_to_move = True
                opposite_direction = "UP"
                move_reason = f"price already up {price_move_pct:.2f}%, wanted DOWN"

            # If we're late, check if we should switch to opposite direction with high confidence
            if is_late_to_move:
                momentum = signals.get("momentum", {})
                order_flow = signals.get("order_flow", {})

                # Calculate edge strength
                raw_edge = edge if edge > 0.5 else (1.0 - edge)
                edge_strength = (raw_edge - MIN_EDGE) / (1.0 - MIN_EDGE)  # 0-1 scale

                # Check if momentum confirms the OPPOSITE direction (the direction price is moving)
                opposite_momentum_confirms = False
                if opposite_direction == "UP" and momentum.get("direction") == "UP":
                    opposite_momentum_confirms = True
                elif (
                    opposite_direction == "DOWN" and momentum.get("direction") == "DOWN"
                ):
                    opposite_momentum_confirms = True

                # Check if order flow confirms the OPPOSITE direction
                opposite_flow_confirms = False
                if (
                    opposite_direction == "UP"
                    and order_flow.get("buy_pressure", 0.5) > 0.6
                ):
                    opposite_flow_confirms = True
                elif (
                    opposite_direction == "DOWN"
                    and order_flow.get("buy_pressure", 0.5) < 0.4
                ):
                    opposite_flow_confirms = True

                # High confidence for direction switch requires strong signals
                has_high_confidence_for_switch = (
                    edge_strength > HIGH_CONFIDENCE_EDGE_THRESHOLD
                    and opposite_momentum_confirms
                    and opposite_flow_confirms
                    and momentum.get("strength", 0) > HIGH_CONFIDENCE_MOMENTUM_THRESHOLD
                )

                if has_high_confidence_for_switch:
                    # Switch to opposite direction
                    log(
                        f"[{symbol}] 🔄 DIRECTION SWITCH: {move_reason}, but high confidence for {opposite_direction} "
                        f"(edge={edge_strength:.2f}, momentum={opposite_momentum_confirms}, flow={opposite_flow_confirms})"
                    )

                    # Update trade parameters to opposite direction
                    if opposite_direction == "UP":
                        token_id, side, price = up_id, "UP", p_up
                    else:  # DOWN
                        token_id, side, price = down_id, "DOWN", 1.0 - p_up
                else:
                    # No high confidence - skip trade entirely
                    log(
                        f"[{symbol}] ⚠️ SKIPPING: {move_reason} and insufficient confidence for direction switch "
                        f"(edge={edge_strength:.2f}, opp_momentum={opposite_momentum_confirms}, opp_flow={opposite_flow_confirms})"
                    )
                    return
            elif side == "DOWN" and price_move_pct > LATE_ENTRY_THRESHOLD_PCT:
                is_late_to_move = True
                move_reason = (
                    f"price already up {price_move_pct:.2f}% (target was lower)"
                )

            # If we're late to the move, check if we have high confidence for a reversal/continuation
            if is_late_to_move:
                # Calculate confidence score from multiple signals
                raw_edge = edge if edge > 0.5 else (1.0 - edge)
                edge_strength = (raw_edge - MIN_EDGE) / (1.0 - MIN_EDGE)  # 0-1 scale

                # Check momentum alignment
                momentum = signals.get("momentum", {})
                momentum_confirms = False
                if side == "UP" and momentum.get("direction") == "UP":
                    momentum_confirms = True
                elif side == "DOWN" and momentum.get("direction") == "DOWN":
                    momentum_confirms = True

                # Check order flow alignment
                order_flow = signals.get("order_flow", {})
                flow_confirms = False
                if side == "UP" and order_flow.get("buy_pressure", 0.5) > 0.6:
                    flow_confirms = True
                elif side == "DOWN" and order_flow.get("buy_pressure", 0.5) < 0.4:
                    flow_confirms = True

                # High confidence requires: strong edge + momentum confirmation + order flow confirmation
                has_high_confidence = (
                    edge_strength > HIGH_CONFIDENCE_EDGE_THRESHOLD
                    and momentum_confirms
                    and flow_confirms
                    and momentum.get("strength", 0) > HIGH_CONFIDENCE_MOMENTUM_THRESHOLD
                )

                if not has_high_confidence:
                    log(
                        f"[{symbol}] ⚠️ SKIPPING: {move_reason} and insufficient confidence for reversal "
                        f"(edge_strength={edge_strength:.2f}, momentum={momentum_confirms}, flow={flow_confirms})"
                    )
                    return
                else:
                    log(
                        f"[{symbol}] ⚡ HIGH CONFIDENCE: {move_reason} but strong reversal signals detected "
                        f"(edge_strength={edge_strength:.2f}, momentum_str={momentum.get('strength', 0):.2f})"
                    )

    # Check filters
    adx_ok, adx_val = adx_allows_trade(symbol)
    bfxd_ok, bfxd_trend = bfxd_allows_trade(symbol, side)

    # RSI from signals
    rsi = signals.get("momentum", {}).get("rsi", 50.0)

    status_icon = "✅" if (adx_ok and bfxd_ok) else "❌"
    filter_text = f"ADX: {adx_val:.1f} {'✅' if adx_ok else '❌'}"
    if ENABLE_BFXD and symbol == "BTC":
        filter_text += f" | BFXD: {bfxd_trend} {'✅' if bfxd_ok else '❌'}"

    core_summary = f"Edge: {edge:.1%} ({bias_text}) | {filter_text} | RSI: {rsi:.1f}"

    if not adx_ok or not bfxd_ok:
        log(f"[{symbol}] ⛔ {core_summary} | status: BLOCKED")
        return

    log(f"[{symbol}] ✅ {core_summary} | status: ENTERING TRADE")

    if price <= 0:
        log(f"[{symbol}] ERROR: Invalid price {price}")
        return

    price = max(0.01, min(0.99, price))
    base_bet = balance * (BET_PERCENT / 100.0)
    # Scale bet based on edge strength
    # Edge is between MIN_EDGE (e.g. 0.565) and 1.0
    raw_edge = edge if edge > 0.5 else (1.0 - edge)
    edge_delta = max(0.0, raw_edge - MIN_EDGE)
    confidence_multiplier = 1.0 + (edge_delta * CONFIDENCE_SCALING_FACTOR)
    confidence_multiplier = min(confidence_multiplier, 3.0)  # Cap at 3x

    target_bet = base_bet * confidence_multiplier
    size = round(target_bet / price, 6)

    MIN_SIZE = 5.0
    bet_usd_effective = target_bet

    if size < MIN_SIZE:
        size = MIN_SIZE
        bet_usd_effective = round(size * price, 4)

    result = place_order(token_id, price, size)

    # Only proceed if order was successful
    if not result["success"]:
        log(f"[{symbol}] ❌ Order failed, skipping trade tracking")
        return

    send_discord(
        f"**[{symbol}] {side} ${bet_usd_effective:.2f}** | Edge {edge:.1%} | Price {price:.4f}"
    )

    # Place limit sell order at 0.99 immediately after market buy (with retry logic)
    limit_sell_order_id = None
    max_retries = 3
    retry_delays = [1, 2, 3]

    log(f"[{symbol}] 📉 Placing limit sell order at 0.99 for {size} units")
    for attempt in range(max_retries):
        if attempt > 0:
            log(
                f"[{symbol}] ⏳ Retry {attempt}/{max_retries - 1} placing limit sell order..."
            )
            time.sleep(retry_delays[attempt - 1])

        sell_limit_result = place_limit_order(
            token_id,
            0.99,
            size,
            SELL,
            silent_on_balance_error=(attempt < max_retries - 1),
        )

        if sell_limit_result["success"]:
            limit_sell_order_id = sell_limit_result["order_id"]
            log(
                f"[{symbol}] ✅ Limit sell order placed at 0.99: {limit_sell_order_id[:10] if limit_sell_order_id else 'N/A'}"
            )
            break
        else:
            error_msg = str(sell_limit_result.get("error", ""))
            if "not enough balance" in error_msg.lower() and attempt < max_retries - 1:
                # Balance might still be settling, will retry
                continue
            else:
                log(f"[{symbol}] ⚠️ Failed to place limit sell order: {error_msg}")
                break

    try:
        window_start, window_end = get_window_times(symbol)
        target_price = get_window_start_price(symbol)

        trade_id = save_trade(
            symbol=symbol,
            window_start=window_start.isoformat(),
            window_end=window_end.isoformat(),
            slug=get_current_slug(symbol),
            token_id=token_id,
            side=side,
            edge=edge,
            price=price,
            size=size,
            bet_usd=bet_usd_effective,
            p_yes=p_up,
            best_bid=best_bid,
            best_ask=best_ask,
            imbalance=imbalance,
            funding_bias=get_funding_bias(symbol),
            order_status=result["status"],
            order_id=result["order_id"],
            limit_sell_order_id=limit_sell_order_id,  # Now set immediately after buy
            target_price=target_price if target_price > 0 else None,
        )
        log(
            f"[{symbol}] 🚀 #{trade_id} {side} ${bet_usd_effective:.2f} @ {price:.4f} | {result['status']} | ID: {result['order_id'][:10] if result['order_id'] else 'N/A'}"
        )
    except Exception as e:
        log(f"[{symbol}] Trade completion error: {e}")


def main():
    """Main bot loop"""
    log("🚀 Starting PolyAstra Trading Bot (Modular Version)...")
    log("📝 FIX: Reversed UP/DOWN logic - now buying undervalued side")
    log(
        f"📊 ADX Filter: {'ENABLED' if ADX_ENABLED else 'DISABLED'} (threshold={ADX_THRESHOLD}, period={ADX_PERIOD}, interval={ADX_INTERVAL})"
    )
    setup_api_creds()
    init_database()

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
    log(f"⚙️  MIN_EDGE: {MIN_EDGE:.1%} | BET: {BET_PERCENT}% | ADX: {ADX_THRESHOLD}")
    log("=" * 90)

    cycle = 0
    last_position_check = time.time()
    last_order_check = time.time()
    last_verbose_log = time.time()

    while True:
        try:
            # Check positions every 1 second (verbose log every 60 seconds)
            now_ts = time.time()
            if now_ts - last_position_check >= 1:
                verbose = now_ts - last_verbose_log >= 60
                check_orders = now_ts - last_order_check >= 30
                check_open_positions(verbose=verbose, check_orders=check_orders)
                last_position_check = now_ts
                if verbose:
                    last_verbose_log = now_ts
                if check_orders:
                    last_order_check = now_ts

            now = datetime.utcnow()
            wait = 900 - ((now.minute % 15) * 60 + now.second)
            if wait <= 0:
                wait += 900

            # Wait in 1-second chunks so we can check positions
            if cycle == 0 or wait > 30:
                log(f"⏱️  Waiting {wait}s until next window...")

            remaining = wait + WINDOW_DELAY_SEC
            while remaining > 0:
                sleep_time = min(1, remaining)
                time.sleep(sleep_time)
                remaining -= sleep_time

                # Check positions during wait (silent unless it's been 60s)
                if remaining > 0:
                    now_ts = time.time()
                    verbose = now_ts - last_verbose_log >= 60
                    check_orders = now_ts - last_order_check >= 30
                    check_open_positions(verbose=verbose, check_orders=check_orders)
                    if verbose:
                        last_verbose_log = now_ts
                    if check_orders:
                        last_order_check = now_ts

            log(
                f"\n{'=' * 90}\n🔄 CYCLE #{cycle + 1} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}\n{'=' * 90}\n"
            )

            # Fetch balance once for the cycle
            current_balance = get_balance(addr)
            log(
                f"💰 Balance: {current_balance:.2f} USDC | Evaluating {len(MARKETS)} markets"
            )

            for sym in MARKETS:
                trade_symbol(sym, current_balance)
                time.sleep(1)

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
