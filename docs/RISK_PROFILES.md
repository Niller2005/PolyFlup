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
| `STOP_LOSS_PERCENT` | 40% | Tighter stop loss to limit losses |
| `ENABLE_TAKE_PROFIT` | YES | Lock in profits at 60% gain |
| `TAKE_PROFIT_PERCENT` | 60% | Conservative profit target |
| `ENABLE_EXIT_PLAN` | YES | Aggressive profit-taking with limit orders |
| `EXIT_PRICE_TARGET` | 0.99 | Exit at 99 cents for near-guaranteed profit |
| `SCALE_IN_MULTIPLIER` | 0.5 | Add 50% more (1.5x total position) |

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
| `STOP_LOSS_PERCENT` | 40% | Standard stop loss with breakeven protection |
| `ENABLE_TAKE_PROFIT` | NO | Let winners run (breakeven protection active) |
| `TAKE_PROFIT_PERCENT` | 80% | Not used (take profit disabled) |
| `ENABLE_EXIT_PLAN` | YES | Aggressive profit-taking with limit orders |
| `EXIT_PRICE_TARGET` | 0.99 | Exit at 99 cents for near-guaranteed profit |
| `SCALE_IN_MULTIPLIER` | 1.0 | Double position size (2x total) |

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
| `STOP_LOSS_PERCENT` | 60% | Wider stop loss to avoid noise |
| `ENABLE_TAKE_PROFIT` | NO | Let all winners run |
| `TAKE_PROFIT_PERCENT` | 100% | Not used |
| `ENABLE_EXIT_PLAN` | YES | Aggressive profit-taking with limit orders |
| `EXIT_PRICE_TARGET` | 0.99 | Exit at 99 cents for near-guaranteed profit |
| `SCALE_IN_MULTIPLIER` | 1.5 | Add 150% more (2.5x total position) |

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
| `STOP_LOSS_PERCENT` | 70% | Very wide stop loss |
| `ENABLE_TAKE_PROFIT` | NO | Never cap upside |
| `TAKE_PROFIT_PERCENT` | 150% | Not used |
| `ENABLE_EXIT_PLAN` | YES | Aggressive profit-taking with limit orders |
| `EXIT_PRICE_TARGET` | 0.99 | Exit at 99 cents for near-guaranteed profit |
| `SCALE_IN_MULTIPLIER` | 2.0 | Triple position size (3x total) |

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

### Exit Plan (Aggressive Profit Taking)
All profiles include **Exit Plan** by default:
- After position ages (default 5 minutes), places limit sell order at EXIT_PRICE_TARGET (99 cents)
- Near-guaranteed profitable exit if market reaches 99 cents before expiry
- Order is automatically cancelled if stop loss or other exit triggers first
- Can be disabled with `ENABLE_EXIT_PLAN=NO` to only use market resolution

### Smart Breakeven Protection
All profiles include **Smart Breakeven Protection**:
- Activates when position is up 20%+ in PnL
- **Only** triggers if market moves AGAINST your position direction
- Allows 5% drawback from peak before exiting
- **Does NOT cap upside** if market continues in your favor
- Works in conjunction with Exit Plan for multi-layered protection

### Dynamic Position Sizing
All profiles use **Confidence-Based Sizing**:
- Base bet size increases with signal strength
- Higher edge = larger position (up to 3x base in Balanced)
- Automatically scales back on weaker signals

### Portfolio Risk Management
All profiles enforce **Maximum Portfolio Exposure**:
- Prevents over-concentration in correlated markets
- Skips new trades if total exposure exceeds limit
- Ensures you never risk too much at once

---

## 🔧 Custom Profile

Want to create your own profile? Key principles:

1. **Position Sizing:** Keep `BET_PERCENT × CONFIDENCE_SCALING_FACTOR × 3` ≤ 50% of balance
2. **Edge Threshold:** Lower `MIN_EDGE` = more trades but lower win rate
3. **Portfolio Cap:** `MAX_PORTFOLIO_EXPOSURE` should be 2-4x your `BET_PERCENT`
4. **Stop Loss:** Tighter = less drawdown but more false exits
5. **Scaling Factor:** Higher = more aggressive on high-confidence signals

**Example Custom Profile (Medium-Aggressive):**
```bash
BET_PERCENT=6.0
MIN_EDGE=0.56
MAX_SPREAD=0.16
CONFIDENCE_SCALING_FACTOR=6.0
STOP_LOSS_PERCENT=55.0
ENABLE_TAKE_PROFIT=NO
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
