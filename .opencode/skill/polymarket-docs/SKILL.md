---
name: polymarket-docs
description: Expert Polymarket API documentation assistant with comprehensive knowledge of CLOB, Gamma, CTF, and WebSocket APIs
---

## What I do

I provide instant access to Polymarket's comprehensive API documentation, including:

- **CLOB API**: Order placement, cancellation, batch operations, authentication
- **Gamma Markets API**: Market data, events, pricing, orderbook snapshots
- **CTF Operations**: Split USDC, merge positions, redeem winning tokens
- **WebSocket**: Real-time market updates, user order notifications
- **Builder Program**: API keys, order attribution, relayer integration
- **Market Data**: Price history, spreads, trades, positions

## How to use me

Ask me questions like:

- "How do I place a limit order on Polymarket?"
- "What's the authentication flow for CLOB API?"
- "How do I get orderbook depth for a token?"
- "Show me WebSocket subscription example"
- "What are the rate limits for Gamma API?"
- "How do I batch multiple orders?"
- "How to split USDC into outcome tokens?"
- "What's the maker rebates program?"
- "How do I check if an order filled?"
- "Show me how to merge winning positions"

## Core Concepts

### CLOB (Central Limit Order Book)

The CLOB is Polymarket's primary trading engine. Key features:

- **L1 Methods**: Wallet-based operations (no API keys needed)
- **L2 Methods**: Authenticated trading operations (requires API credentials)
- **Order Types**: Market orders, limit orders, POST_ONLY, GTC, FOK, IOC
- **Batch Operations**: Place multiple orders in a single API call
- **Fee Structure**: Maker rebates (0.15%), taker fees (1.54%)

### Authentication Levels

1. **Public Methods**: No authentication (market data, orderbook, prices)
2. **L1 Methods**: Wallet signer only (initial setup, approvals)
3. **L2 Methods**: API credentials required (trading, order management)
4. **Builder Methods**: Builder API keys (order attribution)

### Order Types

- **POST_ONLY**: Adds liquidity only, earns maker rebate (0.15%), fails if crossing
- **GTC (Good-Til-Cancel)**: Fills immediately as taker, pays 1.54% fee
- **FOK (Fill-Or-Kill)**: Executes completely or cancels
- **IOC (Immediate-Or-Cancel)**: Fills partially, cancels remainder

### CTF (Conditional Token Framework)

Token operations for managing positions:

- **Split**: Convert USDC into outcome tokens (UP + DOWN)
- **Merge**: Combine outcome tokens back to USDC
- **Redeem**: Exchange winning tokens for $1.00 each after resolution

### WebSocket Channels

Real-time data streams:

- **Market Channel**: Orderbook updates, trade executions, price changes
- **User Channel**: User-specific order fills, cancellations, balance updates
- **Sports Channel**: Live sports results and score updates

## Common Patterns

### Placing an Order

```python
from py_clob_client.client import ClobClient

# Initialize client
client = ClobClient(host, key=private_key, chain_id=137)

# Derive API credentials
client.set_api_creds(client.create_or_derive_api_creds())

# Create order
order = client.create_order({
    "tokenID": "12345",
    "price": 0.65,
    "side": "BUY",
    "size": 100.0,
    "feeRateBps": 0  # POST_ONLY for maker rebate
})

# Place order
response = client.post_order(order)
```

### Batch Order Placement

```python
# Place multiple orders atomically
orders = [
    {"tokenID": "12345", "price": 0.68, "side": "BUY", "size": 50.0},
    {"tokenID": "67890", "price": 0.31, "side": "BUY", "size": 50.0}
]

batch_response = client.post_orders(orders)
```

### Fetching Orderbook

```python
# Get orderbook depth
orderbook = client.get_order_book("12345")

# Get midpoint price
midpoint = client.get_midpoint("12345")

# Get market price for side
price = client.get_market_price("12345", side="BUY")
```

### WebSocket Subscription

```python
from py_clob_client.websocket import WebSocketClient

# Connect to WebSocket
ws = WebSocketClient(host)

# Subscribe to market updates
ws.subscribe_to_market("12345", callback=on_market_update)

# Subscribe to user updates (requires auth)
ws.subscribe_to_user(address, callback=on_user_update)
```

### CTF Operations

```python
# Split USDC into outcome tokens
split_tx = client.split_position(
    token_id="12345",
    amount=100.0  # 100 USDC → 100 UP + 100 DOWN
)

# Merge winning tokens to USDC
merge_tx = client.merge_position(
    token_id="12345",
    amount=50.0  # 50 UP + 50 DOWN → 50 USDC
)

# Redeem winning tokens (after market resolves)
redeem_tx = client.redeem_position(
    token_id="12345",
    amount=100.0  # 100 winning tokens → 100 USDC
)
```

## API Endpoints

### CLOB Base URL
`https://clob.polymarket.com`

### Gamma Markets API Base URL
`https://gamma-api.polymarket.com`

### Data API Base URL
`https://data-api.polymarket.com`

