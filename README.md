# Polymarket Arbitrage Bot

**High-Frequency 5-Minute Portfolio Compounding System**

Zero-fee latency arbitrage for Polymarket's 5-minute crypto prediction markets with external Binance oracle validation.

## Features

| Feature | Description |
|---------|-------------|
| **5-Minute Market Scanner** | Auto-discovers crypto 5-minute prediction markets |
| **Binance Oracle** | Real-time price validation via WebSocket |
| **Multi-Currency Support** | BTC, ETH, SOL, XRP, DOGE, BNB price feeds |
| **Terminal Velocity Mode** | High-speed WebSocket mode at T-60 seconds |
| **High-Frequency Sniper** | Microsecond-accurate order book streaming |
| **Zero-Fee Execution** | Gasless transactions via Polygon relayer |
| **Post-Sale Cooldown** | 10-second protocol after market resolution |
| **Portfolio Compounding** | 5% per trade, auto-scaling with wins |
| **TA Fallback** | RSI-based momentum detection for early entries |
| **Rate Limiting** | Token bucket with automatic cooldown periods |
| **Security Hardening** | Encrypted keys, rate limiting, audit logging |
| **DRY_RUN Mode** | Safe simulation without real trades |

## Architecture

```
+-------------------------------------------------------------+
|                      ORCHESTRATOR                           |
|                       (main.py)                             |
+----------+----------+----------+----------+-----------------+
| Scanner  | Oracle   | Sniper   | Arbiter  | Terminal        |
| (Gamma)  | (Binance)| (WS L1)  | (Logic)  | Velocity        |
+----------+----------+----------+----------+-----------------+
                           |
                           v
+-------------------------------------------------------------+
|              py-clob-client (Gasless Execution)             |
|              Polygon RPC (Free Public Endpoints)            |
+-------------------------------------------------------------+
```

## Quick Start (One-Click Deployment)

### Prerequisites
- Python 3.10+
- Polygon wallet with USDC
- Polymarket API credentials

### Setup

1. **Clone and enter directory**
   ```bash
   cd polym
   ```

2. **Configure credentials**
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   ```

3. **Run** (Windows)
   ```batch
   run.bat
   ```

   **Run** (Unix/Mac)
   ```bash
   chmod +x run.sh
   ./run.sh
   ```

The script will:
- Create virtual environment
- Install dependencies
- Validate configuration
- Check/approve USDC allowances
- Start the bot

## Configuration

Edit `.env` file:

```env
# Required
PRIVATE_KEY=<your-64-char-hex-key>
CLOB_API_KEY=<from-polymarket-profile>
CLOB_SECRET=<from-polymarket-profile>
CLOB_PASSPHRASE=<from-polymarket-profile>

# Trading Mode (start with dry_run!)
TRADING_MODE=dry_run   # dry_run | live_test | autonomous

# Risk Management
STARTING_CAPITAL=100.00
TRADE_ALLOCATION_PCT=0.05  # 5% per trade
MAX_ENTRY_PRICE=0.99
DAILY_LOSS_LIMIT=10.00
```

## Trading Modes

| Mode | Description | Size |
|------|-------------|------|
| `dry_run` | Simulated trades, no real money | 5% simulated |
| `live_test` | Real trades, fixed size | $1.00 per trade |
| `autonomous` | Full auto-compounding | 5% of balance |

**Progression**: `dry_run` → `live_test` → (3 wins) → `autonomous`

## Arbitrage Logic

The bot executes Fill-or-Kill orders at $0.99 ONLY when ALL conditions are met:

1. **Time**: Market resolving in <=1 second
2. **Oracle**: Binance price confirms direction
3. **Price**: Best ask available below $0.99

Expected profit per trade: ~$0.01 per $0.99 (1.01% ROI)

## Security Features

- Private keys loaded from environment only
- Sensitive data redacted from all logs
- Rate limiting on API calls with token bucket
- Pre-signing transaction verification
- Tamper-evident audit logging with bounded memory
- Daily loss limit enforcement

## Project Structure

```
polym/
├── src/
│   ├── main.py              # Orchestrator
│   ├── config.py            # Secure configuration
│   ├── scanner.py           # Market discovery
│   ├── oracle.py            # Binance price feed (multi-currency)
│   ├── sniper.py            # Order book streaming
│   ├── arbitrage.py         # Trade logic
│   ├── executor.py          # Zero-fee execution
│   ├── portfolio.py         # Position management
│   ├── terminal_velocity.py # T-60 WebSocket ignition
│   ├── rate_limiter.py      # Token bucket rate limiting
│   ├── ta_fallback.py       # Technical analysis
│   ├── security.py          # Security hardening
│   ├── dashboard.py         # Real-time status display
│   └── approve.py           # USDC approval script
├── tests/                   # Test suite
├── data/                    # Heuristics & state
├── logs/                    # Application logs
├── .env.example             # Config template
├── requirements.txt         # Dependencies
├── run.bat                  # Windows launcher
└── run.sh                   # Unix launcher
```

## Recent Fixes & Improvements

### Windows Compatibility
- **Unicode/Emoji Fix**: All emoji characters replaced with ASCII-safe alternatives for Windows terminal compatibility

### WebSocket Stability
- **Binance WebSocket Fix**: Corrected combined streams URL format for multi-currency feeds
- **JSON Parsing Fix**: Improved handling of non-JSON WebSocket messages in sniper
- **Protected WebSocket Close**: Safe close operations prevent errors on shutdown

### Bug Fixes
- Fixed Oracle API usage with correct symbol format (e.g., BTCUSDT)
- Fixed ArbitrageOpportunity attribute access in trade execution
- Fixed executor method call (execute_fok_order)
- Added Oracle close() method for clean shutdown
- Fixed potential deadlock in market expiry handling
- Bounded memory growth in security audit logger
- Improved error handling in scanner and sniper modules

## Disclaimer

This software is for educational purposes. Trading involves risk of loss. The authors are not responsible for any financial losses. Always start with `dry_run` mode and small amounts.

## License

MIT License - Use at your own risk.
