#!/bin/bash

# Titanic Umbrel Component - Development Setup for macOS

echo "🚢 Setting up Titanic Umbrel Component for development..."

# Check if .env file exists
if [ ! -f .env ]; then
    echo "📝 Creating .env file from example..."
    cp env.example .env
    echo "✅ .env file created!"
    echo "📝 Please edit .env file with your Firebase Project ID"
else
    echo "✅ .env file already exists"
fi

# Create media directory
if [ ! -d "media" ]; then
    echo "📁 Creating media directory..."
    mkdir -p media
    echo "✅ Media directory created!"
else
    echo "✅ Media directory already exists"
fi

# Check if Rust is installed
if ! command -v cargo &> /dev/null; then
    echo "❌ Rust is not installed!"
    echo "Please install Rust from https://rustup.rs/"
    exit 1
fi

echo "🔧 Installing dependencies..."
cargo build

echo ""
echo "🎉 Development setup complete!"
echo ""
echo "To run the server:"
echo "  cargo run"
echo ""
echo "To test the health endpoint:"
echo "  curl http://localhost:3000/health"
echo ""
echo "Remember to edit .env with your Firebase Project ID!" 