### WebSocket URL
`wss://ws-subscriptions-clob.polymarket.com/ws/market`

## Rate Limits

### Public Endpoints
- **Markets/Events**: 100 requests/minute
- **Orderbook/Prices**: 200 requests/minute
- **Trade History**: 100 requests/minute

### Authenticated Endpoints
- **Order Placement**: 500 requests/minute
- **Order Cancellation**: 500 requests/minute
- **Batch Orders**: 100 requests/minute

### Builder Tier Limits
- **Tier 1 (Free)**: 500 req/min
- **Tier 2**: 1,000 req/min
- **Tier 3**: 2,500 req/min
- **Tier 4**: 5,000 req/min

## Key Documentation Sections

### Getting Started
1. **Developer Quickstart**: Initialize client and place first order
2. **Fetching Market Data**: Get markets, prices, orderbook (no auth)
3. **Authentication**: Understand L1/L2 auth flow
4. **Geographic Restrictions**: Check if location is blocked

### Trading Operations
1. **Create Order**: Place single limit/market order
2. **Batch Orders**: Submit multiple orders atomically
3. **Cancel Orders**: Cancel single, multiple, or all orders
4. **Get Active Orders**: Check open orders for user
5. **Order Status**: Check if order filled/cancelled

### Market Data
1. **List Markets**: Get all active markets with filters
2. **Get Market by ID/Slug**: Fetch specific market details
3. **Orderbook Summary**: Get bid/ask depth
4. **Price History**: Historical timeseries data
5. **Spreads**: Bid-ask spread for multiple tokens

### Builder Program
1. **Builder Profile & Keys**: Obtain API credentials
2. **Order Attribution**: Tag orders with builder ID
3. **Relayer Client**: Execute gasless transactions
4. **Builder Tiers**: Understand rate limits and rewards

### WebSocket Streaming
1. **Market Channel**: Subscribe to orderbook updates
2. **User Channel**: Get real-time order notifications
3. **WSS Authentication**: Authenticate WebSocket connections

### CTF Operations
1. **Split USDC**: Create outcome token positions
2. **Merge Tokens**: Combine positions back to USDC
3. **Redeem Winners**: Exchange winning tokens for $1.00

## Fee Structure

### Trading Fees (Per-Outcome Markets)
- **Maker Rebate**: -0.15% (you earn money for providing liquidity)
- **Taker Fee**: +1.54% (you pay for removing liquidity)

### Order Type Behavior
- **POST_ONLY**: Always maker (earns rebate), fails if crossing
- **GTC**: Can be maker or taker (rebate or fee depending on crossing)
- **FOK/IOC**: Always taker (pays fee)

### Fee Deduction
- **BUY**: Fee taken in **tokens** from proceeds
- **SELL**: Fee taken in **USDC** from proceeds

### Maker Rebates Program
- Earn 0.15% on all orders that add liquidity
- Rebates paid in USDC weekly
- No minimum volume requirements
- Automatically enrolled for all traders

## Common Issues & Solutions

### Order Fails with "Crosses Orderbook"
**Cause**: POST_ONLY order would execute immediately  
**Solution**: Switch to GTC order type or adjust price

### "Insufficient Allowance" Error
**Cause**: Haven't approved CTF contract to spend USDC  
**Solution**: Call `approve()` on USDC token for CTF address

### WebSocket Connection Drops
**Cause**: Idle timeout or network issue  
**Solution**: Implement reconnection logic with exponential backoff

### "Invalid Signature" Error
**Cause**: Wrong chain ID or API credentials expired  
**Solution**: Regenerate API credentials with correct chain ID (137 for Polygon)

### Rate Limit Exceeded
**Cause**: Too many requests in short time  
**Solution**: Implement rate limiting client-side or upgrade builder tier

## Geographic Restrictions

Polymarket is **not available** in:
- United States and its territories
- Cuba, Iran, North Korea, Syria
- Crimea, Donetsk, Luhansk regions

Check restrictions before placing orders:
```python
# Check if address is restricted
is_allowed = client.is_order_scoring(order_id)
```

## Resources

- **Documentation**: https://docs.polymarket.com
- **CLOB Quickstart**: https://docs.polymarket.com/developers/CLOB/quickstart
- **API Reference**: https://docs.polymarket.com/api-reference
- **Builder Program**: https://docs.polymarket.com/developers/builders/builder-intro
- **Discord Community**: https://discord.gg/polymarket
- **Twitter**: https://x.com/polymarket

## When to Use Me

Use this skill when you need:

✅ API endpoint specifications  
✅ Authentication setup guidance  
✅ Order placement examples  
✅ WebSocket integration help  
✅ CTF operation details  
✅ Rate limit information  
✅ Fee calculation explanations  
✅ Error troubleshooting  
✅ Code examples and patterns  

## Related Skills

- **polymarket-trading**: PolyFlup-specific trading strategies and bot context
- **polyflup-ops**: Bot operational commands and deployment
- **python-bot-standards**: Python code quality standards
