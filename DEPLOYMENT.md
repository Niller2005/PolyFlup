# Production Deployment Instructions

## Current Issue on Production

```
❌ ERROR: Failed to initialize RelayClient: No module named 'py_builder_relayer_client'
```

This means the Polymarket Relayer SDK is not installed on production server.

## Deployment Steps

### 1. SSH to Production Server

```bash
ssh root@95.217.40.183
cd /root/polyastra
```

### 2. Pull Latest Code

```bash
git fetch origin
git checkout bayesian
git pull origin bayesian
```

### 3. Install Dependencies

The project uses `uv` for dependency management. Run:

```bash
uv sync
```

This will install all dependencies including:
- `py-builder-relayer-client` (0.0.1)
- `py-builder-signing-sdk` (0.0.2)

### 4. Verify Installation

```bash
uv pip show py-builder-relayer-client
uv pip show py-builder-signing-sdk
```

Expected output:
```
Name: py-builder-relayer-client
Version: 0.0.1
Location: .venv/lib/python3.X/site-packages
Requires: py-builder-signing-sdk, python-dotenv, requests

Name: py-builder-signing-sdk
Version: 0.0.2
Location: .venv/lib/python3.X/site-packages
Requires: python-dotenv, requests
```

### 5. Configure Builder API Credentials (Optional but Recommended)

Edit `.env` and add your Polymarket Builder API credentials:

```bash
nano .env
```

Add these lines:
```bash
# Get these from https://polymarket.com/developers → Builder Profile
POLY_BUILDER_API_KEY=your_key_here
POLY_BUILDER_SECRET=your_secret_here
POLY_BUILDER_PASSPHRASE=your_passphrase_here
ENABLE_RELAYER_CLIENT=YES
```

**Note**: If you don't have Builder API credentials yet:
1. Apply at https://polymarket.com/developers
2. Wait for approval
3. Until then, the bot will use Web3 fallback (requires POL for gas)

### 6. Add POL to EOA for Gas Fallback (Optional)

If using Web3 fallback (no Builder credentials), the EOA needs POL for gas:

```bash
# Send ~0.1 POL to:
0x077ed66F5Bc227067Bc529fFb0e3Db03EdE3090C
```

Check current balance:
```bash
# Using polygonscan or:
cast balance 0x077ed66F5Bc227067Bc529fFb0e3Db03EdE3090C --rpc-url https://polygon-rpc.com
```

### 7. Restart Bot

Depending on how the bot is running:

#### If using systemd:
```bash
systemctl restart polyflup
systemctl status polyflup
```

#### If using screen/tmux:
```bash
# Find the screen session
screen -ls

# Reattach and restart
screen -r polyflup
# Ctrl+C to stop
uv run polyflup.py
# Ctrl+A then D to detach
```

#### If using Docker:
```bash
docker-compose restart
docker-compose logs -f
```

### 8. Verify Bot is Working

Watch the logs:
```bash
tail -f logs/trades_2026.log
```

Expected startup messages:
```
🔄 [Startup] Launching background redemption task...
🔄 [Startup] Found X trades needing redemption...
🌐 [BTC] #XXX Using gasless Relayer for redemption
✅ [BTC] #XXX REDEEM SUCCESS (GASLESS)
💰 [Startup] Redemption complete: X redeemed | Total: $+XX.XX
```

If you see:
```
⚠️  Relayer credentials not configured, using manual Web3
```

That's okay - it will use Web3 fallback (requires POL for gas).

## Troubleshooting

### Module Still Not Found After `uv sync`

Try reinstalling:
```bash
uv pip uninstall py-builder-relayer-client py-builder-signing-sdk
uv sync --force
```

### Web3 Fallback Failing

Check EOA has POL:
```bash
# The EOA address derived from PROXY_PK
# Should show in bot logs at startup
```

Send at least 0.05 POL to cover gas costs.

### Account Mismatch Error

If you see:
```
from field must match key's 0x077e... but it was 0xceee...
```

This is fixed in commit `1585a9c`. Make sure you pulled latest code.

## What This Deployment Fixes

1. ✅ **Missing Relayer SDK** - Now installed via pyproject.toml
2. ✅ **Web3 fallback account mismatch** - Now uses EOA consistently
3. ✅ **raw_transaction attribute** - Fixed for web3.py >= 6.0
4. ✅ **Background redemption** - Auto-redeems old trades on startup
5. ✅ **Auto-redemption at settlement** - Handles new trades automatically

## Expected Behavior After Deployment

### With Builder API Credentials (Gasless):
- All CTF operations (merge/redeem) happen gaslessly via Relayer
- No POL needed for gas
- $0.00 cost per operation

### Without Builder API Credentials (Web3 Fallback):
- CTF operations use manual Web3 transactions
- Requires POL in EOA for gas (~$0.02 per operation)
- Still works, just costs gas fees

## Latest Commit

Branch: `bayesian`
Commit: `1585a9c`

All changes have been tested locally and are ready for production.
