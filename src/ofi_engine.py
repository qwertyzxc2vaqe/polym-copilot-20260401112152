"""
Order Flow Imbalance (OFI) Engine for Binance Order Book Depth.

Calculates real-time buying vs selling pressure by analyzing the volume
of limit orders added/removed at the top 5 levels of the Binance order book.

OFI = Σ(bid_volume_changes) - Σ(ask_volume_changes)
- Positive OFI = Buying pressure (bullish)
- Negative OFI = Selling pressure (bearish)

Used to tilt market maker spreads in the direction of order flow.
"""

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Callable, Awaitable
from enum import Enum

import websockets

logger = logging.getLogger(__name__)


class OFISignal(Enum):
    """Order Flow Imbalance signal direction."""
    STRONG_BUY = "strong_buy"    # OFI > +2 std dev
    BUY = "buy"                  # OFI > +1 std dev
    NEUTRAL = "neutral"          # Within 1 std dev
    SELL = "sell"                # OFI < -1 std dev
    STRONG_SELL = "strong_sell"  # OFI < -2 std dev


@dataclass
class OrderBookLevel:
    """Single price level in order book."""
    price: float
    quantity: float
    timestamp: float = field(default_factory=time.time)


@dataclass
class OrderBookSnapshot:
    """Snapshot of top N levels of order book."""
    symbol: str
    bids: List[OrderBookLevel]  # Sorted by price descending
    asks: List[OrderBookLevel]  # Sorted by price ascending
    timestamp: float = field(default_factory=time.time)
    
    @property
    def best_bid(self) -> Optional[float]:
        return self.bids[0].price if self.bids else None
    
    @property
    def best_ask(self) -> Optional[float]:
        return self.asks[0].price if self.asks else None
    
    @property
    def mid_price(self) -> Optional[float]:
        if self.best_bid and self.best_ask:
            return (self.best_bid + self.best_ask) / 2
        return None
    
    @property
    def spread(self) -> Optional[float]:
        if self.best_bid and self.best_ask:
            return self.best_ask - self.best_bid
        return None
    
    def total_bid_volume(self, levels: int = 5) -> float:
        """Sum of bid quantities at top N levels."""
        return sum(b.quantity for b in self.bids[:levels])
    
    def total_ask_volume(self, levels: int = 5) -> float:
        """Sum of ask quantities at top N levels."""
        return sum(a.quantity for a in self.asks[:levels])


