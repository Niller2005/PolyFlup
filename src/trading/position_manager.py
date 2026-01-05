"""Position monitoring and management"""

import time
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, Any, List, Dict
from src.data.db_connection import db_connection
from src.config.settings import (
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
    CANCEL_UNFILLED_ORDERS,
    UNFILLED_CANCEL_THRESHOLD,
    UNFILLED_TIMEOUT_SECONDS,
    UNFILLED_RETRY_ON_WINNING_SIDE,
    ENABLE_EXIT_PLAN,
    EXIT_PRICE_TARGET,
    ENABLE_REWARD_OPTIMIZATION,
    EXIT_MIN_POSITION_AGE,
    EXIT_CHECK_INTERVAL,
    EXIT_AGGRESSIVE_MODE,
)
from src.utils.logger import log, send_discord
from src.trading.orders import (
    get_clob_client,
    sell_position,
    place_order,
    place_limit_order,
    cancel_order,
    get_order_status,
    get_order,
    get_midpoint,
    get_multiple_market_prices,
    get_balance_allowance,
    get_notifications,
    drop_notifications,
    get_current_positions,
    check_order_scoring,
    get_orders,
    BUY,
    SELL,
)
from src.data.market_data import (
    get_token_ids,
    get_current_slug,
    get_window_times,
    get_funding_bias,
    get_current_spot_price,
)
from src.data.database import save_trade
from src.utils.websocket_manager import ws_manager


# Threading lock to prevent concurrent position checks (prevents database locks)
_position_check_lock = threading.Lock()

# Module-level tracking for exit plan attempts to prevent spamming on errors (e.g. balance errors)
_last_exit_attempt = {}


