#!/usr/bin/env python3
"""
Backfill condition_id for old trades and redeem winning tokens.

This script:
1. Finds all settled trades missing condition_id
2. Fetches condition_id from Gamma API
3. Redeems winning tokens for resolved positions
4. Updates database with redemption tx hashes

Run with: uv run python redeem_old_trades.py
"""

import sys
import time
from src.data.database import db_connection
from src.data.market_data import get_market_metadata
from src.trading.ctf_operations import redeem_winning_tokens
from src.utils.logger import log, log_error


def backfill_condition_ids(dry_run=False, limit=50):
    """
    Backfill condition_id for settled trades that are missing it.

    Args:
        dry_run: If True, only show what would be done without making changes
        limit: Maximum number of trades to process (most recent first)
    """
    log("=" * 70)
    log("🔍 BACKFILLING CONDITION IDs FOR OLD TRADES")
    log("=" * 70)

    with db_connection() as conn:
        c = conn.cursor()

        # Find settled trades without condition_id (most recent first)
        c.execute(
            """
            SELECT id, symbol, slug, final_outcome 
            FROM trades 
            WHERE settled = 1 
                AND condition_id IS NULL 
                AND exited_early = 0
                AND merge_tx_hash IS NULL
            ORDER BY id DESC
            LIMIT ?
        """,
            (limit,),
        )

        trades = c.fetchall()

        if not trades:
            log("✅ No trades need condition_id backfill")
            return 0

        log(f"\n📊 Found {len(trades)} trades missing condition_id")
        log(
            f"{'[DRY RUN MODE]' if dry_run else '[LIVE MODE - Will update database]'}\n"
        )

        updated = 0
        failed = 0

        for trade_id, symbol, slug, final_outcome in trades:
            try:
                # Fetch condition_id from Gamma API
                market_data = get_market_metadata(slug)

                if not market_data or "condition_id" not in market_data:
                    log(
                        f"   ❌ [{symbol}] #{trade_id} Failed to fetch condition_id from {slug}"
                    )
                    failed += 1
                    continue

                condition_id = market_data["condition_id"]

                if not condition_id or condition_id == "0x" + ("0" * 64):
                    log(f"   ❌ [{symbol}] #{trade_id} Invalid condition_id")
                    failed += 1
                    continue

                log(
                    f"   ✅ [{symbol}] #{trade_id} Found condition_id: {condition_id[:16]}..."
                )

                if not dry_run:
                    # Update database with condition_id
                    c.execute(
                        "UPDATE trades SET condition_id = ? WHERE id = ?",
                        (condition_id, trade_id),
                    )

                updated += 1

                # Rate limit API calls
                time.sleep(0.2)

            except Exception as e:
                log(f"   ❌ [{symbol}] #{trade_id} Error: {e}")
                failed += 1
                continue

                condition_id = market_data["condition_id"]

                if not condition_id or condition_id == "0x" + ("0" * 64):
                    log("❌ Invalid condition_id")
                    failed += 1
                    continue

                log(f"✅ {condition_id[:16]}...")

                if not dry_run:
                    # Update database with condition_id
                    c.execute(
                        "UPDATE trades SET condition_id = ? WHERE id = ?",
                        (condition_id, trade_id),
                    )

                updated += 1

                # Rate limit API calls
                time.sleep(0.2)

            except Exception as e:
                log(f"❌ Error: {e}")
                failed += 1
                continue

        log(f"\n📈 Results:")
        log(f"   ✅ Updated: {updated}")
        log(f"   ❌ Failed: {failed}")

        return updated


def redeem_old_trades(dry_run=False, limit=50):
    """
    Redeem winning tokens for old resolved trades.

    Args:
        dry_run: If True, only show what would be done without making changes
        limit: Maximum number of trades to process (most recent first)
    """
    log("\n" + "=" * 70)
    log("💰 REDEEMING WINNING TOKENS FROM OLD TRADES")
    log("=" * 70)

    with db_connection() as conn:
        c = conn.cursor()

        # Find resolved trades that need redemption (most recent first)
        c.execute(
            """
            SELECT id, symbol, condition_id, final_outcome, pnl_usd
            FROM trades 
            WHERE settled = 1 
                AND final_outcome = 'RESOLVED'
                AND exited_early = 0
                AND merge_tx_hash IS NULL
                AND redeem_tx_hash IS NULL
                AND condition_id IS NOT NULL
            ORDER BY id DESC
            LIMIT ?
        """,
            (limit,),
        )

        trades = c.fetchall()

        if not trades:
            log("✅ No trades need redemption")
            return 0

        log(f"\n📊 Found {len(trades)} resolved trades ready for redemption")
        log(
            f"{'[DRY RUN MODE]' if dry_run else '[LIVE MODE - Will execute transactions]'}\n"
        )

        redeemed = 0
        failed = 0
        total_value = 0.0

        for trade_id, symbol, condition_id, final_outcome, pnl_usd in trades:
            try:
                value = pnl_usd or 0.0
                total_value += value

                log(
                    f"   🎫 [{symbol}] #{trade_id} Redeeming tokens (PnL: ${value:+.2f})..."
                )

                if dry_run:
                    log(
                        f"      [Would redeem with condition_id: {condition_id[:16]}...]"
                    )
                    redeemed += 1
                else:
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
                        log(f"      ✅ Redeemed! Tx: {redeem_tx_hash[:16]}...")
                        redeemed += 1
                    else:
                        log(f"      ❌ Redemption failed")
                        failed += 1

                # Rate limit transactions
                time.sleep(2)

            except Exception as e:
                log_error(f"[{symbol}] #{trade_id} Redemption error: {e}")
                failed += 1
                continue

        log(f"\n📈 Results:")
        log(f"   ✅ Redeemed: {redeemed}")
        log(f"   ❌ Failed: {failed}")
        log(f"   💵 Total Value: ${total_value:+.2f}")

        return redeemed


def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Backfill condition_id and redeem old trades"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    parser.add_argument(
        "--skip-backfill",
        action="store_true",
        help="Skip condition_id backfill, only redeem",
    )
    parser.add_argument(
        "--skip-redeem", action="store_true", help="Skip redemption, only backfill"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum number of trades to process (default: 50)",
    )

    args = parser.parse_args()

    if args.dry_run:
        log("⚠️  DRY RUN MODE - No changes will be made\n")

    try:
        # Step 1: Backfill condition_ids
        if not args.skip_backfill:
            updated = backfill_condition_ids(dry_run=args.dry_run, limit=args.limit)

            if updated > 0 and not args.dry_run:
                log(f"\n✅ Successfully backfilled {updated} condition_ids")

        # Step 2: Redeem winning tokens
        if not args.skip_redeem:
            redeemed = redeem_old_trades(dry_run=args.dry_run, limit=args.limit)

            if redeemed > 0 and not args.dry_run:
                log(f"\n✅ Successfully redeemed {redeemed} positions")

        log("\n" + "=" * 70)
        log("🎉 DONE!")
        log("=" * 70)

    except KeyboardInterrupt:
        log("\n⛔ Interrupted by user")
        sys.exit(1)
    except Exception as e:
        log_error(f"Fatal error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
