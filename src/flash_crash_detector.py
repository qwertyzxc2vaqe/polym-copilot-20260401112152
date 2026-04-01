"""
Flash Crash Detector - Rapid Price Movement Detection.

Phase 2 - Task 91: Detect >1% oracle drop in 3 seconds, simulate
pulling all limit orders in <10ms.

Educational purpose only - paper trading simulation.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, List, Callable
from collections import deque
from enum import Enum

logger = logging.getLogger(__name__)


class CrashSeverity(Enum):
    """Severity level of price crash."""
    MINOR = "minor"       # 0.5% - 1%
    MODERATE = "moderate"  # 1% - 2%
    SEVERE = "severe"      # 2% - 5%
    FLASH_CRASH = "flash_crash"  # > 5%


@dataclass
class CrashEvent:
    """Records a detected crash event."""
    event_id: str
    symbol: str
    severity: CrashSeverity
    price_drop_pct: float
    duration_ms: float
    start_price: float
    end_price: float
    start_time: float
    end_time: float
    orders_pulled: int = 0
    pull_latency_ms: float = 0.0
    
    def to_dict(self) -> dict:
        return {
            'event_id': self.event_id,
            'symbol': self.symbol,
            'severity': self.severity.value,
            'price_drop_pct': self.price_drop_pct,
            'duration_ms': self.duration_ms,
            'start_price': self.start_price,
            'end_price': self.end_price,
            'start_time': self.start_time,
            'orders_pulled': self.orders_pulled,
            'pull_latency_ms': self.pull_latency_ms,
        }


class FlashCrashDetector:
    """
    Detects rapid price drops and triggers protective actions.
    
    Configuration:
    - Threshold: 1% drop
    - Window: 3 seconds
    - Action: Pull all orders < 10ms
    """
    
    # Detection thresholds
    DEFAULT_DROP_THRESHOLD_PCT = 1.0  # 1% drop
    DEFAULT_WINDOW_MS = 3000  # 3 seconds
    
    # Severity thresholds
    MINOR_THRESHOLD = 0.5
    MODERATE_THRESHOLD = 1.0
    SEVERE_THRESHOLD = 2.0
    FLASH_CRASH_THRESHOLD = 5.0
    
    def __init__(
        self,
        symbols: List[str] = None,
        drop_threshold_pct: float = None,
        window_ms: float = None,
        on_crash_detected: Optional[Callable] = None,
        on_orders_pulled: Optional[Callable] = None,
    ):
        """
        Initialize flash crash detector.
        
        Args:
            symbols: Symbols to monitor
            drop_threshold_pct: Price drop threshold percentage
            window_ms: Time window in milliseconds
            on_crash_detected: Callback when crash detected
            on_orders_pulled: Callback when orders pulled
        """
        self.symbols = symbols or ['BTC', 'ETH']
        self.drop_threshold_pct = drop_threshold_pct or self.DEFAULT_DROP_THRESHOLD_PCT
        self.window_ms = window_ms or self.DEFAULT_WINDOW_MS
        self.on_crash_detected = on_crash_detected
        self.on_orders_pulled = on_orders_pulled
        
        # Price history per symbol
        self._price_history: Dict[str, deque] = {}
        
        # Crash event history
        self._crash_events: List[CrashEvent] = []
        
        # State
        self._is_in_crash: Dict[str, bool] = {}
        self._last_crash_time: Dict[str, float] = {}
        
        # Statistics
        self._stats = {
            'total_crashes': 0,
            'orders_pulled': 0,
            'avg_pull_latency_ms': 0.0,
        }
        
        # Initialize
        for symbol in self.symbols:
            self._price_history[symbol] = deque(maxlen=1000)
            self._is_in_crash[symbol] = False
            self._last_crash_time[symbol] = 0
    
    def add_price(self, symbol: str, price: float, timestamp: float = None) -> Optional[CrashEvent]:
        """
        Add a price tick and check for crash.
        
        Args:
            symbol: Symbol
            price: Current price
            timestamp: Tick timestamp (ms)
        
        Returns:
            CrashEvent if crash detected, None otherwise
        """
        timestamp = timestamp or time.time() * 1000
        
        if symbol not in self._price_history:
            self._price_history[symbol] = deque(maxlen=1000)
            self._is_in_crash[symbol] = False
            self._last_crash_time[symbol] = 0
        
        # Store tick
        self._price_history[symbol].append({
            'price': price,
            'timestamp': timestamp,
        })
        
        # Check for crash
        return self._check_crash(symbol, price, timestamp)
    
    def _check_crash(
        self,
        symbol: str,
        current_price: float,
        current_time: float,
    ) -> Optional[CrashEvent]:
        """Check if price movement constitutes a crash."""
        history = self._price_history[symbol]
        
        if len(history) < 2:
            return None
        
        # Don't re-trigger within 10 seconds of last crash
        if current_time - self._last_crash_time.get(symbol, 0) < 10000:
            return None
        
        # Find price at start of window
        window_start = current_time - self.window_ms
        start_price = None
        start_time = None
        
        for tick in history:
            if tick['timestamp'] >= window_start:
                start_price = tick['price']
                start_time = tick['timestamp']
                break
        
        if start_price is None or start_price == 0:
            return None
        
        # Calculate drop
        price_change_pct = (current_price - start_price) / start_price * 100
        
        # Only detect drops (negative change)
        if price_change_pct >= -self.drop_threshold_pct:
            if self._is_in_crash.get(symbol, False):
                self._is_in_crash[symbol] = False
            return None
        
        # Crash detected!
        drop_pct = abs(price_change_pct)
        severity = self._classify_severity(drop_pct)
        
        event = CrashEvent(
            event_id=f"crash_{symbol}_{int(current_time)}",
            symbol=symbol,
            severity=severity,
            price_drop_pct=drop_pct,
            duration_ms=current_time - start_time,
            start_price=start_price,
            end_price=current_price,
            start_time=start_time,
            end_time=current_time,
        )
        
        self._is_in_crash[symbol] = True
        self._last_crash_time[symbol] = current_time
        self._crash_events.append(event)
        self._stats['total_crashes'] += 1
        
        logger.warning(
            f"FLASH CRASH DETECTED: {symbol} dropped {drop_pct:.2f}% "
            f"in {event.duration_ms:.0f}ms ({severity.value})"
        )
        
        return event
    
    def _classify_severity(self, drop_pct: float) -> CrashSeverity:
        """Classify crash severity based on drop percentage."""
        if drop_pct >= self.FLASH_CRASH_THRESHOLD:
            return CrashSeverity.FLASH_CRASH
        elif drop_pct >= self.SEVERE_THRESHOLD:
            return CrashSeverity.SEVERE
        elif drop_pct >= self.MODERATE_THRESHOLD:
            return CrashSeverity.MODERATE
        else:
            return CrashSeverity.MINOR
    
    async def simulate_pull_orders(
        self,
        event: CrashEvent,
        order_count: int = 10,
    ) -> float:
        """
        Simulate pulling all orders with <10ms latency.
        
        Args:
            event: Crash event that triggered the pull
            order_count: Number of orders to "pull"
        
        Returns:
            Simulated pull latency in milliseconds
        """
        start_time = time.time()
        
        # Simulate order cancellation (no actual network call)
        # In real implementation, this would be batched cancels
        await asyncio.sleep(0.005)  # 5ms simulated latency
        
        end_time = time.time()
        pull_latency_ms = (end_time - start_time) * 1000
        
        # Update event
        event.orders_pulled = order_count
        event.pull_latency_ms = pull_latency_ms
        
        # Update statistics
        self._stats['orders_pulled'] += order_count
        n = self._stats['total_crashes']
        old_avg = self._stats['avg_pull_latency_ms']
        self._stats['avg_pull_latency_ms'] = (old_avg * (n - 1) + pull_latency_ms) / n
        
        logger.info(
            f"Orders pulled for {event.symbol}: {order_count} orders "
            f"in {pull_latency_ms:.2f}ms"
        )
        
        # Callbacks
        if self.on_crash_detected:
            await self._safe_callback(self.on_crash_detected, event)
        
        if self.on_orders_pulled:
            await self._safe_callback(self.on_orders_pulled, event)
        
        return pull_latency_ms
    
    async def _safe_callback(self, callback: Callable, *args) -> None:
        """Execute callback safely."""
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(*args)
            else:
                callback(*args)
        except Exception as e:
            logger.error(f"Callback error: {e}")
    
    def get_crash_events(
        self,
        symbol: str = None,
        limit: int = 100,
    ) -> List[CrashEvent]:
        """Get recent crash events."""
        events = self._crash_events
        
        if symbol:
            events = [e for e in events if e.symbol == symbol]
        
        return events[-limit:]
    
    def get_statistics(self) -> Dict:
        """Get detector statistics."""
        return {
            **self._stats,
            'crash_events': len(self._crash_events),
            'is_in_crash': {s: v for s, v in self._is_in_crash.items()},
        }
    
    def is_in_crash(self, symbol: str) -> bool:
        """Check if symbol is currently in crash state."""
        return self._is_in_crash.get(symbol, False)
    
    def reset_crash_state(self, symbol: str = None) -> None:
        """Reset crash state for symbol(s)."""
        if symbol:
            self._is_in_crash[symbol] = False
        else:
            for s in self._is_in_crash:
                self._is_in_crash[s] = False


class OBIAutoScaler:
    """
    Order Book Imbalance Auto-Scaler (Task 90).
    
    Automatically widens spread when volume drops by 80%
    to model wider theoretical risk margins during low liquidity.
    """
    
    DEFAULT_VOLUME_DROP_THRESHOLD = 0.8  # 80% drop
    DEFAULT_SPREAD_MULTIPLIER = 2.0      # Double spread on low liquidity
    
    def __init__(
        self,
        symbols: List[str] = None,
        volume_drop_threshold: float = None,
        spread_multiplier: float = None,
    ):
        """
        Initialize OBI auto-scaler.
        
        Args:
            symbols: Symbols to track
            volume_drop_threshold: Volume drop threshold (0.8 = 80%)
            spread_multiplier: Spread multiplier when threshold hit
        """
        self.symbols = symbols or ['BTC', 'ETH']
        self.volume_drop_threshold = volume_drop_threshold or self.DEFAULT_VOLUME_DROP_THRESHOLD
        self.spread_multiplier = spread_multiplier or self.DEFAULT_SPREAD_MULTIPLIER
        
        # Volume history
        self._volume_history: Dict[str, deque] = {}
        self._baseline_volume: Dict[str, float] = {}
        self._current_multiplier: Dict[str, float] = {}
        
        # Initialize
        for symbol in self.symbols:
            self._volume_history[symbol] = deque(maxlen=100)
            self._baseline_volume[symbol] = 0.0
            self._current_multiplier[symbol] = 1.0
    
    def add_volume(self, symbol: str, volume: float) -> float:
        """
        Add volume observation and get current spread multiplier.
        
        Returns current spread multiplier for the symbol.
        """
        if symbol not in self._volume_history:
            self._volume_history[symbol] = deque(maxlen=100)
            self._baseline_volume[symbol] = volume
            self._current_multiplier[symbol] = 1.0
        
        self._volume_history[symbol].append(volume)
        
        # Update baseline (rolling average)
        history = list(self._volume_history[symbol])
        if len(history) >= 10:
            self._baseline_volume[symbol] = sum(history[-50:]) / len(history[-50:])
        
        # Check volume drop
        baseline = self._baseline_volume[symbol]
        if baseline > 0:
            volume_ratio = volume / baseline
            
            if volume_ratio < (1 - self.volume_drop_threshold):
                # Low liquidity - widen spread
                self._current_multiplier[symbol] = self.spread_multiplier
                logger.info(f"Low liquidity detected for {symbol}, widening spread by {self.spread_multiplier}x")
            else:
                # Normal liquidity
                self._current_multiplier[symbol] = 1.0
        
        return self._current_multiplier[symbol]
    
    def get_spread_multiplier(self, symbol: str) -> float:
        """Get current spread multiplier for symbol."""
        return self._current_multiplier.get(symbol, 1.0)
    
    def is_low_liquidity(self, symbol: str) -> bool:
        """Check if symbol is in low liquidity state."""
        return self._current_multiplier.get(symbol, 1.0) > 1.0


# Factory functions
def create_flash_crash_detector(
    symbols: List[str] = None,
    drop_threshold_pct: float = 1.0,
    window_ms: float = 3000,
) -> FlashCrashDetector:
    """Create and return a FlashCrashDetector instance."""
    return FlashCrashDetector(
        symbols=symbols,
        drop_threshold_pct=drop_threshold_pct,
        window_ms=window_ms,
    )


def create_obi_auto_scaler(
    symbols: List[str] = None,
) -> OBIAutoScaler:
    """Create and return an OBIAutoScaler instance."""
    return OBIAutoScaler(symbols=symbols)
