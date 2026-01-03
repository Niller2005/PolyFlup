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


def get_current_spot_price(symbol: str) -> float:
    """
    Get current spot price from Binance for the given symbol.

    Args:
        symbol: Trading symbol (e.g., 'BTC', 'ETH')

    Returns:
        Current spot price in USDT, or -1.0 on error
    """
    pair = BINANCE_FUNDING_MAP.get(symbol.upper())
    if not pair:
        log(f"[{symbol}] No Binance mapping found for spot price")
        return -1.0

    # Convert futures symbol to spot symbol (e.g., BTCUSDT from BTCUSD)
    spot_pair = pair.replace("USD", "USDT") if not pair.endswith("USDT") else pair

    try:
        url = f"https://api.binance.com/api/v3/ticker/price?symbol={spot_pair}"
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        data = response.json()
        price = float(data["price"])
        return price
    except Exception as e:
        log(f"[{symbol}] Error fetching spot price: {e}")
        return -1.0


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
            return -1.0

        return float(adx_value)

    except requests.RequestException as e:
        log(f"[{symbol}] ADX: Binance API error: {e}")
        return -1.0
    except Exception as e:
        log(f"[{symbol}] ADX: Unexpected error: {e}")
        import traceback

        log(traceback.format_exc())
        return -1.0


