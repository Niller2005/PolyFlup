# Risk Profile Guide

Choose the risk profile that matches your trading style and account size. Each profile is designed to balance profit potential with risk management.

---

## 🛡️ CONSERVATIVE
**Best for:** Beginners, small accounts (<$500), risk-averse traders

| Setting | Value | Rationale |
|---------|-------|-----------|
| `BET_PERCENT` | 3.0% | Smaller position sizes preserve capital |
| `MIN_EDGE` | 0.40 | Higher threshold = fewer, higher quality trades |
| `MAX_SPREAD` | 0.12 | Only enter liquid markets with tight spreads |
| `CONFIDENCE_SCALING_FACTOR` | 3.0 | Moderate scaling (max 3x base bet) |
| `MAX_SIZE` | 100.0 | Cap position size at 100 shares (use `NONE` for no cap) |
| `MAX_SIZE_MODE` | CAP | Cap at MAX_SIZE (use `MAXIMIZE` to use higher of balance% or MAX_SIZE) |
| `STOP_LOSS_PRICE` | 0.40 | Conservative emergency sell threshold |
| `ENABLE_PRE_SETTLEMENT_EXIT` | YES | Exit losing side early with high confidence |
| `PRE_SETTLEMENT_MIN_CONFIDENCE` | 0.85 | Higher threshold for pre-settlement exits |
| `COMBINED_PRICE_THRESHOLD` | 0.985 | Stricter combined price for atomic pairs |
| `SCALE_IN_MULTIPLIER` | 1.0 | Disabled (not used with atomic hedging) |

**Expected Results:**
- Lower volatility
- Win rate: ~55-60%
- Average ROI per trade: 20-40%
- Drawdown risk: Low (5-10%)

---

## ⚖️ BALANCED (Default)
**Best for:** Most users, medium accounts ($500-$5000), moderate risk tolerance

| Setting | Value | Rationale |
|---------|-------|-----------|
| `BET_PERCENT` | 5.0% | Standard Kelly fraction for most strategies |
| `MIN_EDGE` | 0.35 | Balanced threshold for good opportunities |
| `MAX_SPREAD` | 0.15 | Accept reasonable spreads |
| `CONFIDENCE_SCALING_FACTOR` | 5.0 | Standard scaling (max 5x base bet) |
| `MAX_SIZE` | 500.0 | Cap position size at 500 shares (use `NONE` for no cap) |
| `MAX_SIZE_MODE` | CAP | Cap at MAX_SIZE (use `MAXIMIZE` to use higher of balance% or MAX_SIZE) |
| `STOP_LOSS_PRICE` | 0.30 | Standard emergency sell threshold |
| `ENABLE_PRE_SETTLEMENT_EXIT` | YES | Exit losing side early with high confidence |
| `PRE_SETTLEMENT_MIN_CONFIDENCE` | 0.80 | Standard threshold for pre-settlement exits |
| `COMBINED_PRICE_THRESHOLD` | 0.99 | Standard combined price for atomic pairs |
| `SCALE_IN_MULTIPLIER` | 1.5 | Disabled (not used with atomic hedging) |

**Expected Results:**
- Moderate volatility
- Win rate: ~52-58%
- Average ROI per trade: 30-60%
- Drawdown risk: Medium (10-15%)

---

## 🔥 AGGRESSIVE
**Best for:** Experienced traders, larger accounts ($5000+), higher risk tolerance

| Setting | Value | Rationale |
|---------|-------|-----------|
| `BET_PERCENT` | 8.0% | Larger base positions for faster growth |
| `MIN_EDGE` | 0.32 | Lower threshold = more trades, more action |
| `MAX_SPREAD` | 0.18 | Accept wider spreads for more opportunities |
| `CONFIDENCE_SCALING_FACTOR` | 7.0 | Aggressive scaling (max 7x base bet) |
| `MAX_SIZE` | 1000.0 | Cap position size at 1000 shares (use `NONE` for no cap) |
| `MAX_SIZE_MODE` | CAP | Cap at MAX_SIZE (use `MAXIMIZE` to use higher of balance% or MAX_SIZE) |
| `STOP_LOSS_PRICE` | 0.20 | Wider emergency sell threshold |
| `ENABLE_PRE_SETTLEMENT_EXIT` | YES | Exit losing side early with high confidence |
| `PRE_SETTLEMENT_MIN_CONFIDENCE` | 0.75 | Lower threshold for more exits |
| `COMBINED_PRICE_THRESHOLD` | 0.99 | Standard combined price for atomic pairs |
| `SCALE_IN_MULTIPLIER` | 2.0 | Disabled (not used with atomic hedging) |

