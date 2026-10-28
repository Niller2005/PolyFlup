---
name: strategy-analysis
description: Analyze PolyFlup strategy performance from trades*.db and logs/ - PnL reconciliation, leg-out rate, exit/hold decisions, fee and edge checks. Use when asked "how is it doing", "why did X happen", or to evaluate a rule change with data.
---

# Strategy analysis (database + logs)

Complements `database-analyzer` / `log-analyzer` (which sync data); this skill is the *method*. Never modify or delete `trades*.db` / `logs/*` - open the DB read-only: `sqlite3 "file:trades_paper.db?mode=ro" "..."`. The bot/TUI may be running.

## Where the data is
| Source | Content |
|---|---|
| `trades.db` / `trades_paper.db` | `trades` (one row per saved pair/position), `shadow_signals` (late-window evals), `paper_fills` / `paper_orders` / `paper_positions` / `paper_account` (paper only), `bot_state` |
| `logs/window_YYYY-MM-DD_HH-MM-SS.log` | everything logged in that 15m window (5m markets log into the enclosing one) |
| `logs/trades_paper.log` (`trades_2025.log` real) | master log; `errors.log` stack traces |
| `logs/reports/` | generated reports |

Symbols: `BTC` = 15m, `BTC-5M` = 5m. Times are UTC.

## 1. Always reconcile PnL first
`trades.pnl_usd` only covers *saved* pairs. Leg-outs (one leg filled, the other timed out and was sold - "Trade skipped - atomic pair failed") never become rows, so they are invisible there. True result (paper):
```sql
-- cash + holdings at cost - start; negative "other" = leg-out losses + fees
SELECT cash, start_cash FROM paper_account;
SELECT ROUND(SUM(CASE WHEN side='BUY' THEN -price*size ELSE price*size END),2), ROUND(SUM(fee),2) FROM paper_fills;
SELECT final_outcome, COUNT(*), ROUND(SUM(pnl_usd),2) FROM trades GROUP BY 1;
```
Or just `uv run tui.py --paper --once` (shows account P&L vs settled trades vs other).
Rows with `final_outcome` `SYNC_MISSING`, `ADOPTED` are bookkeeping artefacts, not results; check them before trusting totals.

## 2. Standard questions
```sql
-- by symbol / window length
SELECT symbol, COUNT(*) n, ROUND(SUM(pnl_usd),2) pnl, ROUND(AVG(pnl_usd),3) avg FROM trades WHERE settled=1 GROUP BY 1;
-- hedged pairs: realised vs locked edge
SELECT id, symbol, size, entry_price, hedge_order_price, ROUND(1-entry_price-hedge_order_price,3) locked, pnl_usd,
       exited_early, hedge_exited_early FROM trades WHERE is_hedged=1 ORDER BY id DESC LIMIT 30;
-- late-window shadow signals: model vs reality
SELECT decision, COUNT(*), ROUND(AVG(won),3) hit, ROUND(AVG(p_use),3) p, ROUND(AVG(ev),4) ev FROM shadow_signals WHERE won IS NOT NULL GROUP BY 1;
```
`uv run analyze_strategy.py [--db PATH]` covers early exits, calibration and shadow signals.

## 2b. Measurement tables (v0.7.3+; empty for older data)
```sql
-- leg-out rate, and did the filled leg win? (n first!)
SELECT mode, symbol, COUNT(*) n, SUM(outcome='timeout') legouts, ROUND(AVG(outcome='timeout'),3) rate,
       ROUND(AVG(CASE WHEN outcome='timeout' THEN filled_leg_won END),3) filled_leg_won,
       ROUND(SUM(realized_pnl),2) legout_pnl FROM pair_attempts WHERE outcome IN ('both','timeout') GROUP BY 1,2;
-- adverse selection vs context: does time in window / distance / confidence predict leg-outs?
SELECT CAST(secs_into_window/60 AS INT) min_in, COUNT(*) n, ROUND(AVG(outcome='timeout'),2) rate FROM pair_attempts WHERE outcome IN ('both','timeout') GROUP BY 1;
-- how the other leg's book looked when the first leg filled (cannot-chase evidence)
SELECT first_leg, ROUND(AVG(other_bid_at_first - (1-CASE first_leg WHEN 'entry' THEN final_entry_price ELSE final_hedge_price END)),3) FROM pair_attempts WHERE outcome='timeout' GROUP BY 1;
-- market path after we sold (what holding would have done)
SELECT a.id, t.t_s, t.entry_bid, t.hedge_bid FROM attempt_ticks t JOIN pair_attempts a ON a.id=t.attempt_id WHERE a.outcome='timeout' AND t.phase='after' ORDER BY a.id, t.t_s;
-- skipped opportunities by reason
SELECT note, COUNT(*) FROM pair_attempts WHERE outcome='skipped' GROUP BY 1 ORDER BY 2 DESC;
-- every order of one attempt
SELECT ts, event, side, price, size, purpose, success FROM order_events WHERE attempt_id = ? ORDER BY id;
```

