#!/bin/bash
# Setup script for development environment
# Downloads the RNNoise model for audio processing

set -e  # Exit on error

echo "🎵 Setting up RNNoise model for audio processing..."

# Create models directory if it doesn't exist
mkdir -p "$(dirname "$0")/models"

# Download RNNoise model
echo "📥 Downloading RNNoise model..."
curl -L -o "$(dirname "$0")/models/rnnoise-model.rnnn" \
  https://github.com/GregorR/rnnoise-models/raw/master/somnolent-hogwash-2018-09-01/sh.rnnn

# Verify download
if [ -f "$(dirname "$0")/models/rnnoise-model.rnnn" ]; then
    MODEL_SIZE=$(wc -c < "$(dirname "$0")/models/rnnoise-model.rnnn" | tr -d ' ')
    echo "✅ RNNoise model downloaded successfully!"
    echo "📊 Model size: $(numfmt --to=iec-i --suffix=B $MODEL_SIZE 2>/dev/null || echo "${MODEL_SIZE} bytes")"
else
    echo "❌ Failed to download RNNoise model"
    exit 1
fi

echo ""
echo "🚀 Setup complete! You can now run:"
echo "   docker compose -f docker-compose.dev.yml up --build -d"
