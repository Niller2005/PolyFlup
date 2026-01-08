"""Enhanced balance validation with symbol-specific tolerance and cross-validation"""

from src.config.settings import (
    ENABLE_ENHANCED_BALANCE_VALIDATION,
    XRP_BALANCE_GRACE_PERIOD_MINUTES,
    XRP_BALANCE_TRUST_FACTOR,
)

import time
from typing import Optional, Dict, Any, List
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
        "position_trust_factor": 0.3,  # REDUCED: Trust position data less, balance more
        "grace_period_minutes": XRP_BALANCE_GRACE_PERIOD_MINUTES,  # Longer grace period for XRP
    },
    "DEFAULT": {
        "zero_balance_threshold": 0.01,
        "api_reliability_weight": 0.8,  # INCREASED: Trust balance API more by default
        "retry_count": 2,
        "retry_delay": 1.0,
        "position_trust_factor": 0.2,  # REDUCED: Trust position data less, balance more
        "grace_period_minutes": 5,  # REDUCED: Shorter grace period
    },
}


def get_symbol_config(symbol: str) -> Dict[str, Any]:
    """Get symbol-specific configuration with fallback to default"""
    return SYMBOL_TOLERANCE_CONFIG.get(
        symbol.upper(), SYMBOL_TOLERANCE_CONFIG["DEFAULT"]
    )


def retry_balance_api_call(
    token_id: str, symbol: str, max_retries: int = 2, retry_delay: float = 1.0
) -> Optional[Dict[str, float]]:
    """
    Retry balance API calls with exponential backoff for unreliable symbols

    Handles common Polymarket API issues:
    - Geographic restrictions
    - USDC.e vs USDC collateral confusion
    - Temporary API unavailability
    """
    config = get_symbol_config(symbol)
    max_retries = config["retry_count"]
    retry_delay = config["retry_delay"]

    for attempt in range(max_retries):
        try:
            balance_info = get_balance_allowance(token_id)
            if balance_info is not None:
                # Additional validation for crypto markets that commonly have issues
                if symbol in ["BTC", "ETH", "SOL", "XRP", "DOGE", "MATIC"]:
                    balance_val = balance_info.get("balance", 0)
                    if balance_val < 0.001:  # Near-zero balance for crypto
                        log(
                            f"   ⚠️  [{symbol}] Balance API returned near-zero ({balance_val:.6f}), may be USDC.e timing issue"
                        )
                        if attempt < max_retries - 1:
                            time.sleep(retry_delay)
                            retry_delay *= 1.5
                            continue

                return balance_info

            if attempt < max_retries - 1:
                log(
                    f"   ⚠️  Balance API attempt {attempt + 1} failed for {symbol}, retrying in {retry_delay}s..."
                )
                time.sleep(retry_delay)
                retry_delay *= 1.5  # Exponential backoff

        except Exception as e:
            error_msg = str(e).lower()

            # Handle geographic restriction errors specifically (don't retry these)
            if any(
                keyword in error_msg
                for keyword in [
                    "restricted",
                    "geoblock",
                    "location",
                    "region",
                    "country",
                ]
            ):
                log(f"   🌍 [{symbol}] Geographic restriction detected: {e}")
                return None  # Don't retry geographic restrictions

            # Handle other errors with retry logic
            if attempt < max_retries - 1:
                log(
                    f"   ⚠️  Balance API error on attempt {attempt + 1} for {symbol}: {e}"
                )
                time.sleep(retry_delay)
                retry_delay *= 1.5
            else:
                log(
                    f"   ❌ Balance API failed after {max_retries} attempts for {symbol}: {e}"
                )

    return None


def get_position_from_data_api(
    user_address: str, token_id: str, symbol: str = ""
) -> Optional[Dict[str, float]]:
    """
    Get position data from Data API as cross-validation source

    DEBUG: Added symbol parameter to track which symbol this position data belongs to
    """
    try:
        url = f"{DATA_API_BASE}/positions?user={user_address}&asset_id={token_id}"

        # DEBUG: Log the exact API call being made
        log(f"   🔍 POSITION API CALL: {url[:100]}... for symbol={symbol}")

        resp = requests.get(url, timeout=15)  # Longer timeout for reliability
        resp.raise_for_status()
        data = resp.json()

        if isinstance(data, list) and len(data) > 0:
            position = data[0]
            position_size = float(position.get("size", 0))

            # DEBUG: Log what we got back
            log(
                f"   🔍 POSITION API RESULT: Got position with size={position_size:.4f} for token={token_id[:20]}..."
            )

            return {
                "size": position_size,
                "avg_price": float(
                    position.get("avg_price", position.get("avgPrice", 0))
                ),
                "condition_id": position.get("conditionId", ""),
            }

        # DEBUG: Log when no position data is found
        log(
            f"   🔍 POSITION API RESULT: No position data found for token={token_id[:20]}... (symbol={symbol})"
        )
        return None

    except Exception as e:
        log(f"   ⚠️  Error getting position from Data API for {symbol}: {e}")
        return None


