# Polymarket Arbitrage Bot

**High-Frequency 5-Minute Portfolio Compounding System**

Zero-fee latency arbitrage for Polymarket's 5-minute crypto prediction markets with external Binance oracle validation.

> **EDUCATIONAL PURPOSE ONLY**: This is a paper-trading sandbox designed for academic research into algorithmic market-making, machine learning price prediction, and micro-market structure. NO REAL FUNDS WILL BE USED unless explicitly configured.

## Features

### Phase 1 - Core Trading Engine

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

### Phase 2 - Institutional ASI Sandbox

| Feature | Description |
|---------|-------------|
| **Cython Optimization** | C-compiled JSON parser for microsecond latency |
| **Redis IPC** | Ultra-fast inter-process communication buffer |
| **20-Level Order Book** | Deep OFI (Order Flow Imbalance) matrix |
| **ZeroMQ Messaging** | Decoupled async data ingestion |
| **PostgreSQL Cold Storage** | Timeseries tick-data for ML training |
| **LSTM Price Prediction** | PyTorch neural network for 1-second forecasts |
| **ONNX Inference** | Sub-millisecond model predictions |
| **Toxic Flow Classifier** | Logistic regression for trade flow analysis |
| **RL Gym Environment** | Reinforcement learning shadow trading |
| **Queue Position Simulator** | CLOB position modeling |
| **Local Matching Engine** | Theoretical instant fill calculation |
| **Risk Metrics** | Sharpe, Sortino, VaR (95%/99%) |
| **Monte Carlo Simulator** | 10,000-run ruin probability analysis |
| **Bayesian Optimization** | Auto-tuning grid parameters |
| **Prometheus Metrics** | Real-time monitoring endpoint |
| **Grafana Dashboards** | Pre-configured visualization panels |
| **Master Orchestrator** | Process management and health checks |
| **Docker Stack** | Redis, PostgreSQL, Prometheus, Grafana |

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
│   ├── main.py                    # Main orchestrator
│   ├── config.py                  # Secure configuration
│   │
│   │ # Phase 1 - Core Trading
│   ├── scanner.py                 # Market discovery
│   ├── oracle.py                  # Binance price feed
│   ├── binance_ws.py              # Extended WebSocket (20-level OB)
│   ├── sniper.py                  # Order book streaming
│   ├── arbitrage.py               # Trade logic
│   ├── executor.py                # Zero-fee execution
│   ├── portfolio.py               # Position management
│   ├── terminal_velocity.py       # T-60 WebSocket mode
│   ├── rate_limiter.py            # Token bucket
│   ├── ta_fallback.py             # Technical analysis
│   ├── security.py                # Security hardening
│   ├── dashboard.py               # TUI dashboard
│   │
│   │ # Phase 2 - Performance
│   ├── parser.pyx                 # Cython JSON parser
│   ├── setup_cython.py            # Cython build script
│   ├── memory_buffer.py           # Redis IPC buffer
│   ├── zmq_publisher.py           # ZMQ async messaging
│   ├── feature_engineering.py     # VWAP, TWAP, Micro-Price
│   │
│   │ # Phase 2 - Machine Learning
│   ├── lstm_model.py              # PyTorch LSTM predictor
│   ├── toxic_flow_classifier.py   # Logistic regression
│   ├── rl_gym_env.py              # RL environment
│   │
│   │ # Phase 2 - Simulation
│   ├── queue_position_simulator.py # CLOB queue modeling
│   ├── local_matching_engine.py   # Instant fill calculator
│   ├── slippage_simulator.py      # Order book consumption
│   ├── adverse_selection_tracker.py # Post-fill PnL tracking
│   ├── auto_hedger.py             # Delta-neutral hedging
│   ├── latency_simulator.py       # Jitter injection
│   │
│   │ # Phase 2 - Risk & Analytics
│   ├── risk_metrics.py            # Sharpe, Sortino, VaR
│   ├── monte_carlo.py             # Ruin probability
│   ├── bayesian_optimizer.py      # Parameter tuning
│   ├── correlation_analyzer.py    # Cross-pair analysis
│   ├── flash_crash_detector.py    # Circuit breaker
│   │
│   │ # Phase 2 - Data & Monitoring
│   ├── database_schema.py         # PostgreSQL schema
│   ├── database_archiver.py       # Parquet archival
│   ├── funding_rate_ingestor.py   # Perpetuals data
│   ├── prometheus_exporter.py     # Metrics endpoint
│   ├── pdf_report_generator.py    # Daily tearsheet
│   ├── discord_webhook.py         # Alert notifications
│   │
│   │ # Phase 2 - System
│   ├── master_orchestrator.py     # Process manager
│   ├── config_hot_reloader.py     # Live config updates
│   └── system_optimization.py     # CPU affinity, memory
│
├── tests/                         # Test suite
│   ├── conftest.py                # Pytest fixtures
│   ├── test_arbitrage.py
│   ├── test_grid_pricer.py
│   ├── test_portfolio.py
│   ├── test_rate_limiter.py
│   └── test_security.py
│
├── data/
│   ├── init_db.sql                # PostgreSQL schema
│   ├── prometheus.yml             # Prometheus config
│   ├── grafana_dashboard.json     # Grafana panels
│   └── grafana_datasources.yml    # Grafana sources
│
├── models/                        # Saved ML models
├── notebooks/
│   └── eda_template.ipynb         # Analysis notebook
│
├── scripts/
│   └── sysctl_optimizations.sh    # Kernel tuning
│
├── logs/                          # Application logs
├── docker-compose.yml             # Docker stack
├── requirements.txt               # Python dependencies
├── .github/workflows/test.yml     # CI/CD pipeline
├── run.bat                        # Windows launcher
└── run.sh                         # Unix launcher
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

