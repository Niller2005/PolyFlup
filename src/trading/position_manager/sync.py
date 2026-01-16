"""Position and order synchronization and recovery"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from src.data.db_connection import db_connection
from src.utils.logger import log
from src.trading.orders import get_current_positions, normalize_token_id, get_orders
from src.utils.websocket_manager import ws_manager
from src.data.market_data import get_token_ids, get_window_times, get_current_slug
from src.data.database import save_trade
from src.trading.settlement import get_market_resolution


def sync_orders_with_exchange():
    """
    Sync order status with the exchange.
    Updates order_status for entry orders.
    """
    log("🔄 Syncing open orders with exchange...")

    try:
        exchange_orders = get_orders()

        if not exchange_orders:
            log("   ℹ No open orders on exchange")
            return

        log(f"   ✅ Exchange returned {len(exchange_orders)} open orders")

        with db_connection() as conn:
            c = conn.cursor()

            orders_map = {}
            for o in exchange_orders:
                o_id = o.get("id") or o.get("orderID") or getattr(o, "id", None)
                if o_id:
                    orders_map[str(o_id)] = o

            c.execute(
                """SELECT id, symbol, order_id, order_status
                   FROM trades WHERE settled = 0 AND order_id IS NOT NULL"""
            )
            db_trades = c.fetchall()

            updated_count = 0
            for trade_id, symbol, order_id, current_status in db_trades:
                if order_id and order_id in orders_map:
                    o = orders_map[order_id]
                    status = o.get("status", "").upper()
                    size_matched = float(
                        o.get("size_matched") or getattr(o, "size_matched", 0)
                    )

                    if current_status != status:
                        log(
                            f"   📊 [{symbol}] #{trade_id} Order {order_id[:10]}: {current_status} -> {status} | Matched: {size_matched:.2f}"
                        )
                        c.execute(
                            "UPDATE trades SET order_status = ? WHERE id = ?",
                            (status, trade_id),
                        )
                        updated_count += 1

            log(f"✓ Order sync complete: {updated_count} updated")
    except Exception as e:
        log(f"⚠️  Error during order sync: {e}")


def sync_positions_with_exchange(user_address: str):
    """
    Sync database state with actual positions on the exchange.
    Ensures size and entry prices are accurate and handles missing/extra positions.
    """
    log(f"🔄 Syncing positions with exchange for {user_address[:10]}...")

    try:
        exchange_positions = get_current_positions(user_address)

        # Create a map of token_id -> position_data for easy lookup
        # We normalize to decimal string for the primary key
        position_map = {}
        for p in exchange_positions:
            aid = (
                p.get("asset")
                or p.get("asset_id")
                or p.get("assetId")
                or p.get("token_id")
            )
            if aid:
                norm_aid = normalize_token_id(aid)
                position_map[norm_aid] = p

        with db_connection() as conn:
            c = conn.cursor()
            now = datetime.now(tz=ZoneInfo("UTC"))

            # 2. Get all open trades from DB
            c.execute(
                "SELECT id, symbol, side, size, token_id, entry_price FROM trades WHERE settled = 0"
            )
            db_trades = c.fetchall()

            # Get all traded token IDs from DB to avoid re-adopting settled ones
            c.execute("SELECT DISTINCT token_id FROM trades")
            all_tracked_token_ids = set()
            for (tid,) in c.fetchall():
                if tid:
                    all_tracked_token_ids.add(normalize_token_id(tid))

            # Track which exchange positions were matched to DB trades (for open ones)
            matched_exchange_ids = set()

            for trade_id, symbol, side, db_size, token_id, db_entry in db_trades:
                tid_str = normalize_token_id(token_id)

                # Check match in position_map
                if tid_str and tid_str in position_map:
                    pos = position_map[tid_str]
                    matched_exchange_ids.add(tid_str)

                    actual_size = float(pos.get("size", 0))
                    actual_price = float(
                        pos.get("avg_price") or pos.get("avgPrice") or db_entry
                    )

                    # Check for significant size mismatch
                    if abs(actual_size - db_size) > 0.001:
                        # CRITICAL FIX: Validate sync data before updating active positions
                        # Prevent syncing active positions to near-zero due to API timing issues
                        if db_size >= 5.0 and actual_size < 0.1:
                            log(
                                f"   ⚠️  [{symbol}] #{trade_id} Rejecting sync: Active position ({db_size:.2f}) "
                                f"would sync to near-zero ({actual_size:.4f}). Likely API timing issue."
                            )
                        else:
                            log(
                                f"   📊 [{symbol}] #{trade_id} Sync: Size mismatch {db_size:.2f} -> {actual_size:.2f}"
                            )

                            # Check if position grew (scale-in filled) and needs additional hedge
                            if actual_size > db_size:
                                size_increase = actual_size - db_size

                                # Check if we have a hedge order for this position
                                c.execute(
                                    "SELECT hedge_order_id, is_hedged FROM trades WHERE id = ?",
                                    (trade_id,),
                                )
                                hedge_row = c.fetchone()

                                if hedge_row and hedge_row[0] and not hedge_row[1]:
                                    # Hedge was placed but not fully filled yet
                                    log(
                                        f"   🔍 [{symbol}] #{trade_id} Position grew by {size_increase:.2f} shares. Checking hedge status..."
                                    )

                                    # Need to check if hedge needs to be increased
                                    # This will be handled by the monitoring loop
                                    c.execute(
                                        "UPDATE trades SET size = ?, bet_usd = ? * ?, order_status = 'HEDGE_SIZE_MISMATCH' WHERE id = ?",
                                        (
                                            actual_size,
                                            actual_size,
                                            actual_price,
                                            trade_id,
                                        ),
                                    )
                                else:
                                    # Normal size update
                                    c.execute(
                                        "UPDATE trades SET size = ?, bet_usd = ? * ? WHERE id = ?",
                                        (
                                            actual_size,
                                            actual_size,
                                            actual_price,
                                            trade_id,
                                        ),
                                    )
                            else:
                                # Position decreased or normal update
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
                                f"   ⚠️  [{symbol}] #{trade_id} exists in DB but not on exchange (size 0). Marking as settled/unfilled."
                            )
                            c.execute(
                                "UPDATE trades SET settled = 1, final_outcome = 'SYNC_MISSING', pnl_usd = 0.0, roi_pct = 0.0 WHERE id = ?",
                                (trade_id,),
                            )

            # 3. Check for untracked positions
            for t_id_str, p_data in position_map.items():
                if t_id_str and t_id_str not in all_tracked_token_ids:
                    size = float(p_data.get("size", 0))
                    if size < 0.001:
                        continue

                    try:
                        avg_price = float(
                            p_data.get("avg_price") or p_data.get("avgPrice") or 0.5
                        )
                        slug = (
                            p_data.get("slug")
                            or p_data.get("market_slug")
                            or "adopted-market"
                        )

                        # Extract symbol from slug (e.g., "btc-updown-15m-123456" -> "BTC")
                        if slug and slug.startswith("-adopted-market"):
                            slug = "adopted-market"
                        if slug and "-" in slug:
                            extracted = slug.split("-")[0].upper()
                            if extracted in ["BTC", "ETH", "SOL", "XRP"]:
                                symbol = extracted
                            else:
                                symbol = (
                                    p_data.get("symbol")
                                    or p_data.get("market")
                                    or p_data.get("title")
                                    or "ADOPTED"
                                )
                        else:
                            symbol = (
                                p_data.get("symbol")
                                or p_data.get("market")
                                or p_data.get("title")
                                or "ADOPTED"
                            )

                        side = p_data.get("outcome", "UNKNOWN").upper()

                        # Check if already resolved before adopting
                        is_resolved, _ = get_market_resolution(slug)
                        if is_resolved:
                            log(
                                f"   ℹ Skipping already resolved untracked position: {symbol} ({side})"
                            )
                            # Add to all_tracked_token_ids so we don't check Gamma again this run
                            all_tracked_token_ids.add(t_id_str)
                            continue

                        log(
                            f"   ⚠️  Found UNTRACKED position: {size} shares of {t_id_str[:10]}..."
                        )
                        log(
                            f"   📥 Adopting untracked position: {symbol} ({side}) {size} shares @ ${avg_price}"
                        )

                        c.execute(
                            """INSERT INTO trades (
                                symbol, slug, token_id, side, entry_price, size, bet_usd, 
                                timestamp, window_start, window_end, settled, order_status, final_outcome
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                symbol,
                                slug,
                                t_id_str,
                                side,
                                avg_price,
                                size,
                                size * avg_price,
                                now.isoformat(),
                                now.isoformat(),
                                (now + timedelta(minutes=15)).isoformat(),
                                0,
                                "FILLED",
                                "ADOPTED",
                            ),
                        )
                    except Exception as e:
                        log(f"   ❌ Failed to adopt position: {e}")

        log("✓ Position sync complete")
    except Exception as e:
        log(f"⚠️  Error during position sync: {e}")


def sync_with_exchange(user_address: str):
    """
    Master sync function that syncs both orders and positions with the exchange.
    """
    log("=" * 90)
    log("🔄 MASTER SYNC: Orders and Positions")
    log("=" * 90)

    sync_orders_with_exchange()
    sync_positions_with_exchange(user_address)

    log("=" * 90)
    log("✓ Master sync complete")
    log("=" * 90)


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
