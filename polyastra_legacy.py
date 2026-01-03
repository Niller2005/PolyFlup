#!/usr/bin/env python3
"""
PolyAstra Trading Bot - Complete Version (FIXED + ADX Filter)
Automated trading bot for 15-minute crypto prediction markets on Polymarket

"""

import os
import sys
import time
import json
import sqlite3
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv, set_key
from eth_account import Account
from web3 import Web3
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType, ApiCreds
from py_clob_client.order_builder.constants import BUY, SELL

# ========================== CONFIGURATION ==========================

try:
    load_dotenv()

    BET_PERCENT = float(os.getenv("BET_PERCENT", "5.0"))  # Percent of balance per trade
    MIN_EDGE = float(os.getenv("MIN_EDGE", "0.565"))
    MAX_SPREAD = float(os.getenv("MAX_SPREAD", "0.15"))

    # Position Management
    ENABLE_STOP_LOSS = os.getenv("ENABLE_STOP_LOSS", "YES").upper() == "YES"
    STOP_LOSS_PERCENT = float(
        os.getenv("STOP_LOSS_PERCENT", "50.0")
    )  # Exit if down 50%
    ENABLE_TAKE_PROFIT = os.getenv("ENABLE_TAKE_PROFIT", "NO").upper() == "YES"
    TAKE_PROFIT_PERCENT = float(
        os.getenv("TAKE_PROFIT_PERCENT", "80.0")
    )  # Exit if up 80%
    ENABLE_REVERSAL = (
        os.getenv("ENABLE_REVERSAL", "NO").upper() == "YES"
    )  # Reverse position on stop loss

    # ADX Filter Configuration
    ADX_ENABLED = os.getenv("ADX", "NO").upper() == "YES"
    ADX_THRESHOLD = float(os.getenv("ADX_THRESHOLD", "20.0"))
    ADX_INTERVAL = os.getenv(
        "ADX_INTERVAL", "15m"
    )  # Kline interval for ADX calculation
    ADX_PERIOD = int(
        os.getenv("ADX_PERIOD", "10")
    )  # ADX period (default 10 for faster reaction)

    # How many seconds after a 15m window starts we begin trading (default 12)
    WINDOW_DELAY_SEC = int(os.getenv("WINDOW_DELAY_SEC", "12"))
    if WINDOW_DELAY_SEC < 0:
        WINDOW_DELAY_SEC = 0
    if WINDOW_DELAY_SEC > 300:
        WINDOW_DELAY_SEC = 300  # simple safety limit

    MARKETS_ENV = os.getenv("MARKETS", "BTC,ETH,XRP,SOL")
    MARKETS = [m.strip().upper() for m in MARKETS_ENV.split(",") if m.strip()]

    PROXY_PK = os.getenv("PROXY_PK")
    FUNDER_PROXY = os.getenv("FUNDER_PROXY", "")
    DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")
    BFXD_URL = os.getenv("BFXD_URL", "").strip()  # external trend filter (optional)

    if not PROXY_PK or not PROXY_PK.startswith("0x"):
        print(
            f"[{datetime.now(tz=ZoneInfo('UTC')).strftime('%Y-%m-%d %H:%M:%S UTC')}] ❌ FATAL: Missing PROXY_PK in .env!",
            flush=True,
        )
        raise SystemExit("Missing PROXY_PK in .env!")

except Exception as e:
    print(
        f"[{datetime.now(tz=ZoneInfo('UTC')).strftime('%Y-%m-%d %H:%M:%S UTC')}] ❌ STARTUP ERROR: {e}",
        flush=True,
    )
    raise SystemExit(1)

BINANCE_FUNDING_MAP = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "XRP": "XRPUSDT",
    "SOL": "SOLUSDT",
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = f"{BASE_DIR}/logs/trades_2025.log"
DB_FILE = f"{BASE_DIR}/trades.db"
REPORTS_DIR = f"{BASE_DIR}/logs/reports"
os.makedirs(f"{BASE_DIR}/logs/reports", exist_ok=True)

CLOB_HOST = "https://clob.polymarket.com"
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CHAIN_ID = 137
SIGNATURE_TYPE = 2  # like in working reference script
POLYGON_RPC = "https://polygon-rpc.com"
USDC_ADDRESS = (
    "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # Bridged USDC (Polymarket standard)
)
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
CTF_ABI = '[{"constant":false,"inputs":[{"name":"collateralToken","type":"address"},{"name":"parentCollectionId","type":"bytes32"},{"name":"conditionId","type":"bytes32"},{"name":"indexSets","type":"uint256[]"}],"name":"redeemPositions","outputs":[],"payable":false,"stateMutability":"nonpayable","type":"function"}]'

# ========================== LOGGER ==========================


def log(text: str) -> None:
    """Log message to console and file"""
    line = (
        f"[{datetime.now(tz=ZoneInfo('UTC')).strftime('%Y-%m-%d %H:%M:%S UTC')}] {text}"
    )
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def send_discord(msg: str) -> None:
    """Send notification to Discord webhook"""
    if not DISCORD_WEBHOOK:
        return
    try:
        requests.post(DISCORD_WEBHOOK, json={"content": msg}, timeout=5)
    except Exception:
        pass


