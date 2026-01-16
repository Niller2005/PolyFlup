"""Background task to redeem old trades on bot startup"""

import time
import threading
import requests
from src.data.database import db_connection
from src.trading.ctf_operations import redeem_winning_tokens
from src.utils.logger import log, log_error, send_discord
from src.config.settings import GAMMA_API_BASE


def get_condition_id_from_slug(slug: str) -> str:
    """Fetch condition_id directly from API using slug"""
    try:
        r = requests.get(f"{GAMMA_API_BASE}/markets/slug/{slug}", timeout=5)
        if r.status_code == 200:
            data = r.json()
            condition_id = data.get("conditionId") or data.get("condition_id") or ""
            return (
                condition_id
                if condition_id and condition_id != "0x" + ("0" * 64)
                else ""
            )
    except Exception as e:
        log_error(f"Error fetching condition_id for {slug}: {e}")
    return ""


def _redeem_old_trades_task():
    """Background task to redeem recent settled trades that need redemption"""
    try:
        # Small delay to let bot fully initialize
        time.sleep(5)

        with db_connection() as conn:
            c = conn.cursor()

            # Find recent settled trades that need redemption (last 24 hours)
            c.execute(
                """
                SELECT id, symbol, slug, final_outcome, pnl_usd, settled_at
                FROM trades 
                WHERE settled = 1 
                    AND final_outcome = 'RESOLVED'
                    AND exited_early = 0
                    AND merge_tx_hash IS NULL
                    AND redeem_tx_hash IS NULL
                    AND datetime(settled_at) > datetime('now', '-1 day')
                ORDER BY id DESC
            """
            )

            trades = c.fetchall()

            if not trades:
                log("✅ [Startup] No recent trades need redemption")
                return

            log(f"🔄 [Startup] Found {len(trades)} trades needing redemption...")

            redeemed = 0
            failed = 0
            skipped = 0
            total_value = 0.0

            for trade_id, symbol, slug, final_outcome, pnl_usd, settled_at in trades:
                try:
                    value = pnl_usd or 0.0
                    total_value += value

                    # Fetch condition_id from API
                    condition_id = get_condition_id_from_slug(slug)

                    if not condition_id:
                        skipped += 1
                        continue

                    # Update database with condition_id
                    c.execute(
                        "UPDATE trades SET condition_id = ? WHERE id = ?",
                        (condition_id, trade_id),
                    )

                    # Execute redemption
                    redeem_tx_hash = redeem_winning_tokens(
                        trade_id, symbol, condition_id
                    )

                    if redeem_tx_hash:
                        # Update database with redemption tx
                        c.execute(
                            "UPDATE trades SET redeem_tx_hash = ? WHERE id = ?",
                            (redeem_tx_hash, trade_id),
                        )
                        redeemed += 1
                    else:
                        failed += 1

                    # Rate limit
                    time.sleep(2)

                except Exception as e:
                    log_error(f"[Startup Redemption] #{trade_id} Error: {e}")
                    failed += 1
                    continue

            # Report results
            if redeemed > 0 or failed > 0:
                summary = f"💰 [Startup] Redemption complete: {redeemed} redeemed"
                if skipped > 0:
                    summary += f", {skipped} skipped (expired)"
                if failed > 0:
                    summary += f", {failed} failed"
                if total_value > 0:
                    summary += f" | Total: ${total_value:+.2f}"

                log(summary)
                send_discord(summary)

    except Exception as e:
        log_error(f"[Startup Redemption] Fatal error: {e}")


def start_background_redemption():
    """Launch background redemption task in a separate thread"""
    thread = threading.Thread(
        target=_redeem_old_trades_task, name="BackgroundRedemption", daemon=True
    )
    thread.start()
    log("🔄 [Startup] Launching background redemption task...")