**Expected Results:**
- Higher volatility
- Win rate: ~50-55%
- Average ROI per trade: 40-80%
- Drawdown risk: High (15-25%)

---

## ⚡ ULTRA AGGRESSIVE
**Best for:** Professional traders, very large accounts ($10k+), maximum conviction

| Setting | Value | Rationale |
|---------|-------|-----------|
| `BET_PERCENT` | 12.0% | Maximum position sizing for explosive growth |
| `MIN_EDGE` | 0.30 | Lowest threshold = maximum trade frequency |
| `MAX_SPREAD` | 0.20 | Enter almost any liquid market |
| `CONFIDENCE_SCALING_FACTOR` | 10.0 | Extreme scaling (max 10x base bet) |
| `MAX_SIZE` | 2000.0 | Cap position size at 2000 shares (use `NONE` for no cap) |
| `MAX_SIZE_MODE` | CAP | Cap at MAX_SIZE (use `MAXIMIZE` to use higher of balance% or MAX_SIZE) |
| `STOP_LOSS_PRICE` | 0.10 | Very wide emergency sell threshold |
| `ENABLE_PRE_SETTLEMENT_EXIT` | YES | Exit losing side early with high confidence |
| `PRE_SETTLEMENT_MIN_CONFIDENCE` | 0.70 | Lower threshold for aggressive exits |
| `COMBINED_PRICE_THRESHOLD` | 0.995 | Relaxed combined price for more opportunities |
| `SCALE_IN_MULTIPLIER` | 2.5 | Disabled (not used with atomic hedging) |

**Expected Results:**
- Extreme volatility
- Win rate: ~48-53%
- Average ROI per trade: 50-120%
- Drawdown risk: Very High (25-40%)

⚠️ **WARNING:** This profile can experience severe drawdowns. Only use if you can handle 30-40% account swings.

---

## 🎯 How to Choose

### Account Size
- **<$500:** Conservative
- **$500-$5000:** Balanced
- **$5000-$10000:** Aggressive
- **$10000+:** Any profile (based on risk tolerance)

### Experience Level
- **Beginner:** Conservative
- **Intermediate:** Balanced
- **Advanced:** Aggressive or Ultra Aggressive

