# Changelog

All notable changes to the PolyFlup trading bot are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project uses semantic versioning.

---

## [Unreleased]

---

## [0.8.3] - 2026-10-28

### Fixed
- **Main source of the `/book` 404 spam (paper)**: resting paper orders left LIVE by an earlier run (restart/crash) were matched against their book every 2s forever. They are now cancelled at start (nothing monitors them any more) and whenever their market's book no longer exists (404); transient errors keep them alive.
- **`/book` 404 "No orderbook exists" spam**: once a window ends its market has no order book, but the position monitor (1s loop) and the new recorders kept asking REST for it. The monitor now uses cached prices only for positions whose window has ended; `position_marks` and the post-attempt ticks skip ended windows and read the websocket cache only (REST fallback only for a live attempt's own ticks).
- `Confidence: N/A` for adopted / held-to-resolution legs (they have no stored confidence) was logged every 5s per position; now once per trade.

---
---

## [0.8.2] - 2026-10-28

See `docs/ANALYSIS_2026-10-28.md` (5 hours, 10 markets, 660 attempts, 420 resolved windows).

### Changed
- **Tolerated lock loss is now a randomised arm per attempt** (`ADAPTIVE_LOCK_ARMS=0.01,0.03,0.06`, `ADAPTIVE_EPSILON=0.3`, recorded in `pair_attempts.lock_arm`, migration 16). Once every arm has `ADAPTIVE_MIN_N` results the best mean result per share is used, exploring 30% of the time. This replaces the replay optimiser, which always picked the 8c maximum because its objective never charged the worse locks on pairs that complete: live, 8c made 32% of completed pairs lock at -4c...-8c (average lock -2.4c vs -0.3c) without lowering the leg-out rate (25% at 3c vs 26-33% at 8c).

### Fixed
- `position_marks` / `account_snapshots` counted an entry leg twice (a trade row and the matching paper holding), inflating `held` and `equity` (peak 1,559 against a real minimum of ~650). One row per token now; cash, fills and `pair_attempts` were never affected, earlier equity snapshots are.

### Findings (no code change)
- Pair strategy: -6c/share in every segment; hold vs sell a leg-out is a wash (paired +2.6c, SE 4.5c); signal edge over the market -1.1c, 95% CI [-6.7, +4.9] (62 window starts); the frozen directional rule D1 failed out-of-sample (-6.5c/share, 49 window starts). Do not build position-adding on this signal.

---
---

## [0.8.1] - 2026-10-28

### Fixed (confidence calculation, `src/trading/strategy.py`)
- **Bayesian evidence had the wrong sign for weak signals**: `(score - 0.5) * 2` made any signal scoring below 0.5 evidence *against* its own direction (e.g. 52% buy pressure became a strong vote for DOWN). A weak signal is now weak evidence, never negative. On live markets the old Bayesian confidence was often 0.4-0.7 where the corrected one is 0.01-0.2, and a few biases flipped. **Bayesian confidences/biases recorded before this version (trades, `evaluations`, the 77% hit rate seen in the calibration check) are not comparable.** Roughly half of all entries used the Bayesian value (random A/B).
- **The A/B selection bypassed the safeguards**: entry code takes `raw_scores["additive_confidence"|"bayesian_confidence"]`, which were the values *before* multi-confirmation, the 85% cap and price validation. Both are now final values; the pre-processing ones are `additive_raw` / `bayesian_raw`.
- **NaN / None / inf from a data source** could become *maximum* confidence (`min(1.0, nan)` is 1.0). All inputs are sanitised and clamped; non-finite results are 0.
- **Failing data source**: an exception in any of the six sources aborted the whole evaluation. Each source is now fetched defensively; 1-2 missing sources scale confidence by 15% each, 3+ = NEUTRAL (`reason: insufficient_data`); `raw_scores["missing_sources"]` is recorded.
- **Order book parsing** relied on `bids[-1]`/`asks[-1]` being the best level (worst-first V2 order); it now uses max/min over valid prices. Empty, crossed or locked books are NEUTRAL.
- **Price validation ran twice** (duplicate block) plus a third time in `logic.py` with hardcoded thresholds that ignored `ENABLE_PRICE_VALIDATION`; it now runs once per method in `calculate_confidence` and the entry gate uses the settings.
- **Momentum quality** had unreachable branches and was asymmetric; flow quality treated 70%+ buying (1.3) and 30%- buying (1.2) differently; flow/VWM direction at exactly 0.5 / 0 was DOWN instead of NEUTRAL; the momentum acceleration boost could push a score above 1. All fixed/symmetric, scores are in [0, 1].
- The combination is now a pure, unit-tested function (`score_signals`); `test_confidence.py` covers sign, NaN, mirror symmetry, caps, order-independence and degraded sources.
- Market websocket: with all ten markets subscribed the server sometimes closed the connection (`1013 slow consumer`) because the client read queue was only 16 messages; it is now 10000 and `book` messages only seed a token's top of book. A reconnect (5s) still happens occasionally under load (one in a 280s run), data continues afterwards.
- New in `raw_scores`: `bayesian_evidence` (signal-only log-odds, excluding the market prior), `missing_sources`.
- Additive confidence is unchanged except for the quality/direction fixes above (live check: identical on 4 of 8 markets, +0.01-0.05 on the others).

---
---

## [0.8.0] - 2026-10-28

### Added
- **Data-driven parameters** (`src/trading/adaptive.py`, `ADAPTIVE=YES`): values that were hard-coded are measured from the current database's `pair_attempts` / `attempt_ticks` (last `ADAPTIVE_WINDOW` attempts, shrunk towards the configured value with `ADAPTIVE_PRIOR_N` pseudo-attempts; the configured values are priors/defaults until `ADAPTIVE_MIN_N` attempts exist):
  - **leg-fail rate, leg-out loss and pair edge** (per market, shrunk to the global estimate) replace `KELLY_LEG_FAIL_RATE` / `KELLY_LEG_LOSS` / `MIN_NET_EDGE` in Kelly sizing;
  - **fill timeout** = p90 of the completion times of finished pairs + 5s (never above the configured/scaled value, min 15s);
  - **tolerated lock loss** replaces `TRAIL_MAX_LOCK_LOSS`: found by replaying recorded leg-outs against their recorded book ticks and picking the tolerance with the best total PnL (0-8c);
  - **explore sizing** (`ADAPTIVE_SIZING=YES`): a market whose measured pair EV `(1-r)*edge - r*loss` is negative trades at the 5-share minimum instead of being switched off, so it keeps producing data at minimal cost; full size returns when EV is >= 0. The estimate and decision are logged (`📐 adaptive: ...`) and stored in `pair_attempts.note`.
  - On the 2026-10-28 paper data every market measures about -4 to -7 c/share, so all markets with >= 10 attempts trade at the minimum size until that changes. `ADAPTIVE=NO` restores the fixed values.

---
### Changed
- Trail escalation ticks scale with the trailed leg's price level: `TRAIL_MAX_TICKS` is the mid-range value and is multiplied by `sqrt(4p(1-p))` (rounded, min 1). Measured best-bid moves per 5s were ~3.3c at 0.3-0.8, ~2.7c at 0.1-0.2/0.8-0.9 and ~1.7c at the extremes, which this follows.

---

## [0.7.6] - 2026-10-28

### Added
- **Raw market data recorder** (`src/data/market_recorder.py`, `MARKET_RECORD_INTERVAL=5`, migration 15), independent of trading:
  - `market_ticks`: top of book of both tokens + spot/strike for every traded market every 5s.
  - `market_trades`: the websocket trade tape (price, size, side) - needed to model queue fills and adverse selection.
  - `market_windows`: the real result (strike, final spot, up_won) of every window, traded or not.
  - `evaluations`: every strategy evaluation (every 20s per market) with confidence, bias, p_up, both methods and the full signal dictionaries, finished with `no_trade` / `trade`.
  - `account_snapshots`: cash, held value, equity and open legs every 30s (paper).
  - `pair_attempts.entry_book / hedge_book / other_book_at_first`: top-5 depth of both tokens at placement and of the other leg at first fill.
- The market websocket now subscribes with `custom_feature_enabled` (best_bid_ask events), handles `book` events, and adds tokens with `operation: subscribe` instead of replacing the subscription - all traded markets now have cached books (fewer REST calls).

### Added
- TUI: leg-outs (one leg filled, other timed out) appear in Recent trades as `L<id>` rows with their real result (`LEG-OUT sold`, or `LEG-OUT held ✓ won / ✗ lost` once resolved).

### Changed
- `EMERGENCY_SELL_CONFIDENCE_HOLD_THRESHOLD` default is now off (2.0): signal-based holds lost 3 of 3 (-92%, -97%, -100%); holding is now only done by the random `LEGOUT_HOLD_SHARE` arm so the comparison stays clean.

---

## [0.7.5] - 2026-10-28

### Added
- **Hold-to-resolution A/B arm for leg-outs** (`LEGOUT_HOLD_SHARE=0.25`): that share of pure leg-outs (one leg filled, other timed out) is kept to resolution instead of sold; recorded as `pair_attempts.resolution='hold_ab'`, with `filled_leg_won` and `realized_pnl` filled after the market resolves. Evidence so far does not favour either: filled legs win ~24% against a ~27c sale price (n~45, two data sets pointing opposite ways), a z-model probability or the entry confidence did not beat the bid (where the model said hold by 10c+, the leg won 11%, n=9), and a taker sale costs only ~1.4c fee. Compare arms with `SELECT resolution, COUNT(*), AVG(realized_pnl) FROM pair_attempts WHERE outcome='timeout' GROUP BY 1`.

---

## [0.7.4] - 2026-10-28

### Changed
- **Trailing may complete a pair at a small certain loss** (`TRAIL_MAX_LOCK_LOSS=0.03`): after one leg fills, the other is rested at best bid but no higher than `1 - filled + 0.03` (previously `1 - filled - 0.01`). At first fill the other leg's bid is typically 3-4c above the old cap, and a leg-out costs ~14c/share. Replaying 15 recorded leg-outs from `attempt_ticks`: 6 would have completed and leg-out PnL improves from -110 to -65 (5c: 10 of 15, -45). Small sample - re-run the replay on new `pair_attempts` data.
### Added
- DOGE markets (Binance DOGEUSDT mapping, position adoption); entry launch order is randomised so no market starves behind `ENTRY_MAX_CONCURRENT`.

---

## [0.7.3] - 2026-10-28

### Added
- **Pair measurement data** (migration 14, `src/trading/pair_log.py`; recording never affects trading):
  - `pair_attempts`: one row per entry+hedge attempt and per skipped opportunity - mode, prices, net edge, confidence, time in window, spot/strike/distance, books and depth at placement, cash/exposure, profile, which leg filled first and when, the other leg's book at that moment, outcome, sale price/size/fee, resolution (sell/hold), and after the market resolves the winner, whether the filled leg won and the realized PnL.
  - `order_events`: every order placed/cancelled/repriced/sold, linked to its attempt.
  - `attempt_ticks`: top of book of both legs every poll while waiting, and every 30s for 16 minutes afterwards (what the market did after we sold).
  - `position_marks`: bid/ask/mid of every open leg every 30s.

### Fixed
- TUI: a held leg without a trade row (e.g. one filled leg of a pair, still waiting for the other) is listed under Open positions as `⚠ 1 leg only` (paper mode).

### Added
- TUI header shows `held` (open legs at market) and `equity` = cash + held, with the change vs the paper starting balance.

### Changed
- TUI layout: Performance and the Paper account span the full right column; Recent trades moved under Open positions on the left.
- TUI: `m` opens a risk-profile dialog (↑/↓ or j/k, Enter applies, Esc cancels); 1/2/3/0 still work.
- TUI: the log moved to its own page (`l` toggles); the dashboard gives the freed space to Recent trades.

---

## [0.7.2] - 2026-10-28

### Changed
- **Hedged pairs are no longer sold early** (`PRE_SETTLEMENT_EXIT_HEDGED=NO`): in the paper run the pre-settlement exit cost about $99 against holding (4 flips); unhedged positions keep the exit.
- **Late-window entries are skipped when the market disagrees** (`LATE_WINDOW_MAX_DISAGREEMENT=0.15`): 23 of 24 shadow ENTERs with a much cheaper ask than our probability lost.
- Hedge-timeout hold requires the entry leg's bid >= $0.50.

### Added
- `docs/ANALYSIS_2026-10-27.md` and analysis scripts in the `strategy-analysis` skill.

---

### Fixed
- Hedge-timeout "hold" now also requires the entry leg's current bid to be >= $0.50; held legs bought at 0.48/0.30 whose market had already moved to ~0.27 both lost >90%.
- Position log line for hedged pairs shows the locked result instead of the entry leg's mark (a pair with a -90% entry leg is not a -90% position).

---

## [0.7.1] - 2026-10-27

### Fixed
- **Pre-settlement exit sold-leg attribution**: when the hedge leg was the winner, the sale of the losing *entry* leg was booked as a hedge sale (the check compared the losing side with the already-swapped winning side, so it never matched). Consequences: wrong PnL in the TUI and settlement, position sync marking the trade `SYNC_MISSING` with PnL 0, and the surviving hedge leg being adopted as a separate trade (its full payout counted as profit). The sale is now recorded against the leg that was sold.
- **Pre-settlement "combined entry"**: after the winner swap the hedge price was added to itself (`$1.32/pair`), so expected-profit logs and the "not a good hedge" warning were wrong.
- **Position sync** no longer marks a trade `SYNC_MISSING` when its entry leg was sold on purpose (`exited_early`).
- **Exposure sizing** counts only legs still held (a sold leg drops out, the other leg stays exposed).
- **Settlement** keeps the recorded early-exit price instead of overwriting it with the resolution price.
- **Migration 13** repairs existing rows with that signature (reopens the trade as an entry exit, removes the adopted duplicate) so settlement recomputes their PnL.

### Changed
- **TUI**: the paper panel shows account P&L (cash + holdings at market − start) next to settled-trade PnL; the difference is leg-out losses and fees, which never become trade rows. The sparkline is labelled "settled".

---

## [0.7.0] - 2026-10-27

### Breaking
- **CLOB V2 migration**: Replaced `py-clob-client` (V1, rejected by production since 2026-04-28) with `py-clob-client-v2>=1.2.0`. Order, market-info, notification and scoring modules import from `py_clob_client_v2`.
- **pUSD collateral**: `USDC_ADDRESS`/`CTF_ADDRESS`/`CTF_ABI` settings removed; `PUSD_ADDRESS` added. `get_balance()` reads pUSD. API-only wallets must wrap USDC.e → pUSD via the CollateralOnramp.
- **Redemption**: `ctf_operations` routes `redeemPositions` through `CtfCollateralAdapter` with pUSD collateral and prepends a one-time `CTF.setApprovalForAll(adapter, true)` when needed. Requires `py-builder-relayer-client>=0.0.3`.
- **Data API v2**: Position sync and settlement audit use `GET /v2/positions` (cursor pagination, snake_case rows); v1 routes retired 2026-10-24. `get_trades_for_user` removed (unused).

### Added
- **Risk profiles** (`conservative`, `balanced`, `aggressive`, `custom`): bundles of 14 risk settings switchable live from the TUI (keys `1/2/3/0`); the bot polls `bot_state` every 2s and rebinds the settings across loaded modules without a restart. `RISK_PROFILE` sets the startup profile; `custom` keeps `.env` values. See `docs/RISK_PROFILES.md`.
- **Cash- and exposure-aware sizing** (`src/trading/exposure.py`): each entry gets `BET_PERCENT` of cash not already committed (wallet cash minus entries in flight) and is clipped so open positions (entry + hedge cost) plus in-flight entries stay under `MAX_EXPOSURE_PERCENT` of equity. Entries are skipped with the reason when the cap or cash is exhausted; the TUI shows exposure as a % of equity.
- **Trailing second leg**: when one leg of a maker pair fills first, the other leg is re-quoted every poll at the best bid (escalating up to `TRAIL_MAX_TICKS` ticks over time), never above `1 − filled − MIN_NET_EDGE`, so the lock only improves or stays ≥ `MIN_NET_EDGE` (`ENABLE_TRAILING`). Works for either leg; the saved trade uses the final fill prices.
- `POLYFLUP_DB_NAME`: run an isolated instance (own DB, log and lock file) next to another one.
- **Kelly position sizing** (`KELLY_SIZING=YES`, off by default): pair entries use a two-outcome Kelly (`+MIN_NET_EDGE` when both legs fill vs `-KELLY_LEG_LOSS` when they leg out, at `KELLY_LEG_FAIL_RATE`); late-window entries use binary Kelly `(p−cost)/(1−cost)` on the fee-inclusive cost with `p` reduced by `KELLY_PROB_HAIRCUT`. Both use `KELLY_FRACTION` (¼) and are capped at `KELLY_MAX_FRACTION` of bankroll; a Kelly stake below the 5-share minimum is skipped, never bumped up.
- **Paper trading** (`uv run polyflup.py --paper`, `src/paper.py`): simulated account on live order books. Taker fills walk the book and pay `0.07·p·(1−p)`; POST_ONLY that would cross is rejected; resting orders fill in a background matcher (`PAPER_MAKER_FILL_MODE=cross` conservative / `touch` optimistic); positions redeem at 1/0 once Gamma resolves. Separate `trades_paper.db` / `trades_paper.log` / lock file, no API credentials or private key needed. View with `uv run tui.py --paper`.
- **Fee-aware entry gate**: a pair is opened only if its guaranteed profit AFTER taker fees (`0.07·p·(1−p)` per leg) is ≥ `MIN_NET_EDGE` (1¢). Near 50/50 a "0.97 combined" taker pair previously lost ~0.5¢/share.
- **Maker pair**: when no taker pair qualifies, POST_ONLY bids are rested on both legs (no fee, rebate share) while both bids are within `MAKER_PAIR_PRICE_BAND`. Previously POST_ONLY orders were priced at ask levels, so they always crossed the book and fell back to taker after 3 failures.
- **Leg-risk EV check**: if one leg fills and the other doesn't, the taker hedge is bought only if it beats flattening or holding (`edge.py`); otherwise the resting hedge is kept until timeout.
- **Late-window conviction mode** (`src/trading/late_window.py`): z-score of Chainlink spot vs strike in the last `LATE_WINDOW_SECONDS`, conservative probability (min of normal model and measured hold rates), EV after fee. Shadow-logs to new `shadow_signals` table (migration 12) and resolves outcomes; live orders only with `LATE_WINDOW_LIVE=YES`.
- **PolyBolt Chainlink feed** (`polybolt.py`): the price source Polymarket resolves on (`price.crypto`, `price.crypto.twap`), authenticated with the existing CLOB API credentials; falls back to Binance with a 2x distance filter.
- `analyze_strategy.py`: early exits vs held, confidence calibration, PnL by market, shadow-signal calibration and realized EV. Tests: `test_edge.py`.
- **5-minute markets**: `MARKETS=BTC,ETH,BTC-5M,ETH-5M` trades `{asset}-updown-5m-{ts}` markets alongside 15m ones. A market key (`BTC`, `BTC-5M`) is the `symbol` everywhere (DB, logs); `src/markets.py` holds key parsing and window math. Entry cutoff, hedge fill/GTC-fallback timeouts scale with window length; pre-settlement exit tiers earlier than 40% of the window left are skipped for 5m. Fees, min size (5), tick (0.01) and CTF redemption path are identical to 15m.
- **Terminal dashboard** (`uv run tui.py`, `src/tui/`): read-only Rich TUI with open positions + live midpoints, today/7d/all-time performance, per-symbol PnL, equity sparkline, recent trades and log tail.
- `SIGNATURE_TYPE` and `POLYGON_RPC` settings.

### Changed
- Entries run in one worker thread per market (`launch_entries`): the fill monitor (up to 90s) no longer blocks position monitoring, settlement or other markets; funds reserved by in-flight entries are subtracted before launching another.
- Log text and pre-settlement startup line now reflect the real fee model and configured confidence schedule.
- `place_batch_orders` posts `post_only` and normal orders as separate V2 batches (V2 takes `post_only` per call) and restores the original order of results.
- Settlement audit compares `realized_pnl` of CLOSED positions keyed by `token_id` (previously read non-existent `assetId`/`pnl`, so it never matched).
- Fee documentation corrected to the official model (`0.07 × p × (1 − p)` pUSD, makers free, 20% rebate pool).

### Removed
- Dead code: `redeem_winnings` (web3_utils), V1 client shims (`heartbeat`, `builder_config`), `get_position_from_data_api`, `get_actual_positions_from_data_api`, `notifications.py.backup`.

---

## [0.6.0] - 2026-01-19

### Added
- **🎯 Atomic Hedging Strategy**: Complete overhaul to simultaneous entry+hedge pairs
  - Batch API placement with combined price threshold (≤ $0.99)
  - POST_ONLY orders by default for maker rebates (0.15%)
  - Smart GTC fallback after 3 POST_ONLY failures (accepts 1.54% taker fees)
  - 120-second fill timeout with immediate cancellation of unfilled orders
  - Eliminates all unhedged positions
- **⏰ Pre-Settlement Exit Strategy**: Evaluates positions T-180s to T-45s before resolution
  - Sells losing side early if confidence > 80% on one side
  - Keeps winning side for full resolution profit
  - Uses same strategy signals as entry logic
- **🚨 Time-Aware Emergency Liquidation**: Adapts pricing based on time remaining
  - **PATIENT** (>600s): Small price drops (1¢), long waits (10-20s) - maximize recovery
  - **BALANCED** (300-600s): Moderate drops (2-5¢), balanced waits (6-10s)
  - **AGGRESSIVE** (<300s): Rapid drops (5-10¢), short waits (5-10s) - ensure liquidation
- **🔬 Smart MIN_ORDER_SIZE Handling**: Positions < 5.0 shares held if winning, orphaned if losing

### Changed
- **Execution Flow**: All trades now atomic pairs (entry+hedge simultaneously)
- **Risk Management**: Shifted from stop-loss to guaranteed profit structure
- **Configuration**: Removed deprecated exit plan, stop loss, and scale-in settings
- **Terminology**: "Trade" = atomic pair (UP + DOWN hedge), not single side

### Deprecated
- `ENABLE_EXIT_PLAN`: No longer primary strategy (atomic hedging replaces it)
- `EXIT_PRICE_TARGET`: Replaced by `COMBINED_PRICE_THRESHOLD`
- `EXIT_MIN_POSITION_AGE`: Not used with atomic hedging
- `ENABLE_STOP_LOSS`: Emergency liquidation replaces traditional stop loss
- `STOP_LOSS_PRICE`: Now used as emergency sell threshold
- `ENABLE_HEDGED_REVERSAL`: All trades are hedged by default
- `ENABLE_SCALE_IN`: Not compatible with atomic hedging strategy
- `SCALE_IN_*`: Scale-in settings deprecated
- `UNFILLED_TIMEOUT_SECONDS`: Replaced by `HEDGE_FILL_TIMEOUT_SECONDS`
- `CANCEL_UNFILLED_ORDERS`: Automatic with atomic hedging

### Technical Details
- **POST_ONLY Tracking**: Per-symbol counter tracks failures, resets on success
- **Batch API**: Atomic placement ensures both orders submitted together
- **Fill Monitoring**: Polls every 5s for up to 120s
- **Emergency Liquidation**: Progressive pricing based on time remaining
- **Pre-Settlement**: Confidence-driven exit logic at optimal timing window

### Documentation
- Updated `README.md` with atomic hedging as primary feature
- Updated `docs/STRATEGY.md` with execution flow sections
- Updated `docs/RISK_PROFILES.md` with atomic hedging parameters
- Cleaned up `.env.example` removing deprecated settings

---

## [0.5.0] - 2026-01-12

### Added
- **Bayesian Confidence Calculation**: New probabilistic method using log-likelihood accumulation with market priors
- **Dual Calculation Framework**: Both additive and Bayesian methods always calculated for A/B testing
- **Quality Factors**: Signal-specific multipliers (0.7-1.5x) based on RSI, buy pressure, divergence magnitude, ADX strength
- **Database Migration 007**: 5 new columns (additive_confidence, additive_bias, bayesian_confidence, bayesian_bias, market_prior_p_up)
- **Multi-Confirmation System**: Graduated reduction (60-85%) requires key signal agreement for high-confidence entries
- **A/B Testing Tools**: `compare_bayesian_additive.py` script for performance comparison, `check_bayesian_data.py` for data verification

### Changed
- **Strategy Module**: Refactored confidence calculation to support dual methods
- **Logging**: Shows Bayesian vs additive comparison when `BAYESIAN_CONFIDENCE=YES`
- **Settings**: Added `BAYESIAN_CONFIDENCE` flag (default: NO)

### Fixed
- **Missing Bayesian Parameters**: Added 5 new parameters to `save_trade()` calls in `src/bot.py` and `src/trading/execution.py`
- **Bayesian Data Population**: Confirmed Bayesian values being populated correctly after bot restart (trades #526-#529)
- **Formatting**: Normalized line endings and whitespace in 5 Python files (no functional changes)

### Technical Details
- **Bayesian Formula**: `confidence = 1 / (1 + exp(-ln(prior_odds) - Σ(log_LR × weight)))`
- **Log-Likelihood**: `evidence = (score - 0.5) × 2`, `log_LR = evidence × 3.0 × quality`
- **Market Prior**: Starts from Polymarket orderbook `p_up` as baseline probability
- **Quality Adjustment**: Applied to log-likelihood, not weighted scores
- **Preserved Features**: All recent improvements (scale-in, price validation, hedged reversal) work with either method

### Documentation
- Updated `README.md` with Bayesian feature overview and analysis tools section
- Updated `docs/STRATEGY.md` with Bayesian calculation details
- Updated `docs/MIGRATIONS.md` with migration 007 documentation
- Created `ISSUES_AND_PLAN.md` for issue tracking and implementation planning
- Updated `.env.example` with `BAYESIAN_CONFIDENCE` setting
- Updated all skills with v0.5.0 information (python-bot-standards, polymarket-trading, polyflup-history, database-sqlite)

---

## [0.4.3] - 2026-01-10

### Added
- **Enhanced Position Reports**: Clean, aligned format with directional emojis (📈📉) and status indicators
- **Real-Time WebSocket Integration**: Near-instant updates via User Channel (fills/cancels) and Market Channel (prices)
- **Batch API Optimization**: Single-call midpoint fetching for all positions
- **Enhanced Balance Validation**: Symbol-specific tolerance with cross-validation for API reliability issues
- **Settlement Auditing**: Automated P&L verification against exchange `closed-positions` API
- **Position Adoption**: Detects and manages untracked exchange positions on startup
- **Reward Optimization**: Exit plans auto-adjust prices to earn liquidity rewards via `check_scoring`
- **Lead/Lag Momentum**: Polymarket-native trend data with 1.2x bonus for agreement, 0.8x penalty for divergence

### Changed
- **Monitoring Cycle**: Reduced from 10s to 1s for real-time responsiveness
- **Exit Plan Logic**: Immediate placement after fill (no minimum age requirement)
- **Price Validation**: Enhanced for high-confidence trades with volatility detection
- **Log Formatting**: Cleaner, less verbose output with better emoji indicators

### Fixed
- **XRP Balance API Issues**: Cross-validation prevents zero-balance errors
- **Exit Order Size Mismatches**: Real-time validation and repair after scale-in
- **MIN_EDGE Enforcement**: Prevents low-confidence entries from bypassing threshold
- **Infinite Repair Loops**: Fixed rounding differences causing continuous order updates
- **Ghost Trade Detection**: Improved grace periods and position data fallback

### Deprecated
- `EXIT_CHECK_INTERVAL`: Now runs on 1-second monitoring cycle
- `EXIT_AGGRESSIVE_MODE`: Always active with 1-second cycle
- `STOP_LOSS_PERCENT`: Replaced by midpoint-based `STOP_LOSS_PRICE`
- `ENABLE_TAKE_PROFIT`: Use `EXIT_PLAN` instead

---

## [0.4.2] - 2026-01-05

### Added
- **WebSocket Foundation**: User and Market channel subscriptions
- **Batch Pricing**: `get_multiple_market_prices` for efficient midpoint fetching
- **Position Adoption**: Startup sync detects untracked exchange positions
- **Settlement Audit**: Automated verification against closed-positions API

### Changed
- **Startup Logic**: Enhanced position sync with market resolution verification
- **Error Handling**: Suppressed 404/Not Found during market transitions

### Fixed
- **API Rate Limiting**: Reduced calls via batch operations
- **Position Sync**: String-based ID comparison for Data API

---

## [0.4.1] - 2025-12

### Added
- **Dynamic Scale-In Timing**: Confidence-weighted entry windows (12m for high conviction)
- **Maker Orders**: Scale-in uses limit orders to earn rebates
- **Enhanced Monitoring**: 10-second position checking cycle
- **Database Migrations**: Automated schema version management

### Changed
- **Scale-In Logic**: Added confidence-based timing adjustments
- **Position Reports**: More detailed status indicators

### Fixed
- **Scale-In Fills**: Improved balance sync after secondary entries
- **Order Status**: Better tracking of unfilled orders

---

## [0.4.0] - 2025-12

### Added
- **Modular Backend**: Refactored `src/trading/orders` and `src/data/market_data`
- **Exit Plan System**: Automatic limit sell orders at 99¢ target
- **Hedged Reversal**: Hold both sides during trend flips
- **Stop Loss Enhancements**: Triple-check logic with 120s cooldown

### Changed
- **Architecture**: Separated concerns into specialized modules
- **Position Manager**: Split into entry, exit, scale, stop_loss, reversal modules
- **Database Schema**: Added reversal tracking columns

### Fixed
- **Deadlock Prevention**: Proper cursor passing in transactions
- **Balance Precision**: 0.0001 threshold for share comparisons

---

## [0.3.0] - 2025-11

### Added
- **Binance Integration**: Multi-source signal integration (momentum, order flow, divergence)
- **Advanced Strategy**: Directional voting system with weighted confidence
- **ADX Filter**: Optional trend strength confirmation
- **Volume-Weighted Momentum**: VWAP-based signals

### Changed
- **Signal Calculation**: Replaced simple edge with composite scoring
- **Position Sizing**: Confidence-based scaling with multiplier

### Fixed
- **Circular Reasoning**: External validation via Binance prevents self-referential pricing

---

## [0.2.0] - 2025-10

### Added
- **SQLite Database**: Complete trade tracking and audit logging
- **Svelte Dashboard**: Real-time UI with charts and statistics
- **Auto-Claim**: Automated winning redemption via CTF contract
- **Discord Notifications**: Real-time trade alerts

### Changed
- **Logging**: Structured logs with emoji indicators
- **Window Timing**: Configurable entry delay and lateness limits

---

## [0.1.0] - 2025-09

### Added
- **Initial Release**: Basic 15-minute crypto prediction market trading
- **Order Book Analysis**: Spread calculation and imbalance detection
- **Position Management**: Entry, monitoring, and settlement
- **Docker Support**: Containerized deployment with docker-compose

---

## Release Schedule

- **Patch versions** (0.x.Y): Bug fixes, minor improvements
- **Minor versions** (0.X.0): New features, enhancements
- **Major versions** (X.0.0): Breaking changes, major rewrites

---

## Unreleased

### Planned Features
- Machine learning signal optimization
- Multi-market correlation analysis
- Advanced backtesting framework
- Portfolio rebalancing logic
- Enhanced risk management profiles

---

For detailed technical documentation, see:
- [README.md](README.md) - Project overview and setup
- [docs/STRATEGY.md](docs/STRATEGY.md) - Trading strategy deep dive
- [docs/POSITION_FLOW.md](docs/POSITION_FLOW.md) - Position lifecycle
- [docs/MIGRATIONS.md](docs/MIGRATIONS.md) - Database schema changes
