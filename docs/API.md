# PolyFlup API Reference

This document describes the external APIs used by PolyFlup and the internal dashboard API.

---

## Table of Contents

1. [Polymarket APIs](#polymarket-apis)
2. [Binance API](#binance-api)
3. [Dashboard API](#dashboard-api)
4. [WebSocket Feeds](#websocket-feeds)

---

## Polymarket APIs

PolyFlup integrates with multiple Polymarket API endpoints for trading, market data, and position management.

### CLOB API (Order Management)

**Base URL**: `https://clob.polymarket.com`

Used by: `py_clob_client` library

#### Key Endpoints

- **Order Placement**
  - `POST /order` - Place buy/sell orders
  - `GET /order/:id` - Get order status
  - `DELETE /order/:id` - Cancel order

- **Market Data**
  - `GET /book` - Get order book for a market
  - `GET /spread` - Get bid-ask spread
  - `GET /markets` - Get market information
  - `GET /tick-size` - Get minimum price increment

- **Account**
  - `GET /balance-allowance` - Get token balance and allowances
  - `GET /open-orders` - Get all open orders
  - `POST /check-scoring` - Check if order earns liquidity rewards

**Authentication**: ECDSA signatures using wallet private key

**Rate Limits**: Managed by `py_clob_client` with automatic throttling

---

### Gamma API (Market Discovery)

**Base URL**: `https://gamma-api.polymarket.com`

#### Key Endpoints

- `GET /events` - Get upcoming prediction markets
  - Query params:
    - `active`: Filter for active markets
    - `closed`: Include settled markets
    - `limit`: Number of results (default: 100)

- `GET /events/:condition_id` - Get specific event details

**Used For**:
- Discovering active 15-minute crypto markets
- Getting market slugs and token IDs
- Checking market resolution status

**Authentication**: None (public API)

**Rate Limits**: Conservative polling (every 30-60 seconds)

---

### Data API (Position Tracking)

**Base URL**: `https://data-api.polymarket.com`

#### Key Endpoints

- `GET /positions/:address` - Get all positions for an address
  - Returns: Position size, entry price, token ID
  - Used for: Balance cross-validation, position adoption

- `GET /trades/:address` - Get trade history
  - Returns: Fill prices, timestamps, sizes
  - Used for: Order fill confirmation

- `GET /closed-positions/:address` - Get settled positions
  - Returns: Final outcomes, realized P&L
  - Used for: Settlement auditing

**Authentication**: None (public API)

**Rate Limits**: 
- Normal: 1 req/10s per position check
- Startup sync: Batch requests with 2s delay

**Cross-Validation**:
```python
# Enhanced balance validation uses this for XRP and other symbols
position_data = get_positions(user_address, token_id)
balance_data = get_balance_allowance(token_id)

if balance_data == 0 and position_data > 0:
    # Use position data as fallback
    actual_balance = position_data
```

---

### Multiple Market Prices (Batch)

**Endpoint**: `POST https://clob.polymarket.com/prices`

**Payload**:
```json
{
  "token_ids": ["123456", "789012", "345678"]
}
```

**Response**:
```json
{
  "123456": "0.5234",
  "789012": "0.7891",
  "345678": "0.4123"
}
```

**Used For**: Fetching all position prices in a single API call

**Optimization**: Reduces API calls from N to 1 per monitoring cycle

---

## Binance API

**Base URL**: `https://api.binance.com`

**Used For**: External market data validation and signal generation

### Klines (Candlestick Data)

**Endpoint**: `GET /api/v3/klines`

**Params**:
- `symbol`: Trading pair (e.g., BTCUSDT)
- `interval`: Timeframe (1m, 5m, 15m)
- `limit`: Number of candles (default: 100)

**Used For**:
- Price momentum (velocity, acceleration, RSI)
- Order flow analysis (taker buy/sell volume)
- Volume-weighted momentum (VWAP)
- ADX trend strength (optional)

**Example Request**:
```bash
GET /api/v3/klines?symbol=BTCUSDT&interval=1m&limit=15
```

**Response** (array of arrays):
```json
[
  [
    1609459200000,  // Open time
    "29000.00",     // Open
    "29100.00",     // High
    "28900.00",     // Low
    "29050.00",     // Close
    "123.456",      // Volume
    1609459259999,  // Close time
    "3580000.00",   // Quote volume
    1000,           // Number of trades
    "60.123",       // Taker buy base volume
    "1740000.00",   // Taker buy quote volume
    "0"             // Ignore
  ]
]
```

**Mapping**:
```python
BINANCE_FUNDING_MAP = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT", 
    "XRP": "XRPUSDT",
    "SOL": "SOLUSDT"
}
```

**Rate Limits**: 
- 1200 requests/minute (IP-based)
- Weight: 1 per klines request

**Caching**: Results cached for 5-10 seconds to reduce calls

---

## Dashboard API

**Base URL**: `http://localhost:3001` (dev) or `http://localhost:3001` (Docker)

**Technology**: Express.js + Better-SQLite3

### Endpoints

#### GET /api/stats

**Description**: Overall trading statistics

**Response**:
```json
{
  "totalTrades": 150,
  "winRate": 58.67,
  "totalPnL": 1234.56,
  "avgROI": 12.5,
  "totalVolume": 9876.54,
  "openPositions": 3,
  "settledTrades": 147
}
```

**Used By**: Dashboard header stats

---

#### GET /api/positions

**Description**: Active open positions

**Response**:
```json
[
  {
    "id": 123,
    "symbol": "BTC",
    "side": "UP",
    "size": 100.5,
    "entryPrice": 0.52,
    "currentPrice": 0.68,
    "unrealizedPnL": 16.08,
    "unrealizedROI": 30.8,
    "windowEnd": "2026-01-10T15:45:00Z",
    "timeRemaining": "8m 30s",
    "exitOrderActive": true,
    "scaledIn": false
  }
]
```

**Used By**: Positions table in dashboard

---

#### GET /api/trades

**Description**: Complete trade history

**Query Params**:
- `limit`: Max results (default: 100)
- `symbol`: Filter by symbol
- `settled`: Filter by settlement status (0 or 1)

**Response**:
```json
[
  {
    "id": 456,
    "timestamp": "2026-01-10T15:30:00Z",
    "symbol": "ETH",
    "side": "DOWN",
    "entryPrice": 0.48,
    "exitPrice": 0.95,
    "size": 50.0,
    "betUsd": 24.0,
    "pnlUsd": 23.5,
    "roiPct": 97.9,
    "finalOutcome": "EXIT_PLAN_FILLED",
    "settled": 1,
    "exitedEarly": 1
  }
]
```

**Used By**: Trade history table

---

#### GET /api/performance

**Description**: Daily performance metrics for charts

**Response**:
```json
[
  {
    "date": "2026-01-10",
    "pnl": 125.50,
    "trades": 12,
    "winRate": 66.67,
    "volume": 1200.00
  }
]
```

**Used By**: Performance charts (LayerChart/D3)

---

## WebSocket Feeds

**Base URL**: `wss://ws-subscriptions-clob.polymarket.com`

**Technology**: Polymarket WebSocket API v1

### User Channel

**Subscribe**:
```json
{
  "type": "subscribe",
  "channel": "user",
  "auth": {
    "apiKey": "your_api_key",
    "apiSecret": "your_secret",
    "apiPassphrase": "your_passphrase"
  }
}
```

**Events**:
- `order_fill` - Order executed
- `order_cancel` - Order cancelled
- `balance_update` - Token balance changed

**Example Message**:
```json
{
  "event_type": "order_fill",
  "order_id": "0x123...",
  "market": "0xabc...",
  "price": "0.75",
  "size": "100.0",
  "side": "BUY",
  "timestamp": 1609459200
}
```

**Used For**:
- Instant fill notifications
- P&L updates
- Position size changes

---

### Market Channel

**Subscribe**:
```json
{
  "type": "subscribe", 
  "channel": "market",
  "markets": ["0xabc...", "0xdef..."]
}
```

**Events**:
- `price_update` - Midpoint price changed
- `spread_update` - Bid/ask spread changed
- `trade` - Public trade executed

**Example Message**:
```json
{
  "event_type": "price_update",
  "market": "0xabc...",
  "price": "0.5234",
  "timestamp": 1609459200
}
```

**Used For**:
- Real-time price updates (1s monitoring cycle)
- Stop loss triggers
- P&L calculation

---

## Authentication & Security

### Polymarket CLOB

**Method**: ECDSA Signature (EIP-712)

**Required**:
- `PROXY_PK`: Polygon wallet private key (0x...)
- `API_KEY`: CLOB API key (auto-generated on first run)
- `API_SECRET`: CLOB API secret
- `API_PASSPHRASE`: CLOB API passphrase

**Setup**:
```bash
# Bot auto-creates credentials on first run
uv run polyflup.py

# Credentials saved to .env automatically
```

**Security Notes**:
- Private key never leaves local machine
- All orders signed locally before submission
- WebSocket auth uses API credentials (not private key)

---

### Binance

**Method**: Public API (no authentication required)

**Rate Limiting**: IP-based, 1200 req/min

**Optimization**:
- Cache results for 5-10 seconds
- Batch requests when possible
- Use weight-efficient endpoints

---

## Error Handling

### Common Errors

**404 Not Found**:
- **Cause**: Market not yet indexed or recently resolved
- **Handling**: Silent suppression during transitions, retry with backoff

**429 Too Many Requests**:
- **Cause**: Rate limit exceeded
- **Handling**: Exponential backoff, request batching

**503 Service Unavailable**:
- **Cause**: Polymarket API temporary outage
- **Handling**: Retry with exponential backoff, fallback to cached data

**WebSocket Disconnect**:
- **Cause**: Network issues, API restart
- **Handling**: Auto-reconnect with backoff, resubscribe to channels

---

## Performance Optimization

### API Call Reduction

**Before Optimization** (v0.4.2):
- 10 positions × 10s cycle = ~1 API call/second
- ~3600 calls/hour

**After Optimization** (v0.4.3):
- Batch prices: 1 call per cycle
- WebSocket: 0 calls for price updates
- ~360 calls/hour (90% reduction)

### Caching Strategy

```python
# Binance data (momentum, order flow)
cache_duration = 5 seconds

# Polymarket market info
cache_duration = 60 seconds

# WebSocket prices
cache_duration = Real-time (no caching needed)
```

---

## Monitoring & Debugging

### Enable API Logging

Set in `src/utils/logger.py`:
```python
API_DEBUG = True  # Log all API calls and responses
```

### Check API Health

```bash
# CLOB health check
curl https://clob.polymarket.com/ok

# Binance health check  
curl https://api.binance.com/api/v3/ping
```

### WebSocket Status

Check logs for:
```
✅ WebSocket: Connected to User Channel
✅ WebSocket: Subscribed to Market Channel (3 markets)
⚠️ WebSocket: Reconnecting... (attempt 2/5)
```

---

## Future Improvements

- GraphQL API integration for advanced queries
- Redis caching layer for distributed systems
- API response time monitoring and alerting
- Automatic fallback to backup API endpoints
- Machine learning model serving API

---

For implementation details, see:
- `src/trading/orders/` - CLOB client and order management
- `src/data/market_data/` - External data sources
- `src/utils/websocket_manager.py` - WebSocket connection handling
- `ui/server.js` - Dashboard API server
