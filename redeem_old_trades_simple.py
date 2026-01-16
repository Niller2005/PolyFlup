#!/usr/bin/env python3
"""
Simple script to redeem winning tokens from recent settled trades.

Run with: uv run python redeem_old_trades_simple.py
"""

import sys
import time
import requests
from src.data.database import db_connection
from src.trading.ctf_operations import redeem_winning_tokens
from src.utils.logger import log, log_error
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


def main():
    log("=" * 70)
    log("💰 REDEEMING WINNING TOKENS FROM RECENT TRADES")
    log("=" * 70)
    log("This script will:")
    log("  1. Find recent settled trades from last 24 hours")
    log("  2. Fetch condition_id from API")
    log("  3. Redeem winning tokens")
    log("")

    with db_connection() as conn:
        c = conn.cursor()

        # Find recent settled trades that need redemption (last 24 hours)
        c.execute("""
            SELECT id, symbol, slug, final_outcome, pnl_usd, settled_at
            FROM trades 
            WHERE settled = 1 
                AND final_outcome = 'RESOLVED'
                AND exited_early = 0
                AND merge_tx_hash IS NULL
                AND redeem_tx_hash IS NULL
                AND datetime(settled_at) > datetime('now', '-1 day')
            ORDER BY id DESC
        """)

        trades = c.fetchall()

        if not trades:
            log("✅ No recent trades need redemption")
            return

        log(f"📊 Found {len(trades)} recent resolved trades")
        log("")

        redeemed = 0
        failed = 0
        skipped = 0
        total_value = 0.0

        for trade_id, symbol, slug, final_outcome, pnl_usd, settled_at in trades:
            try:
                value = pnl_usd or 0.0
                total_value += value

                log(
                    f"🎫 [{symbol}] #{trade_id} (Settled: {settled_at[:16]}, PnL: ${value:+.2f})"
                )

                # Fetch condition_id from API
                log(f"   Fetching condition_id from {slug}...")
                condition_id = get_condition_id_from_slug(slug)

                if not condition_id:
                    log(f"   ❌ Not found (market may be expired)")
                    skipped += 1
                    continue

                log(f"   ✅ Found: {condition_id[:16]}...")

                # Update database with condition_id
                c.execute(
                    "UPDATE trades SET condition_id = ? WHERE id = ?",
                    (condition_id, trade_id),
                )

                # Execute redemption
                log(f"   Redeeming tokens...")
                redeem_tx_hash = redeem_winning_tokens(trade_id, symbol, condition_id)

                if redeem_tx_hash:
                    # Update database with redemption tx
                    c.execute(
                        "UPDATE trades SET redeem_tx_hash = ? WHERE id = ?",
                        (redeem_tx_hash, trade_id),
                    )
                    log(f"   ✅ Redeemed! Tx: {redeem_tx_hash[:16]}...")
                    redeemed += 1
                else:
                    log(f"   ❌ Redemption failed")
                    failed += 1

                log("")

                # Rate limit
                time.sleep(2)

            except Exception as e:
                log_error(f"[{symbol}] #{trade_id} Error: {e}")
                failed += 1
                log("")
                continue

        log("=" * 70)
        log("📈 RESULTS:")
        log(f"   ✅ Redeemed: {redeemed}")
        log(f"   ⏭️  Skipped: {skipped} (markets expired)")
        log(f"   ❌ Failed: {failed}")
        log(f"   💵 Total PnL: ${total_value:+.2f}")
        log("=" * 70)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("\n⛔ Interrupted by user")
        sys.exit(1)
    except Exception as e:
        log_error(f"Fatal error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
