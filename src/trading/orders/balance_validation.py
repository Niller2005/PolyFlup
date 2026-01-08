"""Enhanced balance validation with symbol-specific tolerance and cross-validation"""
from src.config.settings import ENABLE_ENHANCED_BALANCE_VALIDATION, XRP_BALANCE_GRACE_PERIOD_MINUTES, XRP_BALANCE_TRUST_FACTOR

import time
from typing import Optional, Dict, Any
from src.utils.logger import log
from src.trading.orders import get_balance_allowance, get_current_positions
from src.trading.orders.utils import normalize_token_id
from src.config.settings import DATA_API_BASE
import requests


# Symbol-specific tolerance settings for API reliability issues
SYMBOL_TOLERANCE_CONFIG = {
    "XRP": {
        "zero_balance_threshold": 0.1,  # Higher threshold for XRP zero-balance issues
        "api_reliability_weight": XRP_BALANCE_TRUST_FACTOR,  # Lower weight given to XRP balance API
        "retry_count": 3,  # More retries for XRP
        "retry_delay": 2.0,  # Longer delay between retries
        "position_trust_factor": 0.8,  # Trust position data more than balance for XRP
        "grace_period_minutes": XRP_BALANCE_GRACE_PERIOD_MINUTES,  # Longer grace period for XRP
    },
    "DEFAULT": {
        "zero_balance_threshold": 0.01,
        "api_reliability_weight": 0.7,
        "retry_count": 2,
        "retry_delay": 1.0,
        "position_trust_factor": 0.5,
        "grace_period_minutes": 10,
    }
}


def get_symbol_config(symbol: str) -> Dict[str, Any]:
    """Get symbol-specific configuration with fallback to default"""
    return SYMBOL_TOLERANCE_CONFIG.get(symbol.upper(), SYMBOL_TOLERANCE_CONFIG["DEFAULT"])


def retry_balance_api_call(token_id: str, symbol: str, max_retries: int = 2, retry_delay: float = 1.0) -> Optional[Dict[str, float]]:
    """
    Retry balance API calls with exponential backoff for unreliable symbols
    """
    config = get_symbol_config(symbol)
    max_retries = config["retry_count"]
    retry_delay = config["retry_delay"]
    
    for attempt in range(max_retries):
        try:
            balance_info = get_balance_allowance(token_id)
            if balance_info is not None:
                return balance_info
            
            if attempt < max_retries - 1:
                log(f"   ⚠️  Balance API attempt {attempt + 1} failed for {symbol}, retrying in {retry_delay}s...")
                time.sleep(retry_delay)
                retry_delay *= 1.5  # Exponential backoff
                
        except Exception as e:
            if attempt < max_retries - 1:
                log(f"   ⚠️  Balance API error on attempt {attempt + 1} for {symbol}: {e}")
                time.sleep(retry_delay)
                retry_delay *= 1.5
            else:
                log(f"   ❌ Balance API failed after {max_retries} attempts for {symbol}: {e}")
    
    return None


def get_position_from_data_api(user_address: str, token_id: str) -> Optional[Dict[str, float]]:
    """
    Get position data from Data API as cross-validation source
    """
    try:
        url = f"{DATA_API_BASE}/positions?user={user_address}&asset_id={token_id}"
        resp = requests.get(url, timeout=15)  # Longer timeout for reliability
        resp.raise_for_status()
        data = resp.json()
        
        if isinstance(data, list) and len(data) > 0:
            position = data[0]
            return {
                "size": float(position.get("size", 0)),
                "avg_price": float(position.get("avg_price", position.get("avgPrice", 0))),
                "condition_id": position.get("conditionId", ""),
            }
        return None
        
    except Exception as e:
        log(f"   ⚠️  Error getting position from Data API: {e}")
        return None