# ========================== WEB3 ==========================

w3 = Web3(Web3.HTTPProvider(POLYGON_RPC))


def get_balance(addr: str) -> float:
    """Get USDC balance for address"""
    try:
        abi = '[{"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"}]'
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(USDC_ADDRESS), abi=abi
        )
        raw = contract.functions.balanceOf(Web3.to_checksum_address(addr)).call()
        return raw / 1e6
    except Exception:
        return 0.0


def redeem_winnings(condition_id_hex: str, neg_risk: bool = False) -> bool:
    """
    Redeem winnings from CTF contract for a resolved condition.

    Args:
        condition_id_hex: The condition ID (0x...)
        neg_risk: Whether this is a negative risk market (not applicable for 15min crypto markets)

    Returns:
        True if redemption was successful, False otherwise
    """
    try:
        # 15-minute crypto markets are NOT negative risk markets
        # They are standard binary CTF markets
        if neg_risk:
            log(
                f"⚠️ Negative risk redemption not implemented (not needed for 15min markets)"
            )
            return False

        log(f"💰 Attempting to redeem condition {condition_id_hex}...")

        # Get contract instance
        ctf_contract = w3.eth.contract(
            address=Web3.to_checksum_address(CTF_ADDRESS), abi=CTF_ABI
        )

        # Get account
        account = Account.from_key(PROXY_PK)
        my_address = account.address

        # Parse condition_id
        if condition_id_hex.startswith("0x"):
            condition_id = bytes.fromhex(condition_id_hex[2:])
        else:
            condition_id = bytes.fromhex(condition_id_hex)

        # Polymarket standard parameters:
        # - collateralToken: USDC address
        # - parentCollectionId: bytes32(0) (null for Polymarket)
        # - conditionId: the market's condition ID
        # - indexSets: [1, 2] for binary markets (represents outcome A and B)
        parent_collection_id = bytes(32)  # null bytes32
        index_sets = [1, 2]  # Standard for Polymarket binary markets

        # Build transaction
        tx = ctf_contract.functions.redeemPositions(
            Web3.to_checksum_address(USDC_ADDRESS),
            parent_collection_id,
            condition_id,
            index_sets,
        ).build_transaction(
            {
                "from": my_address,
                "nonce": w3.eth.get_transaction_count(my_address),
                "gas": 200000,  # Set reasonable gas limit
                "gasPrice": w3.eth.gas_price,
            }
        )

        # Sign transaction
        signed_tx = w3.eth.account.sign_transaction(tx, private_key=PROXY_PK)

        # Send transaction
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)

        log(f"✅ Redeem TX sent: {w3.to_hex(tx_hash)}")

        # Wait for receipt (with timeout)
        try:
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            if receipt["status"] == 1:
                log(f"✅ Redemption successful! Gas used: {receipt['gasUsed']}")
                return True
            else:
                log(f"❌ Redemption transaction failed")
                return False
        except Exception as e:
            log(f"⚠️ Could not get transaction receipt: {e}")
            return False

    except Exception as e:
        log(f"❌ Redeem error: {e}")
        import traceback

        log(traceback.format_exc())
        return False


# ========================== CLOB CLIENT ==========================

client = ClobClient(
    host=CLOB_HOST,
    key=PROXY_PK,
    chain_id=CHAIN_ID,
    signature_type=SIGNATURE_TYPE,
    funder=FUNDER_PROXY or None,
)

# Hotfix: ensure client has builder_config attribute
if not hasattr(client, "builder_config"):
    client.builder_config = None


def setup_api_creds() -> None:
    """Setup API credentials from .env or generate new ones"""
    api_key = os.getenv("API_KEY")
    api_secret = os.getenv("API_SECRET")
    api_passphrase = os.getenv("API_PASSPHRASE")

    if api_key and api_secret and api_passphrase:
        try:
            creds = ApiCreds(
                api_key=api_key, api_secret=api_secret, api_passphrase=api_passphrase
            )
            client.set_api_creds(creds)
            log("✓ API credentials loaded from .env")
            return
        except Exception as e:
            log(f"⚠ Error loading API creds from .env: {e}")

    try:
        creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)
        set_key(".env", "API_KEY", creds.api_key)
        set_key(".env", "API_SECRET", creds.api_secret)
        set_key(".env", "API_PASSPHRASE", creds.api_passphrase)
        log("✓ API credentials generated and saved")
    except Exception as e:
        log(f"❌ FATAL: API credentials error: {e}")
        raise


# ========================== DATABASE ==========================


