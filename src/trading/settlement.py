"""Trade settlement logic"""

import json
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from src.config.settings import GAMMA_API_BASE
from src.utils.logger import log, send_discord
from src.trading.orders import cancel_order
from src.data.db_connection import db_connection


def get_market_resolution(slug: str):
    """
    Fetch market resolution from Gamma API.
    Returns:
        (resolved, outcome_prices)
        resolved: bool - True if market is fully resolved (prices are 0 or 1)
        outcome_prices: list[float] - [price_up, price_down] e.g. [1.0, 0.0]
    """
    try:
        r = requests.get(f"{GAMMA_API_BASE}/markets/slug/{slug}", timeout=5)
        if r.status_code == 200:
            data = r.json()

            # Check outcomePrices
            outcome_prices = data.get("outcomePrices")
            if isinstance(outcome_prices, str):
                outcome_prices = json.loads(outcome_prices)

            if not outcome_prices or len(outcome_prices) < 2:
                return False, None

            # Parse prices
            p0 = float(outcome_prices[0])
            p1 = float(outcome_prices[1])

            # Check if resolved (one is 1, one is 0)
            # We use a loose check (>= 0.99 or <= 0.01) just in case
            if (p0 >= 0.99 and p1 <= 0.01) or (p0 <= 0.01 and p1 >= 0.99):
                return True, [p0, p1]

    except Exception as e:
        log(f"Error fetching resolution for {slug}: {e}")

    return False, None


def check_and_settle_trades():
    """Check and settle completed trades using definitive API resolution"""
    with db_connection() as conn:
        c = conn.cursor()
        now = datetime.now(tz=ZoneInfo("UTC"))

        # Only check trades where window has ended
        c.execute(
            "SELECT id, symbol, slug, token_id, side, entry_price, size, bet_usd, limit_sell_order_id FROM trades WHERE settled = 0 AND datetime(window_end) < datetime(?)",
            (now.isoformat(),),
        )
        unsettled = c.fetchall()

        if not unsettled:
            return

        total_pnl = 0
        settled_count = 0
        logged_spacing = False

        for (
            trade_id,
            symbol,
            slug,
            token_id,
            side,
            entry_price,
            size,
            bet_usd,
            limit_sell_order_id,
        ) in unsettled:
            try:
                # 1. Get resolution from API
                is_resolved, prices = get_market_resolution(slug)

                if not is_resolved:
                    # Market not resolved yet, skip and check next cycle
                    continue

                # ... (rest of resolution logic)
                # 2. Identify which token we hold (UP or DOWN)
                # Fetch specific market data to match IDs safely
                r = requests.get(f"{GAMMA_API_BASE}/markets/slug/{slug}", timeout=5)
                data = r.json()
                clob_ids = data.get("clobTokenIds") or data.get("clob_token_ids")
                if isinstance(clob_ids, str):
                    try:
                        clob_ids = json.loads(clob_ids)
                    except:
                        # fallback parsing if simple string
                        clob_ids = [
                            x.strip().strip('"')
                            for x in clob_ids.strip("[]").split(",")
                        ]

                # Determine outcome value
                final_price = 0.0
                if prices and clob_ids and len(clob_ids) >= 2:
                    final_price = (
                        float(prices[0])
                        if str(token_id) == str(clob_ids[0])
                        else float(prices[1])
                    )
                else:
                    continue

                # NEW: Cancel limit sell order if it exists (market is resolved now)
                if limit_sell_order_id:
                    cancel_order(limit_sell_order_id)

                exit_value = final_price
                pnl_usd = (exit_value * size) - bet_usd
                roi_pct = (pnl_usd / bet_usd) * 100 if bet_usd > 0 else 0

                c.execute(
                    "UPDATE trades SET final_outcome=?, exit_price=?, pnl_usd=?, roi_pct=?, settled=1, settled_at=? WHERE id=?",
                    (
                        "RESOLVED",
                        final_price,
                        pnl_usd,
                        roi_pct,
                        now.isoformat(),
                        trade_id,
                    ),
                )

                if not logged_spacing:
                    log("")
                    logged_spacing = True

                emoji = "💰" if pnl_usd > 0 else "💀"
                log(
                    f"{emoji} [{symbol}] #{trade_id} {side}: {pnl_usd:+.2f}$ ({roi_pct:+.1f}%)"
                )
                total_pnl += pnl_usd
                settled_count += 1

            except Exception as e:
                log(f"⚠️ [{symbol}] #{trade_id} Error settling trade: {e}")

        if settled_count > 0:
            send_discord(
                f"📊 Settled {settled_count} trades | Total PnL: ${total_pnl:+.2f}"
            )
