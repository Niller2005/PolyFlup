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
)
from src.utils.logger import log, send_discord
from src.utils.web3_utils import get_balance
from src.data.database import init_database, save_trade, generate_statistics
from src.data.market_data import (
    get_token_ids,
    get_current_slug,
    get_window_times,
    get_funding_bias,
    get_current_spot_price,
)
from src.trading.strategy import (
    calculate_edge,
    adx_allows_trade,
    bfxd_allows_trade,
)
from src.trading.orders import setup_api_creds, place_order, get_clob_client
from src.trading.position_manager import check_open_positions
from src.trading.settlement import check_and_settle_trades


def trade_symbol(symbol: str, balance: float):
    """Execute trading logic for a symbol"""
    up_id, down_id = get_token_ids(symbol)
    if not up_id or not down_id:
        log(f"[{symbol}] ❌ Market not found")
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
    target_bet = balance * (BET_PERCENT / 100.0)
    size = round(target_bet / price, 6)

    MIN_SIZE = 5.0
    bet_usd_effective = target_bet

    if size < MIN_SIZE:
        size = MIN_SIZE
        bet_usd_effective = round(size * price, 4)

    log(
        f"[{symbol}] 🚀 TRADE: {side} ${bet_usd_effective:.2f} @ {price:.4f} | Size {size}"
    )
    send_discord(
        f"**[{symbol}] {side} ${bet_usd_effective:.2f}** | Edge {edge:.1%} | Price {price:.4f}"
    )

    result = place_order(token_id, price, size)
    log(f"[{symbol}] 📋 Order: {result['status']} | ID: {result['order_id'][:10]}...")

    try:
        window_start, window_end = get_window_times(symbol)
        target_price = get_current_spot_price(symbol)

        save_trade(
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
            target_price=target_price if target_price > 0 else None,
        )
    except Exception as e:
        log(f"[{symbol}] DB error: {e}")


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
    last_verbose_log = time.time()

    while True:
        try:
            # Check positions every 1 second (verbose log every 60 seconds)
            now_ts = time.time()
            if now_ts - last_position_check >= 1:
                verbose = now_ts - last_verbose_log >= 60
                check_open_positions(verbose=verbose)
                last_position_check = now_ts
                if verbose:
                    last_verbose_log = now_ts

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
                    check_open_positions(verbose=verbose)
                    if verbose:
                        last_verbose_log = now_ts

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