def sync_positions_with_exchange(user_address: str):
    """
    Sync database state with actual positions on the exchange.
    Ensures size and entry prices are accurate and handles missing/extra positions.
    """
    log(f"🔄 Syncing positions with exchange for {user_address[:10]}...")

    try:
        # 1. Get positions from Data API
        exchange_positions = get_current_positions(user_address)

        # Create a map of token_id -> position_data for easy lookup
        # ENSURE token_id is handled as a normalized string for consistent comparison
        pos_map = {}
        for p in exchange_positions:
            aid = p.get("asset_id") or p.get("assetId") or p.get("token_id")
            if aid:
                # Normalize ID: strip and lowercase
                norm_aid = str(aid).strip().lower()
                pos_map[norm_aid] = p

        with db_connection() as conn:
            c = conn.cursor()
            now = datetime.now(tz=ZoneInfo("UTC"))

            # 2. Get all open trades from DB
            c.execute(
                "SELECT id, symbol, side, size, token_id, entry_price FROM trades WHERE settled = 0"
            )
            db_trades = c.fetchall()

            # Track which exchange positions were matched to DB trades
            matched_exchange_ids = set()
            db_token_ids = []

            for trade_id, symbol, side, db_size, token_id, db_entry in db_trades:
                # Normalize DB token_id
                tid_str = str(token_id).strip().lower() if token_id else ""
                db_token_ids.append(tid_str)
                
                if tid_str and tid_str in pos_map:
                    pos = pos_map[tid_str]
                    matched_exchange_ids.add(tid_str)
                    
                    # Data API uses snake_case: avg_price
                    actual_size = float(pos.get("size", 0))
                    actual_price = float(pos.get("avg_price") or pos.get("avgPrice") or db_entry)

                    # Check for significant size mismatch
                    if abs(actual_size - db_size) > 0.001:
                        log(
                            f"   📊 [{symbol}] #{trade_id} Sync: Size mismatch {db_size:.2f} -> {actual_size:.2f}"
                        )
                        c.execute(
                            "UPDATE trades SET size = ?, bet_usd = ? * ? WHERE id = ?",
                            (actual_size, actual_size, actual_price, trade_id),
                        )

                    # Check for entry price mismatch
                    if abs(actual_price - db_entry) > 0.0001:
                        log(
                            f"   📊 [{symbol}] #{trade_id} Sync: Price mismatch ${db_entry:.4f} -> ${actual_price:.4f}"
                        )
                        c.execute(
                            "UPDATE trades SET entry_price = ?, bet_usd = ? * ? WHERE id = ?",
                            (actual_price, actual_size, actual_price, trade_id),
                        )
                else:
                    # Trade is open in DB but not on exchange
                    c.execute("SELECT timestamp FROM trades WHERE id = ?", (trade_id,))
                    ts_row = c.fetchone()
                    if ts_row:
                        try:
                            trade_ts = datetime.fromisoformat(ts_row[0])
                            age_mins = (now - trade_ts).total_seconds() / 60.0
                        except:
                            age_mins = 999

                        if age_mins > 2.0:
                            log(
                                f"   ⚠️ [{symbol}] #{trade_id} exists in DB but not on exchange (size 0). Marking as settled/unfilled."
                            )
                            c.execute(
                                "UPDATE trades SET settled = 1, final_outcome = 'SYNC_MISSING' WHERE id = ?",
                                (trade_id,),
                            )

            # 3. Check for untracked positions
            for t_id_raw, p_data in pos_map.items():
                t_id_str = str(t_id_raw).strip().lower()
                if t_id_str and t_id_str not in db_token_ids:
                    size = float(p_data.get("size", 0))
                    if size < 0.001:
                        continue
                        
                    log(f"   ⚠️ Found UNTRACKED position: {size} shares of {t_id_str[:10]}...")
                    
                    try:
                        avg_price = float(p_data.get("avg_price") or p_data.get("avgPrice") or 0.5)
                        symbol = p_data.get("symbol") or p_data.get("market") or "ADOPTED"
                        
                        log(f"   📥 Adopting untracked position: {symbol} {size} shares @ ${avg_price}")
                        
                        c.execute(
                            """INSERT INTO trades (
                                symbol, slug, token_id, side, entry_price, size, bet_usd, 
                                timestamp, window_start, window_end, settled, order_status, final_outcome
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                symbol, 
                                "adopted-market", 
                                t_id_str, 
                                "UNKNOWN", 
                                avg_price, 
                                size, 
                                size * avg_price,
                                now.isoformat(),
                                now.isoformat(),
                                (now + timedelta(minutes=15)).isoformat(),
                                0,
                                "FILLED",
                                "ADOPTED"
                            )
                        )
                    except Exception as e:
                        log(f"   ❌ Failed to adopt position: {e}")

        log("✓ Position sync complete")
    except Exception as e:
        log(f"⚠️ Error during position sync: {e}")


def get_exit_plan_stats():
    """Get statistics on exit plan performance"""
    try:
        with db_connection() as conn:
            c = conn.cursor()
            c.execute("""
                SELECT 
                    COUNT(CASE WHEN final_outcome = 'EXIT_PLAN_FILLED' THEN 1 END) as exit_plan_successes,
                    COUNT(CASE WHEN final_outcome = 'RESOLVED' AND exited_early = 0 THEN 1 END) as natural_settlements,
                    COUNT(CASE WHEN final_outcome = 'LIMIT_SELL_099' THEN 1 END) as legacy_limit_sells,
                    AVG(CASE WHEN final_outcome = 'EXIT_PLAN_FILLED' THEN roi_pct END) as avg_exit_plan_roi,
                    AVG(CASE WHEN final_outcome = 'RESOLVED' AND exited_early = 0 THEN roi_pct END) as avg_natural_roi
                FROM trades 
                WHERE settled = 1 
                AND datetime(timestamp) >= datetime('now', '-7 days')
            """)
            stats = c.fetchone()
            if stats and any(stats):
                (
                    exit_successes,
                    natural_settlements,
                    legacy_sells,
                    avg_exit_roi,
                    avg_natural_roi,
                ) = stats
                total_exits = exit_successes + natural_settlements + legacy_sells
                if total_exits > 0:
                    exit_rate = (exit_successes / total_exits) * 100
                    log(
                        f"📊 EXIT PLAN STATS (7d): {exit_successes} successful exits ({exit_rate:.1f}%), {natural_settlements} natural settlements | Avg ROI: Exit {avg_exit_roi or 0:.1f}%, Natural {avg_natural_roi or 0:.1f}%"
                    )
                    return {
                        "exit_plan_successes": exit_successes,
                        "natural_settlements": natural_settlements,
                        "legacy_limit_sells": legacy_sells,
                        "exit_success_rate": exit_rate,
                        "avg_exit_plan_roi": avg_exit_roi or 0,
                        "avg_natural_roi": avg_natural_roi or 0,
                    }
            return None
    except Exception as e:
        log(f"⚠️ Error getting exit plan stats: {e}")
        return None


def recover_open_positions():
    """Recover open positions from database on startup"""
    with db_connection() as conn:
        c = conn.cursor()
        now = datetime.now(tz=ZoneInfo("UTC"))
        c.execute(
            """SELECT id, symbol, side, entry_price, size, bet_usd, window_end, order_status, timestamp, token_id
                   FROM trades WHERE settled = 0 AND exited_early = 0 AND datetime(window_end) > datetime(?)""",
            (now.isoformat(),),
        )
        open_positions = c.fetchall()

    if not open_positions:
        log("✓ No open positions to recover")
        return

    log("=" * 90)
    log(f"🔄 RECOVERING {len(open_positions)} OPEN POSITIONS FROM DATABASE")
    log("=" * 90)
    tokens_to_subscribe = []
    for t_id, sym, side, entry, size, bet, w_end, status, ts, tok in open_positions:
        try:
            w_end_dt = datetime.fromisoformat(w_end)
            t_left = (w_end_dt - now).total_seconds() / 60.0
            log(
                f"  [{sym}] Trade #{t_id} {side}: ${bet:.2f} @ ${entry:.4f} | Status: {status} | {t_left:.0f}m left"
            )
            if tok:
                tokens_to_subscribe.append(tok)
        except Exception as e:
            log(f"  ❌ Error recovering trade #{t_id}: {e}")
    if tokens_to_subscribe:
        ws_manager.subscribe_to_prices(tokens_to_subscribe)
    log("=" * 90)


def _get_position_pnl(token_id: str, entry_price: float, size: float, cached_prices: Optional[Dict[str, float]] = None) -> Optional[dict]:
    """Get current market price and calculate P&L"""
    current_price = None
    if cached_prices and str(token_id) in cached_prices:
        current_price = cached_prices[str(token_id)]
    
    if current_price is None:
        current_price = ws_manager.get_price(token_id)
    
    if current_price is None:
        current_price = get_midpoint(token_id)

    if current_price is None:
        client = get_clob_client()
        book: Any = client.get_order_book(token_id)
        if isinstance(book, dict):
            bids, asks = book.get("bids", []), book.get("asks", [])
        else:
            bids, asks = getattr(book, "bids", []), getattr(book, "asks", [])
        if not bids or not asks:
            return None
        best_bid = float(
            bids[-1].price if hasattr(bids[-1], "price") else bids[-1].get("price", 0)
        )
        best_ask = float(
            asks[-1].price if hasattr(asks[-1], "price") else asks[-1].get("price", 0)
        )
        current_price = (best_bid + best_ask) / 2.0
    pnl_usd = (current_price * size) - (entry_price * size)
    pnl_pct = (pnl_usd / (entry_price * size)) * 100 if size > 0 else 0
    return {
        "current_price": current_price,
        "pnl_pct": pnl_pct,
        "pnl_usd": pnl_usd,
        "price_change_pct": ((current_price - entry_price) / entry_price) * 100,
    }


def _check_stop_loss(
    symbol,
    trade_id,
    token_id,
    side,
    entry_price,
    size,
    pnl_pct,
    pnl_usd,
    current_price,
    target_price,
    limit_sell_order_id,
    is_reversal,
    c,
    conn,
    now,
    buy_order_status,
):
    """Check and execute stop loss with REVERSAL support"""
    c.execute("SELECT settled FROM trades WHERE id = ?", (trade_id,))
    if (row := c.fetchone()) and row[0] == 1:
        return True
    if not ENABLE_STOP_LOSS or pnl_pct > -STOP_LOSS_PERCENT or size == 0:
        return False
    if buy_order_status not in ["FILLED", "MATCHED"]:
        return False
    c.execute("SELECT timestamp FROM trades WHERE id = ?", (trade_id,))
    if row := c.fetchone():
        try:
            if (now - datetime.fromisoformat(row[0])).total_seconds() < 30:
                return False
        except:
            pass

    current_spot = get_current_spot_price(symbol)
    is_on_losing_side = True
    if current_spot > 0 and target_price:
        if side == "UP" and current_spot >= target_price:
            is_on_losing_side = False
        elif side == "DOWN" and current_spot <= target_price:
            is_on_losing_side = False

    if not is_on_losing_side:
        log(f"ℹ️ [{symbol}] PnL is bad ({pnl_pct:.1f}%) but on WINNING side - HOLDING")
        return False

    log(f"🛑 [{symbol}] #{trade_id} STOP LOSS: {pnl_pct:.1f}% PnL")
    if limit_sell_order_id:
        if cancel_order(limit_sell_order_id):
            time.sleep(2)

    sell_result = sell_position(token_id, size, current_price)
    if not sell_result["success"]:
        err = sell_result.get("error", "").lower()
        if "balance" in err or "allowance" in err:
            balance_info = get_balance_allowance(token_id)
            actual_balance = balance_info.get("balance", 0) if balance_info else 0
            if actual_balance >= 1.0:
                c.execute(
                    "UPDATE trades SET size = ? WHERE id = ?",
                    (actual_balance, trade_id),
                )
                return False
            c.execute(
                "UPDATE trades SET settled=1, final_outcome='UNFILLED_NO_BALANCE' WHERE id=?",
                (trade_id,),
            )
            return True
        return False

    c.execute(
        "UPDATE trades SET exited_early=1, exit_price=?, pnl_usd=?, roi_pct=?, final_outcome='STOP_LOSS', settled=1, settled_at=? WHERE id=?",
        (current_price, pnl_usd, pnl_pct, now.isoformat(), trade_id),
    )
    send_discord(f"🛑 STOP LOSS [{symbol}] {side} closed at {pnl_pct:+.1f}%")

    # REVERSAL LOGIC
    if not is_reversal and ENABLE_REVERSAL:
        opposite_side = "DOWN" if side == "UP" else "UP"
        log(f"🔄 Reversing [{symbol}] {side} → {opposite_side}")
        up_id, down_id = get_token_ids(symbol.split("-")[0])
        if up_id and down_id:
            opposite_token = down_id if side == "UP" else up_id
            opp_price = round(max(0.01, min(0.99, 1.0 - current_price)), 2)
            rev_res = place_order(opposite_token, opp_price, size)
            if rev_res["success"]:
                log(f"🚀 [{symbol}] Reversal order placed: {opposite_side} @ {opp_price}")
                send_discord(f"🔄 **REVERSED** [{symbol}] {side} → {opposite_side}")
                try:
                    w_start, w_end = get_window_times(symbol.split("-")[0])
                    save_trade(
                        cursor=c,
                        symbol=symbol,
                        window_start=w_start.isoformat(),
                        window_end=w_end.isoformat(),
                        slug=get_current_slug(symbol.split("-")[0]),
                        token_id=opposite_token,
                        side=opposite_side,
                        edge=0.0,
                        price=opp_price,
                        size=size,
                        bet_usd=size * opp_price,
                        p_yes=opp_price if opposite_side == "UP" else 1.0 - opp_price,
                        order_status=rev_res["status"],
                        order_id=rev_res["order_id"],
                        is_reversal=True,
                        target_price=target_price,
                    )
                except Exception as e:
                    log(f"⚠️ DB Error (reversal): {e}")
            else:
                log(f"⚠️ Reversal failed: {rev_res.get('error')}")

    return True


def _update_exit_plan_after_scale_in(
    symbol, trade_id, token_id, new_size, old_order_id, c, conn
):
    if not old_order_id or not ENABLE_EXIT_PLAN:
        return
    status = get_order_status(old_order_id)
    if status in ["FILLED", "MATCHED"]:
        return
    if cancel_order(old_order_id):
        res = place_limit_order(token_id, EXIT_PRICE_TARGET, new_size, SELL, True)
        if res["success"]:
            c.execute(
                "UPDATE trades SET limit_sell_order_id = ? WHERE id = ?",
                (res["order_id"], trade_id),
            )


def _update_exit_plan_to_new_price(
    symbol, trade_id, token_id, size, old_order_id, new_price, c, conn
):
    if not old_order_id:
        return
    if get_order_status(old_order_id) in ["FILLED", "MATCHED"]:
        return
    if cancel_order(old_order_id):
        res = place_limit_order(token_id, new_price, size, SELL, True)
        if res["success"]:
            c.execute(
                "UPDATE trades SET limit_sell_order_id = ? WHERE id = ?",
                (res["order_id"], trade_id),
            )


def _check_exit_plan(
    symbol,
    trade_id,
    token_id,
    size,
    buy_status,
    limit_sell_id,
    ts,
    c,
    conn,
    now,
    verbose,
    side,
    pnl_pct,
    price_change_pct,
    scaled_in,
    scale_in_id,
    entry,
    current_price,
):
    if not ENABLE_EXIT_PLAN or buy_status not in ["FILLED", "MATCHED"] or size == 0:
        return
    try:
        age = (now - datetime.fromisoformat(ts)).total_seconds()
    except:
        age = 0
    last_att = _last_exit_attempt.get(trade_id, 0)
    on_cd = now.timestamp() - last_att < 30

    if not limit_sell_id and age >= EXIT_MIN_POSITION_AGE:
        if on_cd:
            if verbose:
                log(f"   ⏳ [{symbol}] Exit plan cooldown: {30 - (now.timestamp() - last_att):.0f}s left (Trade age: {age:.0f}s)")
            return

        _last_exit_attempt[trade_id] = now.timestamp()
        
        # Check for existing SELL orders on exchange before placing new one
        try:
            open_orders = get_orders(asset_id=token_id)
            for o in open_orders:
                o_side = o.get("side") if isinstance(o, dict) else getattr(o, "side", "")
                if o_side == "SELL":
                    oid = o.get("id") if isinstance(o, dict) else getattr(o, "id", "")
                    if oid:
                        oid_str = str(oid)
                        log(f"   📥 [{symbol}] Found existing exit order on exchange: {oid_str[:10]}... Adopting.")
                        c.execute(
                            "UPDATE trades SET limit_sell_order_id = ? WHERE id = ?",
                            (oid_str, trade_id),
                        )
                        return
        except Exception as e:
            if verbose:
                log(f"   ⚠️ [{symbol}] Error checking existing orders: {e}")

        balance_info = get_balance_allowance(token_id)
        actual_bal = balance_info.get("balance", 0) if balance_info else 0
        
        if actual_bal < size:
            if actual_bal > 0:
                # ALWAYS update size if we have some balance but less than expected
                # This fixes the "stuck" exit plan due to minor rounding differences
                diff = size - actual_bal
                log(f"   🔧 [{symbol}] #{trade_id} Adjusting size to match balance: {size:.4f} -> {actual_bal:.4f} (diff: {diff:.6f})")
                c.execute(
                    "UPDATE trades SET size = ?, bet_usd = ? * entry_price WHERE id = ?", 
                    (actual_bal, actual_bal, trade_id)
                )
                size = actual_bal
            else:
                # Balance is 0
                if age > 300:
                    log(f"   ⚠️ [{symbol}] #{trade_id} has 0 balance after 5m. Settling as ghost trade.")
                    c.execute(
                        "UPDATE trades SET settled=1, final_outcome='GHOST_TRADE_ZERO_BAL' WHERE id=?",
                        (trade_id,),
                    )
                    return
                if verbose:
                    log(f"   ⏳ [{symbol}] #{trade_id} Exit pending: Balance is 0.0 (waiting for API sync...)")
                return

        res = place_limit_order(token_id, EXIT_PRICE_TARGET, size, SELL)
        if res["success"] or res.get("order_id"):
            oid = res.get("order_id")
            c.execute(
                "UPDATE trades SET limit_sell_order_id = ? WHERE id = ?",
                (oid, trade_id),
            )
            limit_sell_id = oid

    if verbose and side:
        scoring_text = ""
        if limit_sell_id:
            is_scoring = check_order_scoring(limit_sell_id)
            scoring_text = " | ✅ SCORING" if is_scoring else " | ❌ NOT SCORING"
            if not is_scoring and ENABLE_REWARD_OPTIMIZATION and current_price > 0.90:
                opt_price = round(current_price + 0.01, 2)
                if opt_price < EXIT_PRICE_TARGET and opt_price >= 0.95:
                    _update_exit_plan_to_new_price(
                        symbol,
                        trade_id,
                        token_id,
                        size,
                        limit_sell_id,
                        opt_price,
                        c,
                        conn,
                    )
                    scoring_text = " | 🔄 OPTIMIZING"

        status = f"Trade #{trade_id} {side} PnL={price_change_pct:+.1f}%"
        if scaled_in:
            status += " | 📊 Scaled in"
        if limit_sell_id:
            status += f" | ⏰ Exit active ({age:.0f}s){scoring_text}"
        else:
            wait_text = ""
            if age < EXIT_MIN_POSITION_AGE:
                wait_text = f" (Waiting {EXIT_MIN_POSITION_AGE - age:.0f}s)"
            status += f" | ⏳ Exit pending ({age:.0f}s){wait_text}"
        log(f"  {'📈' if pnl_pct > 0 else '📉'} [{symbol}] {status}")


def _check_scale_in(
    symbol,
    trade_id,
    token_id,
    entry,
    size,
    bet,
    scaled_in,
    scale_in_id,
    t_left,
    current_price,
    check_orders,
    c,
    conn,
    side,
    price_change_pct,
):
    if not ENABLE_SCALE_IN:
        return
    if scale_in_id and check_orders:
        try:
            o_data = get_order(scale_in_id)
            if o_data and o_data.get("status", "").upper() in ["FILLED", "MATCHED"]:
                s_price = float(o_data.get("price", current_price))
                s_matched = float(o_data.get("size_matched", 0))
                if s_matched > 0:
                    new_size, new_bet = size + s_matched, bet + (s_matched * s_price)
                    c.execute(
                        "SELECT limit_sell_order_id FROM trades WHERE id = ?",
                        (trade_id,),
                    )
                    ls_row = c.fetchone()
                    l_sell_id = ls_row[0] if ls_row else None
                    c.execute(
                        "UPDATE trades SET size=?, bet_usd=?, entry_price=?, scaled_in=1, scale_in_order_id=NULL WHERE id=?",
                        (new_size, new_bet, new_bet / new_size, trade_id),
                    )
                    _update_exit_plan_after_scale_in(
                        symbol, trade_id, token_id, new_size, l_sell_id, c, conn
                    )
                    return
            elif o_data and o_data.get("status", "").upper() in ["CANCELED", "EXPIRED"]:
                c.execute(
                    "UPDATE trades SET scale_in_order_id = NULL WHERE id = ?",
                    (trade_id,),
                )
        except:
            pass

    if scaled_in or scale_in_id:
        return
    if (
        t_left > SCALE_IN_TIME_LEFT
        or t_left <= 0
        or not (SCALE_IN_MIN_PRICE <= current_price <= SCALE_IN_MAX_PRICE)
    ):
        return

    s_size = size * SCALE_IN_MULTIPLIER
    s_price = round(max(0.01, min(0.99, current_price)), 2)
    res = place_order(token_id, s_price, s_size)
    if res["success"]:
        if res["status"].upper() in ["FILLED", "MATCHED"]:
            new_size, new_bet = size + s_size, bet + (s_size * s_price)
            c.execute(
                "UPDATE trades SET size=?, bet_usd=?, entry_price=?, scaled_in=1, scale_in_order_id=NULL WHERE id=?",
                (new_size, new_bet, new_bet / new_size, trade_id),
            )
        else:
            c.execute(
                "UPDATE trades SET scale_in_order_id = ? WHERE id = ?",
                (res["order_id"], trade_id),
            )


def check_open_positions(verbose=True, check_orders=False):
    if not _position_check_lock.acquire(blocking=False):
        return
    try:
        with db_connection() as conn:
            c = conn.cursor()
            now = datetime.now(tz=ZoneInfo("UTC"))
            c.execute(
                "SELECT id, symbol, slug, token_id, side, entry_price, size, bet_usd, window_end, scaled_in, is_reversal, target_price, limit_sell_order_id, order_id, order_status, timestamp, scale_in_order_id FROM trades WHERE settled = 0 AND exited_early = 0 AND datetime(window_end) > datetime(?)",
                (now.isoformat(),),
            )
            open_positions = c.fetchall()
            if not open_positions:
                return

            # PRIORITY 1: Batch price fetching
            token_ids = list(set([str(p[3]) for p in open_positions if p[3]]))
            
            # Try to get prices from WS cache first
            cached_prices = {}
            missing_tokens = []
            for tid in token_ids:
                price = ws_manager.get_price(tid)
                if price:
                    cached_prices[tid] = price
                else:
                    missing_tokens.append(tid)
            
            # Fetch missing prices in batch
            if missing_tokens:
                batch_prices = get_multiple_market_prices(missing_tokens)
                cached_prices.update(batch_prices)

            if verbose:
                log(f"👀 Monitoring {len(open_positions)} positions...")
            for (
                tid,
                sym,
                slug,
                tok,
                side,
                entry,
                size,
                bet,
                w_end,
                sc_in,
                rev,
                target,
                l_sell,
                b_id,
                b_status,
                ts,
                sc_id,
            ) in open_positions:
                try:
                    c.execute("SELECT settled FROM trades WHERE id = ?", (tid,))
                    chk_res = c.fetchone()
                    if chk_res and chk_res[0] == 1:
                        continue
                    curr_b_status = b_status
                    if b_id and (b_status != "FILLED" or entry == 0):
                        curr_b_status = get_order_status(b_id)
                        if curr_b_status in ["FILLED", "MATCHED"]:
                            o_data = get_order(b_id)
                            if o_data:
                                sz = float(o_data.get("size_matched", size))
                                pr = float(o_data.get("price", entry))
                                if (abs(sz - size) > 0.001 and sz > 0) or abs(
                                    pr - entry
                                ) > 0.0001:
                                    size, entry, bet = sz, pr, sz * pr
                                    c.execute(
                                        "UPDATE trades SET size = ?, entry_price = ?, bet_usd = ? WHERE id = ?",
                                        (size, entry, bet, tid),
                                    )
                            c.execute(
                                "UPDATE trades SET order_status = 'FILLED' WHERE id = ?",
                                (tid,),
                            )
                            curr_b_status = "FILLED"
                    
                    if verbose and curr_b_status not in ["FILLED", "MATCHED"]:
                        log(f"  ⏳ [{sym}] #{tid} {side}: Waiting for fill (Status: {curr_b_status})")
                        continue

                    if check_orders and l_sell:
                        o_data = get_order(l_sell)
                        if o_data and o_data.get("status", "").upper() in [
                            "FILLED",
                            "MATCHED",
                        ]:
                            ex_p = float(o_data.get("price", EXIT_PRICE_TARGET))
                            sz_m = float(o_data.get("size_matched", size))
                            pnl_val_f = (ex_p * sz_m) - bet
                            roi_val_f = (pnl_val_f / bet) * 100 if bet > 0 else 0
                            log(f"💰 [{sym}] #{tid} {side}: {pnl_val_f:+.2f}$ ({roi_val_f:+.1f}%)")
                            c.execute(
                                "UPDATE trades SET order_status = 'EXIT_PLAN_FILLED', settled=1, exited_early=1, exit_price=?, pnl_usd=?, roi_pct=?, settled_at=? WHERE id=?",
                                (ex_p, pnl_val_f, roi_val_f, now.isoformat(), tid),
                            )
                            continue

                    pnl_i = _get_position_pnl(tok, entry, size, cached_prices)
                    if not pnl_i:
                        continue
                    cur_p, p_pct_val, p_usd_val, p_chg_val = (
                        pnl_i["current_price"],
                        pnl_i["pnl_pct"],
                        pnl_i["pnl_usd"],
                        pnl_i["price_change_pct"],
                    )
                    if _check_stop_loss(
                        sym,
                        tid,
                        tok,
                        side,
                        entry,
                        size,
                        p_pct_val,
                        p_usd_val,
                        cur_p,
                        target,
                        l_sell,
                        rev,
                        c,
                        conn,
                        now,
                        curr_b_status,
                    ):
                        continue

                    try:
                        w_dt = (
                            datetime.fromisoformat(w_end)
                            if isinstance(w_end, str)
                            else w_end
                        )
                        t_left = (w_dt - now).total_seconds()
                    except:
                        t_left = 0

                    _check_scale_in(
                        sym,
                        tid,
                        tok,
                        entry,
                        size,
                        bet,
                        sc_in,
                        sc_id,
                        t_left,
                        cur_p,
                        check_orders,
                        c,
                        conn,
                        side,
                        p_chg_val,
                    )

                    # Refresh data after scale-in check
                    c.execute(
                        "SELECT size, entry_price, bet_usd, scaled_in, limit_sell_order_id, scale_in_order_id FROM trades WHERE id = ?",
                        (tid,),
                    )
                    row_data_f = c.fetchone()
                    if row_data_f:
                        size, entry, bet, sc_in, l_sell, sc_id = row_data_f

                    _check_exit_plan(
                        sym,
                        tid,
                        tok,
                        size,
                        curr_b_status,
                        l_sell,
                        ts,
                        c,
                        conn,
                        now,
                        verbose,
                        side,
                        p_pct_val,
                        p_chg_val,
                        sc_in,
                        sc_id,
                        entry,
                        cur_p,
                    )
                except Exception as e:
                    log(f"⚠️ [{sym}] #{tid} Error: {e}")
    finally:
        _position_check_lock.release()
