"""Price movement validation for high confidence trades"""

import requests
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from src.config.settings import BINANCE_FUNDING_MAP
from .binance import _create_klines_dataframe


def get_recent_price_movements(symbol: str, timeframes_minutes: List[int] = [5, 15, 30]) -> Dict[str, float]:
    """
    Calculate price movements across multiple timeframes
    
    Args:
        symbol: Trading symbol (BTC, ETH, etc.)
        timeframes_minutes: List of timeframes in minutes to analyze
        
    Returns:
        Dict with percentage changes for each timeframe
    """
    pair = BINANCE_FUNDING_MAP.get(symbol.upper())
    if not pair:
        return {f"{tf}m": 0.0 for tf in timeframes_minutes}
    
    try:
        import pandas as pd
        
        # Get max timeframe + buffer for calculations
        max_minutes = max(timeframes_minutes) + 5
        
        url = f"https://api.binance.com/api/v3/klines?symbol={pair}&interval=1m&limit={max_minutes}"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        df = _create_klines_dataframe(response.json())
        if df is None or len(df) < max_minutes:
            return {f"{tf}m": 0.0 for tf in timeframes_minutes}
        
        close_prices = pd.to_numeric(df["close"])
        movements = {}
        
        for timeframe in timeframes_minutes:
            if len(close_prices) >= timeframe:
                current_price = close_prices.iloc[-1]
                past_price = close_prices.iloc[-(timeframe + 1)]
                movement = ((current_price - past_price) / past_price) * 100.0
                movements[f"{timeframe}m"] = movement
            else:
                movements[f"{tf}m"] = 0.0
                
        return movements
        
    except Exception as e:
        print(f"Error calculating price movements for {symbol}: {e}")
        return {f"{tf}m": 0.0 for tf in timeframes_minutes}


def calculate_volatility_score(symbol: str, lookback_minutes: int = 30) -> float:
    """
    Calculate price volatility score (0-1) based on recent price movements
    
    Args:
        symbol: Trading symbol
        lookback_minutes: Minutes to analyze for volatility
        
    Returns:
        Volatility score from 0 (low) to 1 (high)
    """
    pair = BINANCE_FUNDING_MAP.get(symbol.upper())
    if not pair:
        return 0.0
    
    try:
        import pandas as pd
        import numpy as np
        
        url = f"https://api.binance.com/api/v3/klines?symbol={pair}&interval=1m&limit={lookback_minutes + 5}"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        df = _create_klines_dataframe(response.json())
        if df is None or len(df) < lookback_minutes:
            return 0.0
        
        close_prices = pd.to_numeric(df["close"])
        
        # Calculate rolling volatility (standard deviation of returns)
        returns = close_prices.pct_change().dropna()
        if len(returns) < 5:
            return 0.0
            
        volatility = returns.std() * np.sqrt(1440)  # Annualized volatility (1440 min/day)
        
        # Normalize to 0-1 scale (assuming 100% annualized volatility is extremely high)
        volatility_score = min(volatility / 1.0, 1.0)
        
        return volatility_score
        
    except Exception as e:
        print(f"Error calculating volatility for {symbol}: {e}")
        return 0.0


def detect_price_manipulation(symbol: str) -> Dict[str, any]:
    """
    Detect potential price manipulation patterns
    
    Args:
        symbol: Trading symbol
        
    Returns:
        Dict with manipulation indicators
    """
    pair = BINANCE_FUNDING_MAP.get(symbol.upper())
    if not pair:
        return {"manipulation_detected": False, "score": 0.0, "reasons": []}
    
    try:
        import pandas as pd
        
        # Get recent data for analysis
        url = f"https://api.binance.com/api/v3/klines?symbol={pair}&interval=1m&limit=60"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        df = _create_klines_dataframe(response.json())
        if df is None or len(df) < 30:
            return {"manipulation_detected": False, "score": 0.0, "reasons": []}
        
        high = pd.to_numeric(df["high"])
        low = pd.to_numeric(df["low"])
        close = pd.to_numeric(df["close"])
        volume = pd.to_numeric(df["volume"])
        
        manipulation_score = 0.0
        reasons = []
        
        # Check 1: Extreme wicks (long shadows)
        recent_data = df.tail(15)  # Last 15 minutes
        recent_high = pd.to_numeric(recent_data["high"])
        recent_low = pd.to_numeric(recent_data["low"])
        recent_close = pd.to_numeric(recent_data["close"])
        
        
        for i in range(len(recent_data)):
            curr_open = float(recent_data.iloc[i]['open'])
            body_size = abs(recent_close.iloc[i] - curr_open)
            wick_size = (recent_high.iloc[i] - recent_low.iloc[i]) - body_size
            
            if wick_size > body_size * 3:  # Wick is 3x larger than body
                manipulation_score += 0.2
                reasons.append(f"Extreme wick detected at minute {i}")
                break
        
        # Check 2: Volume spikes without significant price movement
        avg_volume = volume.tail(20).mean()
        recent_volume = volume.tail(5).mean()
        
        if recent_volume > avg_volume * 3:  # 3x average volume
            recent_price_change = abs(close.iloc[-1] - close.iloc[-6]) / close.iloc[-6] * 100
            if recent_price_change < 1.0:  # Less than 1% price change despite volume spike
                manipulation_score += 0.3
                reasons.append("Volume spike without price movement")
        
        # Check 3: Rapid price reversals
        price_changes = close.pct_change().tail(10)
        positive_changes = sum(1 for x in price_changes if x > 0.005)
        negative_changes = sum(1 for x in price_changes if x < -0.005)
        
        if positive_changes >= 4 and negative_changes >= 4:  # High volatility with reversals
            manipulation_score += 0.25
            reasons.append("Frequent price reversals")
        
        
        # Check 4: Price gaps
        for i in range(1, len(recent_data)):
            prev_close = float(recent_close.iloc[i-1])
            curr_open = float(recent_data.iloc[i]['open'])
            gap = abs(curr_open - prev_close) / prev_close * 100
            
            if gap > 0.5:  # 0.5% gap
                manipulation_score += 0.15
                reasons.append(f"Price gap detected: {gap:.2f}%")
                break
        
        return {
            "manipulation_detected": manipulation_score > 0.4,
            "score": min(manipulation_score, 1.0),
            "reasons": reasons
        }
        
    except Exception as e:
        print(f"Error detecting manipulation for {symbol}: {e}")
        return {"manipulation_detected": False, "score": 0.0, "reasons": []}


