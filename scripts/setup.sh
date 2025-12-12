#!/bin/bash
# Setup script for Agent Workflow Engine

set -e

echo "🚀 Agent Workflow Engine - Setup Script"
echo "========================================"

# Check Python version
python_version=$(python3 --version 2>&1 | awk '{print $2}')
echo "✓ Python version: $python_version"

# Create virtual environment
if [ ! -d ".venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv .venv
else
    echo "✓ Virtual environment already exists"
fi

# Activate virtual environment
source .venv/bin/activate || . .venv/Scripts/activate
echo "✓ Virtual environment activated"

# Upgrade pip
echo "🔄 Upgrading pip..."
pip install --upgrade pip setuptools wheel

# Install dependencies
echo "📥 Installing dependencies..."
pip install -r requirements.txt

echo ""
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "1. Activate virtual environment:"
echo "   source .venv/bin/activate  (macOS/Linux)"
echo "   .venv\\Scripts\\activate     (Windows)"
echo ""
echo "2. Run tests:"
echo "   pytest tests/ -v"
echo ""
echo "3. Start the server:"
echo "   python main.py"
echo ""
