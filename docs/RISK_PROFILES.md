# Risk Profiles

Named bundles of settings you can switch **while the bot is running**: press `1` conservative, `2` balanced, `3` aggressive or `0` custom in the TUI (`uv run tui.py`, or `--paper`). The TUI writes your choice to the bot's database; the bot applies it within about 2 seconds and logs `🎚️  Risk profile switched to ...`. Paper and real accounts keep separate profiles. Entries already in flight keep the parameters they started with.

`custom` applies nothing: the values in `.env` stay exactly as written. Set the profile used at startup with `RISK_PROFILE=` in `.env`; a profile you picked in the TUI wins over `.env` on the next restart.

| Setting | Conservative | Balanced | Aggressive | Meaning |
|---|---|---|---|---|
| `BET_PERCENT` | 3 | 6 | 10 | % of free cash per entry |
| `MAX_EXPOSURE_PERCENT` | 25 | 45 | 70 | cap on open positions + entries in flight, % of equity |
| `MAX_SIZE` | 50 | 150 | 500 | max shares per entry |
| `MIN_EDGE` | 0.45 | 0.40 | 0.32 | signal confidence needed to enter |
| `MAX_SPREAD` | 0.12 | 0.15 | 0.18 | widest spread accepted |
| `MIN_NET_EDGE` | 0.02 | 0.01 | 0.01 | guaranteed profit per share after fees |
| `MAKER_PAIR_PRICE_BAND` | 0.30 | 0.20 | 0.12 | maker pair only while both bids are in [band, 1-band] |
| `TRAIL_MAX_TICKS` | 1 | 2 | 3 | how far the trailing leg may chase the market |
| `HEDGE_FILL_TIMEOUT_SECONDS` | 60 | 90 | 120 | how long to wait for the second leg |
| `HEDGE_GTC_FALLBACK_SECONDS` | 20 | 30 | 45 | when to consider a taker hedge |
| `LIQUIDITY_MAX_SPREAD_PCT` | 5 | 8 | 12 | pre-flight liquidity spread limit |
| `LATE_WINDOW_MIN_EV` | 0.03 | 0.015 | 0.01 | min EV/share for late-window entries |
| `KELLY_FRACTION` | 0.15 | 0.25 | 0.35 | fraction of full Kelly (when `KELLY_SIZING=YES`) |
| `KELLY_MAX_FRACTION` | 0.04 | 0.06 | 0.10 | Kelly stake cap, fraction of bankroll |

Everything else (markets, fee model, leg-risk rules, trailing cap `MIN_NET_EDGE`) is shared. `MIN_NET_EDGE` moves in whole cents in practice because prices tick at 1¢.

## Choosing one
- **Conservative**: small accounts or while you are still measuring the leg-fail rate. A 2¢ minimum lock, tight spreads, a half-filled pair is abandoned after 60s, and at most a quarter of equity is ever exposed.
- **Balanced**: the starting point for most accounts.
- **Aggressive**: only with a measured leg-fail rate well under ~20% (see `analyze_strategy.py`); larger positions, wider spreads, longer waits for the second leg. Longer waits mean more time exposed to an unhedged leg.

These numbers are starting points chosen for risk ordering, not tuned results. Check them in paper mode first.

Defined in `src/profiles.py`; applied by `src/risk_runtime.py`. State lives in the `bot_state` table (`risk_profile`, `risk_profile_applied`).
