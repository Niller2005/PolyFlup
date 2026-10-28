"""Configuration and settings management"""

import os
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

from src.markets import parse_market, scale_seconds, window_minutes

load_dotenv()

# Paper trading: simulated account on live order books (src/paper.py). Uses its own
# database/log files so it can never mix with real trades. `polyflup.py --paper` sets this.
PAPER_TRADING = os.getenv("PAPER_TRADING", "NO").upper() == "YES"
PAPER_STARTING_BALANCE = float(os.getenv("PAPER_STARTING_BALANCE", "1000"))
PAPER_MAKER_FILL_MODE = os.getenv("PAPER_MAKER_FILL_MODE", "cross").lower()  # cross | touch

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
MAX_SIZE = os.getenv("MAX_SIZE", "500.0")
if MAX_SIZE.upper() not in ["NONE", "NO", ""]:
    MAX_SIZE = float(MAX_SIZE)
else:
    MAX_SIZE = None  # No cap
MAX_SIZE_MODE = os.getenv("MAX_SIZE_MODE", "CAP")  # CAP or MAXIMIZE

# Hedge Pricing
COMBINED_PRICE_THRESHOLD = float(
    os.getenv("COMBINED_PRICE_THRESHOLD", "0.97")
)  # Max combined entry+hedge price for profit (default 3¢ buffer, increased from 1.5¢)

# Fee-aware edge (CLOB V2: taker fee = shares * rate * p * (1-p) in pUSD, makers pay 0)
TAKER_FEE_RATE = float(os.getenv("TAKER_FEE_RATE", "0.07"))  # crypto markets
MIN_NET_EDGE = float(
    os.getenv("MIN_NET_EDGE", "0.01")
)  # Min guaranteed profit per share AFTER fees to open an entry+hedge pair (1¢)
ENABLE_MAKER_PAIR = (
    os.getenv("ENABLE_MAKER_PAIR", "YES").upper() == "YES"
)  # Rest POST_ONLY bids on both legs when the taker pair has no net edge
MAKER_PAIR_PRICE_BAND = float(
    os.getenv("MAKER_PAIR_PRICE_BAND", "0.20")
)  # Only quote when both bids are within [band, 1-band]: a lopsided book means the window is decided
MAKER_PAIR_IMPROVE_TICKS = int(
    os.getenv("MAKER_PAIR_IMPROVE_TICKS", "0")
)  # Ticks above best bid to quote (0 = join the queue, more = faster fills, less edge)

# Risk profile applied at startup: conservative | balanced | aggressive | custom (= the values in this file).
# Switch live from the TUI (keys 1/2/3/0); see src/profiles.py
RISK_PROFILE = os.getenv("RISK_PROFILE", "balanced").lower()

# Max entry workers running at once (many parallel REST calls produced 'Resource temporarily unavailable')
ENTRY_MAX_CONCURRENT = int(os.getenv("ENTRY_MAX_CONCURRENT", "4"))

# Exposure-aware sizing
MAX_EXPOSURE_PERCENT = float(
    os.getenv("MAX_EXPOSURE_PERCENT", "50")
)  # Open positions + in-flight entries may not exceed this % of equity (cash + exposure)
SIZE_FROM_AVAILABLE_CASH = (
    os.getenv("SIZE_FROM_AVAILABLE_CASH", "YES").upper() == "YES"
)  # BET_PERCENT applies to cash not already committed, so entries shrink as cash is used

# Kelly position sizing (replaces flat BET_PERCENT when KELLY_SIZING=YES)
KELLY_SIZING = os.getenv("KELLY_SIZING", "NO").upper() == "YES"
KELLY_FRACTION = float(os.getenv("KELLY_FRACTION", "0.25"))  # quarter Kelly: estimates are noisy
KELLY_MAX_FRACTION = float(
    os.getenv("KELLY_MAX_FRACTION", "0.08")
)  # Hard cap per position as a fraction of bankroll (also the per-entry reservation)
KELLY_LEG_FAIL_RATE = float(
    os.getenv("KELLY_LEG_FAIL_RATE", "0.30")
)  # Prior: share of pairs where one leg fills and the other cannot be hedged. MEASURE THIS in paper mode.
KELLY_LEG_LOSS = float(
    os.getenv("KELLY_LEG_LOSS", "0.04")
)  # Loss per share when a pair legs out (spread + taker fee to flatten, ~4¢)
KELLY_PROB_HAIRCUT = float(
    os.getenv("KELLY_PROB_HAIRCUT", "0.02")
)  # Subtracted from the late-window win probability before sizing (model error)

