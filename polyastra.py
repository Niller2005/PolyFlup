#!/usr/bin/env python3
"""
PolyAstra Trading Bot - Complete Version
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
from py_clob_client.order_builder.constants import BUY

# ========================== CONFIGURATION ==========================

load_dotenv()

BET_USD = float(os.getenv("BET_USD", "1.1"))
MIN_EDGE = float(os.getenv("MIN_EDGE", "0.565"))
MAX_SPREAD = float(os.getenv("MAX_SPREAD", "0.15"))

# Ile sekund po starcie 15-min okna zaczynamy handel (domyślnie 12)
WINDOW_DELAY_SEC = int(os.getenv("WINDOW_DELAY_SEC", "12"))
if WINDOW_DELAY_SEC < 0:
    WINDOW_DELAY_SEC = 0
if WINDOW_DELAY_SEC > 300:
    WINDOW_DELAY_SEC = 300  # prosty bezpiecznik

MARKETS_ENV = os.getenv("MARKETS", "BTC,ETH,XRP,SOL")
MARKETS = [m.strip().upper() for m in MARKETS_ENV.split(",") if m.strip()]

PROXY_PK = os.getenv("PROXY_PK")
FUNDER_PROXY = os.getenv("FUNDER_PROXY", "")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")
BFXD_URL = os.getenv("BFXD_URL", "").strip()  # zewnętrzny filtr trendu (opcjonalny)

if not PROXY_PK or not PROXY_PK.startswith("0x"):
    raise SystemExit("Missing PROXY_PK in .env!")

BINANCE_FUNDING_MAP = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "XRP": "XRPUSDT",
    "SOL": "SOLUSDT",
}

BASE_DIR = "/home/ubuntu/polyastra"
LOG_FILE = f"{BASE_DIR}/logs/trades_2025.log"
DB_FILE = f"{BASE_DIR}/trades.db"
REPORTS_DIR = f"{BASE_DIR}/logs/reports"
os.makedirs(f"{BASE_DIR}/logs/reports", exist_ok=True)

CLOB_HOST = "https://clob.polymarket.com"
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CHAIN_ID = 137
SIGNATURE_TYPE = 2  # Changed to 2 like in working script
POLYGON_RPC = "https://polygon-rpc.com"
USDC_ADDRESS = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"

# ========================== LOGGER ==========================

def log(text: str) -> None:
    """Log message to console and file"""
    line = f"[{datetime.now(tz=ZoneInfo('UTC')).strftime('%Y-%m-%d %H:%M:%S UTC')}] {text}"
    print(line)
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
        contract = w3.eth.contract(address=USDC_ADDRESS, abi=abi)
        raw = contract.functions.balanceOf(Web3.to_checksum_address(addr)).call()
        return raw / 1e6
    except Exception:
        return 0.0

# ========================== CLOB CLIENT ==========================

client = ClobClient(
    host=CLOB_HOST,
    key=PROXY_PK,
    chain_id=CHAIN_ID,
    signature_type=SIGNATURE_TYPE,
    funder=FUNDER_PROXY or None,
)

# Hotfix: zapewnij, że klient ma atrybut builder_config
if not hasattr(client, "builder_config"):
    client.builder_config = None

def setup_api_creds() -> None:
    """Setup API credentials from .env or generate new ones"""
    api_key = os.getenv("API_KEY")
    api_secret = os.getenv("API_SECRET")
    api_passphrase = os.getenv("API_PASSPHRASE")
    
    if api_key and api_secret and api_passphrase:
        try:
            creds = ApiCreds(api_key=api_key, api_secret=api_secret, api_passphrase=api_passphrase)
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
    c.execute('''
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT, symbol TEXT, window_start TEXT, window_end TEXT,
            slug TEXT, token_id TEXT, side TEXT, edge REAL, entry_price REAL,
            size REAL, bet_usd REAL, p_yes REAL, best_bid REAL, best_ask REAL,
            imbalance REAL, funding_bias REAL, order_status TEXT, order_id TEXT,
            final_outcome TEXT, exit_price REAL, pnl_usd REAL, roi_pct REAL,
            settled BOOLEAN DEFAULT 0, settled_at TEXT
        )
    ''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_symbol ON trades(symbol)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_settled ON trades(settled)')
    conn.commit()
    conn.close()
    log("✓ Database initialized")

def save_trade(**kwargs):
    """Save trade to database"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        INSERT INTO trades (timestamp, symbol, window_start, window_end, slug, token_id,
        side, edge, entry_price, size, bet_usd, p_yes, best_bid, best_ask,
        imbalance, funding_bias, order_status, order_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        datetime.now(tz=ZoneInfo('UTC')).isoformat(),
        kwargs['symbol'], kwargs['window_start'], kwargs['window_end'],
        kwargs['slug'], kwargs['token_id'], kwargs['side'], kwargs['edge'],
        kwargs['price'], kwargs['size'], kwargs['bet_usd'], kwargs['p_yes'],
        kwargs['best_bid'], kwargs['best_ask'], kwargs['imbalance'],
        kwargs['funding_bias'], kwargs['order_status'], kwargs['order_id']
    ))
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
    """Get UP and DOWN token IDs from Gamma API (YES/NO under the hood)"""
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
                        clob_ids = [x.strip().strip('"') for x in clob_ids.strip("[]").split(",")]
                if isinstance(clob_ids, list) and len(clob_ids) >= 2:
                    # przyjmujemy: clob_ids[0] = UP, clob_ids[1] = DOWN
                    log(f"[{symbol}] Tokens found: UP {clob_ids[0][:10]}... | DOWN {clob_ids[1][:10]}...")
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
        return int(requests.get("https://api.alternative.me/fng/", timeout=5).json()["data"][0]["value"])
    except:
        return 50

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
    
    # Get best prices (LAST elements - lists are sorted worst to best)
    best_bid = None
    best_ask = None
    
    if bids:
        best_bid = float(bids[-1].price) if hasattr(bids[-1], 'price') else float(bids[-1].get('price', 0))
    if asks:
        best_ask = float(asks[-1].price) if hasattr(asks[-1], 'price') else float(asks[-1].get('price', 0))
    
    if not best_bid or not best_ask:
        return 0.5, "no bid/ask", 0.5, best_bid, best_ask, 0.5
    
    spread = best_ask - best_bid
    if spread > MAX_SPREAD:
        log(f"[{symbol}] Spread too wide: {spread:.2%}")
        return 0.5, f"spread {spread:.2%}", 0.5, best_bid, best_ask, 0.5
    
    p_up = (best_bid + best_ask) / 2.0
    imbalance_raw = best_bid - (1.0 - best_ask)
    imbalance = max(min((imbalance_raw + 0.1) / 0.2, 1.0), 0.0)
    
    # Calculate edge: 70% price + 30% imbalance
    edge = 0.7 * p_up + 0.3 * imbalance
    edge += get_funding_bias(symbol)
    
    # Fear & Greed adjustment
    fg = get_fear_greed()
    if fg < 30:
        edge += 0.03  # Extreme fear -> bullish bias (UP)
    elif fg > 70:
        edge -= 0.03  # Extreme greed -> bearish bias (DOWN)
    
    log(f"[{symbol}] Edge calculation: p_up={p_up:.4f} bid={best_bid:.4f} ask={best_ask:.4f} imb={imbalance:.4f} edge={edge:.4f}")
    return edge, "OK", p_up, best_bid, best_ask, imbalance

