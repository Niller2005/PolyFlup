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
    adx_allows_trade,
    bfxd_allows_trade,
)


from src.trading.orders import (
    setup_api_creds,
    place_order,
    get_clob_client,
)
from src.trading.position_manager import check_open_positions, recover_open_positions
from src.trading.settlement import check_and_settle_trades


def trade_symbol(symbol: str, balance: float):
    """Execute trading logic for a symbol"""
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

    # Direction Logic:
    # - If confidence > 20%: Follow the bias
    # - If confidence < 20%: Enter OPPOSITE (contrarian play)
    if confidence >= 0.2:
        actual_side = bias
        sizing_confidence = confidence
        log(f"[{symbol}] ✅ Trend Following: {bias} (Confidence: {confidence:.1%})")
    else:
        actual_side = "DOWN" if bias == "UP" else "UP"
        sizing_confidence = 0.2  # Small fixed size for contrarian bets
        log(
            f"[{symbol}] 🔄 Contrarian Entry: {actual_side} (Original Bias: {bias} @ {confidence:.1%})"
        )

    if actual_side == "UP":
        token_id, side, price = up_id, "UP", p_up
    else:
        token_id, side, price = down_id, "DOWN", 1.0 - p_up

    # Check target price alignment (using actual_side)
    target_price = float(get_window_start_price(symbol))

    current_spot = 0.0
    if isinstance(signals, dict):
        current_spot = float(signals.get("current_spot", 0))

    if target_price > 0 and current_spot > 0:
        # Determine if we are on the 'winning' or 'losing' side
        # UP is winning if current_spot > target_price
        # DOWN is winning if current_spot < target_price
        is_winning_side = False
        if side == "UP" and current_spot > target_price:
            is_winning_side = True
        elif side == "DOWN" and current_spot < target_price:
            is_winning_side = True

        if not is_winning_side:
            # We are on the 'losing' side - check if confidence is high enough to bypass
            # High confidence threshold for entering 'losing' side (e.g. 60%)
            LOSING_SIDE_BYPASS_CONFIDENCE = 0.60
            if confidence < LOSING_SIDE_BYPASS_CONFIDENCE:
                log(
                    f"[{symbol}] ⚠️ {side} is on LOSING side (Spot ${current_spot:,.2f} vs Target ${target_price:,.2f}) and confidence {confidence:.1%} < {LOSING_SIDE_BYPASS_CONFIDENCE:.0%}. SKIPPING."
                )
                return
            else:
                log(
                    f"[{symbol}] 🔥 HIGH CONFIDENCE BYPASS: Entering {side} on losing side (Confidence: {confidence:.1%})"
                )

    # Check filters
    adx_ok, adx_val = adx_allows_trade(symbol)
    bfxd_ok, bfxd_trend = bfxd_allows_trade(symbol, side)

    # RSI from signals
    rsi = 50.0
    imbalance_val = 0.5
    if isinstance(signals, dict):
        mom = signals.get("momentum", {})
        if isinstance(mom, dict):
            rsi = mom.get("rsi", 50.0)

        of = signals.get("order_flow", {})
        if isinstance(of, dict):
            imbalance_val = of.get("buy_pressure", 0.5)

    filter_text = f"ADX: {adx_val:.1f} {'✅' if adx_ok else '❌'}"

    if ENABLE_BFXD and symbol == "BTC":
        filter_text += f" | BFXD: {bfxd_trend} {'✅' if bfxd_ok else '❌'}"

    core_summary = (
        f"Confidence: {confidence:.1%} ({bias}) | {filter_text} | RSI: {rsi:.1f}"
    )

    if not adx_ok or not bfxd_ok:
        log(f"[{symbol}] ⛔ {core_summary} | status: BLOCKED")
        return

    log(f"[{symbol}] ✅ {core_summary} | status: ENTERING TRADE")

    if price <= 0:
        log(f"[{symbol}] ERROR: Invalid price {price}")
        return

    price = max(0.01, min(0.99, price))

    # SIZING: base_bet scaled by confidence (0.2 to 1.0)
    # Higher confidence = larger position, up to 4x base_bet
    base_bet = balance * (BET_PERCENT / 100.0)
    confidence_multiplier = 0.5 + (sizing_confidence * 3.5)  # Scale to 0.5x - 4.0x
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
        f"**[{symbol}] {side} ${bet_usd_effective:.2f}** | Confidence {confidence:.1%} | Price {price:.4f}"
    )

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
            edge=confidence,  # Store confidence in edge column for now
            price=price,
            size=size,
            bet_usd=bet_usd_effective,
            p_yes=p_up,
            best_bid=best_bid,
            best_ask=best_ask,
            imbalance=imbalance_val,
            funding_bias=get_funding_bias(symbol),
            order_status=result["status"],
            order_id=result["order_id"],
            limit_sell_order_id=None,
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

    # Recover and start monitoring any existing open positions
    recover_open_positions()

    # Immediately check positions to ensure stop loss/take profit monitoring is active
    log("🔍 Performing initial position check...")
    check_open_positions(verbose=True, check_orders=True)

    cycle = 0
    last_position_check = time.time()
    last_order_check = time.time()
    last_verbose_log = time.time()

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
