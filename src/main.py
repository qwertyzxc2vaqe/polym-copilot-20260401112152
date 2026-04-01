#!/usr/bin/env python3
"""
Main Orchestrator for Polymarket Educational Sandbox.

Implements asyncio.gather for concurrent BTC/ETH simulation.
High-Frequency 5-Minute Portfolio Compounding System

Phase 2D: Multi-Currency Async Orchestration
- Independent per-asset trading loops (BTC/ETH run concurrently)
- Fault isolation: one asset crash doesn't affect others
- Per-asset pause/resume functionality
- Dashboard and terminal velocity integration
"""

import asyncio
import os
import signal
import sys
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Callable
from enum import Enum

# Import all modules
from config import get_config, ConfigurationError
from scanner import MarketScanner, Market5Min
from oracle import BinanceOracle
from sniper import PolymarketSniper
from arbitrage import ArbitrageEngine, ArbitrageOpportunity
from executor import ZeroFeeExecutor
from ta_fallback import TechnicalAnalyzer
from security import SecurityContext
from terminal_velocity import TerminalVelocityController, TerminalPhase
from dashboard import Dashboard

try:
    from ofi_engine import OFIEngine, create_ofi_engine, OFISignal
    HAS_OFI_ENGINE = True
except ImportError:
    HAS_OFI_ENGINE = False

logger = logging.getLogger(__name__)


# ============================================================================
# ENUMS AND DATA CLASSES
# ============================================================================

class ScanningPhase(Enum):
    """Current phase of the scanning loop for an asset."""
    IDLE = "idle"                    # Not actively scanning
    SCANNING = "scanning"            # Scanning for markets
    ANALYZING = "analyzing"          # Analyzing opportunities
    EXECUTING = "executing"          # Executing a trade
    WAITING = "waiting"              # Waiting for next scan interval
    PAUSED = "paused"                # Temporarily paused
    ERROR = "error"                  # In error state


@dataclass
class AssetState:
    """Tracks the current state of scanning/trading for a specific asset."""
    asset: str
    phase: ScanningPhase = ScanningPhase.IDLE
    active_market: Optional[Market5Min] = None
    last_scan: Optional[datetime] = None
    is_paused: bool = False
    pause_until: Optional[datetime] = None
    # Additional tracking
    total_scans: int = 0
    total_trades: int = 0
    total_profit: float = 0.0
    last_error: Optional[str] = None
    error_count: int = 0
    consecutive_errors: int = 0


# ============================================================================
# ASYNC ORCHESTRATION SERVICES (for asyncio.gather)
# ============================================================================

async def run_oracle_service(oracle: BinanceOracle) -> None:
    """Run Binance oracle WebSocket connection continuously.
    
    Args:
        oracle: BinanceOracle instance to run
    """
    try:
        logger.info("[ORACLE] Starting Binance price stream...")
        await oracle.connect()
    except asyncio.CancelledError:
        logger.info("[ORACLE] Cancelled")
        await oracle.close()
        raise
    except Exception as e:
        logger.error(f"[ORACLE] Fatal error: {e}")
        raise


async def run_sniper_service(sniper: PolymarketSniper) -> None:
    """Run Polymarket sniper WebSocket connection continuously.
    
    Args:
        sniper: PolymarketSniper instance to run
    """
    try:
        logger.info("[SNIPER] Starting Polymarket order book streaming...")
        await sniper.run()
    except asyncio.CancelledError:
        logger.info("[SNIPER] Cancelled")
        await sniper.close()
        raise
    except Exception as e:
        logger.error(f"[SNIPER] Fatal error: {e}")
        raise


async def run_ta_service(ta: TechnicalAnalyzer, on_signal: Callable) -> None:
    """Run technical analysis background service.
    
    Args:
        ta: TechnicalAnalyzer instance
        on_signal: Callback function when TA signal occurs
    """
    try:
        logger.info("[TA] Starting technical analysis background service...")
        await ta.run_background_analysis(on_signal)
    except asyncio.CancelledError:
        logger.info("[TA] Cancelled")
        raise
    except Exception as e:
        logger.error(f"[TA] Fatal error: {e}")
        raise


