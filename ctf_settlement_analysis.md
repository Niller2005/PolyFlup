# CTF Settlement Agent Analysis

## Overview
This settlement agent from pladee42's repo provides a **direct CTF contract integration** for merging complete outcome token sets back to USDC.

## Key Features

### 1. **Direct CTF Contract Calls** ✅
Instead of using the Polymarket API/Relayer, it calls the CTF contract directly:
- `mergePositions(conditionId, amount)` - Merges complete sets → USDC
- `balanceOf(account, tokenId)` - Checks token balances

### 2. **Complete Set Detection** ✅
```python
def check_complete_set(condition_id: str, token_ids: list[str]) -> Optional[CompleteSet]:
```
- Checks if you hold BOTH outcome tokens (UP + DOWN)
- Calculates min balance (max mergeable amount)
- Returns `None` if incomplete set

### 3. **Automated Position Monitoring** ✅
```python
class PositionMonitor:
    """Monitors positions for complete sets and triggers settlements."""
```
- Background task that checks every 30s (configurable)
- Auto-merges when complete sets detected
- Non-blocking async implementation

### 4. **Transaction Management** ✅
- Handles nonce, gas price, gas limits
- Signs transactions with EOA private key
- Waits for receipts and checks status
- Dry-run mode for testing

---

## Comparison to Our Current Implementation

### Current: `src/trading/ctf_operations.py`

**Pros:**
- Uses Polymarket Relayer (gasless)
- No transaction fees

**Cons:**
- Relayer can fail (rate limits, downtime)
- Less control over timing
- Requires API key management

### Settlement Agent: Direct CTF Contract

**Pros:**
- **Direct blockchain calls** - no third-party dependency
- **Immediate execution** - no relayer queue
- **Always works** - as long as RPC is up
- **Full control** - custom gas, nonce management

**Cons:**
- **Pays gas fees** (small, ~$0.01-0.05 per merge)
- Requires managing transaction nonces
- More complex error handling

---

## Integration Strategy

### Option 1: Replace Relayer Completely
Use CTF contract directly for all merges/redeems.

**Changes needed:**
1. Add `SettlementAgent` class to `src/trading/ctf_operations.py`
2. Replace `merge_tokens()` to use `agent.merge_positions()`
3. Replace `redeem_winning_tokens()` to use CTF `redeemPositions()`

**Benefits:**
- No more relayer rate limits
- Faster execution
- More reliable

**Drawbacks:**
- Pay gas fees (~$0.01-0.05 per tx)
- Need to manage gas price strategy

### Option 2: Fallback Strategy (Recommended)
Try Relayer first (gasless), fallback to direct CTF if it fails.

**Implementation:**
```python
async def merge_tokens_with_fallback(condition_id, yes_token_id, no_token_id):
    """Try Relayer first, fallback to direct CTF if it fails."""
    
    # Try Relayer (gasless)
    try:
        tx_hash = merge_tokens(condition_id, yes_token_id, no_token_id)
        if tx_hash:
            log("✅ Merged via Relayer (gasless)")
            return tx_hash
    except Exception as e:
        log(f"⚠️ Relayer failed: {e}")
    
    # Fallback to direct CTF
    log("🔄 Falling back to direct CTF contract...")
    agent = SettlementAgent(private_key=PROXY_PK, dry_run=False)
    result = await agent.merge_positions(condition_id, amount)
    
    if result.success:
        log(f"✅ Merged via CTF contract | Gas: ${result.gas_used * gas_price / 1e18:.4f}")
        return result.tx_hash
    else:
        log_error(f"❌ Both methods failed: {result.error}")
        return None
```

### Option 3: Automatic Position Monitor
Run `PositionMonitor` in background to auto-merge complete sets.

**Implementation:**
```python
# In bot.py main()
from src.trading.ctf_settlement import SettlementAgent, PositionMonitor

# Initialize at startup
agent = SettlementAgent(private_key=PROXY_PK, dry_run=False)
monitor = PositionMonitor(agent, check_interval=60.0)

# Start background task
asyncio.create_task(monitor.start())

# Add markets as trades complete
monitor.add_market(condition_id, [yes_token_id, no_token_id])
```

