"""
Terminal Velocity WebSocket Ignition Module.

At T-60 seconds (1 minute remaining), this module:
- Terminates slow 30-second REST polling
- Ignites high-frequency WebSocket streaming for Level 1 orderbook data
- Maximizes bandwidth for the final $0.98 -> $1.00 arbitrage strike

Phase 2: Terminal Velocity Enhancement
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Set, Callable, Any, List
from enum import Enum

from scanner import Market5Min, ScanningPhase
from sniper import PolymarketSniper, OrderBookState, ConnectionState

logger = logging.getLogger(__name__)


class TerminalPhase(Enum):
    """Terminal velocity sub-phases for fine-grained control."""
    INACTIVE = "inactive"           # Not in terminal mode
    IGNITING = "igniting"           # Transitioning to terminal mode
    STREAMING = "streaming"         # High-frequency WS active
    STRIKING = "striking"           # Final execution window (T-10s)
    COMPLETED = "completed"         # Market expired


@dataclass
class TerminalMarketState:
    """
    Tracks the terminal velocity state for a single market.
    
    Each market in terminal phase gets dedicated high-frequency tracking.
    """
    market: Market5Min
    phase: TerminalPhase = TerminalPhase.INACTIVE
    ws_subscribed: bool = False
    rest_polling_stopped: bool = False
    ignition_time: Optional[datetime] = None
    updates_received: int = 0
    last_bid: Optional[float] = None
    last_ask: Optional[float] = None
    last_update_time: Optional[datetime] = None
    
    @property
    def seconds_remaining(self) -> float:
        """Current seconds to market expiry."""
        return self.market.seconds_to_expiry
    
    @property
    def is_in_strike_zone(self) -> bool:
        """True when in final 10-second strike window."""
        return 0 < self.seconds_remaining <= 10
    
    @property
    def is_expired(self) -> bool:
        """True when market has expired."""
        return self.seconds_remaining <= 0


@dataclass
class TerminalVelocityStats:
    """Statistics for terminal velocity operations."""
    markets_ignited: int = 0
    successful_transitions: int = 0
    failed_transitions: int = 0
    total_ws_updates: int = 0
    avg_update_latency_ms: float = 0.0
    strike_opportunities: int = 0


class TerminalVelocityController:
    """
    Controls the transition from REST polling to high-frequency WebSocket
    streaming when markets enter the terminal phase (T-60 seconds).
    
    Key responsibilities:
    1. Monitor markets approaching terminal threshold
    2. Stop REST polling for terminal markets
    3. Ignite WebSocket streams for orderbook data
    4. Track high-frequency updates for final arbitrage strike
    5. Coordinate with ArbitrageEngine for execution
    
    Design:
    - Each market gets its own TerminalMarketState
    - WebSocket subscriptions are managed per-token
    - REST polling signals are sent to the main orchestrator
    - Callbacks notify when strike opportunities arise
    """
    
    # Terminal threshold in seconds (T-60)
    TERMINAL_THRESHOLD = 60
    
    # Strike zone threshold (final 10 seconds)
    STRIKE_THRESHOLD = 10
    
    # Minimum price for arbitrage consideration
    MIN_ARBITRAGE_PRICE = 0.98
    
    def __init__(
        self,
        sniper: PolymarketSniper,
        on_stop_rest_polling: Optional[Callable[[str, Market5Min], Any]] = None,
        on_strike_opportunity: Optional[Callable[[Market5Min, float, float], Any]] = None,
    ):
        """
        Initialize the Terminal Velocity Controller.
        
        Args:
            sniper: PolymarketSniper instance for WebSocket streaming
            on_stop_rest_polling: Callback when REST polling should stop for a market
                Signature: (asset: str, market: Market5Min) -> Any
            on_strike_opportunity: Callback when a strike opportunity is detected
                Signature: (market: Market5Min, best_bid: float, best_ask: float) -> Any
        """
        self._sniper = sniper
        self._on_stop_rest_polling = on_stop_rest_polling
        self._on_strike_opportunity = on_strike_opportunity
        
        # Track terminal markets by condition_id
        self._terminal_markets: Dict[str, TerminalMarketState] = {}
        
        # Token ID to market mapping for update routing
        self._token_to_market: Dict[str, str] = {}  # token_id -> condition_id
        
        # Active monitoring tasks
        self._monitor_tasks: Dict[str, asyncio.Task] = {}
        
        # Statistics
        self.stats = TerminalVelocityStats()
        
        # Lock for thread-safe state updates
        self._state_lock = asyncio.Lock()
        
        # Running flag
        self._running = False
        
        logger.info("TerminalVelocityController initialized", extra={
            "terminal_threshold": self.TERMINAL_THRESHOLD,
            "strike_threshold": self.STRIKE_THRESHOLD,
        })
    
    async def start(self):
        """Start the terminal velocity controller."""
        self._running = True
        
        # Register for sniper updates
        self._sniper.on_update(self._on_orderbook_update)
        
        logger.info("[BOLT] Terminal Velocity Controller STARTED")
    
    async def stop(self):
        """Stop the terminal velocity controller."""
        self._running = False
        
        # Cancel all monitoring tasks
        for task in self._monitor_tasks.values():
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        self._monitor_tasks.clear()
        
        # Unsubscribe from all terminal market tokens
        all_tokens = list(self._token_to_market.keys())
        if all_tokens:
            await self._sniper.unsubscribe(all_tokens)
        
        logger.info("Terminal Velocity Controller stopped")
    
    async def check_market_for_terminal(self, market: Market5Min) -> bool:
        """
        Check if a market should enter terminal velocity mode.
        
        This should be called by the main orchestrator during each scan cycle.
        When a market enters terminal phase, REST polling stops and
        high-frequency WebSocket streaming begins.
        
        Args:
            market: Market to check
            
        Returns:
            True if market is now in terminal mode, False otherwise
        """
        seconds_remaining = market.seconds_to_expiry
        
        # Already tracking this market?
        if market.condition_id in self._terminal_markets:
            state = self._terminal_markets[market.condition_id]
            # Update state and check for expiry
            if state.is_expired:
                await self._handle_market_expiry(market.condition_id)
                return False
            return True
        
        # Not yet in terminal phase?
        if seconds_remaining > self.TERMINAL_THRESHOLD:
            return False
        
        # IGNITION TIME! Market just crossed T-60 threshold
        logger.info(
            f">> TERMINAL VELOCITY IGNITION: {market.asset} market",
            extra={
                "condition_id": market.condition_id,
                "seconds_remaining": seconds_remaining,
                "yes_token": market.yes_token_id,
                "no_token": market.no_token_id,
            }
        )
        
        await self._ignite_terminal_mode(market)
        return True
    
    async def _ignite_terminal_mode(self, market: Market5Min):
        """
        Ignite terminal velocity mode for a market.
        
        1. Create terminal state tracking
        2. Signal REST polling to stop
        3. Subscribe to WebSocket orderbook streams
        4. Start high-frequency monitoring task
        """
        async with self._state_lock:
            # Create terminal state
            state = TerminalMarketState(
                market=market,
                phase=TerminalPhase.IGNITING,
                ignition_time=datetime.now(timezone.utc),
            )
            self._terminal_markets[market.condition_id] = state
            
            # Map tokens to this market
            self._token_to_market[market.yes_token_id] = market.condition_id
            self._token_to_market[market.no_token_id] = market.condition_id
        
        # Signal REST polling to stop for this market
        if self._on_stop_rest_polling:
            try:
                result = self._on_stop_rest_polling(market.asset, market)
                if asyncio.iscoroutine(result):
                    await result
                state.rest_polling_stopped = True
                logger.info(f"[{market.asset}] REST polling TERMINATED for terminal market")
            except Exception as e:
                logger.error(f"Failed to stop REST polling: {e}")
        
        # Subscribe to WebSocket streams for this market's tokens
        token_ids = [market.yes_token_id, market.no_token_id]
        try:
            await self._sniper.subscribe(token_ids)
            state.ws_subscribed = True
            state.phase = TerminalPhase.STREAMING
            logger.info(
                f"[SIGNAL] WebSocket streams IGNITED for {market.asset}",
                extra={"tokens": token_ids}
            )
        except Exception as e:
            logger.error(f"Failed to subscribe to WebSocket: {e}")
            state.phase = TerminalPhase.INACTIVE
            self.stats.failed_transitions += 1
            return
        
        # Start high-frequency monitoring task
        task = asyncio.create_task(
            self._terminal_monitor_task(market.condition_id),
            name=f"terminal-monitor-{market.condition_id[:8]}"
        )
        self._monitor_tasks[market.condition_id] = task
        
        self.stats.markets_ignited += 1
        self.stats.successful_transitions += 1
        
        logger.info(
            f"[BOLT] TERMINAL VELOCITY ACTIVE: {market.asset} @ T-{state.seconds_remaining:.1f}s",
            extra={"condition_id": market.condition_id}
        )
    
    async def _terminal_monitor_task(self, condition_id: str):
        """
        High-frequency monitoring task for a terminal market.
        
        Runs until market expires, checking for:
        - Strike zone entry (T-10s)
        - Arbitrage opportunities ($0.98+ prices)
        - Market expiry
        """
        try:
            while self._running:
                state = self._terminal_markets.get(condition_id)
                if not state:
                    break
                
                # Check for expiry
                if state.is_expired:
                    state.phase = TerminalPhase.COMPLETED
                    logger.info(f"Market {condition_id[:8]} EXPIRED")
                    break
                
                # Check for strike zone (T-10s)
                if state.is_in_strike_zone and state.phase != TerminalPhase.STRIKING:
                    state.phase = TerminalPhase.STRIKING
                    logger.warning(
                        f"[TARGET] STRIKE ZONE ENTERED: {state.market.asset} @ T-{state.seconds_remaining:.1f}s"
                    )
                
                # Check for arbitrage opportunity
                if state.last_bid is not None and state.last_ask is not None:
                    # Look for prices near $1.00 (arbitrage sweet spot)
                    if state.last_bid >= self.MIN_ARBITRAGE_PRICE or state.last_ask <= (1 - self.MIN_ARBITRAGE_PRICE + 0.02):
                        self.stats.strike_opportunities += 1
                        
                        if self._on_strike_opportunity:
                            try:
                                result = self._on_strike_opportunity(
                                    state.market,
                                    state.last_bid,
                                    state.last_ask
                                )
                                if asyncio.iscoroutine(result):
                                    await result
                            except Exception as e:
                                logger.error(f"Strike opportunity callback error: {e}")
                
                # High-frequency polling (100ms intervals)
                await asyncio.sleep(0.1)
                
        except asyncio.CancelledError:
            logger.debug(f"Terminal monitor cancelled: {condition_id[:8]}")
            raise
        except Exception as e:
            logger.error(f"Terminal monitor error: {e}")
        finally:
            # Cleanup
            await self._handle_market_expiry(condition_id)
    
    async def _on_orderbook_update(self, token_id: str, state: OrderBookState):
        """
        Handle orderbook updates from WebSocket sniper.
        
        This is called by the sniper for every orderbook update.
        We filter to only process updates for terminal markets.
        """
        # Is this a terminal market token?
        condition_id = self._token_to_market.get(token_id)
        if not condition_id:
            return
        
        market_state = self._terminal_markets.get(condition_id)
        if not market_state:
            return
        
        # Update state with latest prices
        now = datetime.now(timezone.utc)
        market_state.updates_received += 1
        market_state.last_update_time = now
        self.stats.total_ws_updates += 1
        
        if state.best_bid:
            market_state.last_bid = state.best_bid.price
        if state.best_ask:
            market_state.last_ask = state.best_ask.price
        
        # Log high-frequency updates only in strike zone
        if market_state.is_in_strike_zone:
            logger.debug(
                f"[STATS] {market_state.market.asset} UPDATE @ T-{market_state.seconds_remaining:.1f}s: "
                f"Bid=${market_state.last_bid:.4f} Ask=${market_state.last_ask:.4f}"
            )
    
    async def _handle_market_expiry(self, condition_id: str):
        """Handle cleanup when a market expires."""
        state = None
        token_ids = []
        
        async with self._state_lock:
            state = self._terminal_markets.pop(condition_id, None)
            if not state:
                return
            
            # Unsubscribe from tokens
            token_ids = [state.market.yes_token_id, state.market.no_token_id]
            self._token_to_market.pop(state.market.yes_token_id, None)
            self._token_to_market.pop(state.market.no_token_id, None)
        
        # Unsubscribe outside of lock to avoid potential deadlocks
        if token_ids:
            try:
                await self._sniper.unsubscribe(token_ids)
            except Exception as e:
                logger.debug(f"Unsubscribe error (market expired): {e}")
        
        # Cancel monitor task
        task = self._monitor_tasks.pop(condition_id, None)
        if task and not task.done():
            task.cancel()
        
        if state:
            logger.info(
                f"Terminal session ended: {state.market.asset}",
                extra={
                    "updates_received": state.updates_received,
                    "duration_seconds": (datetime.now(timezone.utc) - state.ignition_time).total_seconds() if state.ignition_time else 0,
                }
            )
    
    def get_terminal_markets(self) -> Dict[str, TerminalMarketState]:
        """Get all currently tracked terminal markets."""
        return self._terminal_markets.copy()
    
    def is_market_terminal(self, condition_id: str) -> bool:
        """Check if a market is in terminal mode."""
        return condition_id in self._terminal_markets
    
    def get_market_state(self, condition_id: str) -> Optional[TerminalMarketState]:
        """Get terminal state for a specific market."""
        return self._terminal_markets.get(condition_id)
    
    def get_stats(self) -> TerminalVelocityStats:
        """Get terminal velocity statistics."""
        return self.stats


# Convenience function for integration
async def create_terminal_velocity_controller(
    sniper: PolymarketSniper,
    on_stop_rest_polling: Optional[Callable] = None,
    on_strike_opportunity: Optional[Callable] = None,
) -> TerminalVelocityController:
    """
    Factory function to create and start a TerminalVelocityController.
    
    Args:
        sniper: PolymarketSniper instance
        on_stop_rest_polling: Callback when REST polling should stop
        on_strike_opportunity: Callback for strike opportunities
        
    Returns:
        Started TerminalVelocityController instance
    """
    controller = TerminalVelocityController(
        sniper=sniper,
        on_stop_rest_polling=on_stop_rest_polling,
        on_strike_opportunity=on_strike_opportunity,
    )
    await controller.start()
    return controller