async def run_dashboard_service(dashboard: Dashboard) -> None:
    """Run the TUI dashboard.
    
    Args:
        dashboard: Dashboard instance to run
    """
    try:
        logger.info("[DASHBOARD] Starting live dashboard...")
        await dashboard.run()
    except asyncio.CancelledError:
        logger.info("[DASHBOARD] Cancelled")
        try:
            await dashboard.stop()
        except:
            pass
        raise
    except Exception as e:
        logger.error(f"[DASHBOARD] Fatal error: {e}")
        raise


async def run_terminal_velocity_service(terminal_velocity: TerminalVelocityController) -> None:
    """Run the Terminal Velocity controller for T-60 WebSocket ignition.
    
    Args:
        terminal_velocity: TerminalVelocityController instance
    """
    try:
        logger.info("[BOLT] Starting Terminal Velocity controller...")
        # Controller runs in the background handling market transitions
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        logger.info("[BOLT] Cancelled")
        try:
            await terminal_velocity.stop()
        except:
            pass
        raise
    except Exception as e:
        logger.error(f"[BOLT] Fatal error: {e}")
        raise


async def run_ofi_engine_service(assets: List[str]) -> None:
    """Run the OFI (Order Flow Imbalance) Engine if available.
    
    Args:
        assets: List of assets to monitor (e.g., ["btcusdt", "ethusdt"])
    """
    if not HAS_OFI_ENGINE:
        logger.info("[OFI] OFI Engine not available, skipping")
        await asyncio.sleep(999999)  # Run indefinitely but don't crash
        return
    
    try:
        logger.info("[OFI] Starting Order Flow Imbalance engine...")
        ofi_engine = await create_ofi_engine(assets)
        await ofi_engine.run()
    except asyncio.CancelledError:
        logger.info("[OFI] Cancelled")
        raise
    except Exception as e:
        logger.error(f"[OFI] Fatal error: {e}")
        raise


async def run_stats_reporter(orchestrator: 'Orchestrator') -> None:
    """Report trading statistics every minute.
    
    Args:
        orchestrator: Parent orchestrator instance
    """
    CRYPTO_SYMBOLS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB"]
    
    while orchestrator._running:
        try:
            await asyncio.sleep(60)
            
            print("\n" + "-" * 40)
            print(f"  [STATS] @ {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")
            print("-" * 40)
            
            for symbol in CRYPTO_SYMBOLS:
                price_data = orchestrator._oracle.get_price(symbol)
                if price_data:
                    print(f"  {symbol}: ${price_data.price:,.2f}")
                else:
                    print(f"  {symbol}: N/A")
            
            print(f"  Trades: {orchestrator._total_trades}")
            print(f"  Profit: ${orchestrator._total_profit:.2f}")
            print("-" * 40 + "\n")
            
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"Stats reporter error: {e}")