**Benefits:**
- Automatic capital recycling
- No manual merge needed
- Runs continuously in background

---

## Code Reuse Plan

### 1. Copy Core Classes
```
pladee42/polymarket-bot/src/settlement/agent.py
  → src/trading/ctf_settlement.py
```

Keep:
- `SettlementAgent` class
- `CompleteSet` dataclass
- `MergeResult` dataclass
- CTF_ABI definitions

Adapt:
- Use our config system (`src/config/settings.py`)
- Use our logger (`src/utils/logger.py`)
- Add redemption support (not just merge)

### 2. Add Redemption Method
```python
async def redeem_positions(
    self,
    condition_id: str,
    index_sets: list[int],
    amount: Decimal,
) -> MergeResult:
    """
    Redeem winning tokens for USDC after market resolution.
    
    Args:
        condition_id: Market condition ID
        index_sets: Winning outcome indices [1] for YES or [2] for NO
        amount: Amount to redeem
    """
    # Call CTF.redeemPositions(collateralToken, parentCollectionId, conditionId, indexSets)
```

### 3. Integration Points

**In `src/trading/ctf_operations.py`:**
```python
# Add at top
from src.trading.ctf_settlement import SettlementAgent

# Global agent instance
_settlement_agent = None

def get_settlement_agent():
    global _settlement_agent
    if _settlement_agent is None:
        _settlement_agent = SettlementAgent(
            private_key=PROXY_PK,
            rpc_url=settings.POLYGON_RPC_URL,
            dry_run=False
        )
    return _settlement_agent

# Update merge_tokens() to use fallback
def merge_tokens(condition_id, yes_token_id, no_token_id):
    try:
        # Try Relayer first
        tx_hash = _merge_via_relayer(...)
        if tx_hash:
            return tx_hash
    except:
        pass
    
    # Fallback to CTF contract
    agent = get_settlement_agent()
    result = asyncio.run(agent.merge_positions(condition_id, amount))
    return result.tx_hash if result.success else None
```

---

## Recommended Approach

### Phase 1: Add Fallback (This Week)
1. Copy `SettlementAgent` to `src/trading/ctf_settlement.py`
2. Update `merge_tokens()` to try Relayer → CTF fallback
3. Test on a few trades
4. Monitor gas costs

### Phase 2: Add Auto-Monitor (Next Week)
1. Implement `PositionMonitor` background task
2. Auto-merge complete sets every 60s
3. Log all merges with gas costs

### Phase 3: Evaluate (After 1 Week)
Compare:
- Relayer success rate vs CTF success rate
- Total gas costs paid
- Merge latency (time to recycle capital)

If CTF is reliable and gas costs are low (<$1/day), consider making it primary method.

---

## Gas Cost Estimation

**Polygon Gas Prices:**
- Merge transaction: ~150,000 gas
- Current gas price: ~50 gwei
- Cost per merge: 150k × 50 gwei = 0.0075 MATIC = **~$0.005 USD**

**Daily Cost (assuming 50 trades/day):**
- 50 trades × $0.005 = **$0.25/day** = **$7.50/month**

**Verdict:** Gas costs are negligible compared to trading volume. Direct CTF is viable!

---

## Implementation TODO

- [ ] Copy `SettlementAgent` from pladee42/polymarket-bot
- [ ] Adapt to our config/logger system
- [ ] Add `redeemPositions()` method for post-resolution redemption
- [ ] Create `ctf_settlement.py` module
- [ ] Update `merge_tokens()` with Relayer → CTF fallback
- [ ] Test on production with dry_run=True first
- [ ] Monitor gas costs for 24h
- [ ] Roll out to production if successful

---

## Conclusion

**YES, this can be used for CTF settlements!** 

The `SettlementAgent` provides a robust, reliable alternative to the Polymarket Relayer. With gas costs at ~$0.005 per transaction, it's economically viable. The fallback strategy (Relayer first, CTF if fails) gives us the best of both worlds:
- Try gasless first
- Guaranteed execution via CTF
- Full capital recycling reliability

Recommend implementing Phase 1 (fallback) ASAP to improve merge reliability.
