"""Background task to periodically redeem old trades"""

import time
import threading
import requests
from src.data.database import db_connection
from src.trading.ctf_operations import redeem_winning_tokens
from src.utils.logger import log, log_error, send_discord
from src.config.settings import GAMMA_API_BASE

# Run redemption check every N seconds (default: 1 hour)
REDEMPTION_CHECK_INTERVAL_SECONDS = 3600


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
    """Background task to periodically redeem recent settled trades that need redemption"""
    try:
        # Small delay to let bot fully initialize
        time.sleep(5)

        run_count = 0

        while True:
            run_count += 1
            prefix = f"[Redemption #{run_count}]"

            try:
                with db_connection() as conn:
                    c = conn.cursor()

                    # Find recent settled trades that need redemption (last 48 hours)
                    c.execute(
                        """
                        SELECT id, symbol, slug, final_outcome, pnl_usd, settled_at
                        FROM trades 
                        WHERE settled = 1 
                            AND final_outcome = 'RESOLVED'
                            AND exited_early = 0
                            AND merge_tx_hash IS NULL
                            AND redeem_tx_hash IS NULL
                            AND datetime(settled_at) > datetime('now', '-2 days')
                        ORDER BY id DESC
                    """
                    )

                    trades = c.fetchall()

                    if not trades:
                        if run_count == 1:
                            log(f"✅ {prefix} No recent trades need redemption")
                    else:
                        log(
                            f"🔄 {prefix} Found {len(trades)} trades needing redemption..."
                        )

                        redeemed = 0
                        failed = 0
                        skipped = 0
                        total_value = 0.0

                        for (
                            trade_id,
                            symbol,
                            slug,
                            final_outcome,
                            pnl_usd,
                            settled_at,
                        ) in trades:
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
                                    log(
                                        f"   ✅ {prefix} [{symbol}] #{trade_id} redeemed (${value:+.2f})"
                                    )
                                    redeemed += 1
                                else:
                                    failed += 1

                                # Rate limit
                                time.sleep(2)

                            except Exception as e:
                                log_error(f"{prefix} #{trade_id} Error: {e}")
                                failed += 1
                                continue

                        # Report results
                        if redeemed > 0 or failed > 0:
                            summary = f"💰 {prefix} Complete: {redeemed} redeemed"
                            if skipped > 0:
                                summary += f", {skipped} skipped (expired)"
                            if failed > 0:
                                summary += f", {failed} failed"
                            if total_value > 0:
                                summary += f" | Total: ${total_value:+.2f}"

                            log(summary)
                            if redeemed > 0:
                                send_discord(summary)

            except Exception as e:
                log_error(f"{prefix} Error in redemption cycle: {e}")

            # Wait for next check interval
            if run_count == 1:
                log(
                    f"🔄 {prefix} Will check again in {REDEMPTION_CHECK_INTERVAL_SECONDS // 60} minutes"
                )

            time.sleep(REDEMPTION_CHECK_INTERVAL_SECONDS)

    except Exception as e:
        log_error(f"[Background Redemption] Fatal error: {e}")


def start_background_redemption():
    """Launch periodic background redemption task in a separate thread"""
    thread = threading.Thread(
        target=_redeem_old_trades_task, name="BackgroundRedemption", daemon=True
    )
    thread.start()
    log(
        f"🔄 [Startup] Launching periodic redemption task (checks every {REDEMPTION_CHECK_INTERVAL_SECONDS // 60} min)..."
    )
