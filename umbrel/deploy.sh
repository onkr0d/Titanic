#!/bin/bash

# Titanic Umbrel Component Deployment Script

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

# Create media directory if it doesn't exist
if [ ! -d "media" ]; then
    echo "📁 Creating media directory..."
    mkdir -p media
fi

# Stop existing container if running
echo "🛑 Stopping existing container..."
docker compose down 2>/dev/null || true

# Build and start the container
echo "🔨 Building and starting container..."
docker compose up -d --build

# Wait for container to be healthy
echo "⏳ Waiting for container to be healthy..."
timeout=60
counter=0

while [ $counter -lt $timeout ]; do
    if docker compose ps | grep -q "healthy"; then
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
    docker compose logs
    exit 1
fi

# Test the health endpoint
echo "🧪 Testing health endpoint..."
if curl -f http://localhost:3000/health > /dev/null 2>&1; then
    echo "✅ Health check passed!"
else
    echo "❌ Health check failed!"
    exit 1
fi

echo "🎉 Titanic Umbrel Component deployed successfully!"
echo ""
echo "📊 Container status:"
docker compose ps
echo ""
echo "📋 Logs:"
docker compose logs --tail=10
echo ""
echo "🌐 Server is running on http://localhost:3000"
echo "📁 Media directory: ./media"
echo ""
echo "To view logs: docker compose logs -f"
echo "To stop: docker compose down" 