# Trailing: after one leg of a maker pair fills, re-quote the other leg to follow the market
ENABLE_TRAILING = os.getenv("ENABLE_TRAILING", "YES").upper() == "YES"
TRAIL_MAX_TICKS = int(
    os.getenv("TRAIL_MAX_TICKS", "2")
)  # Ticks above best bid the trailing leg may escalate to as time passes (never above the MIN_NET_EDGE cap)
TRAIL_MIN_REPRICE_SECONDS = int(os.getenv("TRAIL_MIN_REPRICE_SECONDS", "5"))

# Late-window conviction mode: when the leading side is far from the strike near close
LATE_WINDOW_ENABLED = (
    os.getenv("LATE_WINDOW_ENABLED", "YES").upper() == "YES"
)  # Evaluate + log to shadow_signals (no orders)
LATE_WINDOW_LIVE = (
    os.getenv("LATE_WINDOW_LIVE", "NO").upper() == "YES"
)  # Actually place orders (enable only after reviewing shadow results)
LATE_WINDOW_SECONDS = int(
    os.getenv("LATE_WINDOW_SECONDS", "90")
)  # Evaluate during the last N seconds of a window
# Skip a late-window entry when the ask is more than this below our probability: that gap
# means the market knows something the strike/z estimate does not (23 of 24 such ENTERs lost).
LATE_WINDOW_MAX_DISAGREEMENT = float(os.getenv("LATE_WINDOW_MAX_DISAGREEMENT", "0.15"))
LATE_WINDOW_MIN_EV = float(
    os.getenv("LATE_WINDOW_MIN_EV", "0.015")
)  # Min net EV per share (after taker fee) to enter
LATE_WINDOW_MIN_DISTANCE_PCT = float(
    os.getenv("LATE_WINDOW_MIN_DISTANCE_PCT", "0.02")
)  # Skip if |spot-strike| < this % (Chainlink-vs-Binance basis risk)
LATE_WINDOW_BET_USD = float(os.getenv("LATE_WINDOW_BET_USD", "5.0"))
LATE_WINDOW_EVAL_INTERVAL = int(
    os.getenv("LATE_WINDOW_EVAL_INTERVAL", "10")
)  # Seconds between evaluations per market

# Confidence-Biased Fill Priority
# Makes winning side (high confidence) more aggressive to fill first
ENABLE_CONFIDENCE_BOOST = os.getenv("ENABLE_CONFIDENCE_BOOST", "YES").upper() == "YES"
CONFIDENCE_BOOST_MAX = float(
    os.getenv("CONFIDENCE_BOOST_MAX", "0.03")
)  # Maximum additional price boost for 100% confidence (3¢)
CONFIDENCE_BOOST_MIN_CONFIDENCE = float(
    os.getenv("CONFIDENCE_BOOST_MIN_CONFIDENCE", "0.50")
)  # Only apply boost if confidence >= 50%

# Adaptive Combined Price Threshold (Option C)
ENABLE_ADAPTIVE_THRESHOLD = (
    os.getenv("ENABLE_ADAPTIVE_THRESHOLD", "YES").upper() == "YES"
)
ADAPTIVE_THRESHOLD_MIN = float(
    os.getenv("ADAPTIVE_THRESHOLD_MIN", "0.95")
)  # Minimum threshold (5¢ buffer in worst case)
ADAPTIVE_THRESHOLD_MAX = float(
    os.getenv("ADAPTIVE_THRESHOLD_MAX", "0.985")
)  # Maximum threshold (1.5¢ buffer in best case)
ADAPTIVE_THRESHOLD_BUFFER = float(
    os.getenv("ADAPTIVE_THRESHOLD_BUFFER", "0.02")
)  # Extra buffer beyond spreads (2¢)