## 2c. Raw data for offline research (v0.7.6+)
| Table | Use |
|---|---|
| `market_ticks` | every market, every 5s: up/down bid+ask, spot, strike, secs_left. Replay any pair/maker idea, compute momentum/volatility features offline. |
| `market_trades` | websocket trade tape per token (price, size, side): model whether a resting bid would have been filled and by what. |
| `market_windows` | real result of every window (up_won), including windows we never traded: unbiased base rates. |
| `evaluations` | every strategy evaluation with confidence/bias/p_up/signals JSON and the decision: is the signal predictive at all? |
| `account_snapshots` | cash / held / equity series (equity curve with marks). |
| `pair_attempts.entry_book/hedge_book/other_book_at_first` | JSON top-5 depth `{"bids":[[p,s]..],"asks":[..]}` at placement / first fill. |
```sql
-- is the signal calibrated? confidence vs how often the bias side won the window
SELECT ROUND(e.confidence,1) c, e.bias, COUNT(*) n, ROUND(AVG((e.bias='UP')=w.up_won),3) hit
FROM evaluations e JOIN market_windows w ON w.slug=e.slug WHERE e.bias IN ('UP','DOWN') GROUP BY 1,2;
-- how often would a bid at the touch have traded within 30s (tape)?
SELECT COUNT(*) FROM market_trades WHERE slug=? AND token_id=? AND price<=? AND ts BETWEEN ? AND ?;
-- up-token path of one window
SELECT ts, secs_left, up_bid, up_ask, spot, strike FROM market_ticks WHERE slug=? ORDER BY ts;
```

## 3. Mining the logs
```bash
uv run python .opencode/skill/strategy-analysis/log_events.py            # tally of key decision events (numbers normalised)
uv run python .opencode/skill/strategy-analysis/log_events.py -p "Hedge retry|crosses book" -n 10
grep -a "#12 " logs/window_*.log          # follow one trade across windows (quote the #id)
```
Key log phrases: `Maker pair:` / `Maker pair skipped` (entry decision), `Trail capped` / `Trailing` (second leg), `Leg-risk EV/sh` (taker vs flatten vs hold), `TIMEOUT: Both orders not filled` + `CRITICAL: ... timed out` (leg-out), `Confidence too low ... emergency selling` / `HOLDING ... despite hedge timeout` (leg-out resolution), `PRE-SETTLEMENT EXIT OPPORTUNITY` / `Confidence too low for hedged exit` (late exit), `PAPER maker fill|settled|SELL`, `Syncing positions` / `SYNC_MISSING` / `Adopting untracked`.

Also: `pair_outcomes.py [log]` (leg-out rate by symbol/combined price/fill timing, sold-leg losses) and `slug_pnl.py [db]` (true PnL per market window from fills, classifying pairs vs leg-outs).

## 4. Key ratios to compute
- **Leg-out rate** = `CRITICAL: ... timed out` / (those + `Both orders filled`). Break-even is ~17-20% (pair edge +1-2c vs ~4c loss per leg-out); measured ~50% on paper.
- **Decision mix** after a timeout: sold vs held (grep above) and the PnL of each; stored confidence is entry-time and stale - compare with the bid at decision time.
- **Fee check**: taker fee/share = `0.07*p*(1-p)`; makers 0. `paper_fills.fee` totals should be small if maker-heavy.
- **Per-window summary**: `WINDOW SUMMARY` lines give PnL per window.

## 5. Pitfalls
- Paper maker fills are modelled (`PAPER_MAKER_FILL_MODE=cross` conservative): paper fill rate/adverse selection is not live truth.
- Logs rotate per window; the master log is appended across restarts - a restart resets the paper account only if the DB was deleted (schema version 0 at startup in the log means a fresh DB).
- Compare like with like: separate 5m vs 15m and `MAX_SIZE`/profile changes (`Risk profile switched` lines mark profile changes).
- State uncertainty: small samples (n < 30) say nothing about edge; report n with every rate.
