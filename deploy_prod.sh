#!/bin/bash
# Deploy PolyAstra to production
# Usage: ./deploy_prod.sh [--logs]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FOLLOW_LOGS=false

# Parse arguments
if [ "$1" == "--logs" ] || [ "$1" == "-l" ]; then
    FOLLOW_LOGS=true
fi

echo "🔄 Starting production deployment..."
echo ""

# Pull latest code
echo "📥 Pulling latest code from git..."
git pull
if [ $? -ne 0 ]; then
    echo "❌ Git pull failed"
    exit 1
fi
echo ""

# Build and deploy with docker-compose
echo "🐳 Building and starting containers..."
docker-compose up -d --build
if [ $? -ne 0 ]; then
    echo "❌ Docker compose failed"
    exit 1
fi
echo ""

echo "✅ Deployment complete!"
echo ""

# Follow logs if requested
if [ "$FOLLOW_LOGS" = true ]; then
    echo "📊 Following bot logs (Ctrl+C to exit)..."
    echo ""
    docker logs -f polyastra-bot
else
    echo "💡 Tip: Run with --logs flag to follow bot logs after deployment"
    echo "   ./deploy_prod.sh --logs"
fi