def init_database():
    """Initialize SQLite database"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT, symbol TEXT, window_start TEXT, window_end TEXT,
            slug TEXT, token_id TEXT, side TEXT, edge REAL, entry_price REAL,
            size REAL, bet_usd REAL, p_yes REAL, best_bid REAL, best_ask REAL,
            imbalance REAL, funding_bias REAL, order_status TEXT, order_id TEXT,
            final_outcome TEXT, exit_price REAL, pnl_usd REAL, roi_pct REAL,
            settled BOOLEAN DEFAULT 0, settled_at TEXT, exited_early BOOLEAN DEFAULT 0
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_symbol ON trades(symbol)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_settled ON trades(settled)")
    conn.commit()
    conn.close()
    log("✓ Database initialized")


def save_trade(**kwargs):
    """Save trade to database"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO trades (timestamp, symbol, window_start, window_end, slug, token_id,
        side, edge, entry_price, size, bet_usd, p_yes, best_bid, best_ask,
        imbalance, funding_bias, order_status, order_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            datetime.now(tz=ZoneInfo("UTC")).isoformat(),
            kwargs["symbol"],
            kwargs["window_start"],
            kwargs["window_end"],
            kwargs["slug"],
            kwargs["token_id"],
            kwargs["side"],
            kwargs["edge"],
            kwargs["price"],
            kwargs["size"],
            kwargs["bet_usd"],
            kwargs["p_yes"],
            kwargs["best_bid"],
            kwargs["best_ask"],
            kwargs["imbalance"],
            kwargs["funding_bias"],
            kwargs["order_status"],
            kwargs["order_id"],
        ),
    )
    trade_id = c.lastrowid
    conn.commit()
    conn.close()
    log(f"✓ Trade #{trade_id} saved to database")
    return trade_id


# ========================== MARKET DATA ==========================


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
                    # assume clob_ids[0] = UP, clob_ids[1] = DOWN
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


# ========================== ADX CALCULATION ==========================


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
        # Need enough klines for ADX calculation (at least 2*period + buffer)
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


def adx_allows_trade(symbol: str) -> bool:
    """
    Check if ADX filter allows trade for given symbol.

    Returns True if:
    - ADX filter is disabled (ADX != YES)
    - ADX value is >= ADX_THRESHOLD
    - ADX calculation failed (fail-open for safety)

    Returns False if:
    - ADX filter is enabled AND ADX value < ADX_THRESHOLD
    """
    if not ADX_ENABLED:
        log(f"[{symbol}] ADX: Filter disabled, allowing trade")
        return True

    adx_value = get_adx_from_binance(symbol)

    if adx_value < 0:
        log(f"[{symbol}] ADX: Could not calculate ADX, allowing trade (fail-open)")
        return True

    if adx_value >= ADX_THRESHOLD:
        log(
            f"[{symbol}] ADX: {adx_value:.2f} >= {ADX_THRESHOLD:.2f} threshold, ALLOWING trade"
        )
        return True
    else:
        log(
            f"[{symbol}] ADX: {adx_value:.2f} < {ADX_THRESHOLD:.2f} threshold, BLOCKING trade (weak trend)"
        )
        return False


# ========================== STRATEGY ==========================


def calculate_edge(symbol: str, up_token: str):
    """Calculate edge for trading decision (UP leg as reference)"""
    try:
        book = client.get_order_book(up_token)
        if isinstance(book, dict):
            bids = book.get("bids", []) or []
            asks = book.get("asks", []) or []
        else:
            bids = getattr(book, "bids", []) or []
            asks = getattr(book, "asks", []) or []
    except Exception as e:
        log(f"[{symbol}] Order book error: {e}")
        return 0.5, "order book error", 0.5, None, None, 0.5

    if not bids or not asks:
        return 0.5, "empty order book", 0.5, None, None, 0.5

    best_bid = None
    best_ask = None

    if bids:
        best_bid = (
            float(bids[-1].price)
            if hasattr(bids[-1], "price")
            else float(bids[-1].get("price", 0))
        )
    if asks:
        best_ask = (
            float(asks[-1].price)
            if hasattr(asks[-1], "price")
            else float(asks[-1].get("price", 0))
        )

    if not best_bid or not best_ask:
        return 0.5, "no bid/ask", 0.5, best_bid, best_ask, 0.5

    spread = best_ask - best_bid
    if spread > MAX_SPREAD:
        log(f"[{symbol}] Spread too wide: {spread:.2%}")
        return 0.5, f"spread {spread:.2%}", 0.5, best_bid, best_ask, 0.5

    p_up = (best_bid + best_ask) / 2.0
    imbalance_raw = best_bid - (1.0 - best_ask)
    imbalance = max(min((imbalance_raw + 0.1) / 0.2, 1.0), 0.0)

    # 70% price + 30% imbalance
    edge = 0.7 * p_up + 0.3 * imbalance
    edge += get_funding_bias(symbol)

    fg = get_fear_greed()
    if fg < 30:
        edge += 0.03  # extreme fear -> bullish bias (UP)
    elif fg > 70:
        edge -= 0.03  # extreme greed -> bearish bias (DOWN)

    log(
        f"[{symbol}] Edge calculation: p_up={p_up:.4f} bid={best_bid:.4f} ask={best_ask:.4f} imb={imbalance:.4f} edge={edge:.4f}"
    )
    return edge, "OK", p_up, best_bid, best_ask, imbalance


# ========================== BFXD TREND FILTER ==========================