def cross_validate_balance_position(
    balance_data: Optional[Dict[str, float]], 
    position_data: Optional[Dict[str, float]], 
    symbol: str,
    trade_age_seconds: float
) -> Dict[str, Any]:
    """
    Cross-validate balance data against position data and determine trusted value
    """
    config = get_symbol_config(symbol)
    
    balance_val = balance_data.get("balance", 0) if balance_data else 0
    position_val = position_data.get("size", 0) if position_data else 0
    
    # Calculate discrepancy
    discrepancy = abs(balance_val - position_val)
    
    # Determine trust factors
    balance_trust = config["api_reliability_weight"]
    position_trust = config["position_trust_factor"]
    
    # Special handling for zero balance cases
    if balance_val < config["zero_balance_threshold"] and position_val >= 1.0:
        # Balance shows near-zero but position shows significant size
        if trade_age_seconds < config["grace_period_minutes"] * 60:
            log(f"   ⚠️  [{symbol}] Balance shows zero ({balance_val:.4f}) but position shows {position_val:.2f}. "
                f"Using position data (age: {trade_age_seconds:.0f}s < {config['grace_period_minutes']}m grace period)")
            return {
                "trusted_balance": position_val,
                "source": "position_data",
                "confidence": 0.8,
                "discrepancy": discrepancy,
                "reason": "zero_balance_position_mismatch"
            }
    
    # Handle cases where position is significantly larger than balance
    if position_val > balance_val * 2 and position_val >= 1.0:
        log(f"   ⚠️  [{symbol}] Position ({position_val:.2f}) >> Balance ({balance_val:.4f}). "
            f"Using weighted average with position bias.")
        trusted_balance = (balance_val * balance_trust + position_val * position_trust) / (balance_trust + position_trust)
        return {
            "trusted_balance": trusted_balance,
            "source": "weighted_average",
            "confidence": 0.7,
            "discrepancy": discrepancy,
            "reason": "position_significantly_larger"
        }
    
    # Normal case - trust balance but log significant discrepancies
    if discrepancy > 0.1:
        log(f"   ⚠️  [{symbol}] Balance/position discrepancy: Balance={balance_val:.4f}, Position={position_val:.2f}, Diff={discrepancy:.4f}")
    
    return {
        "trusted_balance": balance_val,
        "source": "balance_api",
        "confidence": balance_trust,
        "discrepancy": discrepancy,
        "reason": "normal_validation"
    }


def get_enhanced_balance_allowance(
    token_id: str, 
    symbol: str, 
    user_address: str,
    trade_age_seconds: float,
    enable_cross_validation: bool = True
) -> Dict[str, Any]:
    """
    Enhanced balance validation with retry logic, cross-validation, and symbol-specific tolerance
    
    Returns:
        Dict containing:
        - balance: Trusted balance value
        - allowance: Allowance value
        - source: Data source used (balance_api, position_data, weighted_average)
        - confidence: Confidence level in the balance value
        - discrepancy: Difference between balance and position data
        - retry_count: Number of API retries attempted
        - cross_validated: Whether cross-validation was performed
    """
    config = get_symbol_config(symbol)
    
    # Step 1: Get balance with retries
    balance_start_time = time.time()
    balance_info = retry_balance_api_call(token_id, symbol, config["retry_count"], config["retry_delay"])
    retry_count = config["retry_count"] if balance_info is None else 1
    
    if balance_info is None:
        log(f"   ❌ [{symbol}] Balance API completely failed after {retry_count} retries")
        # Fall back to position data if available
        if enable_cross_validation:
            position_data = get_position_from_data_api(user_address, token_id)
            if position_data:
                log(f"   ⚠️  [{symbol}] Using position data as fallback: {position_data['size']:.2f}")
                return {
                    "balance": position_data["size"],
                    "allowance": 0,
                    "source": "position_fallback",
                    "confidence": 0.6,
                    "discrepancy": 0,
                    "retry_count": retry_count,
                    "cross_validated": True,
                    "reason": "balance_api_failed_position_fallback"
                }
        
        # Ultimate fallback - return zero with low confidence
        return {
            "balance": 0,
            "allowance": 0,
            "source": "failed_api",
            "confidence": 0.1,
            "discrepancy": 0,
            "retry_count": retry_count,
            "cross_validated": False,
            "reason": "complete_api_failure"
        }
    
    # Step 2: Cross-validate with position data if enabled
    if enable_cross_validation:
        position_data = get_position_from_data_api(user_address, token_id)
        validation_result = cross_validate_balance_position(
            balance_info, position_data, symbol, trade_age_seconds
        )
        
        # Merge validation results with original balance data
        result = {
            "balance": validation_result["trusted_balance"],
            "allowance": balance_info.get("allowance", 0),
            "source": validation_result["source"],
            "confidence": validation_result["confidence"],
            "discrepancy": validation_result["discrepancy"],
            "retry_count": retry_count,
            "cross_validated": True,
            "reason": validation_result["reason"],
            "api_response_time": time.time() - balance_start_time
        }
        
        # Log validation results for XRP specifically
        if symbol.upper() == "XRP":
            log(f"   🔍 [XRP] Balance validation: {result}")
        
        return result
    
    # No cross-validation - return balance data directly
    return {
        "balance": balance_info.get("balance", 0),
        "allowance": balance_info.get("allowance", 0),
        "source": "balance_api_only",
        "confidence": config["api_reliability_weight"],
        "discrepancy": 0,
        "retry_count": retry_count,
        "cross_validated": False,
        "reason": "no_cross_validation",
        "api_response_time": time.time() - balance_start_time
    }
