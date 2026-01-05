"""Position synchronization and recovery"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from src.data.db_connection import db_connection
from src.utils.logger import log
from src.trading.orders import get_current_positions
from src.utils.websocket_manager import ws_manager
from src.data.market_data import get_token_ids, get_window_times, get_current_slug
from src.data.database import save_trade

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
        # We normalize to decimal string for the primary key
        pos_map = {}
        for p in exchange_positions:
            # Data API uses 'asset', Gamma might use 'asset_id' or 'token_id'
            aid = p.get("asset") or p.get("asset_id") or p.get("assetId") or p.get("token_id")
            if aid:
                aid_str = str(aid).strip().lower()
                norm_aid = aid_str
                # If it's hex, convert to decimal string
                if aid_str.startswith("0x"):
                    try:
                        norm_aid = str(int(aid_str, 16))
                    except: pass
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
            db_token_ids = set()

            for trade_id, symbol, side, db_size, token_id, db_entry in db_trades:
                # Normalize DB token_id to decimal string
                tid_raw = str(token_id).strip().lower() if token_id else ""
                tid_str = tid_raw
                if tid_raw.startswith("0x"):
                    try:
                        tid_str = str(int(tid_raw, 16))
                    except: pass
                
                if tid_str:
                    db_token_ids.add(tid_str)
                
                # Check match in pos_map (which is also indexed by decimal string)
                if tid_str and tid_str in pos_map:
                    pos = pos_map[tid_str]
                    matched_exchange_ids.add(tid_str)
                    
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
            for t_id_str, p_data in pos_map.items():
                if t_id_str and t_id_str not in db_token_ids:
                    size = float(p_data.get("size", 0))
                    if size < 0.001:
                        continue
                        
                    log(f"   ⚠️ Found UNTRACKED position: {size} shares of {t_id_str[:10]}...")
                    
                    try:
                        avg_price = float(p_data.get("avg_price") or p_data.get("avgPrice") or 0.5)
                        symbol = p_data.get("symbol") or p_data.get("market") or p_data.get("title") or "ADOPTED"
                        slug = p_data.get("slug") or p_data.get("market_slug") or "adopted-market"
                        side = p_data.get("outcome", "UNKNOWN").upper()
                        
                        log(f"   📥 Adopting untracked position: {symbol} ({side}) {size} shares @ ${avg_price}")
                        
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
                                "ADOPTED"
                            )
                        )
                    except Exception as e:
                        log(f"   ❌ Failed to adopt position: {e}")

        log("✓ Position sync complete")
    except Exception as e:
        log(f"⚠️ Error during position sync: {e}")


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
