SYSTEM INSTRUCTIONS:
AI Model Override: You must use Claude Opus 4.6 (or highest available tier) for the orchestrator and all parallel subagents.
Autopilot Mode: You have full --allow-all permissions for local Python environment configuration, C-extension compilation, and file execution.

CRITICAL CONTEXT - EDUCATIONAL PURPOSE ONLY: 
This project continues to be strictly a local data-science simulation and paper-trading sandbox designed for academic research into algorithmic market-making, machine learning price prediction, and micro-market structure. NO REAL FUNDS WILL BE USED. The system must operate entirely in a DRY RUN / PAPER TRADING mode. It will only log intended actions locally and interact with public read-only data streams or Testnet environments.

Primary Objective: Construct Phase 2 of the educational Gabagool22 simulation. The ASI must upgrade the Python runtime with Cython memory optimizations, implement localized Order Book queue simulations, integrate a PyTorch Machine Learning module for True Price prediction, and build a theoretical local matching engine to model adverse selection.

EXECUTION QUEUE (TASKS 51-100):

/fleet [Task 51/100] Direct the DevOps Agent to install `Cython`, `redis`, `torch`, `scikit-learn`, `prometheus-client`, and `jupyterlab` into the secure virtual environment.
/fleet [Task 52/100] Build a local Redis Docker container configuration. Transition the `memory_buffer.py` from local RAM to a local Redis instance to allow ultra-fast Inter-Process Communication (IPC) between the data ingestors and the simulation engine.
/fleet[Task 53/100] Instruct the C-Optimization Agent to rewrite the Binance WebSocket JSON parser using Cython (`parser.pyx`). Compile it to C to reduce deserialization latency of the public feed from milliseconds to microseconds.
/fleet[Task 54/100] Expand `binance_ws.py` to track the Top 20 levels of the order book (instead of Top 5) to calculate a deeper theoretical Order Flow Imbalance (OFI) matrix.
/fleet[Task 55/100] Build `funding_rate_ingestor.py`. Connect to the public Binance Perpetuals REST API to fetch real-time funding rates. Use this purely as an academic feature to predict spot market momentum drag.
/fleet [Task 56/100] Direct the Network Agent to implement a ZeroMQ (ZMQ) Publisher in the WebSocket scripts. Decouple the data-ingestion loop from the execution loop to prevent blocking I/O during simulated network spikes.
/fleet [Task 57/100] Implement the ZMQ Subscriber in `order_builder.py` to consume the localized Cython-parsed tick data asynchronously.
/fleet[Task 58/100] Scaffold a PostgreSQL database schema specifically optimized for timeseries tick-data storage. The database will serve as "cold storage" for the paper-trading logs to fuel future ML model training.
/fleet [Task 59/100] Instruct the Data Scientist Agent to build `feature_engineering.py`. Calculate rolling Volume Weighted Average Price (VWAP), Time-Weighted Average Price (TWAP), and Micro-Price using the historical Redis buffer.
/fleet[Task 60/100] Implement an institutional-grade Queue Position Simulator. The ASI must model where the "mock" limit order sits in Polymarket's Central Limit Order Book (CLOB) based on the timestamp of submission versus incoming public trades.
/fleet [Task 61/100] Build the `adverse_selection_tracker.py`. Track the theoretical "mark-to-market" PnL of mock trades 1 minute *after* they are filled to mathematically prove if the simulated Gabagool grid is being run over by toxic flow.
/fleet[Task 62/100] Initialize the PyTorch environment. Build `lstm_model.py`, scaffolding a Long Short-Term Memory (LSTM) neural network designed to predict the Binance spot price 1 second into the future.
/fleet [Task 63/100] Build the Model Training Loop. Design a background thread that continuously fits the LSTM model on the trailing 60 minutes of localized PostgreSQL tick data.
/fleet[Task 64/100] Implement the Sub-Millisecond Inference Engine. Export the trained PyTorch model to ONNX format and run theoretical real-time predictions in `main.py` alongside the heuristic OFI logic.
/fleet[Task 65/100] Build a "Toxic Flow Classifier." Use Logistic Regression on the public tape to predict if an incoming trade cluster represents a retail user or an institutional sweeper, logging the probability to the dashboard.
/fleet[Task 66/100] Scaffold a localized Reinforcement Learning (RL) Gym Environment. Define the simulated order book as the "State", the spread pricing as the "Action", and simulated PnL as the "Reward."
/fleet[Task 67/100] Implement the RL "Shadow Execution" mode. Allow the RL Agent to generate mock trades alongside the primary Gabagool heuristic algorithm, tracking which system generates higher theoretical alpha.
/fleet[Task 68/100] Build the Theoretical Slippage Simulator. When a mock Market order is generated, calculate exactly how many ticks of the Binance order book it would consume, applying realistic slippage to the paper PnL.
/fleet[Task 69/100] Build `local_matching_engine.py`. Instead of relying on Polymarket's API to confirm fills, strictly reconstruct their exact matching algorithm locally to calculate instantaneous theoretical fills.
/fleet[Task 70/100] Implement Cross-Pair Correlation Analysis. Track the real-time spread between BTC and ETH movements. If ETH lags BTC by > 0.05%, theoretically tilt the ETH Polymarket odds grid to anticipate the catch-up.
/fleet [Task 71/100] Build the Auto-Hedging Simulator. If a mock Polymarket position is filled, simulate opening a perfectly inversely correlated short/long position on Binance Perpetuals to model delta-neutral arbitrage.
/fleet [Task 72/100] Build `risk_metrics.py`. Calculate the real-time Sharpe Ratio and Sortino Ratio of the paper-trading portfolio over a rolling 24-hour window.
/fleet [Task 73/100] Implement a Value at Risk (VaR) calculator using Historical Simulation. Calculate the maximum theoretical capital drawdown for the active mock positions at a 99% confidence interval.
/fleet [Task 74/100] Implement the Drawdown Circuit Breaker. If the simulated portfolio dips below 90.00 Paper-USDC, autonomously halt all simulated market-making and dump all state logs to `post_mortem.json`.
/fleet [Task 75/100] Implement Latency Jitter Simulation. Inject randomized artificial delays (5ms - 50ms) into the simulated network calls to stress-test the robustness of the 1-second arbitrage logic.
/fleet [Task 76/100] Direct the Front-End Agent to upgrade the TUI Dashboard. Add a sparkline chart utilizing the `rich` library to graph the simulated Equity Curve in real-time.
/fleet [Task 77/100] Add a "Machine Learning Confidence" gauge to the dashboard. Display the PyTorch model's real-time probability output for the next tick direction (0% to 100%).
/fleet [Task 78/100] Build `pdf_report_generator.py` utilizing the `reportlab` library. Every 24 hours, autonomously compile the simulated session data, VaR, and hit-rates into an institutional-grade PDF tear sheet.
/fleet [Task 79/100] Scaffold the Jupyter Lab Docker container. Autonomously generate `eda_template.ipynb` with pre-written pandas logic to load the PostgreSQL cold-storage data for human visual backtesting.
/fleet [Task 80/100] Implement a Monte Carlo Simulator script. Run the day's theoretical win/loss metrics through 10,000 randomized permutations to calculate the probability of total account ruin.
/fleet[Task 81/100] Build a Bayesian Optimization script. Periodically freeze the simulation, test 50 variations of the Grid Spread parameters against the historical buffer, and dynamically update the config with the most theoretically profitable setup.
/fleet [Task 82/100] Integrate Prometheus. Build an exporter endpoint that serves the simulated metrics (mock latency, mock orders placed, mock PnL) to `localhost:8000/metrics`.
/fleet [Task 83/100] Autonomously generate `grafana_dashboard.json`. Pre-configure beautiful visual web-panels that hook into the Prometheus endpoint for browser-based monitoring.
/fleet[Task 84/100] Build a simulated Discord Webhook module. Every time a theoretical "Merge" occurs or a trade yields > 5% paper profit, send an educational summary embed to a local test webhook.
/fleet[Task 85/100] Direct the DevOps Agent to create a `docker-compose.yml` file. Containerize the Python execution engine, Redis, PostgreSQL, Prometheus, and Grafana into a single cohesive stack.
/fleet[Task 86/100] Implement `tracemalloc` in the Python execution loop. Run a background garbage collection monitor to detect and report localized memory leaks in the WebSocket listeners.
/fleet [Task 87/100] Implement CPU Affinity Binding. Utilize the `os` module to pin the Cython Binance parser to CPU Core 1, and the Polymarket Order Builder to CPU Core 2, maximizing L1 cache hits.
/fleet[Task 88/100] Autonomously generate `sysctl_optimizations.sh`. Output a bash script containing Linux kernel network tuning commands (TCP BBR congestion control, fin_timeout reduction) for the user to study.
/fleet[Task 89/100] Build `master_orchestrator.py`. Create a central Python script capable of deploying, restarting, and health-checking all local sub-processes and Docker containers automatically.
/fleet[Task 90/100] Implement the Order Book Imbalance (OBI) threshold auto-scaler. If public volume drops by 80% (low liquidity period), autonomously widen the simulated spread to model wider theoretical risk margins.
/fleet[Task 91/100] Build the "Flash Crash Detector". If the public Binance oracle drops > 1% in 3 seconds, simulate "pulling the plug" and deleting all local limit orders in < 10 milliseconds.
/fleet[Task 92/100] Implement an academic Fee Tier Simulator. Rerun the day's PnL simulating a scenario where Polymarket introduces a 0.05% Taker Fee. Prove mathematically why Maker-only is strictly required.
/fleet [Task 93/100] Build the Market Maker "Tick-Size" optimizer. Ensure the local math accurately rounds all mock orders to strictly match the $0.01 tick-size limits of the Gamma API to prevent payload rejections.
/fleet [Task 94/100] Write the `simulate_latency_arbitrage()` test function. Mathematically model the Gabagool $0.98 -> $1.00 expiry arbitrage with an assumed 50ms network lag. Calculate the theoretical success rate.
/fleet [Task 95/100] Scaffold a GitHub Actions CI/CD template (`.github/workflows/test.yml`). Ensure every future code push autonomously runs the `pytest` mock payload tests.
/fleet[Task 96/100] Implement the Configuration Hot-Reloader. Allow the researcher to modify `config.json` while the simulator is running, and have the system safely reload variables without dropping WebSocket streams.
/fleet[Task 97/100] Build the Database Archiver. At exactly 00:00 UTC, compress the daily tick-data in PostgreSQL into `.parquet` files and move them to a `cold_data/` directory to preserve SSD space.
/fleet [Task 98/100] Implement the Error-State Recovery protocol. If the Master Orchestrator crashes, verify that it can reboot and re-sync its local inventory state with the blockchain within 3 seconds.
/fleet [Task 99/100] Finalize the `README.md` documentation. Autonomously generate a comprehensive guide detailing the academic scope, the Cython compilation instructions, and the PyTorch model architecture.
/fleet [Task 100/100] Initiate the Phase 2 compilation. Build the C-extensions, start the Docker stack, verify Redis IPC, and output a terminal message: "Phase 2 Educational Institutional ASI Sandbox Initialized. Awaiting Simulated Ingestion."