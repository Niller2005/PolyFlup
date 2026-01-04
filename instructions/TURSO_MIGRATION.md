# Turso Database Migration Guide

This document explains how to use Turso (libSQL) as your database backend instead of local SQLite.

## What is Turso?

Turso is a distributed database built on libSQL (SQLite fork) that provides:
- **Edge replication** - Data replicated globally for low latency
- **Remote access** - Access your database from anywhere
- **SQLite compatibility** - Same SQL syntax and features
- **Branching** - Create database branches for development
- **Free tier** - Generous free tier for small projects

## Benefits

- **Production deployment**: No need to manage SQLite file on server
- **Multiple instances**: Run bot on multiple servers sharing same database
- **Backup & recovery**: Automatic backups and point-in-time recovery
- **Development branches**: Test schema changes safely
- **Better concurrency**: Better than file-based SQLite for concurrent access

## Setup Instructions

### 1. Install Turso CLI

```bash
# macOS/Linux
curl -sSfL https://get.tur.so/install.sh | bash

# Windows (PowerShell)
irm https://get.tur.so/install.ps1 | iex
```

### 2. Create Turso Account

```bash
turso auth signup
```

### 3. Create Database

```bash
# Create production database
turso db create polyastra-prod

# Get database URL
turso db show polyastra-prod --url

# Create authentication token
turso db tokens create polyastra-prod
```

### 4. Create Development Branch (Optional)

```bash
# Create a branch from production for local development
turso db create polyastra-dev --from-db polyastra-prod

# Get dev database URL
turso db show polyastra-dev --url

# Create dev token
turso db tokens create polyastra-dev
```

### 5. Configure Environment

Add to your `.env` file:

```env
# For Production
USE_TURSO=YES
TURSO_DATABASE_URL=libsql://polyastra-prod-your-org.turso.io
TURSO_AUTH_TOKEN=eyJhbG...your-token-here

# For Local Development - Choose one:

# Option A: Embedded Replica (RECOMMENDED - fast local reads with remote sync)
USE_EMBEDDED_REPLICA=YES
TURSO_DATABASE_URL=libsql://polyastra-prod-your-org.turso.io
TURSO_AUTH_TOKEN=eyJhbG...your-token-here
EMBEDDED_REPLICA_FILE=trades_replica.db

# Option B: Use Turso development branch (direct remote connection)
USE_TURSO=YES
TURSO_DATABASE_URL=libsql://polyastra-dev-your-org.turso.io
TURSO_AUTH_TOKEN=eyJhbG...your-dev-token-here

# Option C: Use local SQLite only (no sync)
USE_TURSO=NO
```

### 6. Install Dependencies

```bash
pip install -r requirements.txt
# or
uv pip install -r requirements.txt
```

This will install `libsql>=0.1.11` which is required for Turso embedded replica support.

**Note for Docker:** The Dockerfile automatically installs Rust build tools required to compile `libsql` from source.

### 7. Run Migrations

The bot will automatically run migrations on startup when it detects a new/empty database:

```bash
python polyastra.py
```

You should see:
```
📊 Database schema version: 0
🔄 Running 2 pending migrations...
  Migration 1: Add scale_in_order_id column
    ✓ Migration 1 completed
  Migration 2: Verify timestamp column
    ✓ Migration 2 completed
✓ All migrations completed. Schema version: 2
✓ Database initialized
```

## Branch-Based Workflow

### Development Workflow

```bash
# 1. Create development branch
turso db create polyastra-dev --from-db polyastra-prod

# 2. Update .env to use dev branch
USE_TURSO=YES
TURSO_DATABASE_URL=libsql://polyastra-dev...
TURSO_AUTH_TOKEN=...

# 3. Develop and test

# 4. When ready, update production config
USE_TURSO=YES
TURSO_DATABASE_URL=libsql://polyastra-prod...
TURSO_AUTH_TOKEN=...

# 5. Deploy to production
```

### Local Development Options

**Option 1: Embedded Replica (RECOMMENDED)**
```bash
# Best for local dev and production - fast local reads, syncs with remote
USE_TURSO=NO
USE_EMBEDDED_REPLICA=YES
TURSO_DATABASE_URL=libsql://polyastra-prod...
TURSO_AUTH_TOKEN=...
EMBEDDED_REPLICA_FILE=trades_replica.db

# Benefits:
# - Microsecond-level local reads (no network latency)
# - Automatic sync every 30 seconds
# - Syncs immediately after writes
# - Works offline with cached data
# - Dramatically reduces network overhead for high-frequency reads
```

**Option 2: Local SQLite Only**
```bash
# Use local SQLite for development (no sync)
USE_TURSO=NO

# Deploy to production uses Turso
USE_TURSO=YES
TURSO_DATABASE_URL=...
TURSO_AUTH_TOKEN=...
```

**Manual Sync for Embedded Replica**
```bash
# Force a manual sync with remote database
python sync_replica.py
```

See [EMBEDDED_REPLICAS.md](./EMBEDDED_REPLICAS.md) for detailed setup and usage.

## Migrating Existing Data

If you have an existing SQLite database and want to migrate to Turso:

### Option 1: Turso CLI Import

```bash
# Export your SQLite database
sqlite3 trades.db .dump > trades.sql

# Import to Turso
turso db shell polyastra-prod < trades.sql
```

### Option 2: Manual CSV Export/Import

```python
# export_data.py
import sqlite3
import csv

conn = sqlite3.connect('trades.db')
c = conn.cursor()
c.execute("SELECT * FROM trades")

with open('trades.csv', 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow([desc[0] for desc in c.description])  # Headers
    writer.writerows(c.fetchall())

conn.close()
```

