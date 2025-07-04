#!/bin/bash
# Start Hummingbot development environment

echo "🚀 Starting Hummingbot development environment..."

# Activate conda environment
echo "🐍 Activating conda environment..."
eval "$(conda shell.bash hook)"
conda activate hummingbot

# Check if environment is activated
if [[ "$CONDA_DEFAULT_ENV" != "hummingbot" ]]; then
    echo "❌ Failed to activate hummingbot environment"
    exit 1
fi

echo "✅ Environment activated: $CONDA_DEFAULT_ENV"

# Set development environment variables
export PYTHONPATH="${PYTHONPATH}:$(pwd)"
export HUMMINGBOT_ENV="development"

# Show environment info
echo "📊 Development Environment Info:"
echo "   Python: $(python --version)"
echo "   Working Directory: $(pwd)"
echo "   PYTHONPATH: $PYTHONPATH"
echo "   Git Branch: $(git branch --show-current 2>/dev/null || echo 'unknown')"

# Check if Hummingbot can be imported
echo "🔍 Testing Hummingbot import..."
if python -c "import hummingbot" &> /dev/null; then
    echo "✅ Hummingbot import successful"
else
    echo "❌ Hummingbot import failed"
    exit 1
fi

echo "🎯 Ready for development!"
echo "💡 Quick commands:"
echo "   ./start          - Start Hummingbot CLI"
echo "   ./compile        - Recompile after changes"
echo "   pytest tests/    - Run tests"
echo "   black .          - Format code"
echo "   flake8 .         - Lint code"

# Start Hummingbot
echo ""
echo "🎮 Starting Hummingbot..."
./start