---

## Phase 2 Architecture

```
+------------------------------------------------------------------+
|                    MASTER ORCHESTRATOR                           |
|                  (master_orchestrator.py)                        |
+--------+--------+--------+--------+--------+--------+------------+
|        |        |        |        |        |        |            |
v        v        v        v        v        v        v            v
+------+ +------+ +------+ +------+ +------+ +------+ +-----------+
|Binance|→|Cython|→| ZMQ  |→|Redis |→|Feature|→| LSTM |→|   Main   |
|  WS   | |Parser| | Pub  | | IPC  | |Engine | |Model | |Execution |
+------+ +------+ +------+ +------+ +------+ +------+ +-----------+
                                                            |
    +-------------------------------------------------------+
    |                                                       |
    v                                                       v
+---------------+  +---------------+  +---------------+  +--------+
|  Queue Pos    |  |   Adverse     |  |    Local      |  | Order  |
|  Simulator    |  |  Selection    |  |   Matching    |  | Builder|
+---------------+  +---------------+  +---------------+  +--------+
                                                            |
                                                            v
+------------------------------------------------------------------+
|                     DOCKER INFRASTRUCTURE                        |
+--------+--------+-----------+--------+---------------------------+
| Redis  |Postgres| Prometheus|Grafana |       JupyterLab          |
| (IPC)  |(Cold)  | (Metrics) |(Viz)   |       (Analysis)          |
+--------+--------+-----------+--------+---------------------------+
```

## Phase 2 Quick Start

### 1. Start Docker Infrastructure

```bash
# Start Redis, PostgreSQL, Prometheus, Grafana, JupyterLab
docker-compose up -d
```

### 2. Compile Cython Parser

```bash
cd src
python setup_cython.py build_ext --inplace
```

### 3. Install Python Dependencies

```bash
pip install -r requirements.txt
```

### 4. Run the Simulation

```bash
# Windows
run.bat

# Unix/Mac
./run.sh
```

## Docker Services

| Service | Port | Credentials | Description |
|---------|------|-------------|-------------|
| Redis | 6379 | - | IPC buffer for tick data |
| PostgreSQL | 5432 | polym/polym_dev | Cold storage database |
| Prometheus | 9090 | - | Metrics collection |
| Grafana | 3000 | admin/polym_admin | Dashboard visualization |
| JupyterLab | 8888 | Token: polym_dev | EDA and backtesting |

