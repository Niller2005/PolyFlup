"""Main bot loop"""

import time
from typing import Optional
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
    BET_PERCENT,
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
    calculate_confidence,
    bfxd_allows_trade,
)


from src.trading.orders import (
    setup_api_creds,
    place_order,
    place_batch_orders,
    get_clob_client,
)
from src.trading.position_manager import (
    check_open_positions,
    recover_open_positions,
    get_exit_plan_stats,
)
from src.trading.settlement import check_and_settle_trades


def _determine_trade_side(bias: str, confidence: float) -> tuple[str, float]:
    """Determine actual trading side and confidence for sizing"""
    if confidence >= 0.2:
        actual_side = bias
        sizing_confidence = confidence
    else:
        actual_side = "DOWN" if bias == "UP" else "UP"
        sizing_confidence = 0.2

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

    size = round(target_bet / price, 6)

    MIN_SIZE = 5.0
    bet_usd_effective = target_bet

    if size < MIN_SIZE:
        size = MIN_SIZE
        bet_usd_effective = round(size * price, 4)

    return size, bet_usd_effective


def _prepare_trade_params(symbol: str, balance: float) -> Optional[dict]:
    """
    Prepare trade parameters without executing the order

    Returns:
        Dict with trade parameters or None if no trade should be made
    """
    up_id, down_id = get_token_ids(symbol)
    if not up_id or not down_id:
        log(f"[{symbol}] ❌ Market not found")
        return

    client = get_clob_client()
    confidence, bias, p_up, best_bid, best_ask, signals = calculate_confidence(
        symbol, up_id, client
    )

    if bias == "NEUTRAL":
        log(f"[{symbol}] ⚪ Confidence: {confidence:.1%} ({bias}) - NO TRADE")
        return

    actual_side, sizing_confidence = _determine_trade_side(bias, confidence)

    if confidence >= 0.2:
        log(f"[{symbol}] ✅ Trend Following: {bias} (Confidence: {confidence:.1%})")
    else:
        log(
            f"[{symbol}] 🔄 Contrarian Entry: {actual_side} (Original Bias: {bias} @ {confidence:.1%})"
        )

    if actual_side == "UP":
        token_id, side, price = up_id, "UP", p_up
    else:
        token_id, side, price = down_id, "DOWN", 1.0 - p_up

    target_price = float(get_window_start_price(symbol))

    current_spot = 0.0
    if isinstance(signals, dict):
        current_spot = float(signals.get("current_spot", 0))

    if not _check_target_price_alignment(
        symbol, side, confidence, current_spot, target_price
    ):
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
        return

    log(f"[{symbol}] ✅ {core_summary} | status: ENTERING TRADE")

    if price <= 0:
        log(f"[{symbol}] ERROR: Invalid price {price}")
        return

    price = max(0.01, min(0.99, price))

    size, bet_usd_effective = _calculate_bet_size(balance, price, sizing_confidence)

    # Return trade parameters
    window_start, window_end = get_window_times(symbol)
    target_price_final = get_window_start_price(symbol)

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
        "target_price": target_price_final if target_price_final > 0 else None,
        "window_start": window_start,
        "window_end": window_end,
        "slug": get_current_slug(symbol),
    }


def trade_symbol(symbol: str, balance: float):
    """Execute trading logic for a symbol (single order)"""
    trade_params = _prepare_trade_params(symbol, balance)

    if not trade_params:
        return

    # Place order
    result = place_order(
        trade_params["token_id"], trade_params["price"], trade_params["size"]
    )

    # Only proceed if order was successful
    if not result["success"]:
        log(f"[{trade_params['symbol']}] ❌ Order failed, skipping trade tracking")
        return

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
        log(
            f"[{trade_params['symbol']}] 🚀 #{trade_id} {trade_params['side']} ${trade_params['bet_usd']:.2f} @ {trade_params['price']:.4f} | {result['status']} | ID: {result['order_id'][:10] if result['order_id'] else 'N/A'}"
        )
    except Exception as e:
        log(f"[{trade_params['symbol']}] Trade completion error: {e}")


def trade_symbols_batch(symbols: list, balance: float):
    """Execute trading logic for multiple symbols using batch orders"""
    # Prepare all trades
    trade_params_list = []
    for symbol in symbols:
        params = _prepare_trade_params(symbol, balance)
        if params:
            trade_params_list.append(params)

    if not trade_params_list:
        return

    # Build batch order list
    batch_orders = []
    for params in trade_params_list:
        batch_orders.append(
            {
                "token_id": params["token_id"],
                "price": params["price"],
                "size": params["size"],
                "side": "BUY",  # All initial orders are BUYs
            }
        )

    # Place batch orders
    results = place_batch_orders(batch_orders)

    # Process results and save trades
    for params, result in zip(trade_params_list, results):
        if not result["success"]:
            log(
                f"[{params['symbol']}] ❌ Order failed: {result.get('error', 'Unknown error')}"
            )
            continue

        send_discord(
            f"**[{params['symbol']}] {params['side']} ${params['bet_usd']:.2f}** | Confidence {params['confidence']:.1%} | Price {params['price']:.4f}"
        )

        try:
            trade_id = save_trade(
                symbol=params["symbol"],
                window_start=params["window_start"].isoformat(),
                window_end=params["window_end"].isoformat(),
                slug=params["slug"],
                token_id=params["token_id"],
                side=params["side"],
                edge=params["confidence"],
                price=params["price"],
                size=params["size"],
                bet_usd=params["bet_usd"],
                p_yes=params["p_up"],
                best_bid=params["best_bid"],
                best_ask=params["best_ask"],
                imbalance=params["imbalance"],
                funding_bias=params["funding_bias"],
                order_status=result["status"],
                order_id=result["order_id"],
                limit_sell_order_id=None,
                target_price=params["target_price"],
            )
            log(
                f"[{params['symbol']}] 🚀 #{trade_id} {params['side']} ${params['bet_usd']:.2f} @ {params['price']:.4f} | {result['status']} | ID: {result['order_id'][:10] if result['order_id'] else 'N/A'}"
            )
        except Exception as e:
            log(f"[{params['symbol']}] Trade completion error: {e}")


def main():
    """Main bot loop"""
    log("🚀 Starting PolyAstra Trading Bot (Modular Version)...")
    log(
        f"📊 ADX System: {'INTEGRATED' if ADX_ENABLED else 'DISABLED'} (period={ADX_PERIOD}, interval={ADX_INTERVAL})"
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
    log(f"⚙️  MIN_EDGE: {MIN_EDGE:.1%} | BET: {BET_PERCENT}%")
    log("=" * 90)

    # Recover and start monitoring any existing open positions
    recover_open_positions()

    # Immediately check positions to ensure stop loss/take profit monitoring is active
    log("🔍 Performing initial position check...")
    check_open_positions(verbose=True, check_orders=True)

    cycle = 0
    last_position_check = time.time()
    last_order_check = time.time()
    last_verbose_log = time.time()
    last_exit_stats_log = time.time()

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
            log(
                f"💰 Balance: {current_balance:.2f} USDC | Evaluating {len(MARKETS)} markets"
            )

            # Use batch orders for multiple markets (more efficient)
            if len(MARKETS) > 1:
                trade_symbols_batch(MARKETS, current_balance)
            elif len(MARKETS) == 1:
                trade_symbol(MARKETS[0], current_balance)

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
