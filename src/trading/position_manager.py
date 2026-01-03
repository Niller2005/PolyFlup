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
)
from src.utils.logger import log, send_discord
from src.trading.orders import get_clob_client, sell_position, place_order
from src.data.market_data import get_token_ids


def check_open_positions():
    """Check open positions every minute and manage them"""
    if not ENABLE_STOP_LOSS and not ENABLE_TAKE_PROFIT:
        return

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    now = datetime.now(tz=ZoneInfo("UTC"))

    # Get unsettled trades that are still in their window
    c.execute(
        """SELECT id, symbol, slug, token_id, side, entry_price, size, bet_usd, window_end 
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

            log(
                f"  [{symbol}] Trade #{trade_id} {side}: Entry=${entry_price:.4f} Current=${current_price:.4f} PnL={pnl_pct:+.1f}%"
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
