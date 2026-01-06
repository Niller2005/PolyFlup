"""Configuration and settings management"""

import os
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

# Trading Configuration
BET_PERCENT = float(os.getenv("BET_PERCENT", "5.0"))
MIN_EDGE = float(
    os.getenv("MIN_EDGE", "0.35")
)  # Lowered based on observed max confidence
CONTRARIAN_THRESHOLD = float(
    os.getenv("CONTRARIAN_THRESHOLD", "0.10")
)  # Threshold for flipping bias
CONFIDENCE_SCALING_FACTOR = float(os.getenv("CONFIDENCE_SCALING_FACTOR", "5.0"))
MAX_SPREAD = float(os.getenv("MAX_SPREAD", "0.15"))
WINDOW_START_PRICE_BUFFER_PCT = float(
    os.getenv("WINDOW_START_PRICE_BUFFER_PCT", "0.05")
)


# Position Management
ENABLE_STOP_LOSS = os.getenv("ENABLE_STOP_LOSS", "YES").upper() == "YES"
STOP_LOSS_PERCENT = float(os.getenv("STOP_LOSS_PERCENT", "40.0"))
ENABLE_TAKE_PROFIT = os.getenv("ENABLE_TAKE_PROFIT", "NO").upper() == "YES"
TAKE_PROFIT_PERCENT = float(os.getenv("TAKE_PROFIT_PERCENT", "80.0"))
ENABLE_REVERSAL = os.getenv("ENABLE_REVERSAL", "YES").upper() == "YES"

# Unfilled Order Management (separate from stop loss)
CANCEL_UNFILLED_ORDERS = os.getenv("CANCEL_UNFILLED_ORDERS", "NO").upper() == "YES"
UNFILLED_CANCEL_THRESHOLD = float(
    os.getenv("UNFILLED_CANCEL_THRESHOLD", "15.0")
)  # % price move to cancel unfilled orders
UNFILLED_TIMEOUT_SECONDS = int(
    os.getenv("UNFILLED_TIMEOUT_SECONDS", "300")
)  # Cancel unfilled orders after 5 minutes (300s)
UNFILLED_RETRY_ON_WINNING_SIDE = (
    os.getenv("UNFILLED_RETRY_ON_WINNING_SIDE", "YES").upper() == "YES"
)  # Retry on winning side if unfilled


# Position Scaling
ENABLE_SCALE_IN = os.getenv("ENABLE_SCALE_IN", "YES").upper() == "YES"
SCALE_IN_MIN_PRICE = float(
    os.getenv("SCALE_IN_MIN_PRICE", "0.60")
)  # Min 60 cents (60%+ win probability)
SCALE_IN_MAX_PRICE = float(os.getenv("SCALE_IN_MAX_PRICE", "0.90"))  # Max 90 cents
SCALE_IN_TIME_LEFT = int(
    os.getenv("SCALE_IN_TIME_LEFT", "300")
)  # 5 minutes (300 seconds)
SCALE_IN_MULTIPLIER = float(
    os.getenv("SCALE_IN_MULTIPLIER", "1.5")
)  # Add 150% more (2.5x total position)

# Exit Plan Configuration
ENABLE_EXIT_PLAN = os.getenv("ENABLE_EXIT_PLAN", "YES").upper() == "YES"
EXIT_PRICE_TARGET = float(os.getenv("EXIT_PRICE_TARGET", "0.99"))  # Target exit price
ENABLE_REWARD_OPTIMIZATION = (
    os.getenv("ENABLE_REWARD_OPTIMIZATION", "NO").upper() == "YES"
)  # Adjust orders to earn rewards
REWARD_OPT_MIN_MIDPOINT = float(os.getenv("REWARD_OPT_MIN_MIDPOINT", "0.85"))
REWARD_OPT_MIN_PRICE = float(os.getenv("REWARD_OPT_MIN_PRICE", "0.90"))
REWARD_OPT_PRICE_OFFSET = float(os.getenv("REWARD_OPT_PRICE_OFFSET", "0.01"))
EXIT_MIN_POSITION_AGE = int(
    os.getenv("EXIT_MIN_POSITION_AGE", "60")
)  # Minimum position age in seconds (1 minute) before exit plan activates
EXIT_CHECK_INTERVAL = int(
    os.getenv("EXIT_CHECK_INTERVAL", "60")
)  # Check for exit opportunities every N seconds
EXIT_AGGRESSIVE_MODE = (
    os.getenv("EXIT_AGGRESSIVE_MODE", "NO").upper() == "YES"
)  # More frequent exit checks (half the interval)