def bfxd_allows_trade(symbol: str, direction: str) -> bool:
    """
    External BTC trend filter (BFXD_URL).

    - Active only if BFXD_URL is set.
    - Applies only to BTC markets (symbol == 'BTC').
    - Rules:
        * trend BTC/USDT == 'UP'   -> allow only UP, block DOWN
        * trend BTC/USDT == 'DOWN' -> allow only DOWN, block UP
        * missing/invalid/error    -> allow everything (no filter)
    Additionally: logs WHAT we want to buy, WHAT trend was read, and IF it matches.
    """
    symbol_u = symbol.upper()
    direction_u = direction.upper()

    if not BFXD_URL:
        log(f"[{symbol}] BFXD: URL not set, skipping trend filter (side={direction_u})")
        return True

    if symbol_u != "BTC":
        log(
            f"[{symbol}] BFXD: symbol {symbol_u} != BTC, skipping trend filter (side={direction_u})"
        )
        return True

    try:
        log(
            f"[{symbol}] BFXD: fetching trend from {BFXD_URL} for side={direction_u}..."
        )
        r = requests.get(BFXD_URL, timeout=5)
        r.raise_for_status()
        data = r.json()

        if not isinstance(data, dict):
            log(
                f"[{symbol}] BFXD: invalid JSON (expected dict), allowing trade (side={direction_u})"
            )
            return True

        trend = str(data.get("BTC/USDT", "")).upper()
        if not trend:
            log(
                f"[{symbol}] BFXD: no BTC/USDT entry, allowing trade (side={direction_u}, trend=None)"
            )
            return True

        if trend not in ("UP", "DOWN"):
            log(
                f"[{symbol}] BFXD: unknown trend '{trend}', allowing trade (side={direction_u})"
            )
            return True

        match = trend == direction_u
        log(f"[{symbol}] BFXD: direction={direction_u}, trend={trend}, match={match}")

        if match:
            log(f"[{symbol}] BFXD: trend agrees with side={direction_u}, trade allowed")
            return True
        else:
            log(
                f"[{symbol}] BFXD: trend disagrees (trend={trend}, side={direction_u}), trade BLOCKED"
            )
            return False

    except Exception as e:
        log(
            f"[{symbol}] BFXD: error fetching/parsing trend ({e}), allowing trade (side={direction_u})"
        )
        return True


# ========================== ORDER MANAGER ==========================


def place_order(token_id: str, price: float, size: float) -> dict:
    """Place order on CLOB - using global client with hotfix for builder_config"""
    try:
        log(f"Placing order: {size} shares at ${price:.4f}")

        order_client = client

        if not hasattr(order_client, "builder_config"):
            order_client.builder_config = None

        api_key = os.getenv("API_KEY")
        api_secret = os.getenv("API_SECRET")
        api_passphrase = os.getenv("API_PASSPHRASE")
        if api_key and api_secret and api_passphrase:
            try:
                creds = ApiCreds(
                    api_key=api_key,
                    api_secret=api_secret,
                    api_passphrase=api_passphrase,
                )
                order_client.set_api_creds(creds)
            except Exception as e:
                log(f"⚠ Error setting API creds in place_order: {e}")

        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=BUY,
        )

        signed_order = order_client.create_order(order_args)
        resp = order_client.post_order(signed_order, OrderType.GTC)

        status = resp.get("status", "UNKNOWN") if resp else "UNKNOWN"
        order_id = resp.get("orderID") if resp else None

        log(f"✓ Order placed: {status} (ID: {order_id})")
        return {"success": True, "status": status, "order_id": order_id, "error": None}

    except Exception as e:
        log(f"❌ Order error: {e}")
        import traceback

        log(traceback.format_exc())
        return {"success": False, "status": "ERROR", "order_id": None, "error": str(e)}


def sell_position(token_id: str, size: float, current_price: float) -> dict:
    """Sell existing position (market sell to CLOB)"""
    try:
        # Sell at slightly below current market price for quick fill
        sell_price = max(0.01, current_price - 0.01)

        log(
            f"💸 Selling {size} shares of token {token_id[:10]}... at ${sell_price:.4f}"
        )

        # Setup client with credentials
        sell_client = client
        if not hasattr(sell_client, "builder_config"):
            sell_client.builder_config = None

        api_key = os.getenv("API_KEY")
        api_secret = os.getenv("API_SECRET")
        api_passphrase = os.getenv("API_PASSPHRASE")

        if api_key and api_secret and api_passphrase:
            try:
                creds = ApiCreds(
                    api_key=api_key,
                    api_secret=api_secret,
                    api_passphrase=api_passphrase,
                )
                sell_client.set_api_creds(creds)
            except Exception as e:
                log(f"⚠ Error setting API creds in sell_position: {e}")

        # Create SELL order
        order_args = OrderArgs(
            token_id=token_id,
            price=sell_price,
            size=size,
            side=SELL,  # SELL instead of BUY
        )

        signed_order = sell_client.create_order(order_args)
        resp = sell_client.post_order(signed_order, OrderType.GTC)

        status = resp.get("status", "UNKNOWN") if resp else "UNKNOWN"
        order_id = resp.get("orderID") if resp else None

        log(f"✓ SELL order placed: {status} (ID: {order_id})")
        return {
            "success": True,
            "sold": size,
            "price": sell_price,
            "status": status,
            "order_id": order_id,
        }

    except Exception as e:
        log(f"❌ Sell error: {e}")
        import traceback

        log(traceback.format_exc())
        return {"success": False, "error": str(e)}


