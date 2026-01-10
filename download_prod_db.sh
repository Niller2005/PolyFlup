#!/bin/bash
# Download production database from server
# Usage: ./download_prod_db.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Downloading production database..."
scp root@95.217.40.183:/root/polyflup/trades.db "$SCRIPT_DIR/trades.db"

if [ $? -eq 0 ]; then
    echo ""
    echo "✅ Database downloaded successfully to $SCRIPT_DIR/trades.db"
else
    echo ""
    echo "❌ Failed to download database"
    exit 1
fi