Then manually insert into Turso or use bulk import.

### Option 3: Fresh Start

Simply start fresh with Turso - old trades in SQLite remain accessible for reference.

## Monitoring

### Check Database Size

```bash
turso db show polyastra-prod
```

### View Recent Queries

```bash
turso db shell polyastra-prod
> SELECT * FROM trades ORDER BY timestamp DESC LIMIT 10;
```

### Database Branches

```bash
# List all databases
turso db list

# Show database info
turso db show polyastra-prod

# Delete a branch
turso db destroy polyastra-dev
```

## Troubleshooting

### Connection Errors

```
Error: Failed to connect to Turso database
```

**Solutions:**
1. Check `TURSO_DATABASE_URL` format: `libsql://your-db.turso.io`
2. Verify `TURSO_AUTH_TOKEN` is valid (tokens can expire)
3. Check network connectivity
4. Regenerate token: `turso db tokens create polyastra-prod`

### Schema Mismatch

```
Error: no such column: scale_in_order_id
```

**Solution:** Migrations didn't run. Delete database and recreate:
```bash
turso db destroy polyastra-prod
turso db create polyastra-prod
python polyastra.py  # Migrations will run automatically
```

### Performance Issues

**For Direct Turso Connection:**
If experiencing high latency, switch to embedded replica mode for better performance.

**For Embedded Replica:**
- Reads are instant (local SQLite file)
- Syncs happen every 30 seconds for reads
- Writes sync immediately to remote
- No performance issues expected

**General Tips:**
1. Use batch operations where possible
2. Check your region: `turso db locations`
3. **Recommended:** Use embedded replica mode for production

## Limits (Free Tier)

- **Databases**: 3 databases
- **Locations**: 3 locations (edge replication)
- **Rows read**: 1 billion/month
- **Rows written**: 25 million/month
- **Storage**: 9 GB total

For this bot's usage (15-min intervals), these limits are more than sufficient.

## Cost (Paid Tiers)

If you exceed free tier:
- **Scaler**: $29/month - 50 databases, 1 trillion reads, 500M writes
- **Enterprise**: Custom pricing

## Comparison: SQLite vs Turso

| Feature | Local SQLite | Turso |
|---------|-------------|-------|
| **Cost** | Free | Free tier available |
| **Setup** | Zero config | Requires signup |
| **Access** | Local file only | Remote access |
| **Backups** | Manual | Automatic |
| **Replication** | None | Global edge |
| **Concurrent writes** | Limited | Better |
| **Deployment** | Need file management | Managed service |
| **Development** | Simple | Branches available |

## Recommendations

### Use Local SQLite if:
- Running bot on single machine
- Want zero external dependencies
- Testing/development only
- Comfortable managing database files

### Use Turso if:
- Running bot on cloud/VPS
- Want automatic backups
- Running multiple bot instances
- Want production-grade database
- Need remote access to data

## Security Notes

1. **Never commit** `.env` with Turso credentials
2. **Rotate tokens** periodically: `turso db tokens create polyastra-prod`
3. **Use separate tokens** for dev and production
4. **Revoke old tokens**: `turso db tokens revoke <token>`

## Docker Compose

Update `docker-compose.yml` to use Turso:

```yaml
services:
  bot:
    build: .
    environment:
      - USE_TURSO=YES
      - TURSO_DATABASE_URL=${TURSO_DATABASE_URL}
      - TURSO_AUTH_TOKEN=${TURSO_AUTH_TOKEN}
    # Remove volume mount for trades.db
```

## Support

- Turso Docs: https://docs.turso.tech
- Turso Discord: https://discord.gg/turso
- GitHub Issues: https://github.com/tursodatabase/libsql

## Migration Checklist

- [ ] Install Turso CLI
- [ ] Create Turso account
- [ ] Create production database
- [ ] Create development branch (optional)
- [ ] Get database URLs and tokens
- [ ] Update `.env` with Turso credentials
- [ ] Install `libsql-client` package
- [ ] Test connection: `python polyastra.py`
- [ ] Verify migrations ran successfully
- [ ] Migrate existing data (if needed)
- [ ] Update deployment scripts/Docker
- [ ] Document credentials securely
- [ ] Set up monitoring/alerts

## Code Changes

All database code is already compatible with Turso! The migration involved:

1. **Updated `requirements.txt`**: Added `libsql>=0.1.11` for embedded replica support
2. **Updated `pyproject.toml`**: Added `libsql>=0.1.11` for uv package manager
3. **Updated `settings.py`**: Added `USE_TURSO`, `USE_EMBEDDED_REPLICA`, `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`
4. **Updated `db_connection.py`**: 
   - Created context manager supporting SQLite, Turso, and Embedded Replicas
   - Automatic 30-second sync interval for reads
   - Immediate sync after writes
   - Proper transaction management
5. **Updated `Dockerfile`**: Added Rust build tools required for compiling `libsql`
6. **Updated `database.py`**: Skip `PRAGMA journal_mode=WAL` for Turso (not supported)
7. **Updated migrations**: Use generic connection type instead of `sqlite3.Connection`
8. **Removed manual commits**: All `conn.commit()` calls removed - handled by context manager
9. **No changes** to SQL queries - 100% SQLite compatible!

The bot automatically switches between SQLite, Turso, and Embedded Replicas based on environment variables.

---

**Next Steps:** Set up your Turso account and update your `.env` file!