def check_open_positions():
    """Check open positions every minute and manage them"""
    if not ENABLE_STOP_LOSS and not ENABLE_TAKE_PROFIT:
        return

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    now = datetime.now(tz=ZoneInfo("UTC"))

    # Get unsettled trades that are still in their window
    c.execute(
        """SELECT id, symbol, slug, token_id, side, entry_price, size, bet_usd, window_end 
           FROM trades 
           WHERE settled = 0 
           AND exited_early = 0
           AND datetime(window_end) > datetime(?)""",
        (now.isoformat(),),
    )
    open_positions = c.fetchall()

    if not open_positions:
        conn.close()
        return

    log(f"🔍 Checking {len(open_positions)} open positions...")

    for (
        trade_id,
        symbol,
        slug,
        token_id,
        side,
        entry_price,
        size,
        bet_usd,
        window_end,
    ) in open_positions:
        try:
            # Get current market price
            book = client.get_order_book(token_id)
            if isinstance(book, dict):
                bids = book.get("bids", []) or []
                asks = book.get("asks", []) or []
            else:
                bids = getattr(book, "bids", []) or []
                asks = getattr(book, "asks", []) or []

            if not bids or not asks:
                continue

            best_bid = float(
                bids[-1].price
                if hasattr(bids[-1], "price")
                else bids[-1].get("price", 0)
            )
            best_ask = float(
                asks[-1].price
                if hasattr(asks[-1], "price")
                else asks[-1].get("price", 0)
            )
            current_price = (best_bid + best_ask) / 2.0

            # Calculate current P&L
            current_value = current_price * size
            pnl_usd = current_value - bet_usd
            pnl_pct = (pnl_usd / bet_usd) * 100 if bet_usd > 0 else 0

            log(
                f"  [{symbol}] Trade #{trade_id} {side}: Entry=${entry_price:.4f} Current=${current_price:.4f} PnL={pnl_pct:+.1f}%"
            )

            # Check stop loss
            if ENABLE_STOP_LOSS and pnl_pct <= -STOP_LOSS_PERCENT:
                log(
                    f"🛑 STOP LOSS triggered for trade #{trade_id}: {pnl_pct:.1f}% loss"
                )

                # Sell current position
                sell_result = sell_position(token_id, size, current_price)

                if sell_result["success"]:
                    # Mark as exited early
                    c.execute(
                        """UPDATE trades 
                           SET exited_early=1, exit_price=?, pnl_usd=?, roi_pct=?, 
                               final_outcome='STOP_LOSS', settled=1, settled_at=? 
                           WHERE id=?""",
                        (current_price, pnl_usd, pnl_pct, now.isoformat(), trade_id),
                    )
                    send_discord(
                        f"🛑 **STOP LOSS** [{symbol}] {side} closed at {pnl_pct:+.1f}%"
                    )

                    # Optionally reverse position
                    if ENABLE_REVERSAL:
                        log(f"🔄 Reversing position for [{symbol}]...")
                        # Get opposite token ID
                        up_id, down_id = get_token_ids(symbol)
                        if up_id and down_id:
                            opposite_token = down_id if side == "UP" else up_id
                            opposite_side = "DOWN" if side == "UP" else "UP"
                            opposite_price = 1.0 - current_price

                            # Place reverse order with same size
                            reverse_result = place_order(
                                opposite_token, opposite_price, size
                            )
                            if reverse_result["success"]:
                                log(
                                    f"✅ Reversed to {opposite_side} @ ${opposite_price:.4f}"
                                )
                                send_discord(
                                    f"🔄 **REVERSED** [{symbol}] Now {opposite_side}"
                                )

            # Check take profit
            elif ENABLE_TAKE_PROFIT and pnl_pct >= TAKE_PROFIT_PERCENT:
                log(
                    f"🎯 TAKE PROFIT triggered for trade #{trade_id}: {pnl_pct:.1f}% gain"
                )

                # Sell current position
                sell_result = sell_position(token_id, size, current_price)

                if sell_result["success"]:
                    c.execute(
                        """UPDATE trades 
                           SET exited_early=1, exit_price=?, pnl_usd=?, roi_pct=?, 
                               final_outcome='TAKE_PROFIT', settled=1, settled_at=? 
                           WHERE id=?""",
                        (current_price, pnl_usd, pnl_pct, now.isoformat(), trade_id),
                    )
                    send_discord(
                        f"🎯 **TAKE PROFIT** [{symbol}] {side} closed at {pnl_pct:+.1f}%"
                    )

        except Exception as e:
            log(f"⚠️ Error checking position #{trade_id}: {e}")

    conn.commit()
    conn.close()