async def run_btc_simulation(
    orchestrator: 'Orchestrator',
    scanner: MarketScanner,
    arbitrage: ArbitrageEngine,
    executor: ZeroFeeExecutor,
) -> None:
    """
    BTC market simulation loop.
    
    Independent trading loop for BTC markets that:
    - Scans for BTC 5-minute markets every 30 seconds
    - Analyzes for arbitrage opportunities
    - Executes profitable trades
    - Handles T-60 terminal velocity transitions
    - Supports dynamic pause/resume
    
    Args:
        orchestrator: Parent orchestrator for state management
        scanner: Market scanner instance
        arbitrage: Arbitrage analysis engine
        executor: Trade executor
    """
    asset = "BTC"
    state = orchestrator._asset_states[asset]
    base_interval = orchestrator.ASSET_SCAN_INTERVALS.get(asset, 30.0)
    
    logger.info(f"[{asset}] Starting independent trading loop (interval: {base_interval}s)")
    
    while orchestrator._running:
        try:
            # Check if paused
            if state.is_paused:
                state.phase = ScanningPhase.PAUSED
                if state.pause_until and datetime.now(timezone.utc) >= state.pause_until:
                    logger.info(f"[{asset}] Pause expired, resuming")
                    state.is_paused = False
                    state.pause_until = None
                else:
                    await asyncio.sleep(1.0)
                    continue
            
            # Scan phase
            state.phase = ScanningPhase.SCANNING
            state.last_scan = datetime.now(timezone.utc)
            state.total_scans += 1
            
            markets = await orchestrator._scan_markets_for_asset(asset)
            
            if markets:
                logger.debug(f"[{asset}] Found {len(markets)} markets")
                state.active_market = markets[0] if markets else None
                
                # Check for market expiry (T=0)
                if state.active_market and state.active_market.seconds_to_expiry <= 0:
                    logger.warning(f"[{asset}] Market expired (T=0), initiating post-sale cooldown")
                    expired_token_ids = [state.active_market.yes_token_id, state.active_market.no_token_id]
                    await orchestrator._sniper.unsubscribe(expired_token_ids)
                    orchestrator.pause_asset(asset, duration_seconds=10.0, reason="post-sale-cooldown")
                    await asyncio.sleep(1.0)
                    continue
                
                # Terminal velocity check (T-60)
                terminal_markets = []
                rest_markets = []
                for market in markets:
                    is_terminal = await orchestrator._terminal_velocity.check_market_for_terminal(market)
                    if is_terminal:
                        terminal_markets.append(market)
                    else:
                        rest_markets.append(market)
                
                if terminal_markets:
                    logger.info(f"[{asset}] [BOLT] {len(terminal_markets)} market(s) in terminal mode")
                
                # Analysis phase
                state.phase = ScanningPhase.ANALYZING
                if rest_markets:
                    token_ids = []
                    for m in rest_markets:
                        token_ids.extend([m.yes_token_id, m.no_token_id])
                    await orchestrator._sniper.subscribe(token_ids)
                
                opportunities: List[ArbitrageOpportunity] = []
                for market in rest_markets:
                    opp = await arbitrage.analyze_market(market)
                    if opp and opp.profit_margin > 0:
                        opportunities.append(opp)
                
                if opportunities:
                    logger.info(f"[{asset}] Found {len(opportunities)} profitable opportunities")
                    state.phase = ScanningPhase.EXECUTING
                    best = max(opportunities, key=lambda x: x.profit_margin)
                    await orchestrator._execute_opportunity(best, asset)
            else:
                state.active_market = None
            
            state.consecutive_errors = 0
            
            # Wait phase
            state.phase = ScanningPhase.WAITING
            scan_interval = orchestrator._get_dynamic_scan_interval(markets) if markets else base_interval
            await asyncio.sleep(scan_interval)
            
        except asyncio.CancelledError:
            logger.info(f"[{asset}] Trading loop cancelled")
            state.phase = ScanningPhase.IDLE
            raise
        except Exception as e:
            state.phase = ScanningPhase.ERROR
            state.last_error = str(e)
            state.error_count += 1
            state.consecutive_errors += 1
            logger.error(f"[{asset}] Trading loop error: {e}")
            backoff = min(60, 2 ** min(state.consecutive_errors, 6))
            logger.info(f"[{asset}] Backing off {backoff}s")
            await asyncio.sleep(backoff)
    
    state.phase = ScanningPhase.IDLE
    logger.info(f"[{asset}] Trading loop stopped")