# ========================== BFXD TREND FILTER ==========================

def bfxd_allows_trade(symbol: str, direction: str) -> bool:
    """
    Zewnętrzny filtr trendu (BFXD_URL):

    - działa tylko jeśli BFXD_URL jest ustawione,
    - dotyczy tylko BTC (symbol == 'BTC'),
    - zasady:
        * jeśli trend BTC/USDT == 'UP'   -> pozwalaj TYLKO na UP, blokuj DOWN
        * jeśli trend BTC/USDT == 'DOWN' -> pozwalaj TYLKO na DOWN, blokuj UP
        * jeśli brak wpisu / błąd / dziwny trend -> pozwalaj na wszystko (brak filtra)
    """
    if not BFXD_URL:
        return True

    symbol_u = symbol.upper()
    direction_u = direction.upper()

    if symbol_u != "BTC":
        return True

    try:
        r = requests.get(BFXD_URL, timeout=5)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            log(f"[{symbol}] BFXD: invalid JSON (expected object), BUY allowed (no strict filter)")
            return True

        trend = str(data.get("BTC/USDT", "")).upper()
        if not trend:
            log(f"[{symbol}] BFXD: no BTC/USDT entry in trend map, BUY allowed")
            return True

        if trend not in ("UP", "DOWN"):
            log(f"[{symbol}] BFXD: unknown trend '{trend}', BUY allowed")
            return True

        if trend == direction_u:
            log(f"[{symbol}] BFXD: trend BTC/USDT={trend}, direction={direction_u}, BUY allowed")
            return True
        else:
            log(f"[{symbol}] BFXD: trend BTC/USDT={trend}, direction={direction_u}, skipping BUY")
            return False

    except Exception as e:
        log(f"[{symbol}] BFXD: error fetching trend ({e}), BUY allowed (fallback)")
        return True