# Pre-Flight Liquidity Check (Option A)
ENABLE_LIQUIDITY_CHECK = os.getenv("ENABLE_LIQUIDITY_CHECK", "YES").upper() == "YES"
LIQUIDITY_MIN_DEPTH_RATIO = float(
    os.getenv("LIQUIDITY_MIN_DEPTH_RATIO", "0.5")
)  # Need 50% of trade size in orderbook depth
LIQUIDITY_MAX_SPREAD_PCT = float(
    os.getenv("LIQUIDITY_MAX_SPREAD_PCT", "8.0")
)  # Reject if spread > 8%
LIQUIDITY_MAX_COMBINED_SPREAD_PCT = float(
    os.getenv("LIQUIDITY_MAX_COMBINED_SPREAD_PCT", "12.0")
)  # Reject if entry+hedge spread > 12%
LIQUIDITY_MIN_FILL_VELOCITY = float(
    os.getenv("LIQUIDITY_MIN_FILL_VELOCITY", "10.0")
)  # Minimum shares filled per minute (0 = disabled)
LIQUIDITY_FILL_LOOKBACK_SECONDS = int(
    os.getenv("LIQUIDITY_FILL_LOOKBACK_SECONDS", "120")
)  # Look at last 120 seconds of fills


# Position Management
ENABLE_STOP_LOSS = os.getenv("ENABLE_STOP_LOSS", "YES").upper() == "YES"
STOP_LOSS_PERCENT = float(os.getenv("STOP_LOSS_PERCENT", "40.0"))
STOP_LOSS_PRICE = float(
    os.getenv("STOP_LOSS_PRICE", "0.30")
)  # Sell if midpoint <= 30 cents
LOSING_SIDE_MIN_CONFIDENCE = float(
    os.getenv("LOSING_SIDE_MIN_CONFIDENCE", "0.40")
)  # Min 40% confidence for losers
ENABLE_TAKE_PROFIT = os.getenv("ENABLE_TAKE_PROFIT", "NO").upper() == "YES"
TAKE_PROFIT_PERCENT = float(os.getenv("TAKE_PROFIT_PERCENT", "80.0"))
ENABLE_REVERSAL = os.getenv("ENABLE_REVERSAL", "YES").upper() == "YES"
ENABLE_HEDGED_REVERSAL = os.getenv("ENABLE_HEDGED_REVERSAL", "YES").upper() == "YES"

# CTF Merge Strategy
ENABLE_CTF_MERGE = (
    os.getenv("ENABLE_CTF_MERGE", "NO").upper() == "YES"
)  # Merge hedged positions immediately to free capital

# Phase 2: WebSocket Order Fill Monitoring
ENABLE_WEBSOCKET_MONITORING = (
    os.getenv("ENABLE_WEBSOCKET_MONITORING", "NO").upper() == "YES"
)  # Use WebSocket fill notifications instead of polling (75-90% faster)

# Unfilled Order Management (separate from stop loss)
CANCEL_UNFILLED_ORDERS = os.getenv("CANCEL_UNFILLED_ORDERS", "NO").upper() == "YES"
UNFILLED_CANCEL_THRESHOLD = float(
    os.getenv("UNFILLED_CANCEL_THRESHOLD", "15.0")
)  # % price move to cancel unfilled orders
UNFILLED_TIMEOUT_SECONDS = int(
    os.getenv("UNFILLED_TIMEOUT_SECONDS", "300")
)  # Cancel unfilled orders after 5 minutes (300s)

# Hedge Order Monitoring
HEDGE_FILL_TIMEOUT_SECONDS = int(
    os.getenv("HEDGE_FILL_TIMEOUT_SECONDS", "90")
)  # Time to wait for hedge to fill before cancelling entry
HEDGE_GTC_FALLBACK_SECONDS = int(
    os.getenv("HEDGE_GTC_FALLBACK_SECONDS", "30")
)  # Switch hedge to GTC (taker) after this many seconds if entry filled but hedge not
HEDGE_POLL_INTERVAL_SECONDS = int(
    os.getenv("HEDGE_POLL_INTERVAL_SECONDS", "5")
)  # Check hedge fill status every N seconds