async def run_eth_simulation(
    orchestrator: 'Orchestrator',
    scanner: MarketScanner,
    arbitrage: ArbitrageEngine,
    executor: ZeroFeeExecutor,
) -> None:
    """
    ETH market simulation loop.
    
    Independent trading loop for ETH markets that:
    - Scans for ETH 5-minute markets every 30 seconds
    - Analyzes for arbitrage opportunities
    - Executes profitable trades
    - Handles T-60 terminal velocity transitions
    - Supports dynamic pause/resume
    
    Args:
        orchestrator: Parent orchestrator for state management
        scanner: Market scanner instance
        arbitrage: Arbitrage analysis engine
        executor: Trade executor
    """
    asset = "ETH"
    state = orchestrator._asset_states[asset]
    base_interval = orchestrator.ASSET_SCAN_INTERVALS.get(asset, 30.0)
    
    logger.info(f"[{asset}] Starting independent trading loop (interval: {base_interval}s)")
    
    while orchestrator._running:
        try:
            # Check if paused
            if state.is_paused:
                state.phase = ScanningPhase.PAUSED
                if state.pause_until and datetime.now(timezone.utc) >= state.pause_until:
                    logger.info(f"[{asset}] Pause expired, resuming")
                    state.is_paused = False
                    state.pause_until = None
                else:
                    await asyncio.sleep(1.0)
                    continue
            
            # Scan phase
            state.phase = ScanningPhase.SCANNING
            state.last_scan = datetime.now(timezone.utc)
            state.total_scans += 1
            
            markets = await orchestrator._scan_markets_for_asset(asset)
            
            if markets:
                logger.debug(f"[{asset}] Found {len(markets)} markets")
                state.active_market = markets[0] if markets else None
                
                # Check for market expiry (T=0)
                if state.active_market and state.active_market.seconds_to_expiry <= 0:
                    logger.warning(f"[{asset}] Market expired (T=0), initiating post-sale cooldown")
                    expired_token_ids = [state.active_market.yes_token_id, state.active_market.no_token_id]
                    await orchestrator._sniper.unsubscribe(expired_token_ids)
                    orchestrator.pause_asset(asset, duration_seconds=10.0, reason="post-sale-cooldown")
                    await asyncio.sleep(1.0)
                    continue
                
                # Terminal velocity check (T-60)
                terminal_markets = []
                rest_markets = []
                for market in markets:
                    is_terminal = await orchestrator._terminal_velocity.check_market_for_terminal(market)
                    if is_terminal:
                        terminal_markets.append(market)
                    else:
                        rest_markets.append(market)
                
                if terminal_markets:
                    logger.info(f"[{asset}] [BOLT] {len(terminal_markets)} market(s) in terminal mode")
                
                # Analysis phase
                state.phase = ScanningPhase.ANALYZING
                if rest_markets:
                    token_ids = []
                    for m in rest_markets:
                        token_ids.extend([m.yes_token_id, m.no_token_id])
                    await orchestrator._sniper.subscribe(token_ids)
                
                opportunities: List[ArbitrageOpportunity] = []
                for market in rest_markets:
                    opp = await arbitrage.analyze_market(market)
                    if opp and opp.profit_margin > 0:
                        opportunities.append(opp)
                
                if opportunities:
                    logger.info(f"[{asset}] Found {len(opportunities)} profitable opportunities")
                    state.phase = ScanningPhase.EXECUTING
                    best = max(opportunities, key=lambda x: x.profit_margin)
                    await orchestrator._execute_opportunity(best, asset)
            else:
                state.active_market = None
            
            state.consecutive_errors = 0
            
            # Wait phase
            state.phase = ScanningPhase.WAITING
            scan_interval = orchestrator._get_dynamic_scan_interval(markets) if markets else base_interval
            await asyncio.sleep(scan_interval)
            
        except asyncio.CancelledError:
            logger.info(f"[{asset}] Trading loop cancelled")
            state.phase = ScanningPhase.IDLE
            raise
        except Exception as e:
            state.phase = ScanningPhase.ERROR
            state.last_error = str(e)
            state.error_count += 1
            state.consecutive_errors += 1
            logger.error(f"[{asset}] Trading loop error: {e}")
            backoff = min(60, 2 ** min(state.consecutive_errors, 6))
            logger.info(f"[{asset}] Backing off {backoff}s")
            await asyncio.sleep(backoff)
    
    state.phase = ScanningPhase.IDLE
    logger.info(f"[{asset}] Trading loop stopped")


# ============================================================================
# ORCHESTRATOR CLASS (manages component lifecycle)
# ============================================================================