def validate_price_movement_for_trade(
    symbol: str, 
    confidence: float, 
    current_spot: float,
    max_movement_threshold: float = 20.0,
    min_confidence_threshold: float = 0.75
) -> Dict[str, any]:
    """
    Validate price movement for high confidence trades
    
    Args:
        symbol: Trading symbol
        confidence: Current confidence score
        current_spot: Current spot price
        max_movement_threshold: Maximum allowed price movement percentage
        min_confidence_threshold: Minimum confidence to trigger validation
        
    Returns:
        Dict with validation results and adjusted confidence
    """
    # Only validate if confidence is high enough
    if confidence < min_confidence_threshold:
        return {
            "valid": True,
            "original_confidence": confidence,
            "adjusted_confidence": confidence,
            "reduction_reason": "",
            "price_data": {}
        }
    
    try:
        # Get recent price movements
        movements = get_recent_price_movements(symbol, [5, 15, 30])
        
        # Calculate volatility
        volatility_score = calculate_volatility_score(symbol, 30)
        
        # Detect manipulation
        manipulation = detect_price_manipulation(symbol)
        
        # Initialize validation results
        validation_result = {
            "valid": True,
            "original_confidence": confidence,
            "adjusted_confidence": confidence,
            "reduction_reason": "",
            "price_data": {
                "movements": movements,
                "volatility_score": volatility_score,
                "manipulation_score": manipulation["score"],
                "manipulation_detected": manipulation["manipulation_detected"],
                "manipulation_reasons": manipulation["reasons"]
            }
        }
        
        # Check for extreme movements
        extreme_movements = []
        for timeframe, movement in movements.items():
            if abs(movement) > max_movement_threshold:
                extreme_movements.append(f"{timeframe}: {movement:.1f}%")
        
        confidence_reduction = 0.0
        reduction_reasons = []
        
        # Apply penalties based on findings
        if extreme_movements:
            # Reduce confidence based on largest movement
            largest_movement = max(abs(movements[tf]) for tf in movements)
            confidence_reduction += (largest_movement / max_movement_threshold) * 0.3
            reduction_reasons.append(f"Extreme price movement: {largest_movement:.1f}%")
        
        if volatility_score > 0.7:  # High volatility
            confidence_reduction += 0.2
            reduction_reasons.append(f"High volatility: {volatility_score:.2f}")
        
        if manipulation["manipulation_detected"]:
            confidence_reduction += 0.25
            reduction_reasons.append(f"Potential manipulation: {', '.join(manipulation['reasons'])}")
        
        # Check for recent price reversal patterns
        if len(movements) >= 2:
            # Look for rapid reversals (e.g., 15min up, 5min down)
            recent_15m = movements.get("15m", 0)
            recent_5m = movements.get("5m", 0)
            
            if (recent_15m > 10 and recent_5m < -5) or (recent_15m < -10 and recent_5m > 5):
                confidence_reduction += 0.15
                reduction_reasons.append("Rapid price reversal pattern")
        
        # Apply confidence reduction
        if confidence_reduction > 0:
            validation_result["adjusted_confidence"] = max(
                confidence - confidence_reduction,
                min_confidence_threshold - 0.1  # Don't go below threshold - 0.1
            )
            validation_result["reduction_reason"] = "; ".join(reduction_reasons)
            
            # Mark as invalid if confidence drops significantly
            if validation_result["adjusted_confidence"] < min_confidence_threshold - 0.05:
                validation_result["valid"] = False
        
        return validation_result
        
    except Exception as e:
        print(f"Error validating price movement for {symbol}: {e}")
        return {
            "valid": True,
            "original_confidence": confidence,
            "adjusted_confidence": confidence,
            "reduction_reason": f"Validation error: {str(e)}",
            "price_data": {}
        }
