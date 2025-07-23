#!/bin/bash

# Titanic Umbrel component deployment script for production

set -e

echo "🚢 Deploying Titanic Umbrel Component..."

# Check if .env file exists
if [ ! -f .env ]; then
    echo "❌ .env file not found!"
    echo "Please create a .env file by copying the example:"
    echo "cp env.example .env"
    echo ""
    echo "Then edit the .env file with your Firebase Project ID:"
    echo "FIREBASE_PROJECT_ID=your-firebase-project-id"
    exit 1
fi

# Load environment variables
source .env

# Check required environment variables
if [ -z "$FIREBASE_PROJECT_ID" ]; then
    echo "❌ FIREBASE_PROJECT_ID is required in .env file"
    exit 1
fi

echo "✅ Environment variables loaded"

# Stop existing container if running
echo "🛑 Stopping existing container..."
docker compose -f docker-compose.prod.yml down 2>/dev/null || true

# Build and start the container
echo "🔨 Building and starting container..."
docker compose -f docker-compose.prod.yml up -d --build

# Wait for container to be healthy
echo "⏳ Waiting for container to be healthy..."
timeout=60
counter=0

while [ $counter -lt $timeout ]; do
    if docker compose -f docker-compose.prod.yml ps | grep -q "healthy"; then
        echo "✅ Container is healthy!"
        break
    fi
    
    echo "⏳ Waiting for health check... ($counter/$timeout)"
    sleep 5
    counter=$((counter + 5))
done

if [ $counter -eq $timeout ]; then
    echo "❌ Container failed to become healthy within $timeout seconds"
    echo "📋 Container logs:"
    docker compose -f docker-compose.prod.yml logs
    exit 1
fi

# Test the health endpoint
echo "🧪 Testing health endpoint..."
if curl -f http://localhost:3029/health > /dev/null 2>&1; then
    echo "✅ Health check passed!"
else
    echo "❌ Health check failed!"
    exit 1
fi

echo "🎉 Titanic Umbrel Component deployed successfully!"
echo ""
echo "📊 Container status:"
docker compose -f docker-compose.prod.yml ps
echo ""
echo "📋 Logs:"
docker compose -f docker-compose.prod.yml logs --tail=10
echo ""
echo "🌐 Server is running on http://localhost:3029"
echo "📁 Media is stored in your Umbrel's downloads directory."
echo ""
echo "To view logs: docker compose -f docker-compose.prod.yml logs -f"
echo "To stop: docker compose -f docker-compose.prod.yml down" 