### Risk Tolerance
- **Low (can't handle >10% drawdown):** Conservative
- **Medium (comfortable with 10-20% drawdown):** Balanced
- **High (comfortable with 20-30% drawdown):** Aggressive
- **Extreme (comfortable with 30%+ drawdown):** Ultra Aggressive

---

## 📊 Key Features Across All Profiles

### Atomic Hedging Strategy
All profiles use **Atomic Hedging** by default:
- Places simultaneous entry+hedge pairs via batch API
- Combined price threshold ensures guaranteed profit structure
- POST_ONLY orders for maker rebates (0.15%), GTC fallback after 3 failures
- 120-second fill timeout with immediate cancellation
- No unhedged positions

### Pre-Settlement Exit Strategy
All profiles include **Pre-Settlement Exit** by default:
- Evaluates positions T-180s to T-45s before resolution
- Sells losing side early if confidence exceeds threshold
- Keeps winning side for full resolution profit
- Confidence threshold varies by profile (70-85%)

### Time-Aware Emergency Liquidation
All profiles use **Progressive Emergency Pricing**:
- **PATIENT** (>600s): Small drops, long waits - maximize recovery
- **BALANCED** (300-600s): Moderate drops, balanced waits
- **AGGRESSIVE** (<300s): Rapid drops, short waits - ensure liquidation

### Smart MIN_ORDER_SIZE Handling
All profiles enforce **Intelligent Small Position Management**:
- Positions < 5.0 shares cannot be sold (exchange minimum)
- If winning: HOLD through resolution (let it profit)
- If losing: ORPHAN (accept small loss, avoid failed sells)

### Portfolio Risk Management
All profiles enforce **Maximum Portfolio Exposure**:
- Prevents over-concentration in correlated markets
- Skips new trades if total exposure exceeds limit
- Ensures you never risk too much at once

---

## 🧮 Confidence Calculation Methods

PolyFlup supports two confidence calculation methods for A/B testing. Both methods are **always calculated and stored** - you choose which one to use for trading.

### Additive Method (Default)

- **Formula**: `confidence = (winning_total - (losing_total × 0.2)) × lead_lag_bonus`
- **Characteristics**: Simple directional voting with weighted aggregation
- **Conflict Handling**: Penalizes confidence when signals disagree
- **Quality Adjustment**: Multiplier applied to weighted scores

### Bayesian Method (Alternative, v0.5.0+)

- **Formula**: `confidence = 1 / (1 + exp(-ln(prior_odds) - Σ(log_LR × weight)))`
- **Characteristics**: Proper probability theory with log-likelihood accumulation
- **Market Prior**: Starts from Polymarket orderbook probability
- **Conflict Handling**: Conflicting signals naturally cancel (better math)
- **Quality Adjustment**: Multiplier (0.7-1.5x) applied to log-likelihood

### Configuration

```env
BAYESIAN_CONFIDENCE=NO   # Use additive (default)
BAYESIAN_CONFIDENCE=YES  # Use Bayesian (experimental)
```

### Recommendation

1. **Start with Additive** (default) to collect baseline data
2. **Run for 100+ trades** with `BAYESIAN_CONFIDENCE=NO`
3. **Compare performance** using `uv run python compare_bayesian_additive.py`
4. **Switch to Bayesian** if it shows superior win rate

**Note**: Bayesian is generally more conservative (e.g., trade #529 showed additive=43.6%, Bayesian=3.1%). Consider lowering `MIN_EDGE` slightly when switching to Bayesian.

---

## 🔧 Custom Profile

Want to create your own profile? Key principles:

1. **Position Sizing:** Keep `BET_PERCENT × CONFIDENCE_SCALING_FACTOR × 3` ≤ 50% of balance
2. **Edge Threshold:** Lower `MIN_EDGE` = more trades but lower win rate
3. **Portfolio Cap:** `MAX_PORTFOLIO_EXPOSURE` should be 2-4x your `BET_PERCENT`
4. **Size Cap:** `MAX_SIZE` prevents oversized positions (set to `NONE` to disable)
5. **Stop Loss:** Tighter (e.g., $0.40) = less drawdown but more false exits
6. **Scaling Factor:** Higher = more aggressive on high-confidence signals
7. **Confidence Method:** Choose between additive (default) and Bayesian (experimental)

**Example Custom Profile (Medium-Aggressive):**
```bash
BET_PERCENT=6.0
MIN_EDGE=0.56
MAX_SPREAD=0.16
CONFIDENCE_SCALING_FACTOR=6.0
MAX_SIZE=750.0
STOP_LOSS_PRICE=0.25
ENABLE_EXIT_PLAN=YES
EXIT_PRICE_TARGET=0.99
SCALE_IN_MULTIPLIER=1.2
```

**Example Custom Profile (No Size Cap):**
```bash
BET_PERCENT=6.0
MIN_EDGE=0.56
MAX_SPREAD=0.16
CONFIDENCE_SCALING_FACTOR=6.0
MAX_SIZE=NONE
STOP_LOSS_PRICE=0.25
ENABLE_EXIT_PLAN=YES
EXIT_PRICE_TARGET=0.99
SCALE_IN_MULTIPLIER=1.2
```

---

## 📈 Monitoring Your Profile

After 100+ trades, review these metrics:
- **Win Rate:** Should match profile expectations
- **Average ROI:** Should be positive and match profile range
- **Max Drawdown:** Should not exceed profile risk level
- **Sharpe Ratio:** Higher is better (>1.0 is good, >2.0 is excellent)

If results deviate significantly, consider adjusting your profile or reviewing market conditions.
