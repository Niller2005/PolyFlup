"""Order related constants"""

# CLOB V2 accepts the plain "BUY"/"SELL" strings (py_clob_client_v2.Side is an IntEnum)
BUY = "BUY"
SELL = "SELL"

# API Constraints
MIN_TICK_SIZE = 0.01
MIN_ORDER_SIZE = 5.0  # Minimum size in shares

# Retry Configuration
MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]  # Exponential backoff in seconds

# Known API Error Messages
API_ERRORS = {
    "INVALID_ORDER_MIN_TICK_SIZE": "Price breaks minimum tick size rules",
    "INVALID_ORDER_MIN_SIZE": "Size lower than minimum",
    "INVALID_ORDER_DUPLICATED": "Order already placed",
    "INVALID_ORDER_NOT_ENOUGH_BALANCE": "Not enough balance/allowance",
    "INVALID_ORDER_EXPIRATION": "Invalid expiration time",
    "INVALID_ORDER_ERROR": "Could not insert order",
    "EXECUTION_ERROR": "Could not execute trade",
    "ORDER_DELAYED": "Order delayed due to market conditions",
    "DELAYING_ORDER_ERROR": "Error delaying order",
    "FOK_ORDER_NOT_FILLED_ERROR": "FOK order not fully filled",
    "MARKET_NOT_READY": "Market not ready for orders",
}
