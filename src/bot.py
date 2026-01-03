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
)
from src.utils.logger import log, send_discord
from src.utils.web3_utils import get_balance
from src.data.database import init_database, save_trade, generate_statistics
from src.data.market_data import (
    get_token_ids,
    get_current_slug,
    get_window_times,
    get_funding_bias,
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
        log(f"[{symbol}] Market not found, skipping")
        return

    client = get_clob_client()
    edge, reason, p_up, best_bid, best_ask, imbalance = calculate_edge(
        symbol, up_id, client
    )

    # ============================================================
    # STRATEGY LOGIC
    # ============================================================
    # High edge (>= MIN_EDGE) = UP is overvalued -> Buy DOWN
    # Low edge (<= 1-MIN_EDGE) = UP is undervalued -> Buy UP
    # ============================================================

    if edge <= (1.0 - MIN_EDGE):
        token_id, side, price = up_id, "UP", p_up
        log(
            f"[{symbol}] 📉 LOW edge ({edge:.4f} <= {1.0 - MIN_EDGE:.4f}) -> UP is undervalued, buying UP"
        )
    elif edge >= MIN_EDGE:
        token_id, side, price = down_id, "DOWN", 1.0 - p_up
        log(
            f"[{symbol}] 📈 HIGH edge ({edge:.4f} >= {MIN_EDGE:.4f}) -> UP is overvalued, buying DOWN"
        )
    else:
        log(
            f"[{symbol}] ⚪ PASS | Edge {edge:.1%} in neutral zone ({1 - MIN_EDGE:.1%} - {MIN_EDGE:.1%})"
        )
        return

    # LOG: what we want to buy before trend filter
    log(f"[{symbol}] Direction decision: side={side}, edge={edge:.4f}, p_up={p_up:.4f}")

    # ADX trend strength filter
    log(f"[{symbol}] 📊 ADX check: enabled={ADX_ENABLED}, threshold={ADX_THRESHOLD}")
    if not adx_allows_trade(symbol):
        log(
            f"[{symbol}] ⛔ ADX FILTER BLOCKED TRADE (symbol={symbol}, side={side}) - Weak Trend ⛔"
        )
        return

    # BFXD trend filter
    if not bfxd_allows_trade(symbol, side):
        log(
            f"[{symbol}] ⛔ BFXD FILTER BLOCKED TRADE (symbol={symbol}, side={side}) ⛔"
        )
        return

    if price <= 0:
        log(f"[{symbol}] ERROR: Invalid price {price}")
        return

    price = max(0.01, min(0.99, price))

    # Use BET_PERCENT of available balance
    target_bet = balance * (BET_PERCENT / 100.0)
    log(
        f"[{symbol}] 🎯 Target bet: ${target_bet:.2f} ({BET_PERCENT}% of ${balance:.2f})"
    )

    size = round(target_bet / price, 6)

    MIN_SIZE = 5.0
    bet_usd_effective = target_bet

    if size < MIN_SIZE:
        old_size = size
        size = MIN_SIZE
        bet_usd_effective = round(size * price, 4)
        log(
            f"[{symbol}] Size {old_size:.4f} < min {MIN_SIZE}, bumping to {size:.4f}. "
            f"Effective stake ≈ ${bet_usd_effective:.2f}"
        )

    log(
        f"[{symbol}] 📈 {side} ${bet_usd_effective:.2f} | Edge {edge:.1%} | "
        f"Price {price:.4f} | Size {size} | Balance {balance:.2f}"
    )
    send_discord(
        f"**[{symbol}] {side} ${bet_usd_effective:.2f}** | Edge {edge:.1%} | Price {price:.4f}"
    )

    result = place_order(token_id, price, size)
    log(f"[{symbol}] Order status: {result['status']}")

    try:
        window_start, window_end = get_window_times(symbol)
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
        )
    except Exception as e:
        log(f"[{symbol}] Database error: {e}")


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
    log(f"🤖 POLYASTRA | Markets: {', '.join(MARKETS)}")
    log(
        f"💼 Wallet ({log_addr_type}): {addr[:10]}...{addr[-8:]} | Balance: {get_balance(addr):.2f} USDC"
    )
    log(
        f"⚙️  MIN_EDGE: {MIN_EDGE:.1%} | BET: {BET_PERCENT}% of balance | MAX_SPREAD: {MAX_SPREAD:.1%}"
    )
    log(f"🕒 WINDOW_DELAY_SEC: {WINDOW_DELAY_SEC}s")
    log(
        f"📈 ADX: {'YES' if ADX_ENABLED else 'NO'} | Threshold: {ADX_THRESHOLD} | Period: {ADX_PERIOD} | Interval: {ADX_INTERVAL}"
    )
    log("=" * 90)
    log(
        f"🛡️  Stop Loss: {'ENABLED' if ENABLE_STOP_LOSS else 'DISABLED'} ({STOP_LOSS_PERCENT}%)"
    )
    log(
        f"🎯 Take Profit: {'ENABLED' if ENABLE_TAKE_PROFIT else 'DISABLED'} ({TAKE_PROFIT_PERCENT}%)"
    )
    log(f"🔄 Auto Reverse: {'ENABLED' if ENABLE_REVERSAL else 'DISABLED'}")
    log(
        f"📈 Scale In: {'ENABLED' if ENABLE_SCALE_IN else 'DISABLED'} (${SCALE_IN_MIN_PRICE:.2f}-${SCALE_IN_MAX_PRICE:.2f}, {SCALE_IN_TIME_LEFT}s left, +{SCALE_IN_MULTIPLIER * 100:.0f}%)"
    )
    log("=" * 90)

    cycle = 0
    last_position_check = time.time()

    while True:
        try:
            # Check positions every 60 seconds
            now_ts = time.time()
            if now_ts - last_position_check >= 60:
                check_open_positions()
                last_position_check = now_ts

            now = datetime.utcnow()
            wait = 900 - ((now.minute % 15) * 60 + now.second)
            if wait <= 0:
                wait += 900

            # Wait in 60-second chunks so we can check positions
            log(f"⏱️  Waiting {wait}s until next window + {WINDOW_DELAY_SEC}s delay...")

            remaining = wait + WINDOW_DELAY_SEC
            while remaining > 0:
                sleep_time = min(60, remaining)
                time.sleep(sleep_time)
                remaining -= sleep_time

                # Check positions during wait
                if remaining > 0:
                    check_open_positions()

            log(
                f"\n{'=' * 90}\n🔄 CYCLE #{cycle + 1} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}\n{'=' * 90}\n"
            )

            # Fetch balance once for the cycle
            current_balance = get_balance(addr)
            log(f"💰 Current Balance: {current_balance:.2f} USDC")

            for sym in MARKETS:
                log(f"\n{'=' * 30} {sym} {'=' * 30}")
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
