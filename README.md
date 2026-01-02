# PolyAstra Polymarket Trading Bot

PolyAstra is a fully automated trading bot for **15-minute up/down crypto markets on Polymarket**.

It trades on markets like:

- `btc-updown-15m-<timestamp>`
- `eth-updown-15m-<timestamp>`
- `xrp-updown-15m-<timestamp>`
- `sol-updown-15m-<timestamp>`

and places **UP / DOWN** bets based on:

- on-chain orderbook data from the Polymarket CLOB,
- funding bias from Binance Futures,
- the Fear & Greed Index,
- an optional external trend filter (BFXD).

---

## Features

- ✅ Automatic trading on 15-minute up/down crypto markets (default: BTC, ETH, XRP, SOL).
- ✅ Edge-based decision-making using:
  - mid price from the orderbook,
  - bid/ask imbalance,
  - funding rate bias (Binance Futures),
  - Fear & Greed sentiment.
- ✅ Direction logic:
  - `edge >= MIN_EDGE` → buy **UP**,
  - `edge <= 1 - MIN_EDGE` → buy **DOWN**,
  - otherwise → **PASS**.
- ✅ Enforces Polymarket’s **minimum order size** (5 tokens) by automatically increasing the size if necessary.
- ✅ Configurable delay after each 15-minute window starts (`WINDOW_DELAY_SEC`).
- ✅ External trend filter for BTC via `BFXD_URL`:
  - `"BTC/USDT": "UP"` → only **UP** trades are allowed, **DOWN** trades are blocked.
  - `"BTC/USDT": "DOWN"` → only **DOWN** trades are allowed, **UP** trades are blocked.
  - missing / invalid / failing endpoint → no filtering (fails open).
- ✅ SQLite database with full trade history (edge, PnL, ROI, etc.).
- ✅ Periodic settlement: simulates exit at market price after the window ends and computes realized PnL.
- ✅ Performance reports (win rate, total PnL, ROI) written to disk and optionally sent to Discord.
- ✅ Discord notifications for entries, reports and critical errors (optional).

---

## Requirements

- **Option A (Docker):** Docker & Docker Compose installed.
- **Option B (Local):** Python 3.10+ and [uv](https://github.com/astral-sh/uv) installed.
- Access to Polymarket via:
  - a **proxy wallet private key** on Polygon (`PROXY_PK`),
  - optionally a `FUNDER_PROXY` address.
- Sufficient **USDC on Polygon** on the proxy wallet.

---

## Installation & Usage

### 1. Configuration
First, copy the example environment file and configure your credentials:

```bash
cp .env.example .env
# Edit .env and fill in PROXY_PK and other settings
```

### 2. Running with Docker (Recommended)
This runs the bot, a background dashboard generator, and a local web dashboard.

```bash
docker-compose up -d --build
```

- **View Logs:** `docker-compose logs -f polyastra`
- **View Dashboard:** Open [http://localhost:8000](http://localhost:8000)

### 3. Running Locally with uv
If you prefer running directly on your machine:

1. **Install dependencies:**
   ```bash
   uv sync
   ```

2. **Run the bot:**
   ```bash
   uv run polyastra.py
   ```

3. **(Optional) Run dashboard generator:**
   To generate the dashboard HTML file in the `public/` directory:
   ```bash
   uv run generate_dashboard.py
   ```

---

## How it works (high level)

1. Time is divided into **15-minute slots**:  
   `[HH:00–HH:15)`, `[HH:15–HH:30)`, `[HH:30–HH:45)`, `[HH:45–HH:60)`.

2. For each cycle:
   - the bot waits until the next 15-minute boundary (`:00 / :15 / :30 / :45`),
   - then waits an additional `WINDOW_DELAY_SEC` seconds (configurable buffer),
   - for each symbol in `MARKETS` (e.g. `BTC, ETH, XRP, SOL`):
     - constructs the slug, e.g. `btc-updown-15m-<timestamp>`,
     - fetches **UP** and **DOWN** token IDs from Gamma API,
     - reads the orderbook for the **UP** token from the CLOB,
     - calculates an **edge** score,
     - decides: **UP / DOWN / PASS**,
     - applies the BTC trend filter from `BFXD_URL` (if configured),
     - computes order size (ensuring minimum 5 tokens),
     - sends a **BUY** order to the Polymarket CLOB.
3. After the 15-minute window ends:
   - the bot considers trades on that market **settleable**,
   - it re-reads the orderbook for the traded token,
   - computes a final "exit" price using the mid price (bid/ask average),
   - for an UP position: exit value ≈ `final_price`,  
     for a DOWN position: exit value ≈ `1 - final_price`,
   - calculates PnL in USDC and ROI, then marks trades as settled in SQLite.
4. Every N cycles (e.g. every ~4 hours) and on shutdown, the bot generates a performance report.