# ADX Filter
ADX_ENABLED = os.getenv("ADX", "NO").upper() == "YES"
ADX_INTERVAL = os.getenv("ADX_INTERVAL", "15m")
ADX_PERIOD = int(os.getenv("ADX_PERIOD", "10"))

# Enhanced Binance Integration
MOMENTUM_LOOKBACK_MINUTES = int(os.getenv("MOMENTUM_LOOKBACK_MINUTES", "15"))
ENABLE_MOMENTUM_FILTER = os.getenv("ENABLE_MOMENTUM_FILTER", "YES").upper() == "YES"
ENABLE_ORDER_FLOW = os.getenv("ENABLE_ORDER_FLOW", "YES").upper() == "YES"
ENABLE_DIVERGENCE = os.getenv("ENABLE_DIVERGENCE", "YES").upper() == "YES"
ENABLE_VWM = os.getenv("ENABLE_VWM", "YES").upper() == "YES"

# External Trend Filter (Legacy)
ENABLE_BFXD = os.getenv("ENABLE_BFXD", "NO").upper() == "YES"

# Timing
WINDOW_DELAY_SEC = int(os.getenv("WINDOW_DELAY_SEC", "12"))
MAX_ENTRY_LATENESS_SEC = int(
    os.getenv("MAX_ENTRY_LATENESS_SEC", "600")
)  # Skip entry if > 10m late (allowed more lateness)
if WINDOW_DELAY_SEC < 0:
    WINDOW_DELAY_SEC = 0
if WINDOW_DELAY_SEC > 300:
    WINDOW_DELAY_SEC = 300

# Markets
MARKETS_ENV = os.getenv("MARKETS", "BTC,ETH,XRP,SOL")
MARKETS = [m.strip().upper() for m in MARKETS_ENV.split(",") if m.strip()]

# Credentials
PROXY_PK = os.getenv("PROXY_PK")
FUNDER_PROXY = os.getenv("FUNDER_PROXY", "")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")
BFXD_URL = os.getenv("BFXD_URL", "").strip()

if not PROXY_PK or not PROXY_PK.startswith("0x"):
    print(
        f"[{datetime.now(tz=ZoneInfo('UTC')).strftime('%Y-%m-%d %H:%M:%S UTC')}] ❌ FATAL: Missing PROXY_PK in .env!",
        flush=True,
    )
    raise SystemExit("Missing PROXY_PK in .env!")

# Constants
BINANCE_FUNDING_MAP = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "XRP": "XRPUSDT",
    "SOL": "SOLUSDT",
}

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOG_FILE = f"{BASE_DIR}/logs/trades_2025.log"
# Database Configuration
DB_FILE = f"{BASE_DIR}/trades.db"
REPORTS_DIR = f"{BASE_DIR}/logs/reports"
os.makedirs(REPORTS_DIR, exist_ok=True)

# API Endpoints
CLOB_HOST = "https://clob.polymarket.com"
CLOB_WSS_HOST = "wss://ws-subscriptions-clob.polymarket.com"
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
DATA_API_BASE = "https://data-api.polymarket.com"
CHAIN_ID = 137
SIGNATURE_TYPE = 2
POLYGON_RPC = "https://polygon-rpc.com"

# Contracts
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
CTF_ABI = '[{"constant":false,"inputs":[{"name":"collateralToken","type":"address"},{"name":"parentCollectionId","type":"bytes32"},{"name":"conditionId","type":"bytes32"},{"name":"indexSets","type":"uint256[]"}],"name":"redeemPositions","outputs":[],"payable":false,"stateMutability":"nonpayable","type":"function"}]'