@dataclass
class OFIState:
    """Current OFI state for dashboard display."""
    symbol: str
    ofi_value: float              # Raw OFI value
    ofi_normalized: float         # -1 to +1 normalized
    signal: OFISignal
    bid_pressure: float           # Total bid volume change
    ask_pressure: float           # Total ask volume change
    imbalance_ratio: float        # bid_volume / ask_volume
    spike_detected: bool          # >0.1% move in 500ms
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class OFIEngine:
    """
    Real-time Order Flow Imbalance calculator.
    
    Connects to Binance depth WebSocket and calculates OFI by tracking
    changes in order book volume at the top 5 price levels.
    
    Features:
    - Sub-second OFI updates via WebSocket
    - Rolling window for OFI normalization (configurable)
    - Spike detection for directional bias override
    - Callbacks for signal notifications
    """
    
    # Binance WebSocket endpoints
    WS_BASE_URL = "wss://stream.binance.com:9443/ws"
    
    # OFI configuration
    TOP_LEVELS = 5                # Track top 5 bid/ask levels
    ROLLING_WINDOW_SECONDS = 60   # Window for normalization
    SPIKE_THRESHOLD_PCT = 0.001   # 0.1% price change
    SPIKE_WINDOW_MS = 500         # 500ms window for spike detection
    
    def __init__(
        self,
        symbols: List[str] = None,
        on_signal: Optional[Callable[[str, OFIState], Awaitable[None]]] = None,
    ):
        """
        Initialize OFI Engine.
        
        Args:
            symbols: List of symbols to track (e.g., ["btcusdt", "ethusdt"])
            on_signal: Async callback when signal changes
        """
        self.symbols = [s.lower() for s in (symbols or ["btcusdt", "ethusdt"])]
        self.on_signal = on_signal
        
        # State per symbol
        self._snapshots: Dict[str, OrderBookSnapshot] = {}
        self._prev_snapshots: Dict[str, OrderBookSnapshot] = {}
        self._ofi_history: Dict[str, deque] = {
            s: deque(maxlen=1000) for s in self.symbols
        }
        self._price_history: Dict[str, deque] = {
            s: deque(maxlen=100) for s in self.symbols
        }
        self._ofi_state: Dict[str, OFIState] = {}
        
        # Connection state
        self._ws = None
        self._running = False
        self._reconnect_delay = 1
        self._last_message_time: Dict[str, float] = {}
    
    async def start(self):
        """Start OFI engine and connect to Binance WebSocket."""
        self._running = True
        
        while self._running:
            try:
                await self._connect_and_stream()
            except Exception as e:
                if self._running:
                    logger.error(f"OFI WebSocket error: {e}, reconnecting in {self._reconnect_delay}s")
                    await asyncio.sleep(self._reconnect_delay)
                    self._reconnect_delay = min(self._reconnect_delay * 2, 30)
    
    async def stop(self):
        """Stop OFI engine and close WebSocket."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        logger.info("OFI Engine stopped")
    
    async def _connect_and_stream(self):
        """Connect to Binance and stream depth updates."""
        # Build combined stream URL for all symbols
        streams = "/".join([f"{s}@depth@100ms" for s in self.symbols])
        url = f"{self.WS_BASE_URL}/{streams}" if len(self.symbols) == 1 else \
              f"wss://stream.binance.com:9443/stream?streams={streams}"
        
        logger.info(f"Connecting to Binance OFI stream: {self.symbols}")
        
        async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
            self._ws = ws
            self._reconnect_delay = 1  # Reset backoff on successful connect
            logger.info("OFI WebSocket connected")
            
            async for message in ws:
                if not self._running:
                    break
                
                try:
                    await self._process_message(message)
                except Exception as e:
                    logger.warning(f"Error processing OFI message: {e}")
    
    async def _process_message(self, message: str):
        """Process incoming depth update message."""
        data = json.loads(message)
        
        # Handle combined stream format
        if "stream" in data:
            symbol = data["stream"].split("@")[0]
            data = data["data"]
        else:
            symbol = data.get("s", "").lower()
        
        if not symbol or symbol not in self.symbols:
            return
        
        # Parse order book update
        bids = [
            OrderBookLevel(price=float(b[0]), quantity=float(b[1]))
            for b in data.get("b", [])[:self.TOP_LEVELS]
        ]
        asks = [
            OrderBookLevel(price=float(a[0]), quantity=float(a[1]))
            for a in data.get("a", [])[:self.TOP_LEVELS]
        ]
        
        # Sort: bids descending, asks ascending
        bids.sort(key=lambda x: x.price, reverse=True)
        asks.sort(key=lambda x: x.price)
        
        current_time = time.time()
        snapshot = OrderBookSnapshot(
            symbol=symbol,
            bids=bids,
            asks=asks,
            timestamp=current_time,
        )
        
        # Store for OFI calculation
        self._prev_snapshots[symbol] = self._snapshots.get(symbol)
        self._snapshots[symbol] = snapshot
        self._last_message_time[symbol] = current_time
        
        # Calculate OFI if we have previous snapshot
        if self._prev_snapshots.get(symbol):
            await self._calculate_ofi(symbol)
    
    async def _calculate_ofi(self, symbol: str):
        """Calculate Order Flow Imbalance for symbol."""
        current = self._snapshots[symbol]
        previous = self._prev_snapshots[symbol]
        
        # Calculate volume changes at each level
        bid_change = current.total_bid_volume(self.TOP_LEVELS) - \
                     previous.total_bid_volume(self.TOP_LEVELS)
        ask_change = current.total_ask_volume(self.TOP_LEVELS) - \
                     previous.total_ask_volume(self.TOP_LEVELS)
        
        # OFI = bid volume increase - ask volume increase
        # Positive = buying pressure, Negative = selling pressure
        ofi = bid_change - ask_change
        
        # Store in history
        self._ofi_history[symbol].append((time.time(), ofi))
        
        # Track price for spike detection
        if current.mid_price:
            self._price_history[symbol].append((time.time(), current.mid_price))
        
        # Normalize OFI (-1 to +1) using rolling std dev
        ofi_normalized = self._normalize_ofi(symbol, ofi)
        
        # Determine signal
        signal = self._classify_signal(ofi_normalized)
        
        # Check for price spike
        spike_detected = self._detect_spike(symbol)
        
        # Calculate imbalance ratio
        total_bid = current.total_bid_volume(self.TOP_LEVELS)
        total_ask = current.total_ask_volume(self.TOP_LEVELS)
        imbalance_ratio = total_bid / total_ask if total_ask > 0 else 1.0
        
        # Update state
        state = OFIState(
            symbol=symbol.upper(),
            ofi_value=ofi,
            ofi_normalized=ofi_normalized,
            signal=signal,
            bid_pressure=bid_change,
            ask_pressure=ask_change,
            imbalance_ratio=imbalance_ratio,
            spike_detected=spike_detected,
        )
        
        prev_signal = self._ofi_state.get(symbol, OFIState(
            symbol=symbol.upper(), ofi_value=0, ofi_normalized=0,
            signal=OFISignal.NEUTRAL, bid_pressure=0, ask_pressure=0,
            imbalance_ratio=1.0, spike_detected=False
        )).signal
        
        self._ofi_state[symbol] = state
        
        # Fire callback on signal change or spike
        if self.on_signal and (signal != prev_signal or spike_detected):
            try:
                await self.on_signal(symbol, state)
            except Exception as e:
                logger.warning(f"OFI callback error: {e}")
    
    def _normalize_ofi(self, symbol: str, ofi: float) -> float:
        """Normalize OFI to -1 to +1 range using rolling statistics."""
        history = self._ofi_history[symbol]
        
        if len(history) < 10:
            # Not enough data, use simple clip
            return max(-1.0, min(1.0, ofi / 100))
        
        # Get OFI values from history
        values = [v for _, v in history]
        
        # Calculate mean and std dev
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        std_dev = variance ** 0.5 if variance > 0 else 1.0
        
        if std_dev == 0:
            return 0.0
        
        # Z-score normalization, clamped to [-1, 1]
        z_score = (ofi - mean) / std_dev
        return max(-1.0, min(1.0, z_score / 2))  # Divide by 2 to scale
    
    def _classify_signal(self, ofi_normalized: float) -> OFISignal:
        """Classify OFI into signal category."""
        if ofi_normalized > 0.8:
            return OFISignal.STRONG_BUY
        elif ofi_normalized > 0.3:
            return OFISignal.BUY
        elif ofi_normalized < -0.8:
            return OFISignal.STRONG_SELL
        elif ofi_normalized < -0.3:
            return OFISignal.SELL
        else:
            return OFISignal.NEUTRAL
    
    def _detect_spike(self, symbol: str) -> bool:
        """
        Detect price spike (>0.1% change in 500ms).
        
        Used for 79% directional bias override.
        """
        history = self._price_history[symbol]
        
        if len(history) < 2:
            return False
        
        current_time = time.time()
        current_price = history[-1][1]
        
        # Find price from ~500ms ago
        cutoff_time = current_time - (self.SPIKE_WINDOW_MS / 1000)
        
        old_price = None
        for ts, price in history:
            if ts <= cutoff_time:
                old_price = price
        
        if old_price is None or old_price == 0:
            return False
        
        # Calculate percentage change
        pct_change = abs(current_price - old_price) / old_price
        
        return pct_change > self.SPIKE_THRESHOLD_PCT
    
    def get_state(self, symbol: str) -> Optional[OFIState]:
        """Get current OFI state for symbol."""
        return self._ofi_state.get(symbol.lower())
    
    def get_all_states(self) -> Dict[str, OFIState]:
        """Get OFI states for all tracked symbols."""
        return {k.upper(): v for k, v in self._ofi_state.items()}
    
    def get_snapshot(self, symbol: str) -> Optional[OrderBookSnapshot]:
        """Get current order book snapshot for symbol."""
        return self._snapshots.get(symbol.lower())
    
    def is_stale(self, symbol: str, threshold_seconds: float = 2.0) -> bool:
        """Check if data for symbol is stale."""
        last_time = self._last_message_time.get(symbol.lower(), 0)
        return (time.time() - last_time) > threshold_seconds
    
    def get_directional_bias(self, symbol: str) -> float:
        """
        Get directional bias factor for spread tilting.
        
        Returns:
            -1.0 to +1.0 where:
            - +1.0 = Strong buying pressure, tilt towards YES
            - -1.0 = Strong selling pressure, tilt towards NO
            - 0.0 = Neutral
        """
        state = self.get_state(symbol)
        if not state:
            return 0.0
        
        # Use normalized OFI directly
        return state.ofi_normalized


async def create_ofi_engine(
    symbols: List[str] = None,
    on_signal: Optional[Callable[[str, OFIState], Awaitable[None]]] = None,
) -> OFIEngine:
    """
    Factory function to create and start OFI engine.
    
    Args:
        symbols: List of symbols to track
        on_signal: Async callback for signal changes
    
    Returns:
        Running OFIEngine instance
    """
    engine = OFIEngine(symbols=symbols, on_signal=on_signal)
    
    # Start in background
    asyncio.create_task(engine.start())
    
    # Wait for initial connection
    await asyncio.sleep(1)
    
    logger.info(f"OFI Engine created for {engine.symbols}")
    return engine


# Test
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    async def on_signal(symbol: str, state: OFIState):
        print(f"[{symbol}] OFI: {state.ofi_normalized:+.2f} | Signal: {state.signal.value} | Spike: {state.spike_detected}")
    
    async def main():
        engine = await create_ofi_engine(
            symbols=["btcusdt", "ethusdt"],
            on_signal=on_signal
        )
        
        try:
            while True:
                await asyncio.sleep(5)
                for symbol in ["btcusdt", "ethusdt"]:
                    state = engine.get_state(symbol)
                    if state:
                        print(f"{symbol}: bias={engine.get_directional_bias(symbol):+.2f}")
        except KeyboardInterrupt:
            await engine.stop()
    
    asyncio.run(main())