class Orchestrator:
    """
    Main orchestrator coordinating all bot components in a unified async event loop.
    
    Phase 2D: Multi-Currency Async Orchestration with asyncio.gather
    - Independent per-asset trading loops (BTC/ETH run concurrently but independently)
    - Fault isolation: one asset loop crash doesn't affect others
    - Per-asset pause/resume functionality
    - Dashboard and terminal velocity integration
    - OFI Engine for order flow analysis
    
    Components:
    - MarketScanner: Continuously find 5-minute markets
    - BinanceOracle: Stream real-time BTC/ETH prices
    - PolymarketSniper: Stream order book data
    - ArbitrageEngine: Analyze opportunities
    - ZeroFeeExecutor: Execute trades
    - TechnicalAnalyzer: Fallback signals
    - Dashboard: TUI monitoring
    - TerminalVelocityController: T-60 WebSocket ignition
    - OFIEngine: Order flow imbalance analysis
    """
    
    # Supported assets for independent trading loops
    SUPPORTED_ASSETS = ["BTC", "ETH"]
    
    # Scan intervals per asset (seconds between scans)
    ASSET_SCAN_INTERVALS = {
        "BTC": 30.0,   # 2 scans per minute for BTC
        "ETH": 30.0,   # 2 scans per minute for ETH
    }
    
    def __init__(self):
        self._config = get_config()
        self._running = False
        self._shutdown_event = asyncio.Event()
        
        # Initialize components (will be set in initialize())
        self._scanner: Optional[MarketScanner] = None
        self._oracle: Optional[BinanceOracle] = None
        self._sniper: Optional[PolymarketSniper] = None
        self._arbitrage: Optional[ArbitrageEngine] = None
        self._executor: Optional[ZeroFeeExecutor] = None
        self._ta: Optional[TechnicalAnalyzer] = None
        self._security: Optional[SecurityContext] = None
        self._terminal_velocity: Optional[TerminalVelocityController] = None
        self._dashboard: Optional[Dashboard] = None
        
        # Per-asset state tracking (Phase 2D)
        self._asset_states: Dict[str, AssetState] = {
            asset: AssetState(asset=asset)
            for asset in self.SUPPORTED_ASSETS
        }
        
        # Trading state (aggregate)
        self._active_positions: List = []
        self._total_trades = 0
        self._total_profit = 0.0
        
        # Shared scan cache to avoid duplicate scans
        self._scan_cache: Optional[List[Market5Min]] = None
        self._scan_cache_time: Optional[datetime] = None
        self._scan_cache_lock = asyncio.Lock()
        self._scan_cache_ttl = 1.0  # Cache valid for 1 second
    
    async def initialize(self):
        """Initialize all components."""
        logger.info("Initializing Polymarket Arbitrage Bot...")
        
        # Security context first
        self._security = SecurityContext()
        logger.info("[OK] Security context initialized")
        
        # Initialize executor (needs API credentials)
        self._executor = ZeroFeeExecutor(self._config)
        await self._executor.initialize()
        logger.info("[OK] Zero-fee executor initialized")
        
        # Initialize data sources
        self._oracle = BinanceOracle.from_config()
        logger.info("[OK] Binance oracle initialized")
        
        self._sniper = PolymarketSniper()
        logger.info("[OK] Polymarket sniper initialized")
        
        self._scanner = MarketScanner()
        logger.info("[OK] Market scanner initialized")
        
        # Initialize analysis engines
        self._arbitrage = ArbitrageEngine(
            oracle=self._oracle,
            sniper=self._sniper,
            max_entry_price=self._config.trading.max_entry_price,
            time_threshold_seconds=self._config.trading.time_threshold_seconds,
        )
        logger.info("[OK] Arbitrage engine initialized")
        
        # Initialize TA fallback
        self._ta = TechnicalAnalyzer(oracle=self._oracle)
        logger.info("[OK] Technical analyzer initialized")
        
        # Initialize Dashboard
        self._dashboard = Dashboard(self._oracle, self._scanner, self._config)
        logger.info("[OK] Dashboard initialized")
        
        # Initialize Terminal Velocity Controller
        self._terminal_velocity = TerminalVelocityController(
            sniper=self._sniper,
            on_stop_rest_polling=self._on_terminal_stop_rest,
            on_strike_opportunity=self._on_terminal_strike,
        )
        await self._terminal_velocity.start()
        logger.info("[OK] [BOLT] Terminal Velocity Controller initialized")
        
        logger.info("All components initialized successfully")
        self._print_config_summary()
    
    def _print_config_summary(self):
        """Print configuration summary."""
        print("\n" + "-" * 60)
        print("  CONFIGURATION SUMMARY")
        print("-" * 60)
        print(f"  Mode:              {self._config.trading.mode.upper()}")
        print(f"  Starting Capital:  ${self._config.trading.starting_capital:.2f}")
        print(f"  Trade Allocation:  {self._config.trading.trade_allocation_pct * 100:.1f}%")
        print(f"  Max Entry Price:   ${self._config.trading.max_entry_price:.2f}")
        print(f"  Time Threshold:    {self._config.trading.time_threshold_seconds}s")
        print(f"  Daily Loss Limit:  ${self._config.trading.daily_loss_limit:.2f}")
        print("-" * 60 + "\n")
    
    async def run(self):
        """
        Main event loop using asyncio.gather for concurrent execution.
        
        Runs all components concurrently:
        - Oracle (Binance price streaming)
        - Sniper (Polymarket order book streaming)
        - Technical Analysis background service
        - BTC simulation loop
        - ETH simulation loop
        - Dashboard
        - Terminal Velocity controller
        - OFI Engine (if available)
        - Stats reporter
        """
        self._running = True
        
        # Setup signal handlers for graceful shutdown
        self._setup_signal_handlers()
        
        logger.info("Bot started - press Ctrl+C to stop")
        logger.info("Running asyncio.gather orchestration with concurrent BTC/ETH simulations")
        print("\n>> Bot is running...\n")
        
        try:
            # Run all services concurrently using asyncio.gather
            await asyncio.gather(
                # Core data services
                run_oracle_service(self._oracle),
                run_sniper_service(self._sniper),
                run_ta_service(self._ta, self._on_ta_signal),
                
                # Simulation loops (BTC and ETH run completely asynchronously)
                run_btc_simulation(self, self._scanner, self._arbitrage, self._executor),
                run_eth_simulation(self, self._scanner, self._arbitrage, self._executor),
                
                # UI and monitoring
                run_dashboard_service(self._dashboard),
                run_terminal_velocity_service(self._terminal_velocity),
                run_ofi_engine_service(["btcusdt", "ethusdt"]),
                
                # Stats reporting
                run_stats_reporter(self),
                
                # Wait for shutdown signal
                self._wait_for_shutdown(),
            )
        except asyncio.CancelledError:
            logger.info("Orchestration cancelled")
        except Exception as e:
            logger.error(f"Orchestration error: {e}")
            raise
        finally:
            self._running = False
            logger.info("Shutting down...")
            await self._cleanup()
            logger.info("Bot shutdown complete")
    
    async def _wait_for_shutdown(self) -> None:
        """Wait for shutdown signal."""
        try:
            await self._shutdown_event.wait()
        except asyncio.CancelledError:
            raise
    
    def _setup_signal_handlers(self):
        """Setup graceful shutdown signal handlers."""
        def signal_handler():
            logger.info("Shutdown signal received")
            self._shutdown_event.set()
        
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, signal_handler)
            except NotImplementedError:
                # Windows doesn't support add_signal_handler
                pass
    
    async def _scan_markets_for_asset(self, asset: str) -> List[Market5Min]:
        """
        Scan for markets specific to one asset.
        Uses shared cache to avoid duplicate API calls when multiple asset loops scan simultaneously.
        
        Args:
            asset: The asset to scan for (e.g., "BTC", "ETH")
            
        Returns:
            List of Market5Min objects for the specified asset
        """
        async with self._scan_cache_lock:
            now = datetime.now(timezone.utc)
            
            # Check if cache is valid
            if (self._scan_cache is not None and 
                self._scan_cache_time is not None and
                (now - self._scan_cache_time).total_seconds() < self._scan_cache_ttl):
                # Use cached results
                all_markets = self._scan_cache
            else:
                # Perform fresh scan and cache it
                all_markets = await self._scanner.scan_markets()
                self._scan_cache = all_markets
                self._scan_cache_time = now
        
        # Filter to only this asset's markets
        asset_markets = [m for m in all_markets if m.asset.upper() == asset.upper()]
        
        return asset_markets
    
    def _get_dynamic_scan_interval(self, markets: List[Market5Min]) -> float:
        """
        Calculate dynamic scan interval based on time to next market close.
        
        Markets finish at minutes ending in 0 and 5 (e.g., :00, :05, :10, etc.)
        - T-5min to T-1min: scan every 30s (normal cruising)
        - T-1min to T-0: scan every 100ms (high-frequency for arbitrage)
        
        Args:
            markets: List of markets to consider
            
        Returns:
            Scan interval in seconds
        """
        if not markets:
            return 30.0  # Default interval when no markets
        
        # Find the closest market to expiry
        min_seconds = min(m.seconds_to_expiry for m in markets)
        closest_market = min(markets, key=lambda m: m.seconds_to_expiry)
        
        if min_seconds <= 0:
            # Market expired, use fast interval to detect it
            return 0.1
        elif min_seconds <= 60:
            # T-1min to T-0: High-frequency scanning (100ms)
            logger.debug(f"[HIGH-FREQ] T-{min_seconds:.1f}s to {closest_market.asset} close - 100ms scan interval")
            return 0.1
        elif min_seconds <= 300:
            # T-5min to T-1min: Normal cruising (30s)
            return 30.0
        else:
            # More than 5 minutes out: slower scanning (30s)
            return 30.0
    
    def pause_asset(self, asset: str, duration_seconds: float = 0, reason: str = "manual"):
        """
        Pause scanning/trading for a specific asset.
        
        Args:
            asset: The asset to pause (e.g., "BTC", "ETH")
            duration_seconds: How long to pause (0 = indefinite until resume)
            reason: Reason for pause (e.g., "manual", "post-sale-cooldown", "error-backoff")
        """
        if asset not in self._asset_states:
            logger.warning(f"Unknown asset: {asset}")
            return
        
        state = self._asset_states[asset]
        state.is_paused = True
        
        if duration_seconds > 0:
            state.pause_until = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)
            pause_until_time = state.pause_until.strftime("%H:%M:%S UTC")
            logger.info(
                f"[{asset}] Paused for {duration_seconds}s (reason: {reason}) "
                f"| Will resume at {pause_until_time}"
            )
        else:
            state.pause_until = None
            logger.info(f"[{asset}] Paused indefinitely (reason: {reason})")
    
    def resume_asset(self, asset: str):
        """
        Resume scanning/trading for a specific asset.
        
        Args:
            asset: The asset to resume (e.g., "BTC", "ETH")
        """
        if asset not in self._asset_states:
            logger.warning(f"Unknown asset: {asset}")
            return
        
        state = self._asset_states[asset]
        state.is_paused = False
        state.pause_until = None
        logger.info(f"[{asset}] Resumed")
    
    def get_asset_state(self, asset: str) -> Optional[AssetState]:
        """Get the current state for a specific asset."""
        return self._asset_states.get(asset)
    
    def get_all_asset_states(self) -> Dict[str, AssetState]:
        """Get states for all assets (for dashboard)."""
        return self._asset_states.copy()
    
    async def _execute_opportunity(self, opp: ArbitrageOpportunity, asset: str):
        """Execute arbitrage opportunity for a specific asset.
        
        Args:
            opp: ArbitrageOpportunity to execute
            asset: The asset this opportunity is for (e.g., "BTC", "ETH")
        """
        try:
            logger.info(f"[{asset}] Executing opportunity: {opp.market.condition_id}")
            logger.info(f"[{asset}]   Side: {opp.signal.value}, Price: ${opp.entry_price:.4f}")
            logger.info(f"[{asset}]   Expected profit: {opp.profit_margin * 100:.2f}%")
            
            # Calculate position size based on config
            max_size = self._config.trading.starting_capital * self._config.trading.trade_allocation_pct
            position_size = max_size  # Use configured allocation
            
            if position_size < 1.0:  # Minimum $1 trade
                logger.debug(f"[{asset}] Position too small, skipping")
                return
            
            # Execute via zero-fee executor (FOK order)
            result = await self._executor.execute_fok_order(
                token_id=opp.token_id,
                price=opp.entry_price,
                size=position_size,
                side="BUY",  # Arbitrage always buys the winning side
            )
            
            if result.status.value in ("filled", "submitted"):
                # Update asset-specific state
                state = self._asset_states[asset]
                state.total_trades += 1
                # Profit = payout (1.0) - entry price, multiplied by filled size
                realized_profit = (1.0 - opp.entry_price) * result.filled_size
                state.total_profit += realized_profit
                
                # Update aggregate state
                self._total_trades += 1
                self._total_profit += realized_profit
                logger.info(f"[{asset}] Trade executed successfully: {result.order_id}")
            else:
                logger.warning(f"[{asset}] Trade failed: {result.error_message}")
                
        except Exception as e:
            logger.error(f"[{asset}] Execution error: {e}")
    
    async def _on_terminal_stop_rest(self, asset: str, market: Market5Min):
        """
        Callback from Terminal Velocity Controller when REST polling should stop.
        
        Called at T-60 seconds when a market enters terminal phase.
        This signals the asset loop to skip REST polling for this specific market.
        
        Args:
            asset: The asset (e.g., "BTC", "ETH")
            market: The market entering terminal phase
        """
        logger.info(
            f"[BOLT] [{asset}] Terminal Velocity: Stopping REST polling for market {market.condition_id[:8]}",
            extra={"seconds_remaining": market.seconds_to_expiry}
        )
        # The terminal_velocity controller now handles this market via WebSocket
        # The asset trading loop will check is_market_terminal() before REST polling
    
    async def _on_terminal_strike(self, market: Market5Min, best_bid: float, best_ask: float):
        """
        Callback from Terminal Velocity Controller when a strike opportunity is detected.
        
        Called when prices reach $0.98+ in the terminal phase.
        This is the optimal moment for final arbitrage execution.
        
        Args:
            market: The market with the opportunity
            best_bid: Current best bid price
            best_ask: Current best ask price
        """
        logger.warning(
            f"[TARGET] STRIKE OPPORTUNITY: {market.asset} Bid=${best_bid:.4f} Ask=${best_ask:.4f} @ T-{market.seconds_to_expiry:.1f}s"
        )
        
        # Analyze and potentially execute the arbitrage opportunity
        try:
            opp = await self._arbitrage.analyze_market(market)
            if opp and opp.profit_margin > 0:
                logger.info(f"[TARGET] [{market.asset}] Strike opportunity confirmed: {opp.profit_margin * 100:.2f}% profit")
                await self._execute_opportunity(opp, market.asset)
        except Exception as e:
            logger.error(f"Strike opportunity analysis error: {e}")
    
    def _on_ta_signal(self, signal):
        """Handle TA fallback signals when arbitrage opportunities are scarce."""
        logger.info(f"TA Signal: {signal.signal_type.value} for {signal.symbol}")
        logger.info(f"  Confidence: {signal.confidence:.2f}, Price: ${signal.price:.2f}")
        # TA signals can be used to inform trading decisions when pure arbitrage
        # opportunities are not available
    
    async def _cleanup(self):
        """Cleanup resources on shutdown."""
        try:
            if self._terminal_velocity:
                await self._terminal_velocity.stop()
            if self._oracle:
                await self._oracle.close()
            if self._sniper:
                await self._sniper.close()
            if self._executor:
                await self._executor.close()
        except Exception as e:
            logger.error(f"Cleanup error: {e}")


