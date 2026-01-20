#!/bin/bash
# Deploy PolyFlup to production
# Usage: ./deploy_prod.sh [--pull] [--logs]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PULL_CODE=false
FOLLOW_LOGS=false

# Parse arguments
for arg in "$@"; do
    case $arg in
        --logs|-l)
            FOLLOW_LOGS=true
            ;;
        --pull|-p)
            PULL_CODE=true
            ;;
    esac
done

echo "🔄 Starting production deployment..."
echo ""

# Pull latest code if requested
if [ "$PULL_CODE" = true ]; then
    echo "📥 Pulling latest code from git..."
    git pull
    if [ $? -ne 0 ]; then
        echo "❌ Git pull failed"
        exit 1
    fi
    echo ""
fi

# Get git version info for build
BRANCH=$(git rev-parse --abbrev-ref HEAD)
COMMIT=$(git rev-parse --short HEAD)
export GIT_VERSION="${BRANCH}@${COMMIT}"
echo "🔧 Building version: ${GIT_VERSION}"
echo ""

# Build and deploy with docker-compose
echo "🐳 Building and starting containers..."
docker compose up -d --build
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
    docker logs -f polyflup-bot
else
    echo "💡 Tips:"
    echo "   ./deploy_prod.sh --pull         (pull latest code before deploying)"
    echo "   ./deploy_prod.sh --logs         (follow logs after deployment)"
    echo "   ./deploy_prod.sh --pull --logs  (pull, deploy, and follow logs)"
fi
