# Embedded Replicas for Local Development

This guide explains how to use Turso embedded replicas for local development with PolyAstra.

## What are Embedded Replicas?

Embedded replicas are a Turso feature that allows you to have a local SQLite database that automatically syncs with a remote Turso database. This gives you the best of both worlds:

- ⚡ **Fast local reads** - Microsecond-level query performance (no network latency)
- 🔄 **Automatic sync** - Changes sync bidirectionally with remote database
- 📖 **Read-your-writes** - After a write succeeds, you can immediately read the new data
- 💾 **Local-first** - Works even when offline (with cached data)
- 🔒 **Safe writes** - Writes go to remote database by default (prevents local-only test data)

## When to Use Embedded Replicas

**Use embedded replicas for:**
- Local development when you want real production data
- Testing with production-like data without affecting the remote database
- Fast local queries while maintaining sync with production
- Offline development scenarios

**Don't use embedded replicas for:**
- Production deployment (use direct Turso connection instead with `USE_TURSO=YES`)
- CI/CD environments (use local SQLite with `USE_TURSO=NO, USE_EMBEDDED_REPLICA=NO`)

## Setup

### 1. Install Dependencies

```bash
pip install libsql
```

### 2. Get Turso Credentials

You need a Turso database first. If you don't have one:

```bash
# Install Turso CLI
curl -sSfL https://get.tur.so/install.sh | bash

# Create a database
turso db create polyastra

# Get database URL
turso db show polyastra --url

# Create auth token
turso db tokens create polyastra
```

### 3. Configure Environment Variables

Add these to your `.env` file:

```bash
# Enable embedded replica mode
USE_EMBEDDED_REPLICA=YES

# Turso credentials (from step 2)
TURSO_DATABASE_URL=libsql://your-db.turso.io
TURSO_AUTH_TOKEN=your_auth_token_here

# Local replica file (optional - defaults to trades_replica.db)
EMBEDDED_REPLICA_FILE=trades_replica.db
```

### 4. Run the Bot

```bash
python polyastra.py
```

The bot will now:
1. Connect to the local replica file (`trades_replica.db`)
2. Sync with remote Turso database on startup
3. Read from local replica (fast)
4. Write to remote database (safe)
5. Auto-sync after each write (read-your-writes guarantee)

## How It Works

### Connection Flow

```python
# When USE_EMBEDDED_REPLICA=YES
with db_connection() as conn:
    # 1. Opens local SQLite file (trades_replica.db)
    # 2. Syncs with remote Turso database
    # 3. All reads served from local file (fast)
    
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM trades")  # Read from local
    
    cursor.execute("INSERT INTO trades ...")  # Write to remote
    # commit happens automatically on exit, which syncs changes back to local
```

### Sync Behavior

- **On connection**: Syncs local replica with remote database
- **On commit**: Syncs changes from remote back to local (read-your-writes)
- **Manual sync**: Use `python sync_replica.py` to force a sync

## Manual Sync

Force a manual sync at any time:

```bash
python sync_replica.py
```

This is useful when:
- You want to fetch latest data from remote
- Another process has made changes to the remote database
- You're debugging sync issues

## Troubleshooting

### "libsql is required" Error

Install the package:
```bash
pip install libsql
```

### "TURSO_DATABASE_URL must be set" Error

Make sure you have these in your `.env`:
```bash
USE_EMBEDDED_REPLICA=YES
TURSO_DATABASE_URL=libsql://your-db.turso.io
TURSO_AUTH_TOKEN=your_token
```

### Replica File Doesn't Exist

The replica file will be created automatically on first connection. If you want to start fresh:

```bash
# Remove old replica file
rm trades_replica.db trades_replica.db-shm trades_replica.db-wal

# Run bot (will create new replica and sync)
python polyastra.py
```

### Sync is Slow

Sync speed depends on:
- Your internet connection to Turso
- Database size
- Number of changes since last sync

For large databases, the first sync may take longer. Subsequent syncs are incremental (only sync changes).

## Comparison: Database Modes

| Feature | Local SQLite | Turso (Remote) | Embedded Replica |
|---------|--------------|----------------|-------------------|
| Read speed | ⚡ Fast | 🐌 Network latency | ⚡ Fast |
| Write speed | ⚡ Fast | 🐌 Network latency | 🐌 Network latency |
| Works offline | ✅ Yes | ❌ No | ✅ Yes (cached) |
| Multi-user sync | ❌ No | ✅ Yes | ✅ Yes |
| Production ready | ❌ No | ✅ Yes | ⚠️ Dev only |
| Setup complexity | ✅ Simple | ⚠️ Credentials | ⚠️ Credentials |

## Configuration Reference

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `USE_EMBEDDED_REPLICA` | `NO` | Enable embedded replica mode |
| `TURSO_DATABASE_URL` | - | Turso database URL (required) |
| `TURSO_AUTH_TOKEN` | - | Turso auth token (required) |
| `EMBEDDED_REPLICA_FILE` | `trades_replica.db` | Local replica file path |

### Example Configurations

**Local Development (Recommended):**
```bash
USE_TURSO=NO
USE_EMBEDDED_REPLICA=YES
TURSO_DATABASE_URL=libsql://your-db.turso.io
TURSO_AUTH_TOKEN=your_token
```

**Production:**
```bash
USE_TURSO=YES
USE_EMBEDDED_REPLICA=NO
TURSO_DATABASE_URL=libsql://your-db.turso.io
TURSO_AUTH_TOKEN=your_token
```

**Local Development (No Sync):**
```bash
USE_TURSO=NO
USE_EMBEDDED_REPLICA=NO
# Uses local trades.db file
```

## Additional Resources

- [Turso Embedded Replicas Docs](https://docs.turso.tech/features/embedded-replicas/introduction)
- [libsql Python SDK](https://github.com/tursodatabase/libsql-client-py)
- [Turso Quickstart](https://docs.turso.tech/quickstart)

## Need Help?

If you encounter issues with embedded replicas:
1. Check this documentation
2. Review the logs for sync errors
3. Try manually syncing with `python sync_replica.py`
4. Check Turso status at [status.turso.tech](https://status.turso.tech)