def get_actual_positions_from_data_api(user_address: str) -> List[Dict[str, Any]]:
    """
    Get ALL current positions from Data API to verify actual holdings
    """
    try:
        url = f"{DATA_API_BASE}/positions?user={user_address}"
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if isinstance(data, list):
            return data
        elif isinstance(data, dict) and "positions" in data:
            return data["positions"]
        return []

    except (requests.RequestException, ValueError, KeyError) as e:
        log(f"   ⚠️  Error getting all positions from Data API: {e}")
        return []


def cross_validate_balance_position(
    balance_data: Optional[Dict[str, float]],
    position_data: Optional[Dict[str, float]],
    symbol: str,
    trade_age_seconds: float,
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
            log(
                f"   ⚠️  [{symbol}] Balance shows zero ({balance_val:.4f}) but position shows {position_val:.2f}. "
                f"Using position data (age: {trade_age_seconds:.0f}s < {config['grace_period_minutes']}m grace period)"
            )
            return {
                "trusted_balance": position_val,
                "source": "position_data",
                "confidence": 0.8,
                "discrepancy": discrepancy,
                "reason": "zero_balance_position_mismatch",
            }

    # Handle cases where position is significantly larger than balance
    if position_val > balance_val * 2 and position_val >= 1.0:
        log(
            f"   ⚠️  [{symbol}] Position ({position_val:.2f}) >> Balance ({balance_val:.4f}). "
            f"Using BALANCE data to prevent insufficient funds errors."
        )
        # Always use actual balance when position shows significantly more than balance
        # This prevents "Insufficient funds" errors when trying to sell more than we actually have
        return {
            "trusted_balance": balance_val,
            "source": "balance_api",
            "confidence": balance_trust,
            "discrepancy": discrepancy,
            "reason": "position_significantly_larger_use_balance",
        }

    # Normal case - trust balance but log significant discrepancies
    if discrepancy > 0.1:
        log(
            f"   ⚠️  [{symbol}] Balance/position discrepancy: Balance={balance_val:.4f}, Position={position_val:.2f}, Diff={discrepancy:.4f}"
        )

    return {
        "trusted_balance": balance_val,
        "source": "balance_api",
        "confidence": balance_trust,
        "discrepancy": discrepancy,
        "reason": "normal_validation",
    }


def log_balance_discrepancy(
    symbol: str,
    balance_val: float,
    position_val: float,
    source: str,
    reason: str,
    market_type: str = "unknown",
) -> None:
    """
    Log significant balance/position discrepancies for monitoring
    """
    discrepancy = abs(balance_val - position_val)
    if discrepancy > 1.0:  # Log any significant discrepancy
        log(
            f"   ⚠️  [{symbol}] BALANCE DISCREPANCY: Balance={balance_val:.4f}, Position={position_val:.4f}, Diff={discrepancy:.4f}"
        )
        log(f"   ⚠️  [{symbol}] Source: {source}, Reason: {reason}")

        # Warn about potential trading issues
        if position_val > balance_val * 1.5:
            log(
                f"   🚨 [{symbol}] WARNING: Position shows more shares than available balance!"
            )
            log(
                f"   🚨 [{symbol}] This could cause 'Insufficient funds' errors when selling."
            )

            # Add specific guidance based on common Polymarket issues
            if symbol in ["BTC", "ETH", "SOL", "XRP"]:
                log(
                    f"   🚨 [{symbol}] CRYPTO MARKET: May indicate USDC.e collateral vs position timing issue"
                )
            else:
                log(
                    f"   🚨 [{symbol}] Verify position settlement status and recent trades"
                )


def get_market_type_info(token_id: str) -> Dict[str, str]:
    """
    Get market type information for better error handling
    Returns market type (crypto, sports, politics, etc.) based on token patterns
    """
    # Common token patterns for different market types
    crypto_tokens = ["BTC", "ETH", "SOL", "XRP", "DOGE", "MATIC", "ADA", "BNB"]
    sports_tokens = ["NBA", "NFL", "MLB", "SOCCER", "TENNIS", "GOLF"]

    # Extract symbol from token_id if possible
    token_upper = token_id.upper() if token_id else ""

    # Check for crypto patterns
    for crypto in crypto_tokens:
        if crypto in token_upper:
            return {"type": "crypto", "symbol": crypto}

    # Check for sports patterns
    for sport in sports_tokens:
        if sport in token_upper:
            return {"type": "sports", "symbol": sport}

    # Default to unknown
    return {"type": "unknown", "symbol": "UNKNOWN"}


def get_enhanced_balance_allowance(
    token_id: str,
    symbol: str,
    user_address: str,
    trade_age_seconds: float,
    enable_cross_validation: bool = True,
) -> Dict[str, Any]:
    """
    Enhanced balance validation with retry logic, cross-validation, and symbol-specific tolerance

    CRITICAL: Always returns BALANCE API data for trading to prevent insufficient funds errors.
    Position data is used only for logging and monitoring discrepancies.

    Returns:
        Dict containing:
        - balance: Actual tradable balance from CLOB API (ALWAYS used for trading)
        - allowance: Allowance value from CLOB API
        - source: Data source used (always balance_api for trading)
        - confidence: Confidence level in the balance value
        - discrepancy: Difference between balance and position data (for monitoring)
        - retry_count: Number of API retries attempted
        - cross_validated: Whether cross-validation was performed (for logging)
    """
    # Create a unique ID for this call to track it through logs
    import uuid

    call_id = str(uuid.uuid4())[:8]

    # DEBUG: Detailed entry logging with call ID to track symbol mix-ups
    log(
        f"   🔍 BALANCE ENTRY [{call_id}]: symbol={symbol}, token={token_id[:20]}..., user={user_address[:10]}..., age={trade_age_seconds:.0f}s"
    )

    # Store original symbol to detect any mix-ups during processing
    original_symbol = symbol
    """
    Enhanced balance validation with retry logic, cross-validation, and symbol-specific tolerance

    CRITICAL: Always returns BALANCE API data for trading to prevent insufficient funds errors.
    Position data is used only for logging and monitoring discrepancies.

    Returns:
        Dict containing:
        - balance: Actual tradable balance from CLOB API (ALWAYS used for trading)
        - allowance: Allowance value from CLOB API
        - source: Data source used (always balance_api for trading)
        - confidence: Confidence level in the balance value
        - discrepancy: Difference between balance and position data (for monitoring)
        - retry_count: Number of API retries attempted
        - cross_validated: Whether cross-validation was performed (for logging)
    """
    config = get_symbol_config(symbol)

    # Step 1: Get balance with retries - THIS IS THE SOURCE OF TRUTH FOR TRADING
    balance_start_time = time.time()

    # Get market type for better error handling
    market_info = get_market_type_info(token_id)
    market_type = market_info["type"]

    # Adjust retry strategy based on market type
    if market_type == "crypto":
        # Crypto markets often have USDC.e timing issues, be more patient
        balance_info = retry_balance_api_call(
            token_id, symbol, config["retry_count"] + 1, config["retry_delay"] * 1.5
        )
        retry_count = config["retry_count"] + 1 if balance_info is None else 1
    else:
        balance_info = retry_balance_api_call(
            token_id, symbol, config["retry_count"], config["retry_delay"]
        )
        retry_count = config["retry_count"] if balance_info is None else 1

    if balance_info is None:
        log(
            f"   ❌ [{symbol}] Balance API completely failed after {retry_count} retries (Market: {market_type})"
        )
        # Even if balance API fails, we CANNOT use position data for trading
        # Return zero balance to prevent insufficient funds errors
        return {
            "balance": 0,
            "allowance": 0,
            "source": "balance_api_failed",
            "confidence": 0.1,
            "discrepancy": 0,
            "retry_count": retry_count,
            "cross_validated": False,
            "reason": "balance_api_failed_zero_balance_for_safety",
        }

    # Step 2: Cross-validate with position data for monitoring ONLY
    actual_balance = balance_info.get("balance", 0)

    # Get market type for better error handling
    market_info = get_market_type_info(token_id)
    market_type = market_info["type"]

    if enable_cross_validation:
        position_data = get_position_from_data_api(
            user_address, token_id, original_symbol
        )

        # Get position data for discrepancy logging
        position_val = position_data.get("size", 0) if position_data else 0

        # Calculate discrepancy for logging and enhanced logic
        discrepancy = abs(actual_balance - position_val)

        # DEBUG: Verify symbol consistency before logging discrepancies
        if symbol != original_symbol:
            log(
                f"   🚨 BALANCE SYMBOL MISMATCH [{call_id}]: Original={original_symbol}, Current={symbol}, Token={token_id[:20]}..."
            )
            # Use the original symbol for consistency
            symbol = original_symbol

        # DEBUG: Verify symbol consistency before logging discrepancies
        if symbol != original_symbol:
            log(
                f"   🚨 BALANCE SYMBOL MISMATCH [{call_id}]: Original={original_symbol}, Current={symbol}, Token={token_id[:20]}..."
            )
            # Use the original symbol for consistency
            symbol = original_symbol

        # DEBUG: Log the final comparison being made with call ID
        log(
            f"   🔍 BALANCE COMPARISON [{call_id}]: Symbol={original_symbol}, Balance={actual_balance:.4f}, Position={position_val:.4f}, Diff={abs(actual_balance - position_val):.4f}"
        )

        # Log significant discrepancies for monitoring with market type context
        log_balance_discrepancy(
            original_symbol,
            actual_balance,
            position_val,
            "balance_validation",
            "routine_check",
            market_type,
        )

        # Use the original symbol for consistency
        symbol = original_symbol

        # DEBUG: Log the final comparison being made with call ID
        log(
            f"   🔍 BALANCE COMPARISON [{call_id}]: Symbol={original_symbol}, Balance={actual_balance:.4f}, Position={position_val:.4f}, Diff={abs(actual_balance - position_val):.4f}"
        )

        # SIMPLIFIED: For SELL orders, use database position size - it's already correct!
        # Database shows what you actually own, balance shows what's available for new trades
        # Stop comparing them - it's just confusing

        # Only log significant discrepancies for monitoring, but don't change the logic
        if discrepancy > 1.0:
            log(
                f"   ℹ️  [{original_symbol}] Position vs Balance: Position={position_val:.4f}, Balance={actual_balance:.4f}, Diff={discrepancy:.4f}"
            )
            log(
                f"   ℹ️  [{original_symbol}] Using database position size for exit orders (what you actually own)"
            )

        # Always return database position size for exit orders - it's the source of truth
        result = {
            "balance": size,  # Use database position size (what you actually own)
            "allowance": balance_info.get("allowance", 0),
            "source": "database_position_size",
            "confidence": 0.9,  # High confidence in database data
            "discrepancy": discrepancy,
            "retry_count": retry_count,
            "cross_validated": True,
            "reason": "using_database_position_size_for_exit_orders",
            "api_response_time": time.time() - balance_start_time,
        }
        return result

        # Log significant discrepancies for monitoring with market type context
        log_balance_discrepancy(
            original_symbol,
            actual_balance,
            position_val,
            "balance_validation",
            "routine_check",
            market_type,
        )

        # DEBUG: Log the final comparison being made
        log(
            f"   🔍 BALANCE COMPARISON: Symbol={original_symbol}, Balance={actual_balance:.4f}, Position={position_val:.4f}, Diff={abs(actual_balance - position_val):.4f}"
        )

        # DEBUG: Log the final comparison being made
        log(
            f"   🔍 BALANCE COMPARISON: Symbol={original_symbol}, Balance={actual_balance:.4f}, Position={position_val:.4f}, Diff={abs(actual_balance - position_val):.4f}"
        )

        # Log significant discrepancies for monitoring with market type context
        log_balance_discrepancy(
            original_symbol,
            actual_balance,
            position_val,
            "balance_validation",
            "routine_check",
            market_type,
        )

        # Calculate discrepancy for logging
        discrepancy = abs(actual_balance - position_val)

        # Return balance data with position discrepancy info for monitoring
        result = {
            "balance": actual_balance,  # ALWAYS use actual balance for trading
            "allowance": balance_info.get("allowance", 0),
            "source": "balance_api_with_position_monitoring",
            "confidence": config["api_reliability_weight"],
            "discrepancy": discrepancy,
            "retry_count": retry_count,
            "cross_validated": True,
            "reason": "using_balance_api_position_for_monitoring_only",
            "api_response_time": time.time() - balance_start_time,
        }

        return result

    # No cross-validation - return balance data directly
    return {
        "balance": actual_balance,
        "allowance": balance_info.get("allowance", 0),
        "source": "balance_api_only",
        "confidence": config["api_reliability_weight"],
        "discrepancy": 0,
        "retry_count": retry_count,
        "cross_validated": False,
        "reason": "balance_api_only_no_position_validation",
        "api_response_time": time.time() - balance_start_time,
    }

    # Step 2: Cross-validate with position data if enabled
    if enable_cross_validation:
        position_data = get_position_from_data_api(user_address, token_id)

        # Also get all positions for better logging and verification
        all_positions = get_actual_positions_from_data_api(user_address)
        symbol_positions = [p for p in all_positions if p.get("asset", "") == token_id]

        actual_position_size = 0
        if symbol_positions:
            actual_position_size = float(symbol_positions[0].get("size", 0))
            log(f"   🔍 [{symbol}] Data API position size: {actual_position_size:.4f}")

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
            "api_response_time": time.time() - balance_start_time,
        }

        # Enhanced logging for significant discrepancies across all symbols
        if result["discrepancy"] > 5:  # Log for any symbol with >5 share discrepancy
            log(f"   🔍 [{symbol}] Balance validation: {result}")
            log(
                f"   🔍 [{symbol}] Actual balance: {balance_info.get('balance', 0):.4f}"
            )
            if symbol_positions:
                log(f"   🔍 [{symbol}] Data API position: {actual_position_size:.4f}")

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
        "api_response_time": time.time() - balance_start_time,
    }