# ========================== ORDER MANAGER ==========================

def place_order(token_id: str, price: float, size: float) -> dict:
    """Place order on CLOB - using global client with hotfix for builder_config"""
    try:
        log(f"Placing order: {size} shares at ${price:.4f}")
        
        # Używamy globalnego klienta
        order_client = client

        # Hotfix: dopilnuj, że obiekt ma builder_config
        if not hasattr(order_client, "builder_config"):
            order_client.builder_config = None

        # (opcjonalnie) ustaw jeszcze raz API creds z .env, jeśli są
        api_key = os.getenv("API_KEY")
        api_secret = os.getenv("API_SECRET")
        api_passphrase = os.getenv("API_PASSPHRASE")
        if api_key and api_secret and api_passphrase:
            try:
                creds = ApiCreds(
                    api_key=api_key,
                    api_secret=api_secret,
                    api_passphrase=api_passphrase
                )
                order_client.set_api_creds(creds)
            except Exception as e:
                log(f"⚠ Error setting API creds in place_order: {e}")
        
        # Create order arguments
        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=BUY,
        )
        
        # Step 1: Create order (returns signed order ready to post)
        signed_order = order_client.create_order(order_args)
        
        # Step 2: Post the signed order
        resp = order_client.post_order(signed_order, OrderType.GTC)
        
        status = resp.get("status", "UNKNOWN") if resp else "UNKNOWN"
        order_id = resp.get("orderID") if resp else None
        
        log(f"✓ Order placed: {status} (ID: {order_id})")
        return {
            'success': True,
            'status': status,
            'order_id': order_id,
            'error': None
        }
        
    except Exception as e:
        log(f"❌ Order error: {e}")
        import traceback
        log(traceback.format_exc())
        return {
            'success': False,
            'status': 'ERROR',
            'order_id': None,
            'error': str(e)
        }

# ========================== SETTLEMENT ==========================

def check_and_settle_trades():
    """Check and settle completed trades"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    now = datetime.now(tz=ZoneInfo('UTC'))
    c.execute('SELECT id, symbol, slug, token_id, side, entry_price, size, bet_usd FROM trades WHERE settled = 0 AND datetime(window_end) < datetime(?)', (now.isoformat(),))
    unsettled = c.fetchall()
    
    if not unsettled:
        log("ℹ No trades to settle")
        conn.close()
        return
    
    log(f"📊 Settling {len(unsettled)} trades...")
    total_pnl = 0
    
    for trade_id, symbol, slug, token_id, side, entry_price, size, bet_usd in unsettled:
        try:
            # Get final price from order book
            book = client.get_order_book(token_id)
            bids = getattr(book, "bids", []) or []
            asks = getattr(book, "asks", []) or []
            if bids and asks:
                final_price = (float(bids[-1].price if hasattr(bids[-1], 'price') else bids[-1].get('price', 0)) + 
                              float(asks[-1].price if hasattr(asks[-1], 'price') else asks[-1].get('price', 0))) / 2.0
            else:
                final_price = 0.5
            
            # Calculate PnL (simplified - uses market price as exit)
            side_u = (side or "").upper()
            if side_u in ("UP", "YES"):
                exit_value = final_price
            else:
                exit_value = 1.0 - final_price

            pnl_usd = (exit_value * size) - bet_usd
            roi_pct = (pnl_usd / bet_usd) * 100 if bet_usd > 0 else 0
            
            c.execute('UPDATE trades SET final_outcome=?, exit_price=?, pnl_usd=?, roi_pct=?, settled=1, settled_at=? WHERE id=?',
                     ('PENDING', final_price, pnl_usd, roi_pct, now.isoformat(), trade_id))
            
            emoji = "✅" if pnl_usd > 0 else "❌"
            log(f"{emoji} Trade #{trade_id} [{symbol}] {side}: {pnl_usd:+.2f}$ ({roi_pct:+.1f}%)")
            total_pnl += pnl_usd
            
        except Exception as e:
            log(f"⚠️ Error settling trade #{trade_id}: {e}")
    
    conn.commit()
    conn.close()
    
    if len(unsettled) > 0:
        send_discord(f"📊 Settled {len(unsettled)} trades | Total PnL: ${total_pnl:+.2f}")

# ========================== REPORTS ==========================

def generate_statistics():
    """Generate performance statistics report"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT COUNT(*), SUM(bet_usd), SUM(pnl_usd), AVG(roi_pct) FROM trades WHERE settled = 1')
    result = c.fetchone()
    total_trades = result[0] or 0
    
    if not total_trades:
        log("ℹ No settled trades for analysis")
        conn.close()
        return
    
    total_invested, total_pnl, avg_roi = result[1] or 0, result[2] or 0, result[3] or 0
    c.execute('SELECT COUNT(*) FROM trades WHERE settled = 1 AND pnl_usd > 0')
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
    report.append(f"Total ROI:        {(total_pnl/total_invested)*100:.2f}%")
    report.append("=" * 80)
    
    report_text = "\n".join(report)
    log(report_text)
    
    report_file = f"{REPORTS_DIR}/report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    with open(report_file, 'w') as f:
        f.write(report_text)
    
    send_discord(f"📊 **PERFORMANCE REPORT**\n```\n{report_text}\n```")
    conn.close()

