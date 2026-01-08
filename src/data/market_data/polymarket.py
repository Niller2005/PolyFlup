"""Polymarket-specific market data functions"""

import time
import json
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from src.config.settings import GAMMA_API_BASE, CLOB_HOST


def get_current_slug(symbol: str) -> str:
    """Generate slug for current 15-minute window"""
    now_et = datetime.now(tz=ZoneInfo("America/New_York"))
    minute_slot = (now_et.minute // 15) * 15
    window_start_et = now_et.replace(minute=minute_slot, second=0, microsecond=0)
    window_start_utc = window_start_et.astimezone(ZoneInfo("UTC"))
    ts = int(window_start_utc.timestamp())
    return f"{symbol.lower()}-updown-15m-{ts}"


def get_window_times(symbol: str):
    """Get window start and end times in ET"""
    now_et = datetime.now(tz=ZoneInfo("America/New_York"))
    minute_slot = (now_et.minute // 15) * 15
    window_start_et = now_et.replace(minute=minute_slot, second=0, microsecond=0)
    window_end_et = window_start_et + timedelta(minutes=15)
    return window_start_et, window_end_et


def format_window_range(start_et: datetime, end_et: datetime) -> str:
    """Format window range as 'January 6, 10:30-10:45AM ET'"""
    month = start_et.strftime("%B")
    day = start_et.day
    start_t = start_et.strftime("%I:%M").lstrip("0")
    end_t = end_et.strftime("%I:%M%p").lstrip("0")
    return f"{month} {day}, {start_t}-{end_t} ET"


def get_token_ids(symbol: str):
    """Get UP and DOWN token IDs from Gamma API"""
    slug = get_current_slug(symbol)
    for attempt in range(1, 13):
        try:
            r = requests.get(f"{GAMMA_API_BASE}/markets/slug/{slug}", timeout=5)
            if r.status_code == 200:
                m = r.json()
                clob_ids = m.get("clobTokenIds") or m.get("clob_token_ids")
                if isinstance(clob_ids, str):
                    try:
                        clob_ids = json.loads(clob_ids)
                    except:
                        clob_ids = [
                            x.strip().strip('"')
                            for x in clob_ids.strip("[]").split(",")
                        ]
                if isinstance(clob_ids, list) and len(clob_ids) >= 2:
                    return clob_ids[0], clob_ids[1]
            elif r.status_code == 404 and attempt == 1:
                from src.utils.logger import log

                log(f"[{symbol}] 🔍 Market slug not found: {slug}")
        except Exception as e:
            if attempt == 1:
                from src.utils.logger import log

                log(f"[{symbol}] ❌ Error fetching token IDs: {e}")
        if attempt < 12:
            time.sleep(4)
    return None, None


def get_polymarket_momentum(token_id: str, interval: str = "1m") -> dict:
    """Calculate momentum based on Polymarket's own price history"""
    try:
        url = f"{CLOB_HOST}/prices-history"
        params = {"interval": interval, "token_id": token_id}
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        history = resp.json()
        if not history or not isinstance(history, list) or len(history) < 5:
            return {"velocity": 0.0, "direction": "NEUTRAL", "strength": 0.0}
        prices = []
        for h in history:
            p = h.get("p") or h.get("price")
            if p is not None:
                prices.append(float(p))
        if len(prices) < 5:
            return {"velocity": 0.0, "direction": "NEUTRAL", "strength": 0.0}
        velocity = (
            ((prices[-1] - prices[0]) / prices[0]) * 100.0 if prices[0] > 0 else 0
        )
        direction = (
            "UP" if velocity > 0.005 else "DOWN" if velocity < -0.005 else "NEUTRAL"
        )
        return {
            "velocity": velocity,
            "direction": direction,
            "strength": min(abs(velocity) * 20, 1.0),
            "last_price": prices[-1],
        }
    except:
        return {"velocity": 0.0, "direction": "NEUTRAL", "strength": 0.0}


# Cache for outcome prices (UP and DOWN token prices from market slug API)
_outcome_prices_cache: dict = {}
_outcome_prices_cache_timestamp: float = 0.0
CACHE_TTL_SECONDS = 10.0  # Refresh cache every 10 seconds


def get_outcome_prices(symbol: str) -> dict:
    """
    Get UP and DOWN token prices from Polymarket market slug API.

    Returns dict with:
        - "up_token_id": The UP token ID
        - "down_token_id": The DOWN token ID
        - "up_price": The UP token midpoint price
        - "down_price": The DOWN token midpoint price
        - "up_wins": Boolean - True if UP is winning (up_price >= 0.50)
        - "down_wins": Boolean - True if DOWN is winning (down_price <= 0.50)
    """
    import time
    from src.utils.logger import log

    global _outcome_prices_cache, _outcome_prices_cache_timestamp

    slug = get_current_slug(symbol)
    now = time.time()

    # Return cached data if still valid
    if (
        slug in _outcome_prices_cache
        and (now - _outcome_prices_cache_timestamp) < CACHE_TTL_SECONDS
    ):
        return _outcome_prices_cache[slug]

    try:
        r = requests.get(f"{GAMMA_API_BASE}/markets/slug/{slug}", timeout=5)
        if r.status_code != 200:
            log(f"⚠️  [{symbol}] Failed to fetch outcome prices: HTTP {r.status_code}")
            return {}

        market_data = r.json()
        outcome_prices = market_data.get("outcomePrices")
        if not outcome_prices:
            log(f"⚠️  [{symbol}] No outcome prices in market data")
            return {}

        # Parse outcomePrices: format can be JSON array or comma-separated string
        # e.g., ["0.835", "0.165"] or "0.835,0.165"
        if isinstance(outcome_prices, str):
            # Try parsing as JSON array first
            try:
                import json

                parsed_prices = json.loads(outcome_prices)
                if isinstance(parsed_prices, list) and len(parsed_prices) >= 2:
                    up_price = float(parsed_prices[0]) if parsed_prices[0] else None
                    down_price = float(parsed_prices[1]) if parsed_prices[1] else None
                else:
                    # Fallback to comma-separated parsing
                    prices = outcome_prices.split(",")
                    up_price = float(prices[0].strip()) if len(prices) > 0 else None
                    down_price = float(prices[1].strip()) if len(prices) > 1 else None
            except:
                # JSON parsing failed, try comma-separated
                prices = outcome_prices.split(",")
                up_price = float(prices[0].strip()) if len(prices) > 0 else None
                down_price = float(prices[1].strip()) if len(prices) > 1 else None
        elif isinstance(outcome_prices, list):
            # Already parsed as list
            up_price = float(outcome_prices[0]) if len(outcome_prices) > 0 else None
            down_price = float(outcome_prices[1]) if len(outcome_prices) > 1 else None
        else:
            # Fallback if not string format
            up_price = None
            down_price = None

        # Get token IDs
        clob_ids = market_data.get("clobTokenIds") or market_data.get("clob_token_ids")
        up_token_id, down_token_id = get_token_ids(symbol)

        # Use bestBid/bestAsk for midpoint prices (actual market data, not indices)
        best_bid = (
            float(market_data.get("bestBid", 0.50))
            if market_data.get("bestBid")
            else 0.50
        )
        best_ask = (
            float(market_data.get("bestAsk", 0.50))
            if market_data.get("bestAsk")
            else 0.50
        )

        # Determine winning sides based on midpoint prices
        up_wins = best_bid >= 0.50
        down_wins = best_ask <= 0.50

        result = {
            "up_token_id": up_token_id,
            "down_token_id": down_token_id,
            "up_price": best_bid,
            "down_price": best_ask,
            "up_wins": up_wins,
            "down_wins": down_wins,
        }

        # Cache the result
        _outcome_prices_cache[slug] = result
        _outcome_prices_cache_timestamp = now

        return result

    except Exception as e:
        from src.utils.logger import log_error

        log_error(f"[{symbol}] Error fetching outcome prices: {e}")

        # FALLBACK: Use Binance spot price to calculate approximate UP/DOWN prices
        try:
            from .binance import get_current_spot_price

            spot_price = get_current_spot_price(symbol)
            if spot_price <= 0:
                return {}

            target_price = get_window_start_price(symbol)
            if not target_price:
                return {}

            target_price = float(target_price)
            price_change_pct = (
                (spot_price - target_price) / target_price if target_price > 0 else 0
            )

            # Calculate approximate UP price based on spot movement
            # If spot moved up significantly, UP token price increases
            # If spot moved down significantly, DOWN token price increases (UP decreases)
            if price_change_pct > 0.005:  # Spot up 0.5% or more
                up_price = min(0.95, 0.50 + price_change_pct * 5)
            elif price_change_pct < -0.005:  # Spot down 0.5% or more
                up_price = max(0.05, 0.50 + price_change_pct * 5)
            else:
                up_price = 0.50  # Near target, ambiguous

            down_price = 1.0 - up_price

            # Clamp to valid range
            up_price = max(0.01, min(0.99, up_price))
            down_price = max(0.01, min(0.99, down_price))

            up_token_id, down_token_id = get_token_ids(symbol)

            # Determine winning sides based on approximate prices
            up_wins = up_price >= 0.50
            down_wins = down_price <= 0.50

            result = {
                "up_token_id": up_token_id,
                "down_token_id": down_token_id,
                "up_price": up_price,
                "down_price": down_price,
                "up_wins": up_wins,
                "down_wins": down_wins,
            }

            # Don't cache fallback data (want to retry Polymarket ASAP)
            return result
        except Exception as fallback_error:
            log_error(f"[{symbol}] Spot price fallback also failed: {fallback_error}")
            return {}
