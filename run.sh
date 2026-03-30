#!/bin/bash
#
# POLYMARKET ARBITRAGE BOT - One-Click Deployment
# High-Frequency 5-Minute Portfolio Compounding System
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Change to script directory
cd "$(dirname "$0")"

echo
echo "============================================================"
echo "  POLYMARKET ARBITRAGE BOT - One-Click Deployment"
echo "============================================================"
echo

# Function to print status
ok() {
    echo -e "${GREEN}[OK]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

# Check Python installation
echo "Checking Python installation..."
if ! command -v python3 &> /dev/null; then
    error "Python 3 not found in PATH."
    echo "Please install Python 3.10+ using your package manager:"
    echo "  Ubuntu/Debian: sudo apt install python3 python3-venv python3-pip"
    echo "  macOS:         brew install python@3.11"
    echo "  Fedora:        sudo dnf install python3"
    exit 1
fi

# Check Python version
PYVER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PYMAJOR=$(echo "$PYVER" | cut -d. -f1)
PYMINOR=$(echo "$PYVER" | cut -d. -f2)

if [ "$PYMAJOR" -lt 3 ] || ([ "$PYMAJOR" -eq 3 ] && [ "$PYMINOR" -lt 10 ]); then
    error "Python 3.10+ required. Found Python $PYVER"
    exit 1
fi
ok "Python $PYVER detected"

# Create virtual environment if needed
if [ ! -d "venv" ]; then
    echo
    echo "Creating virtual environment..."
    python3 -m venv venv
    if [ $? -ne 0 ]; then
        error "Failed to create virtual environment"
        echo "Try: sudo apt install python3-venv"
        exit 1
    fi
    ok "Virtual environment created"
fi

# Activate virtual environment
echo
echo "Activating virtual environment..."
source venv/bin/activate
if [ $? -ne 0 ]; then
    error "Failed to activate virtual environment"
    exit 1
fi
ok "Virtual environment activated"

# Upgrade pip
echo
echo "Upgrading pip..."
python -m pip install --upgrade pip --quiet

# Install dependencies
echo
echo "Installing dependencies..."
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt --quiet
    if [ $? -ne 0 ]; then
        error "Failed to install dependencies"
        echo "Try running: pip install -r requirements.txt"
        exit 1
    fi
    ok "Dependencies installed"
else
    warn "requirements.txt not found"
fi

# Check .env file
echo
if [ ! -f ".env" ]; then
    echo "============================================================"
    warn ".env file not found!"
    echo "============================================================"
    echo
    echo "The bot requires API credentials to run."
    echo
    
    if [ -f ".env.example" ]; then
        echo "Creating .env from .env.example..."
        cp .env.example .env
        echo
        echo "Please edit .env and fill in your credentials:"
        echo "  - PRIVATE_KEY: Your Polygon wallet private key"
        echo "  - CLOB_API_KEY: Your Polymarket API key"
        echo "  - CLOB_SECRET: Your Polymarket API secret"
        echo "  - CLOB_PASSPHRASE: Your Polymarket passphrase"
        echo
        
        # Try to open editor
        if [ -n "$EDITOR" ]; then
            echo "Opening .env in $EDITOR..."
            $EDITOR .env
        elif command -v nano &> /dev/null; then
            echo "Opening .env in nano..."
            nano .env
        elif command -v vim &> /dev/null; then
            echo "Opening .env in vim..."
            vim .env
        else
            echo "Please edit .env manually and run this script again."
        fi
    else
        error ".env.example not found. Cannot create .env file."
    fi
    exit 1
fi
ok ".env file found"

# Validate required .env fields
echo
echo "Validating configuration..."
if ! grep -q "PRIVATE_KEY=" .env; then
    error "PRIVATE_KEY not found in .env"
    exit 1
fi

# Check if key is actually set (not just the variable name)
if grep -q "PRIVATE_KEY=$" .env || grep -q 'PRIVATE_KEY=""' .env; then
    error "PRIVATE_KEY is empty in .env"
    exit 1
fi

# Check for CLOB credentials
if ! grep -q "CLOB_API_KEY=" .env; then
    warn "CLOB_API_KEY not found. Run: python3 src/derive_creds.py"
fi

ok "Configuration validated"

# Create necessary directories
echo
echo "Creating directories..."
mkdir -p logs data
ok "Directories ready"

# Run USDC approval check
echo
echo "============================================================"
echo "  Checking USDC Approvals"
echo "============================================================"
echo

if [ -f "src/approve.py" ]; then
    python src/approve.py
    if [ $? -ne 0 ]; then
        echo
        error "Approval check failed."
        echo "Please ensure you have USDC in your wallet and try again."
        exit 1
    fi
else
    warn "approve.py not found, skipping approval check"
fi

# Display startup info
echo
echo "============================================================"
echo "  Starting Bot"
echo "============================================================"
echo
echo "  Mode: Production"
echo "  Logs: logs/bot.log"
echo "  Press Ctrl+C to stop"
echo
echo "============================================================"
echo

# Handle signals for graceful shutdown
trap 'echo; echo "Shutting down..."; exit 0' INT TERM

# Start the bot
python src/main.py

# Handle exit
echo
echo "Bot stopped."
