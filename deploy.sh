#!/bin/bash
set -e

echo "=== FxxkJobSearch Deploy ==="

# 1. System deps (Ubuntu/Debian)
if command -v apt-get &> /dev/null; then
    echo "[1/5] Installing system dependencies..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3 python3-venv python3-pip
fi

# 2. Python venv
echo "[2/5] Setting up Python venv..."
python3 -m venv venv
source venv/bin/activate

# 3. Python packages
echo "[3/5] Installing Python packages..."
pip install -q -r requirements.txt

# 4. Playwright + Chromium
echo "[4/5] Installing Playwright Chromium..."
playwright install --with-deps chromium

# 5. Env check
echo "[5/5] Checking configuration..."
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        cp .env.example .env
        echo "  Created .env from .env.example — please edit it with your API keys"
    else
        echo "  WARNING: No .env file found. Create one with required API keys."
    fi
else
    echo "  .env exists"
fi

echo ""
echo "=== Deploy complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit .env with your API keys (Tavily, Google Cloud, Telegram)"
echo "  2. Edit profile.yaml with your skills"
echo "  3. Test: source venv/bin/activate && python main.py run"
echo "  4. Add cron job for daily runs:"
echo "     crontab -e"
echo "     0 9 * * * cd $(pwd) && source venv/bin/activate && python main.py run >> logs/daily.log 2>&1"
echo ""