# ========================== SETTLEMENT ==========================


def get_market_resolution(slug: str):
    """
    Fetch market resolution from Gamma API.
    Returns:
        (resolved, outcome_prices)
        resolved: bool - True if market is fully resolved (prices are 0 or 1)
        outcome_prices: list[float] - [price_up, price_down] e.g. [1.0, 0.0]
    """
    try:
        r = requests.get(f"{GAMMA_API_BASE}/markets/slug/{slug}", timeout=5)
        if r.status_code == 200:
            data = r.json()

            # Check outcomePrices
            outcome_prices = data.get("outcomePrices")
            if isinstance(outcome_prices, str):
                outcome_prices = json.loads(outcome_prices)

            if not outcome_prices or len(outcome_prices) < 2:
                return False, None

            # Parse prices
            p0 = float(outcome_prices[0])
            p1 = float(outcome_prices[1])

            # Check if resolved (one is 1, one is 0)
            # We use a loose check (>= 0.99 or <= 0.01) just in case
            if (p0 >= 0.99 and p1 <= 0.01) or (p0 <= 0.01 and p1 >= 0.99):
                return True, [p0, p1]

    except Exception as e:
        log(f"Error fetching resolution for {slug}: {e}")

    return False, None


def check_and_settle_trades():
    """Check and settle completed trades using definitive API resolution"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    now = datetime.now(tz=ZoneInfo("UTC"))

    # Only check trades where window has ended
    c.execute(
        "SELECT id, symbol, slug, token_id, side, entry_price, size, bet_usd FROM trades WHERE settled = 0 AND datetime(window_end) < datetime(?)",
        (now.isoformat(),),
    )
    unsettled = c.fetchall()

    if not unsettled:
        log("ℹ No trades to settle")
        conn.close()
        return

    log(f"📊 Checking settlement for {len(unsettled)} trades...")
    total_pnl = 0
    settled_count = 0

    for trade_id, symbol, slug, token_id, side, entry_price, size, bet_usd in unsettled:
        try:
            # 1. Get resolution from API
            is_resolved, prices = get_market_resolution(slug)

            if not is_resolved:
                # Market not resolved yet, skip and check next cycle
                continue

            # 2. Identify which token we hold (UP or DOWN)
            # Fetch specific market data to match IDs safely
            r = requests.get(f"{GAMMA_API_BASE}/markets/slug/{slug}", timeout=5)
            data = r.json()
            clob_ids = data.get("clobTokenIds") or data.get("clob_token_ids")
            if isinstance(clob_ids, str):
                try:
                    clob_ids = json.loads(clob_ids)
                except:
                    # fallback parsing if simple string
                    clob_ids = [
                        x.strip().strip('"') for x in clob_ids.strip("[]").split(",")
                    ]

            # Determine outcome value
            final_price = 0.0
            if clob_ids and len(clob_ids) >= 2:
                if str(token_id) == str(clob_ids[0]):
                    final_price = float(prices[0])  # UP Price
                elif str(token_id) == str(clob_ids[1]):
                    final_price = float(prices[1])  # DOWN Price
                else:
                    log(
                        f"⚠️ Trade #{trade_id}: Token ID mismatch (held: {token_id} vs {clob_ids}), cannot settle."
                    )
                    continue
            else:
                log(f"⚠️ Trade #{trade_id}: Could not parse clobTokenIds.")
                continue

            exit_value = final_price
            pnl_usd = (exit_value * size) - bet_usd
            roi_pct = (pnl_usd / bet_usd) * 100 if bet_usd > 0 else 0

            # Auto-claim winnings if profitable
            if pnl_usd > 0:
                condition_id_hex = data.get("conditionId")
                if condition_id_hex:
                    log(
                        f"💰 Trade #{trade_id} won ${pnl_usd:.2f}, attempting to redeem..."
                    )
                    redeem_winnings(condition_id_hex)
                else:
                    log(f"⚠️ Trade #{trade_id}: No conditionId found, cannot redeem")

            c.execute(
                "UPDATE trades SET final_outcome=?, exit_price=?, pnl_usd=?, roi_pct=?, settled=1, settled_at=? WHERE id=?",
                ("RESOLVED", final_price, pnl_usd, roi_pct, now.isoformat(), trade_id),
            )

            emoji = "✅" if pnl_usd > 0 else "❌"
            log(
                f"{emoji} Trade #{trade_id} [{symbol}] {side}: {pnl_usd:+.2f}$ ({roi_pct:+.1f}%)"
            )
            total_pnl += pnl_usd
            settled_count += 1

        except Exception as e:
            log(f"⚠️ Error settling trade #{trade_id}: {e}")

    conn.commit()
    conn.close()

    if settled_count > 0:
        send_discord(
            f"📊 Settled {settled_count} trades | Total PnL: ${total_pnl:+.2f}"
        )


# ========================== REPORTS ==========================


def generate_statistics():
    """Generate performance statistics report"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "SELECT COUNT(*), SUM(bet_usd), SUM(pnl_usd), AVG(roi_pct) FROM trades WHERE settled = 1"
    )
    result = c.fetchone()
    total_trades = result[0] or 0

    if not total_trades:
        log("ℹ No settled trades for analysis")
        conn.close()
        return

    total_invested, total_pnl, avg_roi = result[1] or 0, result[2] or 0, result[3] or 0
    c.execute("SELECT COUNT(*) FROM trades WHERE settled = 1 AND pnl_usd > 0")
    winning_trades = c.fetchone()[0]
    win_rate = (winning_trades / total_trades) * 100

    report = []
    report.append("=" * 80)
    report.append("📊 POLYASTRA TRADING PERFORMANCE REPORT")
    report.append("=" * 80)
    report.append(f"Total trades:     {total_trades}")
    report.append(f"Win rate:         {win_rate:.1f}%")
    report.append(f"Total PnL:        ${total_pnl:.2f}")
    report.append(f"Total invested:   ${total_invested:.2f}")
    report.append(f"Average ROI:      {avg_roi:.2f}%")
    report.append(f"Total ROI:        {(total_pnl / total_invested) * 100:.2f}%")
    report.append("=" * 80)

    report_text = "\n".join(report)
    log(report_text)

    report_file = f"{REPORTS_DIR}/report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    with open(report_file, "w") as f:
        f.write(report_text)

    send_discord(f"📊 **PERFORMANCE REPORT**\n```\n{report_text}\n```")
    conn.close()


