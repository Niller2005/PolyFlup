# PolyFlup Glossary

A comprehensive guide to terms, concepts, and technologies used in the PolyFlup trading bot and dashboard.

## Trading & Strategy

### Exit Plan
An automated strategy that places a limit sell order at $0.99 (or a target price) as soon as a position is opened. This ensures profits are captured immediately if the market reaches its target.

### Hedged Reversal
A strategy where the bot holds both **UP** and **DOWN** positions for the same market window simultaneously. This occurs when a trend flips with high confidence. The losing side is eventually cleared by a stop loss.

### Scale-In
The process of adding more capital to an existing winning position. In PolyFlup, scale-ins are executed using Market Orders (FAK) to ensure immediate execution, followed by an automatic update of the Exit Plan to cover the new total size.

### Midpoint Stop Loss
A safety mechanism that triggers a market sell if the **midpoint price** of a token drops to or below a specific threshold (default: $0.30). This is preferred over percentage-based stop losses to avoid being stopped out by spread volatility.

### Confidence Score
A numerical value (usually 0.0 to 1.0) calculated by the strategy module based on technical indicators (ADX, RSI, VWM) and momentum. Entries typically require a minimum "Edge" (e.g., 0.565).

### Underdog
A position on the side currently trading below $0.50. Entering an underdog position requires higher confidence (default: 40%) due to the lower probability of success.

### Winning Side / Losing Side
Terms used to describe the relative position of a trade based on current market probability.
- **Winning Side**: The outcome token trading above $0.50 (high probability). The bot prioritizes scaling into these positions.
- **Losing Side**: The outcome token trading below $0.50 (low probability). In a **Hedged Reversal**, the losing side is the one the bot expects to be stopped out as the trend shifts toward the reversal.

### Market Window
The specific 15-minute time frame for a Polymarket prediction event (e.g., "BTC Price at 10:45 AM"). Each window is a distinct market that expires at a set time.

### Cycle
A single iteration of the bot's main processing loop. During a cycle, the bot evaluates signals, manages orders, monitors positions, and performs self-healing checks. Cycles typically run every 10-60 seconds.

---

## Polymarket & CLOB

### CLOB (Central Limit Order Book)
Polymarket's matching engine where orders are matched peer-to-peer. It supports limit orders, market orders, and various time-in-force instructions.

### Gamma API
Polymarket's market data API used for fetching event details, market structures, and user positions.

### USDC.e
Bridged USDC from Ethereum on the Polygon network. This is the primary collateral used for all trading on Polymarket.

### Token ID
A unique identifier for an outcome token (e.g., the "YES" or "NO" side of a market). PolyFlup handles both Hexadecimal IDs (CLOB) and Decimal IDs (Data API).

### Tick Size
The minimum price increment allowed for a market (e.g., 0.01 or 0.001). All orders must be rounded to the tick size to be valid.

### Order Types
- **GTC (Good-Til-Cancelled):** Order remains active until filled or manually cancelled.
- **FOK (Fill-Or-Kill):** Order must be filled entirely and immediately, or it is cancelled.
- **FAK (Fill-And-Kill):** Partial fills are accepted immediately; the remainder is cancelled. Used for market orders.
- **GTD (Good-Til-Date):** Order expires at a specific Unix timestamp.

---

## Technical Infrastructure

### UV
A fast Python package installer and resolver used to manage PolyFlup's backend dependencies and environment.

### WAL Mode (Write-Ahead Logging)
An SQLite configuration that allows for better concurrency, enabling the bot to write to the database while the UI or other processes read from it.

### Schema Version
A version number stored in the database to track which migrations have been applied. This ensures the database structure stays in sync with the code.

### Proxy PK
The private key used by the bot to sign transactions and manage the proxy wallet on Polymarket.

### Svelte / SvelteKit
The frontend framework used to build the PolyFlup dashboard.

### Shadcn-Svelte
A collection of UI components used for the dashboard's design system.

### Biome
The tool used for formatting and linting the JavaScript/Svelte codebase.
