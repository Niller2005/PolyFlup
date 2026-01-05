# Migration Guide: Turso → Local SQLite

This guide helps you migrate from Turso database back to local SQLite for better performance.

## Why Migrate?

**Performance Issues with Direct Turso:**
- ⚡ Network latency: 500-1000ms per query
- 🐌 Slows down position checks (called every 1-2 seconds)
- ⏰ Causes 5-10 minute delays in trading cycles
- 💸 Costs money for API calls

**Benefits of Local SQLite:**
- ⚡ Instant queries: <1ms (1000x faster!)
- 🚀 No network dependency
- 💰 Free
- 📦 Simple file-based storage

---

## Migration Steps

### Step 1: Export Data from Turso

**IMPORTANT: Do this BEFORE changing .env!**

```bash
# Run export script (creates trades_backup_from_turso.db)
python export_from_turso.py
```

This will:
- Connect to your current Turso database
- Export all trades
- Export schema version
- Save to `trades_backup_from_turso.db`

**Verify export succeeded:**
```bash
python -c "
import sqlite3
conn = sqlite3.connect('trades_backup_from_turso.db')
c = conn.cursor()
c.execute('SELECT COUNT(*) FROM trades')
print(f'✓ Backup contains {c.fetchone()[0]} trades')
conn.close()
"
```

---

### Step 2: Update Configuration

**Edit `.env`:**

```env
# =============================================================================
# Database Configuration - CHANGED TO LOCAL SQLITE
# =============================================================================

USE_TURSO=NO                      # ← Changed from YES to NO
USE_EMBEDDED_REPLICA=NO           # ← Already NO

# Comment out Turso credentials (not needed for local)
# TURSO_DATABASE_URL=libsql://your-db.turso.io
# TURSO_AUTH_TOKEN=your_token_here
# EMBEDDED_REPLICA_FILE=trades_replica.db
```

**Save the file.**

---

### Step 3: Stop the Bot

```bash
# If running, press Ctrl+C
# Or if in Docker:
docker-compose down
```

---

### Step 4: Backup Current Local Database (Safety)

```bash
# Backup existing trades.db if it exists
cp trades.db trades.db.before_migration 2>/dev/null || echo "No existing trades.db"

# Also backup WAL files if they exist
cp trades.db-wal trades.db-wal.backup 2>/dev/null || true
cp trades.db-shm trades.db-shm.backup 2>/dev/null || true
```

---

### Step 5: Restore Turso Data to Local Database

```bash
# Copy the Turso backup to become the new local database
cp trades_backup_from_turso.db trades.db

# Verify the data
python check_db.py
```

**Expected output:**
```
Total trades: 44
Recent 5 trades:
(44, '2026-01-05T02:17:38...', 'SOL', 1)
(43, '2026-01-05T02:17:36...', 'XRP', 0)
...

=== Journal Mode ===
('wal',)
```

---

### Step 6: Restart Bot

```bash
# Start bot
python polyastra.py

# Or with Docker:
docker-compose up -d --build
```

**Expected startup logs:**
```
[2026-01-05 ...] 🚀 Starting PolyAstra Trading Bot (Modular Version)...
[2026-01-05 ...] ✓ Database initialized
[2026-01-05 ...] 📊 Database schema version: 2
[2026-01-05 ...] ✓ Database schema is up to date
[2026-01-05 ...] 🤖 POLYASTRA | Wallet: 0xceee... | Balance: 88.23 USDC
```

---

### Step 7: Verification Checklist

Monitor the bot for 1-2 cycles and verify:

- [  ] Bot starts without errors
- [  ] Database schema version: 2
- [  ] Positions recover from database on startup
- [  ] New trades save correctly
- [  ] Exit plans place within ~5 minutes of position creation
- [  ] Settlements work
- [  ] No "Connection object used after close" errors
- [  ] **Cycles start on time** (02:30:15, 02:45:15, not 5-10 min late)
- [  ] Logs are clean (no spam)
- [  ] Balance checks work

---

## Performance Expectations

**Before (Turso):**
```
02:30:00 ← Should start here
02:39:06 ← Actually starts (9 min late!)
```

**After (Local SQLite):**
```
02:30:00 ← Should start here
02:30:15 ← Actually starts (on time!)
```

**Position check performance:**
- Turso: ~2-5 seconds per cycle
- Local: ~0.01 seconds per cycle (200-500x faster!)

---

## Backup Strategy (IMPORTANT!)

Since Turso won't auto-backup anymore, you need manual backups:

### Option 1: Daily Backup Script

Create `daily_backup.sh`:
```bash
#!/bin/bash
BACKUP_DIR="backups"
mkdir -p $BACKUP_DIR

DATE=$(date +%Y%m%d_%H%M%S)
cp trades.db "$BACKUP_DIR/trades_$DATE.db"

# Keep last 30 days only
find $BACKUP_DIR -name "trades_*.db" -mtime +30 -delete

echo "✓ Backed up to $BACKUP_DIR/trades_$DATE.db"
```

Run daily via cron:
```bash
crontab -e
# Add: 0 3 * * * /path/to/polyastra/daily_backup.sh
```

### Option 2: Git Backups

```bash
# Every day, commit database
git add -f trades.db
git commit -m "DB backup $(date +%Y-%m-%d)"
git push
```

### Option 3: Cloud Sync

```bash
# Sync to cloud storage (Dropbox, Google Drive, etc.)
cp trades.db ~/Dropbox/polyastra_backups/trades_$(date +%Y%m%d).db
```

---

## Rollback Plan

If something goes wrong, you can rollback:

```bash
# Stop bot
# Restore .env to USE_TURSO=YES
# Restart bot

# Your Turso database still has all the old data
# Nothing is deleted from Turso in this migration
```

---

## What Gets Removed

After migration, you can optionally remove Turso-related files:

```bash
# Optional cleanup (after confirming local SQLite works)
rm trades_replica.db 2>/dev/null || true
rm sync_replica.py 2>/dev/null || true
# Keep export_from_turso.py for future reference
```

---

## Ready to Migrate?

Run these commands in order:

```bash
# 1. Export from Turso
python export_from_turso.py

# 2. Update .env (set USE_TURSO=NO)
# (Edit manually in your editor)

# 3. Backup current local DB
cp trades.db trades.db.before_migration 2>/dev/null || true

# 4. Copy Turso data to local
cp trades_backup_from_turso.db trades.db

# 5. Verify
python check_db.py

# 6. Restart bot
python polyastra.py
```

---

**Total time: ~5 minutes**

Let me know when you're ready to start!