# Emergency Sell Progressive Pricing (Time-based strategy)
# When hedge timeout triggers emergency sell, uses progressive pricing with wait periods
# Strategy: Start aggressive (near bid), progressively lower price with increasing wait times
EMERGENCY_SELL_ENABLE_PROGRESSIVE = (
    os.getenv("EMERGENCY_SELL_ENABLE_PROGRESSIVE", "YES").upper() == "YES"
)  # Enable time-based progressive pricing
EMERGENCY_SELL_WAIT_SHORT = int(
    os.getenv("EMERGENCY_SELL_WAIT_SHORT", "5")
)  # Wait time for aggressive prices (5s)
EMERGENCY_SELL_WAIT_MEDIUM = int(
    os.getenv("EMERGENCY_SELL_WAIT_MEDIUM", "8")
)  # Wait time for medium prices (8s)
EMERGENCY_SELL_WAIT_LONG = int(
    os.getenv("EMERGENCY_SELL_WAIT_LONG", "10")
)  # Wait time for conservative prices (10s)
EMERGENCY_SELL_FALLBACK_PRICE = float(
    os.getenv("EMERGENCY_SELL_FALLBACK_PRICE", "0.10")
)  # Final fallback price if all attempts fail
EMERGENCY_SELL_HOLD_IF_WINNING = (
    os.getenv("EMERGENCY_SELL_HOLD_IF_WINNING", "YES").upper() == "YES"
)  # Hold position if it's winning or still reasonable vs market
EMERGENCY_SELL_PRICE_TOLERANCE_PCT = float(
    os.getenv("EMERGENCY_SELL_PRICE_TOLERANCE_PCT", "10.0")
)  # Hold if price within this % of entry (default 10%)
EMERGENCY_SELL_MIN_PROFIT_CENTS = float(
    os.getenv("EMERGENCY_SELL_MIN_PROFIT_CENTS", "5.0")
)  # Consider "winning" if +5¢ or more
EMERGENCY_SELL_CONFIDENCE_HOLD_THRESHOLD = float(
    os.getenv("EMERGENCY_SELL_CONFIDENCE_HOLD_THRESHOLD", "0.70")
)  # Hold unhedged position if confidence >= this threshold (default 70%)

# Pre-Settlement Exit Strategy for Unhedged Positions
# Pre-Settlement Exit Strategy - Progressive timing with decreasing confidence requirements
# Sell losing side of hedged/unhedged position before window close to extract additional value
# Selling the losing leg of a hedged pair before resolution gives up the locked profit for the
# market's own flip probability minus fee and spread: it lost ~$99 vs holding in the 2026-10-27 run.
PRE_SETTLEMENT_EXIT_HEDGED = os.getenv("PRE_SETTLEMENT_EXIT_HEDGED", "NO").upper() == "YES"
ENABLE_PRE_SETTLEMENT_EXIT = (
    os.getenv("ENABLE_PRE_SETTLEMENT_EXIT", "YES").upper() == "YES"
)  # Enable pre-settlement exit strategy
PRE_SETTLEMENT_EXIT_SECONDS = int(
    os.getenv("PRE_SETTLEMENT_EXIT_SECONDS", "180")
)  # Start checking N seconds before window close (default 180s = 3 min)
PRE_SETTLEMENT_MIN_CONFIDENCE = float(
    os.getenv("PRE_SETTLEMENT_MIN_CONFIDENCE", "0.85")
)  # Minimum confidence at earliest exit window (default 85% - be VERY sure we're selling losing side)
PRE_SETTLEMENT_CHECK_INTERVAL = int(
    os.getenv("PRE_SETTLEMENT_CHECK_INTERVAL", "5")
)  # Check for pre-settlement exits every N seconds (default 5s)
PRE_SETTLEMENT_PRICE_MAX_AGE = int(
    os.getenv("PRE_SETTLEMENT_PRICE_MAX_AGE", "180")
)  # Max age of cached prices in seconds (default 180s = 3 min)

