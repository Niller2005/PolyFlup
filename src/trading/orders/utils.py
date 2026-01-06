"""Order validation and utility functions"""

import time
from typing import Optional, Any
from src.utils.logger import log
from .constants import (
    MIN_TICK_SIZE,
    MIN_ORDER_SIZE,
    API_ERRORS,
    MAX_RETRIES,
    RETRY_DELAYS,
)


def normalize_token_id(tid: Any) -> str:
    """Normalize token ID to decimal string"""
    if tid is None:
        return ""
    s = str(tid).strip().lower()
    if not s:
        return ""
    # If it's already a pure decimal string, return it
    if s.isdigit():
        return s
    # If it's hex (starts with 0x or contains a-f), try to convert
    if s.startswith("0x"):
        try:
            return str(int(s, 16))
        except:
            return s
    # Try converting anyway if it looks like a hex hash (longer than 10 chars)
    if len(s) > 10:
        try:
            return str(int(s, 16))
        except:
            return s
    return s


def _validate_price(
    price: float, tick_size: float = MIN_TICK_SIZE
) -> tuple[bool, Optional[str]]:
    if price <= 0:
        return False, "Price must be > 0"
    if price < 0.01 or price > 0.99:
        return False, "Price must be 0.01-0.99"
    decimal_places = 2
    if tick_size == 0.1:
        decimal_places = 1
    elif tick_size == 0.01:
        decimal_places = 2
    elif tick_size == 0.001:
        decimal_places = 3
    elif tick_size == 0.0001:
        decimal_places = 4
    if round(price, decimal_places) != price:
        return False, f"Price must be rounded to {tick_size}"
    return True, None


def _validate_size(size: float) -> tuple[bool, Optional[str]]:
    if size < MIN_ORDER_SIZE:
        return False, f"Order size must be at least {MIN_ORDER_SIZE}"
    return True, None


def truncate_float(val: float, decimals: int) -> float:
    """Truncate float to N decimal places without rounding up"""
    import math

    factor = 10**decimals
    return math.floor(val * factor) / factor


def _validate_order(price: float, size: float) -> tuple[bool, Optional[str]]:
    valid, err = _validate_price(price)
    if not valid:
        return False, err
    valid, err = _validate_size(size)
    if not valid:
        return False, err
    return True, None


def _parse_api_error(error_str: str) -> str:
    error_upper = error_str.upper()
    for code, desc in API_ERRORS.items():
        if code in error_upper:
            return f"{code}: {desc}"
    if "BALANCE" in error_upper or "ALLOWANCE" in error_upper:
        return "Insufficient funds"
    if "RATE" in error_upper and "LIMIT" in error_upper:
        return "Rate limit"
    return error_str


def _should_retry(error_str: str) -> bool:
    error_upper = error_str.upper()
    return any(
        k in error_upper for k in ["TIMEOUT", "RATE LIMIT", "503", "502", "CONNECTION"]
    )


def _execute_with_retry(func, *args, **kwargs):
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_err = e
            if not _should_retry(str(e)):
                raise
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAYS[attempt]
                log(
                    f"⏳ Retry {attempt + 2}/{MAX_RETRIES} after {delay}s: {_parse_api_error(str(e))}"
                )
                time.sleep(delay)
    if last_err:
        raise last_err
