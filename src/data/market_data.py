"""Market data fetching from APIs"""

import time
import json
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from src.config.settings import (
    GAMMA_API_BASE,
    BINANCE_FUNDING_MAP,
    ADX_PERIOD,
    ADX_INTERVAL,
)
from src.utils.logger import log


def get_current_slug(symbol: str) -> str:
    """Generate slug for current 15-minute window"""
    now_et = datetime.now(tz=ZoneInfo("America/New_York"))
    minute_slot = (now_et.minute // 15) * 15
    window_start_et = now_et.replace(minute=minute_slot, second=0, microsecond=0)
    window_start_utc = window_start_et.astimezone(ZoneInfo("UTC"))
    ts = int(window_start_utc.timestamp())
    slug = f"{symbol.lower()}-updown-15m-{ts}"
    log(f"[{symbol}] Window slug: {slug}")
    return slug


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
                    log(
                        f"[{symbol}] Tokens found: UP {clob_ids[0][:10]}... | DOWN {clob_ids[1][:10]}..."
                    )
                    return clob_ids[0], clob_ids[1]
        except Exception as e:
            log(f"[{symbol}] Error fetching tokens: {e}")
        if attempt < 12:
            time.sleep(4)
    return None, None


def get_funding_bias(symbol: str) -> float:
    """Get funding rate bias from Binance futures"""
    pair = BINANCE_FUNDING_MAP.get(symbol)
    if not pair:
        return 0.0
    try:
        url = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={pair}"
        funding = float(requests.get(url, timeout=5).json()["lastFundingRate"])
        return funding * 1000.0
    except:
        return 0.0


def get_fear_greed() -> int:
    """Get Fear & Greed Index"""
    try:
        return int(
            requests.get("https://api.alternative.me/fng/", timeout=5).json()["data"][
                0
            ]["value"]
        )
    except:
        return 50


def get_adx_from_binance(symbol: str) -> float:
    """
    Fetch klines from Binance and calculate ADX for symbol/USDT pair using ta library.

    Args:
        symbol: Trading symbol (e.g., 'BTC', 'ETH')

    Returns:
        ADX value (0-100) or -1.0 on error
    """
    try:
        import pandas as pd
        from ta.trend import ADXIndicator
    except ImportError:
        log(f"[{symbol}] ADX: Missing 'ta' library. Install with: pip install ta")
        return -1.0

    pair = BINANCE_FUNDING_MAP.get(symbol.upper())
    if not pair:
        log(f"[{symbol}] ADX: No Binance mapping found for symbol")
        return -1.0

    try:
        # Need enough klines for ADX calculation
        limit = ADX_PERIOD * 3 + 10
        url = f"https://api.binance.com/api/v3/klines?symbol={pair}&interval={ADX_INTERVAL}&limit={limit}"

        log(
            f"[{symbol}] ADX: Fetching klines from Binance ({pair}, {ADX_INTERVAL}, limit={limit})..."
        )

        response = requests.get(url, timeout=10)
        response.raise_for_status()
        klines = response.json()

        if not klines or len(klines) < ADX_PERIOD * 2:
            log(f"[{symbol}] ADX: Insufficient klines data (got {len(klines)})")
            return -1.0

        # Convert to DataFrame
        df = pd.DataFrame(
            klines,
            columns=[
                "open_time",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "close_time",
                "quote_volume",
                "trades",
                "taker_buy_base",
                "taker_buy_quote",
                "ignore",
            ],
        )

        # Convert to numeric
        df["high"] = pd.to_numeric(df["high"])
        df["low"] = pd.to_numeric(df["low"])
        df["close"] = pd.to_numeric(df["close"])

        # Calculate ADX using ta library
        adx_indicator = ADXIndicator(
            high=df["high"],
            low=df["low"],
            close=df["close"],
            window=ADX_PERIOD,
            fillna=False,
        )

        adx_series = adx_indicator.adx()
        adx_value = adx_series.iloc[-1]

        if pd.isna(adx_value):
            log(f"[{symbol}] ADX: Calculated value is NaN")
            return -1.0

        log(f"[{symbol}] ADX: Calculated value = {adx_value:.2f}")
        return float(adx_value)

    except requests.RequestException as e:
        log(f"[{symbol}] ADX: Binance API error: {e}")
        return -1.0
    except Exception as e:
        log(f"[{symbol}] ADX: Unexpected error: {e}")
        import traceback

        log(traceback.format_exc())
        return -1.0