# ============================================================================
# LOGGING AND ENTRY POINT
# ============================================================================

def setup_logging():
    """Configure logging for the application."""
    log_format = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"
    
    # Ensure logs directory exists
    os.makedirs("logs", exist_ok=True)
    
    # Create handlers with explicit UTF-8 encoding to avoid Windows cp1252 issues
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    
    # File handler with UTF-8 encoding
    file_handler = logging.FileHandler("logs/bot.log", mode="a", encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    
    # Configure root logger
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        datefmt=date_format,
        handlers=[console_handler, file_handler]
    )
    
    # Reduce noise from third-party libraries
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def check_python_version():
    """Ensure Python version is 3.10+."""
    if sys.version_info < (3, 10):
        print("[ERROR] Python 3.10+ is required")
        print(f"   Current version: {sys.version}")
        sys.exit(1)


async def main():
    """Entry point."""
    # Version check
    check_python_version()
    
    # Setup logging
    setup_logging()
    
    # Print banner
    print()
    print("=" * 60)
    print("  ██████╗  ██████╗ ██╗  ██╗   ██╗███╗   ███╗")
    print("  ██╔══██╗██╔═══██╗██║  ╚██╗ ██╔╝████╗ ████║")
    print("  ██████╔╝██║   ██║██║   ╚████╔╝ ██╔████╔██║")
    print("  ██╔═══╝ ██║   ██║██║    ╚██╔╝  ██║╚██╔╝██║")
    print("  ██║     ╚██████╔╝███████╗██║   ██║ ╚═╝ ██║")
    print("  ╚═╝      ╚═════╝ ╚══════╝╚═╝   ╚═╝     ╚═╝")
    print()
    print("  POLYMARKET ARBITRAGE BOT")
    print("  High-Frequency 5-Minute Portfolio Compounding System")
    print("  Task-36: asyncio.gather Orchestration")
    print("=" * 60)
    print()
    
    try:
        orchestrator = Orchestrator()
        await orchestrator.initialize()
        await orchestrator.run()
        
    except ConfigurationError as e:
        print(f"\n[ERROR] Configuration Error: {e}")
        print("   Please check your .env file and try again.")
        sys.exit(1)
        
    except KeyboardInterrupt:
        print("\n\n👋 Shutting down gracefully...")
        
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        print(f"\n[ERROR] Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