# ========================== MAIN TRADING ==========================

def trade_symbol(symbol: str):
    """Execute trading logic for a symbol"""
    up_id, down_id = get_token_ids(symbol)
    if not up_id or not down_id:
        log(f"[{symbol}] Market not found, skipping")
        return
    
    edge, reason, p_up, best_bid, best_ask, imbalance = calculate_edge(symbol, up_id)
    addr = Account.from_key(PROXY_PK).address
    balance = get_balance(addr)
    
    # Trading decision: UP / DOWN
    if edge >= MIN_EDGE:
        token_id, side, price = up_id, "UP", p_up
    elif edge <= (1.0 - MIN_EDGE):
        token_id, side, price = down_id, "DOWN", 1.0 - p_up
    else:
        log(f"[{symbol}] PASS | Edge {edge:.1%} (threshold: {MIN_EDGE:.1%}/{1-MIN_EDGE:.1%})")
        return

    # BFXD trend filter: BTC, dopasowanie kierunku
    if not bfxd_allows_trade(symbol, side):
        log(f"[{symbol}] BFXD filter prevented BUY (symbol={symbol}, side={side})")
        return
    
    if price <= 0:
        log(f"[{symbol}] ERROR: Invalid price {price}")
        return
    
    # Clamp price to valid range
    price = max(0.01, min(0.99, price))

    # Bazowy size wynikający z BET_USD
    size = round(BET_USD / price, 6)

    # OPCJA C: wymuszamy min. 5 sztuk, kosztem większego realnego stake
    MIN_SIZE = 5.0
    bet_usd_effective = BET_USD

    if size < MIN_SIZE:
        old_size = size
        size = MIN_SIZE
        bet_usd_effective = round(size * price, 4)  # realna kwota w USDC
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
    
    # Place order
    result = place_order(token_id, price, size)
    log(f"[{symbol}] Order status: {result['status']}")
    
    # Save to database – zapisujemy realny stake, nie bazowe BET_USD
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
            order_status=result['status'],
            order_id=result['order_id'],
        )
    except Exception as e:
        log(f"[{symbol}] Database error: {e}")

# ========================== MAIN ==========================

def main():
    """Main bot loop"""
    log("🚀 Starting PolyAstra Trading Bot...")
    setup_api_creds()
    init_database()
    
    addr = Account.from_key(PROXY_PK).address
    log("=" * 90)
    log(f"🤖 POLYASTRA | Markets: {', '.join(MARKETS)}")
    log(f"💼 Wallet: {addr[:10]}...{addr[-8:]} | Balance: {get_balance(addr):.2f} USDC")
    log(f"⚙️  MIN_EDGE: {MIN_EDGE:.1%} | BET: ${BET_USD} | MAX_SPREAD: {MAX_SPREAD:.1%}")
    log(f"🕒 WINDOW_DELAY_SEC: {WINDOW_DELAY_SEC}s")
    log("=" * 90)
    
    cycle = 0
    while True:
        try:
            # Calculate time until next 15-min window
            now = datetime.utcnow()
            wait = 900 - ((now.minute % 15) * 60 + now.second)
            if wait <= 0:
                wait += 900
            
            log(f"⏱️  Waiting {wait}s until next window + {WINDOW_DELAY_SEC}s delay...")
            time.sleep(wait + WINDOW_DELAY_SEC)  # konfigurowalny bufor po starcie okna
            
            log(f"\n{'='*90}\n🔄 CYCLE #{cycle + 1} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}\n{'='*90}\n")
            
            # Trade all symbols
            for sym in MARKETS:
                trade_symbol(sym)
                time.sleep(1)
            
            # Settle completed trades
            check_and_settle_trades()
            cycle += 1
            
            # Generate report every 4 hours (16 cycles)
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