## Cython Compilation

The Binance WebSocket JSON parser is optimized using Cython for sub-millisecond deserialization:

```bash
cd src

# Build the C extension
python setup_cython.py build_ext --inplace

# Verify compilation
python -c "from parser import parse_ticker_fast; print('Cython parser loaded!')"
```

**Note**: If Cython compilation fails, the system automatically falls back to the pure Python parser.

## PyTorch LSTM Model

### Architecture

```
Input (60 ticks × 8 features)
         ↓
LSTM Layer 1 (64 hidden units)
         ↓
LSTM Layer 2 (64 hidden units, dropout=0.2)
         ↓
Fully Connected (64 → 32)
         ↓
ReLU + Dropout
         ↓
Output (1 = predicted % change)
```

### Input Features

1. Price deviation from mid
2. Spread (basis points)
3. Volume (normalized)
4. Order Flow Imbalance (-1 to 1)
5. VWAP deviation
6. Volatility
7. Time of day (sin encoding)
8. Time of day (cos encoding)

### Training

The model continuously trains on the trailing 60 minutes of tick data:

```python
from lstm_model import create_lstm_predictor

predictor = create_lstm_predictor(symbols=['BTC', 'ETH'])

# Add ticks
predictor.add_tick('BTC', price=65000, bid=64999, ask=65001, volume=1.5, ofi=0.2)

# Get prediction
result = predictor.predict('BTC')
print(f"Predicted direction: {result.direction}, Confidence: {result.confidence:.2%}")
```

### ONNX Export

Export models for production inference:

```python
predictor.export_to_onnx('BTC', 'models/lstm_BTC.onnx')
```

## Risk Management

### Metrics Calculated

- **Sharpe Ratio**: Risk-adjusted return (annualized)
- **Sortino Ratio**: Downside risk-adjusted return
- **Max Drawdown**: Largest peak-to-trough decline
- **VaR (95%/99%)**: Value at Risk using historical simulation
- **Win Rate**: Percentage of profitable trades

### Circuit Breakers

| Trigger | Action |
|---------|--------|
| Balance < 90 USDC | Halt all trading, dump state to `post_mortem.json` |
| Flash crash (>1% in 3s) | Cancel all orders in <10ms |
| Low liquidity (volume -80%) | Auto-widen spreads |

## Monitoring

### Prometheus Metrics

Access metrics at `http://localhost:8000/metrics`:

```
polym_paper_pnl_total{symbol="BTC"} 42.50
polym_orders_placed_total{symbol="BTC"} 150
polym_latency_ms{quantile="0.99"} 12.5
polym_ofi_current{symbol="BTC",depth="5"} 0.35
polym_ml_confidence{symbol="BTC"} 0.82
```

### Grafana Dashboards

Pre-configured panels at `http://localhost:3000`:

- Paper PnL Equity Curve
- Order Latency Histogram
- OFI Heatmap (20-level depth)
- ML Prediction Confidence
- Risk Metrics Summary

## System Optimization

### Linux Kernel Tuning

Run the optimization script (requires sudo):

```bash
sudo ./scripts/sysctl_optimizations.sh
```

This configures:
- TCP BBR congestion control
- Reduced fin_timeout
- Increased socket buffers
- Disabled TCP slow start after idle
- Reduced swappiness

### CPU Affinity

The system pins processes to specific CPU cores:
- Core 1: Cython Binance parser
- Core 2: Polymarket Order Builder
- Core 3+: ML inference

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ -v --cov=src --cov-report=html

# Run mock payload tests only
pytest tests/ -v -k "mock"
```

## CI/CD

GitHub Actions automatically runs on push:
- Unit tests (Python 3.10, 3.11, 3.12)
- Type checking (mypy)
- Security scan (safety)
- Coverage upload (Codecov)

## License

MIT License - Use at your own risk.
