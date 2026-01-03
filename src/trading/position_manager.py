"""Position monitoring and management"""

import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
from src.config.settings import (
    DB_FILE,
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
from src.trading.orders import get_clob_client, sell_position, place_order
from src.data.market_data import (
    get_token_ids,
    get_current_slug,
    get_window_times,
    get_funding_bias,
)
from src.data.database import save_trade


def check_open_positions(verbose: bool = True):
    """
    Check open positions and manage them

    Args:
        verbose: If True, log position checks. If False, only log actions (stop loss, etc.)
    """
    if not ENABLE_STOP_LOSS and not ENABLE_TAKE_PROFIT and not ENABLE_SCALE_IN:
        return

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    now = datetime.now(tz=ZoneInfo("UTC"))

    # Get unsettled trades that are still in their window
    c.execute(
        """SELECT id, symbol, slug, token_id, side, entry_price, size, bet_usd, window_end, scaled_in 
           FROM trades 
           WHERE settled = 0 
           AND exited_early = 0
           AND datetime(window_end) > datetime(?)""",
        (now.isoformat(),),
    )
    open_positions = c.fetchall()

    if not open_positions:
        conn.close()
        return

    if verbose:
        log(f"🔍 Checking {len(open_positions)} open positions...")

    client = get_clob_client()

    for (
        trade_id,
        symbol,
        slug,
        token_id,
        side,
        entry_price,
        size,
        bet_usd,
        window_end,
        scaled_in,
    ) in open_positions:
        try:
            # Get current market price
            book = client.get_order_book(token_id)
            if isinstance(book, dict):
                bids = book.get("bids", []) or []
                asks = book.get("asks", []) or []
            else:
                bids = getattr(book, "bids", []) or []
                asks = getattr(book, "asks", []) or []

            if not bids or not asks:
                continue

            best_bid = float(
                bids[-1].price
                if hasattr(bids[-1], "price")
                else bids[-1].get("price", 0)
            )
            best_ask = float(
                asks[-1].price
                if hasattr(asks[-1], "price")
                else asks[-1].get("price", 0)
            )
            current_price = (best_bid + best_ask) / 2.0

            # Calculate current P&L
            current_value = current_price * size
            pnl_usd = current_value - bet_usd
            pnl_pct = (pnl_usd / bet_usd) * 100 if bet_usd > 0 else 0

            if verbose:
                log(
                    f"  [{symbol}] Trade #{trade_id} {side}: Entry=${entry_price:.4f} Current=${current_price:.4f} PnL={pnl_pct:+.1f}%"
                )

            # Calculate time left until window ends
            window_end_dt = datetime.fromisoformat(window_end)
            time_left_seconds = (window_end_dt - now).total_seconds()

            # Check if we should scale into position
            if (
                ENABLE_SCALE_IN
                and not scaled_in
                and time_left_seconds <= SCALE_IN_TIME_LEFT
                and time_left_seconds > 0
                and SCALE_IN_MIN_PRICE <= current_price <= SCALE_IN_MAX_PRICE
            ):
                log(
                    f"📈 SCALE IN triggered for trade #{trade_id}: price=${current_price:.2f}, {time_left_seconds:.0f}s left"
                )

                # Calculate additional size
                additional_size = size * SCALE_IN_MULTIPLIER
                additional_bet = additional_size * current_price

                # Place additional order
                scale_result = place_order(token_id, current_price, additional_size)

                if scale_result["success"]:
                    # Update trade with new size and bet
                    new_total_size = size + additional_size
                    new_total_bet = bet_usd + additional_bet
                    new_avg_price = new_total_bet / new_total_size

                    c.execute(
                        """UPDATE trades 
                           SET size=?, bet_usd=?, entry_price=?, scaled_in=1 
                           WHERE id=?""",
                        (new_total_size, new_total_bet, new_avg_price, trade_id),
                    )

                    log(
                        f"✅ Scaled in! Size: {size:.2f} → {new_total_size:.2f} (+{additional_size:.2f})"
                    )
                    log(f"   Avg price: ${entry_price:.4f} → ${new_avg_price:.4f}")
                    send_discord(
                        f"📈 **SCALED IN** [{symbol}] {side} +${additional_bet:.2f} @ ${current_price:.2f} ({time_left_seconds:.0f}s left)"
                    )

            # Check stop loss
            if ENABLE_STOP_LOSS and pnl_pct <= -STOP_LOSS_PERCENT:
                log(
                    f"🛑 STOP LOSS triggered for trade #{trade_id}: {pnl_pct:.1f}% loss"
                )

                # Sell current position
                sell_result = sell_position(token_id, size, current_price)

                if sell_result["success"]:
                    # Mark as exited early
                    c.execute(
                        """UPDATE trades 
                           SET exited_early=1, exit_price=?, pnl_usd=?, roi_pct=?, 
                               final_outcome='STOP_LOSS', settled=1, settled_at=? 
                           WHERE id=?""",
                        (current_price, pnl_usd, pnl_pct, now.isoformat(), trade_id),
                    )
                    send_discord(
                        f"🛑 **STOP LOSS** [{symbol}] {side} closed at {pnl_pct:+.1f}%"
                    )

                    # Optionally reverse position
                    if ENABLE_REVERSAL:
                        log(f"🔄 Reversing position for [{symbol}]...")
                        # Get opposite token ID
                        up_id, down_id = get_token_ids(symbol)
                        if up_id and down_id:
                            opposite_token = down_id if side == "UP" else up_id
                            opposite_side = "DOWN" if side == "UP" else "UP"
                            opposite_price = 1.0 - current_price

                            # Place reverse order with same size
                            reverse_result = place_order(
                                opposite_token, opposite_price, size
                            )
                            if reverse_result["success"]:
                                log(
                                    f"✅ Reversed to {opposite_side} @ ${opposite_price:.4f}"
                                )
                                send_discord(
                                    f"🔄 **REVERSED** [{symbol}] Now {opposite_side}"
                                )

                                # Save reversed trade to database
                                try:
                                    window_start, window_end = get_window_times(symbol)
                                    bet_usd_effective = size * opposite_price

                                    save_trade(
                                        symbol=symbol,
                                        window_start=window_start.isoformat(),
                                        window_end=window_end.isoformat(),
                                        slug=get_current_slug(symbol),
                                        token_id=opposite_token,
                                        side=opposite_side,
                                        edge=0.0,  # Reversal trade, no edge calculation
                                        price=opposite_price,
                                        size=size,
                                        bet_usd=bet_usd_effective,
                                        p_yes=opposite_price
                                        if opposite_side == "UP"
                                        else 1.0 - opposite_price,
                                        best_bid=None,
                                        best_ask=None,
                                        imbalance=0.5,
                                        funding_bias=get_funding_bias(symbol),
                                        order_status=reverse_result["status"],
                                        order_id=reverse_result["order_id"],
                                    )
                                    log(f"✓ Reversed trade saved to database")
                                except Exception as e:
                                    log(f"⚠️ Error saving reversed trade: {e}")

            # Check take profit
            elif ENABLE_TAKE_PROFIT and pnl_pct >= TAKE_PROFIT_PERCENT:
                log(
                    f"🎯 TAKE PROFIT triggered for trade #{trade_id}: {pnl_pct:.1f}% gain"
                )

                # Sell current position
                sell_result = sell_position(token_id, size, current_price)

                if sell_result["success"]:
                    c.execute(
                        """UPDATE trades 
                           SET exited_early=1, exit_price=?, pnl_usd=?, roi_pct=?, 
                               final_outcome='TAKE_PROFIT', settled=1, settled_at=? 
                           WHERE id=?""",
                        (current_price, pnl_usd, pnl_pct, now.isoformat(), trade_id),
                    )
                    send_discord(
                        f"🎯 **TAKE PROFIT** [{symbol}] {side} closed at {pnl_pct:+.1f}%"
                    )

        except Exception as e:
            log(f"⚠️ Error checking position #{trade_id}: {e}")

    conn.commit()
    conn.close()
