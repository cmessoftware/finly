#!/bin/bash
# Quick build script with automatic commit hash
# Usage: ./build.sh

echo "🔨 Building SmartFI with automatic version..."

# Get current commit hash
COMMIT_HASH=$(git log -1 --format=%h)
export COMMIT_HASH

echo "📌 Version: v1.2.0.$COMMIT_HASH"
echo ""

# Build and start
docker-compose build --no-cache
docker-compose up -d

echo ""
echo "✅ Build complete!"
echo "🌐 Access: http://localhost:3000"
echo "📋 Logs: docker-compose logs -f"