# ========================== MAIN TRADING ==========================


def trade_symbol(symbol: str, balance: float):
    """Execute trading logic for a symbol"""
    up_id, down_id = get_token_ids(symbol)
    if not up_id or not down_id:
        log(f"[{symbol}] Market not found, skipping")
        return

    edge, reason, p_up, best_bid, best_ask, imbalance = calculate_edge(symbol, up_id)

    # ============================================================
    # FIX: ODWRÓCONA LOGIKA - kupujemy NIEDOWARTOŚCIOWANĄ stronę
    # ============================================================
    # Wysoki edge (>= MIN_EDGE) = rynek wycenia UP wysoko = UP przewartościowane = kupuj DOWN
    # Niski edge (<= 1-MIN_EDGE) = rynek wycenia UP nisko = UP niedowartościowane = kupuj UP
    # ============================================================

    if edge <= (1.0 - MIN_EDGE):
        # Edge niski = UP jest tanie/niedowartościowane -> kupuj UP
        token_id, side, price = up_id, "UP", p_up
        log(
            f"[{symbol}] 📉 LOW edge ({edge:.4f} <= {1.0 - MIN_EDGE:.4f}) -> UP is undervalued, buying UP"
        )
    elif edge >= MIN_EDGE:
        # Edge wysoki = UP jest drogie/przewartościowane -> kupuj DOWN
        token_id, side, price = down_id, "DOWN", 1.0 - p_up
        log(
            f"[{symbol}] 📈 HIGH edge ({edge:.4f} >= {MIN_EDGE:.4f}) -> UP is overvalued, buying DOWN"
        )
    else:
        log(
            f"[{symbol}] ⚪ PASS | Edge {edge:.1%} in neutral zone ({1 - MIN_EDGE:.1%} - {MIN_EDGE:.1%})"
        )
        return

    # LOG: what we want to buy before trend filter
    log(f"[{symbol}] Direction decision: side={side}, edge={edge:.4f}, p_up={p_up:.4f}")

    # ADX trend strength filter (applies to all symbols)
    log(f"[{symbol}] 📊 ADX check: enabled={ADX_ENABLED}, threshold={ADX_THRESHOLD}")
    if not adx_allows_trade(symbol):
        log(
            f"[{symbol}] ⛔ ADX FILTER BLOCKED TRADE (symbol={symbol}, side={side}) - Weak Trend ⛔"
        )
        return

    # BFXD trend filter (BTC only)
    log(
        f"[{symbol}] 🔍 BFXD check: side={side}, url={'set' if BFXD_URL else 'not set'}"
    )
    if not bfxd_allows_trade(symbol, side):
        log(
            f"[{symbol}] ⛔ BFXD FILTER BLOCKED TRADE (symbol={symbol}, side={side}) ⛔"
        )
        return

    if price <= 0:
        log(f"[{symbol}] ERROR: Invalid price {price}")
        return

    price = max(0.01, min(0.99, price))

    # Use BET_PERCENT of available balance
    target_bet = balance * (BET_PERCENT / 100.0)
    log(
        f"[{symbol}] 🎯 Target bet: ${target_bet:.2f} ({BET_PERCENT}% of ${balance:.2f})"
    )

    size = round(target_bet / price, 6)

    MIN_SIZE = 5.0
    bet_usd_effective = target_bet

    if size < MIN_SIZE:
        old_size = size
        size = MIN_SIZE
        bet_usd_effective = round(size * price, 4)
        log(
            f"[{symbol}] Size {old_size:.4f} < min {MIN_SIZE}, bumping to {size:.4f}. "
            f"Effective stake ≈ ${bet_usd_effective:.2f}"
        )

    log(
        f"[{symbol}] 📈 {side} ${bet_usd_effective:.2f} | Edge {edge:.1%} | "
        f"Price {price:.4f} | Size {size} | Balance {balance:.2f}"
    )
    send_discord(
        f"**[{symbol}] {side} ${bet_usd_effective:.2f}** | Edge {edge:.1%} | Price {price:.4f}"
    )

    result = place_order(token_id, price, size)
    log(f"[{symbol}] Order status: {result['status']}")

    try:
        window_start, window_end = get_window_times(symbol)
        save_trade(
            symbol=symbol,
            window_start=window_start.isoformat(),
            window_end=window_end.isoformat(),
            slug=get_current_slug(symbol),
            token_id=token_id,
            side=side,
            edge=edge,
            price=price,
            size=size,
            bet_usd=bet_usd_effective,
            p_yes=p_up,
            best_bid=best_bid,
            best_ask=best_ask,
            imbalance=imbalance,
            funding_bias=get_funding_bias(symbol),
            order_status=result["status"],
            order_id=result["order_id"],
        )
    except Exception as e:
        log(f"[{symbol}] Database error: {e}")