# Progressive confidence thresholds (time_before_close: min_confidence)
# Markets close ~2-3 minutes before settlement, so we use progressive thresholds
# HIGH thresholds ensure we're VERY confident we're selling the losing side
PRE_SETTLEMENT_CONFIDENCE_SCHEDULE = {
    180: 0.90,  # T-3min: Extremely safe, require 90%+ confidence
    120: 0.85,  # T-2min: Very safe, require 85%+ confidence
    60: 0.80,  # T-1min: Still safe, require 80%+ confidence
    45: 0.75,  # T-45s: Market likely closed, 75%+ confidence (don't go lower - risk is too high)
}

# Enhanced Balance Validation for API Reliability Issues
ENABLE_ENHANCED_BALANCE_VALIDATION = (
    os.getenv("ENABLE_ENHANCED_BALANCE_VALIDATION", "YES").upper() == "YES"
)
BALANCE_CROSS_VALIDATION_TIMEOUT = int(
    os.getenv("BALANCE_CROSS_VALIDATION_TIMEOUT", "15")
)  # seconds
XRP_BALANCE_GRACE_PERIOD_MINUTES = int(
    os.getenv("XRP_BALANCE_GRACE_PERIOD_MINUTES", "15")
)
XRP_BALANCE_TRUST_FACTOR = float(
    os.getenv("XRP_BALANCE_TRUST_FACTOR", "0.3")
)  # Lower trust in XRP balance API
UNFILLED_RETRY_ON_WINNING_SIDE = (
    os.getenv("UNFILLED_RETRY_ON_WINNING_SIDE", "YES").upper() == "YES"
)  # Retry on winning side if unfilled


# Position Scaling
ENABLE_SCALE_IN = os.getenv("ENABLE_SCALE_IN", "YES").upper() == "YES"
SCALE_IN_MIN_PRICE = float(
    os.getenv("SCALE_IN_MIN_PRICE", "0.50")
)  # Min 50 cents (50%+ win probability)
SCALE_IN_MAX_PRICE = float(os.getenv("SCALE_IN_MAX_PRICE", "0.75"))  # Max 75 cents
SCALE_IN_TIME_LEFT = int(
    os.getenv("SCALE_IN_TIME_LEFT", "450")
)  # 7.5 minutes (450 seconds)
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
    os.getenv("EXIT_MIN_POSITION_AGE", "0")
)  # Disabled by default for immediate exit plan placement
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

# Markets: BTC (15m, default) or BTC-5M for 5-minute windows, e.g. MARKETS=BTC,ETH,BTC-5M,ETH-5M
MARKETS_ENV = (os.getenv("MARKETS_PAPER") if PAPER_TRADING else None) or os.getenv("MARKETS", "BTC,ETH,XRP,SOL")
MARKETS = []
for _entry in (m for m in MARKETS_ENV.split(",") if m.strip()):
    _key = parse_market(_entry).key
    if _key not in MARKETS:
        MARKETS.append(_key)
# Window log files rotate on the longest window so 5m markets don't churn them
PRIMARY_MARKET = max(MARKETS, key=window_minutes) if MARKETS else None


def max_entry_lateness(symbol: str) -> int:
    """Latest entry (seconds into the window). `MAX_ENTRY_LATENESS_SEC_5M` overrides the
    5m value, which otherwise scales proportionally from the 15m setting (600s -> 200s)."""
    override = os.getenv(f"MAX_ENTRY_LATENESS_SEC_{window_minutes(symbol)}M")
    if override:
        return int(override)
    return int(scale_seconds(MAX_ENTRY_LATENESS_SEC, symbol))

# Credentials
PROXY_PK = os.getenv("PROXY_PK")
if PAPER_TRADING and not PROXY_PK:
    # Nothing is ever signed in paper mode; a throwaway key only satisfies client setup.
    PROXY_PK = "0x" + "1" * 64
FUNDER_PROXY = os.getenv("FUNDER_PROXY", "")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")
BFXD_URL = os.getenv("BFXD_URL", "").strip()

