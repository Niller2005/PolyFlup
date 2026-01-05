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
        except:
            pass
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