# ========================== MAIN ==========================


def main():
    """Main bot loop"""
    log("🚀 Starting PolyAstra Trading Bot (FIXED VERSION + ADX Filter)...")
    log("📝 FIX: Reversed UP/DOWN logic - now buying undervalued side")
    log(
        f"📊 ADX Filter: {'ENABLED' if ADX_ENABLED else 'DISABLED'} (threshold={ADX_THRESHOLD}, period={ADX_PERIOD}, interval={ADX_INTERVAL})"
    )
    setup_api_creds()
    init_database()

    if FUNDER_PROXY and FUNDER_PROXY.startswith("0x"):
        addr = FUNDER_PROXY
        log_addr_type = "Funder"
    else:
        addr = Account.from_key(PROXY_PK).address
        log_addr_type = "Proxy"

    log("=" * 90)
    log(f"🤖 POLYASTRA | Markets: {', '.join(MARKETS)}")
    log(
        f"💼 Wallet ({log_addr_type}): {addr[:10]}...{addr[-8:]} | Balance: {get_balance(addr):.2f} USDC"
    )
    log(
        f"⚙️  MIN_EDGE: {MIN_EDGE:.1%} | BET: {BET_PERCENT}% of balance | MAX_SPREAD: {MAX_SPREAD:.1%}"
    )
    log(f"🕒 WINDOW_DELAY_SEC: {WINDOW_DELAY_SEC}s")
    log(
        f"📈 ADX: {'YES' if ADX_ENABLED else 'NO'} | Threshold: {ADX_THRESHOLD} | Period: {ADX_PERIOD} | Interval: {ADX_INTERVAL}"
    )
    log("=" * 90)
    log(
        f"🛡️  Stop Loss: {'ENABLED' if ENABLE_STOP_LOSS else 'DISABLED'} ({STOP_LOSS_PERCENT}%)"
    )
    log(
        f"🎯 Take Profit: {'ENABLED' if ENABLE_TAKE_PROFIT else 'DISABLED'} ({TAKE_PROFIT_PERCENT}%)"
    )
    log(f"🔄 Auto Reverse: {'ENABLED' if ENABLE_REVERSAL else 'DISABLED'}")
    log("=" * 90)

    cycle = 0
    last_position_check = time.time()

    while True:
        try:
            # Check positions every 60 seconds
            now_ts = time.time()
            if now_ts - last_position_check >= 60:
                check_open_positions()
                last_position_check = now_ts

            now = datetime.utcnow()
            wait = 900 - ((now.minute % 15) * 60 + now.second)
            if wait <= 0:
                wait += 900

            # Wait in 60-second chunks so we can check positions
            log(f"⏱️  Waiting {wait}s until next window + {WINDOW_DELAY_SEC}s delay...")

            remaining = wait + WINDOW_DELAY_SEC
            while remaining > 0:
                sleep_time = min(60, remaining)
                time.sleep(sleep_time)
                remaining -= sleep_time

                # Check positions during wait
                if remaining > 0:
                    check_open_positions()

            log(
                f"\n{'=' * 90}\n🔄 CYCLE #{cycle + 1} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}\n{'=' * 90}\n"
            )

            # Fetch balance once for the cycle
            current_balance = get_balance(addr)
            log(f"💰 Current Balance: {current_balance:.2f} USDC")

            for sym in MARKETS:
                log(f"\n{'=' * 30} {sym} {'=' * 30}")
                trade_symbol(sym, current_balance)
                time.sleep(1)

            check_and_settle_trades()
            cycle += 1

            if cycle % 16 == 0:
                log("\n📊 Generating performance report...")
                generate_statistics()

        except KeyboardInterrupt:
            log("\n⛔ Bot stopped by user")
            log("📊 Generating final report...")
            generate_statistics()
            break

        except Exception as e:
            log(f"❌ Critical error: {e}")
            import traceback

            log(traceback.format_exc())
            send_discord(f"❌ Error: {e}")
            time.sleep(60)


if __name__ == "__main__":
    main()