def get_price_momentum(symbol: str, lookback_minutes: int = 15) -> dict:
    """
    Calculate price momentum from Binance spot data.

    Args:
        symbol: Trading symbol (e.g., 'BTC', 'ETH')
        lookback_minutes: Minutes to look back for momentum calculation

    Returns:
        dict with momentum metrics or error values
        {
            'velocity': float,  # % change over period
            'acceleration': float,  # rate of change of velocity
            'rsi': float,  # RSI indicator
            'direction': str,  # 'UP' or 'DOWN'
            'strength': float,  # 0-1, magnitude of momentum
        }
    """
    try:
        import pandas as pd
        from ta.momentum import RSIIndicator
    except ImportError:
        log(f"[{symbol}] Momentum: Missing required libraries")
        return {
            "velocity": 0.0,
            "acceleration": 0.0,
            "rsi": 50.0,
            "direction": "NEUTRAL",
            "strength": 0.0,
        }

    pair = BINANCE_FUNDING_MAP.get(symbol.upper())
    if not pair:
        log(f"[{symbol}] Momentum: No Binance mapping found")
        return {
            "velocity": 0.0,
            "acceleration": 0.0,
            "rsi": 50.0,
            "direction": "NEUTRAL",
            "strength": 0.0,
        }

    try:
        # Fetch 1-minute klines for precise momentum
        limit = max(30, lookback_minutes + 20)  # Extra for RSI calculation
        url = f"https://api.binance.com/api/v3/klines?symbol={pair}&interval=1m&limit={limit}"

        response = requests.get(url, timeout=10)
        response.raise_for_status()
        klines = response.json()

        if not klines or len(klines) < lookback_minutes:
            log(f"[{symbol}] Momentum: Insufficient data")
            return {
                "velocity": 0.0,
                "acceleration": 0.0,
                "rsi": 50.0,
                "direction": "NEUTRAL",
                "strength": 0.0,
            }

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

        df["close"] = pd.to_numeric(df["close"])
        df["open"] = pd.to_numeric(df["open"])

        # Calculate velocity (% change over lookback period)
        current_price = df["close"].iloc[-1]
        past_price = (
            df["close"].iloc[-lookback_minutes]
            if len(df) >= lookback_minutes
            else df["close"].iloc[0]
        )
        velocity = ((current_price - past_price) / past_price) * 100.0

        # Calculate acceleration (change in velocity)
        mid_point = max(1, lookback_minutes // 2)
        if len(df) >= lookback_minutes:
            mid_price = df["close"].iloc[-mid_point]
            recent_velocity = ((current_price - mid_price) / mid_price) * 100.0
            early_velocity = ((mid_price - past_price) / past_price) * 100.0
            acceleration = recent_velocity - early_velocity
        else:
            acceleration = 0.0

        # Calculate RSI (14 period)
        if len(df) >= 15:
            rsi_indicator = RSIIndicator(close=df["close"], window=14, fillna=True)
            rsi = float(rsi_indicator.rsi().iloc[-1])
        else:
            rsi = 50.0

        # Determine direction and strength
        direction = "UP" if velocity > 0 else "DOWN" if velocity < 0 else "NEUTRAL"
        strength = min(abs(velocity) / 2.0, 1.0)  # Normalize to 0-1 (2% = max strength)

        return {
            "velocity": velocity,
            "acceleration": acceleration,
            "rsi": rsi,
            "direction": direction,
            "strength": strength,
        }

    except Exception as e:
        log(f"[{symbol}] Momentum: Error calculating: {e}")
        return {
            "velocity": 0.0,
            "acceleration": 0.0,
            "rsi": 50.0,
            "direction": "NEUTRAL",
            "strength": 0.0,
        }


def get_order_flow_analysis(symbol: str) -> dict:
    """
    Analyze Binance order flow for buy/sell pressure.

    Args:
        symbol: Trading symbol (e.g., 'BTC', 'ETH')

    Returns:
        dict with order flow metrics
        {
            'buy_pressure': float,  # 0-1, higher = more buying
            'volume_ratio': float,  # taker buy volume / total volume
            'large_trade_direction': str,  # 'BUY', 'SELL', or 'NEUTRAL'
            'trade_intensity': float,  # trades per minute
        }
    """
    pair = BINANCE_FUNDING_MAP.get(symbol.upper())
    if not pair:
        log(f"[{symbol}] OrderFlow: No Binance mapping found")
        return {
            "buy_pressure": 0.5,
            "volume_ratio": 0.5,
            "large_trade_direction": "NEUTRAL",
            "trade_intensity": 0.0,
        }

    try:
        import pandas as pd

        # Fetch recent klines with volume data (5 minutes of 1m candles)
        url = f"https://api.binance.com/api/v3/klines?symbol={pair}&interval=1m&limit=5"

        response = requests.get(url, timeout=10)
        response.raise_for_status()
        klines = response.json()

        if not klines:
            log(f"[{symbol}] OrderFlow: No data")
            return {
                "buy_pressure": 0.5,
                "volume_ratio": 0.5,
                "large_trade_direction": "NEUTRAL",
                "trade_intensity": 0.0,
            }

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

        df["volume"] = pd.to_numeric(df["volume"])
        df["taker_buy_base"] = pd.to_numeric(df["taker_buy_base"])
        df["trades"] = pd.to_numeric(df["trades"])

        # Calculate buy pressure (taker buy / total volume)
        total_volume = df["volume"].sum()
        buy_volume = df["taker_buy_base"].sum()

        if total_volume > 0:
            volume_ratio = buy_volume / total_volume
            buy_pressure = volume_ratio  # 0.5 = balanced, >0.5 = buy pressure, <0.5 = sell pressure
        else:
            volume_ratio = 0.5
            buy_pressure = 0.5

        # Determine large trade direction
        if volume_ratio > 0.55:
            large_trade_direction = "BUY"
        elif volume_ratio < 0.45:
            large_trade_direction = "SELL"
        else:
            large_trade_direction = "NEUTRAL"

        # Calculate trade intensity (trades per minute)
        trade_intensity = df["trades"].mean()

        return {
            "buy_pressure": buy_pressure,
            "volume_ratio": volume_ratio,
            "large_trade_direction": large_trade_direction,
            "trade_intensity": trade_intensity,
        }

    except Exception as e:
        log(f"[{symbol}] OrderFlow: Error: {e}")
        return {
            "buy_pressure": 0.5,
            "volume_ratio": 0.5,
            "large_trade_direction": "NEUTRAL",
            "trade_intensity": 0.0,
        }


def get_cross_exchange_divergence(symbol: str, polymarket_p_up: float) -> dict:
    """
    Compare Polymarket implied probability vs Binance price movement.

    Args:
        symbol: Trading symbol (e.g., 'BTC', 'ETH')
        polymarket_p_up: Current Polymarket UP token price (probability of upward move)

    Returns:
        dict with divergence analysis
        {
            'binance_direction': str,  # Expected Binance direction based on recent movement
            'polymarket_direction': str,  # Direction Polymarket is pricing in
            'divergence': float,  # -1 to 1, negative = Polymarket too bearish, positive = too bullish
            'opportunity': str,  # 'BUY_UP', 'BUY_DOWN', or 'NEUTRAL'
        }
    """
    pair = BINANCE_FUNDING_MAP.get(symbol.upper())
    if not pair:
        log(f"[{symbol}] Divergence: No Binance mapping found")
        return {
            "binance_direction": "NEUTRAL",
            "polymarket_direction": "NEUTRAL",
            "divergence": 0.0,
            "opportunity": "NEUTRAL",
        }

    try:
        import pandas as pd

        # Fetch 15-minute window worth of 1m klines
        url = (
            f"https://api.binance.com/api/v3/klines?symbol={pair}&interval=1m&limit=15"
        )

        response = requests.get(url, timeout=10)
        response.raise_for_status()
        klines = response.json()

        if not klines or len(klines) < 10:
            log(f"[{symbol}] Divergence: Insufficient data")
            return {
                "binance_direction": "NEUTRAL",
                "polymarket_direction": "NEUTRAL",
                "divergence": 0.0,
                "opportunity": "NEUTRAL",
            }

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

        df["close"] = pd.to_numeric(df["close"])
        df["open"] = pd.to_numeric(df["open"])

        # Calculate Binance movement trend
        start_price = df["open"].iloc[0]
        current_price = df["close"].iloc[-1]
        price_change_pct = ((current_price - start_price) / start_price) * 100.0

        # Determine Binance implied direction (based on recent movement)
        # Strong uptrend = high probability of UP, strong downtrend = high probability of DOWN
        if price_change_pct > 0.5:
            binance_direction = "UP"
            binance_implied_p_up = 0.55 + min(
                price_change_pct / 10.0, 0.3
            )  # 0.55-0.85 range
        elif price_change_pct < -0.5:
            binance_direction = "DOWN"
            binance_implied_p_up = 0.45 - min(
                abs(price_change_pct) / 10.0, 0.3
            )  # 0.15-0.45 range
        else:
            binance_direction = "NEUTRAL"
            binance_implied_p_up = 0.5

        # Determine Polymarket direction
        if polymarket_p_up > 0.55:
            polymarket_direction = "UP"
        elif polymarket_p_up < 0.45:
            polymarket_direction = "DOWN"
        else:
            polymarket_direction = "NEUTRAL"

        # Calculate divergence (negative = Polymarket underpricing UP, positive = overpricing UP)
        divergence = polymarket_p_up - binance_implied_p_up

        # Determine opportunity
        if divergence < -0.1:  # Polymarket too bearish vs Binance trend
            opportunity = "BUY_UP"
        elif divergence > 0.1:  # Polymarket too bullish vs Binance trend
            opportunity = "BUY_DOWN"
        else:
            opportunity = "NEUTRAL"

        return {
            "binance_direction": binance_direction,
            "polymarket_direction": polymarket_direction,
            "divergence": divergence,
            "opportunity": opportunity,
            "binance_implied_p_up": binance_implied_p_up,
        }

    except Exception as e:
        log(f"[{symbol}] Divergence: Error: {e}")
        return {
            "binance_direction": "NEUTRAL",
            "polymarket_direction": "NEUTRAL",
            "divergence": 0.0,
            "opportunity": "NEUTRAL",
        }


def get_volume_weighted_momentum(symbol: str) -> dict:
    """
    Calculate volume-weighted momentum indicators.

    Args:
        symbol: Trading symbol (e.g., 'BTC', 'ETH')

    Returns:
        dict with volume-weighted metrics
        {
            'vwap_distance': float,  # % distance from VWAP (+ = above, - = below)
            'volume_trend': str,  # 'INCREASING', 'DECREASING', 'STABLE'
            'momentum_quality': float,  # 0-1, higher = higher volume confirming trend
        }
    """
    pair = BINANCE_FUNDING_MAP.get(symbol.upper())
    if not pair:
        log(f"[{symbol}] VWM: No Binance mapping found")
        return {"vwap_distance": 0.0, "volume_trend": "STABLE", "momentum_quality": 0.0}

    try:
        import pandas as pd

        # Fetch 15-minute window of 1m klines
        url = (
            f"https://api.binance.com/api/v3/klines?symbol={pair}&interval=1m&limit=15"
        )

        response = requests.get(url, timeout=10)
        response.raise_for_status()
        klines = response.json()

        if not klines or len(klines) < 10:
            log(f"[{symbol}] VWM: Insufficient data")
            return {
                "vwap_distance": 0.0,
                "volume_trend": "STABLE",
                "momentum_quality": 0.0,
            }

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

        df["close"] = pd.to_numeric(df["close"])
        df["high"] = pd.to_numeric(df["high"])
        df["low"] = pd.to_numeric(df["low"])
        df["volume"] = pd.to_numeric(df["volume"])

        # Calculate VWAP (Volume Weighted Average Price)
        df["typical_price"] = (df["high"] + df["low"] + df["close"]) / 3
        df["vwap_component"] = df["typical_price"] * df["volume"]
        vwap = (
            df["vwap_component"].sum() / df["volume"].sum()
            if df["volume"].sum() > 0
            else df["close"].iloc[-1]
        )

        # Distance from VWAP
        current_price = df["close"].iloc[-1]
        vwap_distance = ((current_price - vwap) / vwap) * 100.0

        # Volume trend
        early_vol = df["volume"].iloc[:5].mean()
        recent_vol = df["volume"].iloc[-5:].mean()

        if recent_vol > early_vol * 1.2:
            volume_trend = "INCREASING"
        elif recent_vol < early_vol * 0.8:
            volume_trend = "DECREASING"
        else:
            volume_trend = "STABLE"

        # Momentum quality (high volume on trend moves = high quality)
        price_direction = 1 if df["close"].iloc[-1] > df["close"].iloc[0] else -1
        volume_normalized = (
            (recent_vol - df["volume"].mean()) / df["volume"].std()
            if df["volume"].std() > 0
            else 0
        )
        momentum_quality = (
            min(abs(volume_normalized) / 2.0, 1.0)
            if price_direction * volume_normalized > 0
            else 0.0
        )

        return {
            "vwap_distance": vwap_distance,
            "volume_trend": volume_trend,
            "momentum_quality": momentum_quality,
        }

    except Exception as e:
        log(f"[{symbol}] VWM: Error: {e}")
        return {"vwap_distance": 0.0, "volume_trend": "STABLE", "momentum_quality": 0.0}