# Polymarket Builder API (for gasless CTF operations via Relayer)
POLY_BUILDER_API_KEY = os.getenv("POLY_BUILDER_API_KEY", "")
POLY_BUILDER_SECRET = os.getenv("POLY_BUILDER_SECRET", "")
POLY_BUILDER_PASSPHRASE = os.getenv("POLY_BUILDER_PASSPHRASE", "")
ENABLE_RELAYER_CLIENT = os.getenv("ENABLE_RELAYER_CLIENT", "YES").upper() == "YES"

# Manual Web3 redemption fallback (requires MATIC for gas)
# Only used when relayer quota is exhausted or unavailable
ENABLE_MANUAL_REDEMPTION_FALLBACK = (
    os.getenv("ENABLE_MANUAL_REDEMPTION_FALLBACK", "NO").upper() == "YES"
)

if not PROXY_PK or not PROXY_PK.startswith("0x"):
    print(
        f"[{datetime.now(tz=ZoneInfo('UTC')).strftime('%Y-%m-%d %H:%M:%S UTC')}] ❌ FATAL: Missing PROXY_PK in .env!",
        flush=True,
    )
    raise SystemExit("Missing PROXY_PK in .env!")


# Price Movement Validation for High Confidence Trades
ENABLE_PRICE_VALIDATION = os.getenv("ENABLE_PRICE_VALIDATION", "YES").upper() == "YES"
PRICE_VALIDATION_MAX_MOVEMENT = float(
    os.getenv("PRICE_VALIDATION_MAX_MOVEMENT", "20.0")
)  # Max 20% movement
PRICE_VALIDATION_MIN_CONFIDENCE = float(
    os.getenv("PRICE_VALIDATION_MIN_CONFIDENCE", "0.75")
)  # Validate trades > 75% confidence
PRICE_VALIDATION_VOLATILITY_THRESHOLD = float(
    os.getenv("PRICE_VALIDATION_VOLATILITY_THRESHOLD", "0.7")
)  # High volatility threshold

# Bayesian Confidence Calculation
BAYESIAN_CONFIDENCE = os.getenv("BAYESIAN_CONFIDENCE", "NO").upper() == "YES"

# Constants
BINANCE_FUNDING_MAP = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "XRP": "XRPUSDT",
    "SOL": "SOLUSDT",
    "DOGE": "DOGEUSDT",
}

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.makedirs(f"{BASE_DIR}/logs", exist_ok=True)
# POLYFLUP_DB_NAME runs an isolated instance (own DB, log and lock), e.g. an experiment next to a live paper bot
DB_NAME = os.getenv("POLYFLUP_DB_NAME") or ("trades_paper" if PAPER_TRADING else "trades")
LOG_FILE = f"{BASE_DIR}/logs/{'trades_paper' if PAPER_TRADING else 'trades_2025'}{'' if DB_NAME in ('trades', 'trades_paper') else '_' + DB_NAME}.log"
ERROR_LOG_FILE = f"{BASE_DIR}/logs/errors.log"
# Database Configuration
DB_FILE = f"{BASE_DIR}/{DB_NAME}.db"
REPORTS_DIR = f"{BASE_DIR}/logs/reports"
os.makedirs(REPORTS_DIR, exist_ok=True)

# API Endpoints
CLOB_HOST = "https://clob.polymarket.com"
CLOB_WSS_HOST = "wss://ws-subscriptions-clob.polymarket.com"
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
DATA_API_BASE = "https://data-api.polymarket.com"
CHAIN_ID = 137
# Wallet type: 0=EOA, 1=POLY_PROXY (Magic/email), 2=POLY_GNOSIS_SAFE, 3=POLY_1271 (deposit wallet)
SIGNATURE_TYPE = int(os.getenv("SIGNATURE_TYPE", "2"))
POLYGON_RPC = os.getenv("POLYGON_RPC", "https://polygon-rpc.com")

# Contracts (CLOB V2 collateral is pUSD, 6 decimals; replaced USDC.e on 2026-04-28)
PUSD_ADDRESS = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
