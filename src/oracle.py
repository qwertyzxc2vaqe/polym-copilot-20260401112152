"""
Binance Oracle WebSocket Module

Provides real-time price feeds from Binance for crypto/USDT pairs.
Supported: BTC, ETH, SOL, XRP, DOGE, BNB
Not available on Binance: HYPE (requires alternative oracle)
Used for validating "True Asset Price" in crypto prediction markets.
"""

import asyncio
import json
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, Deque
from threading import Lock

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

logger = logging.getLogger(__name__)


@dataclass
class PriceData:
    """Represents a single price point from Binance."""
    symbol: str  # BTC or ETH
    price: float
    timestamp: datetime
    bid: float
    ask: float
    
    @property
    def mid_price(self) -> float:
        """Calculate mid price from bid/ask spread."""
        return (self.bid + self.ask) / 2
    
    @property
    def spread(self) -> float:
        """Calculate bid/ask spread."""
        return self.ask - self.bid
    
    @property
    def spread_pct(self) -> float:
        """Calculate spread as percentage of mid price."""
        mid = self.mid_price
        if mid == 0:
            return 0.0
        return (self.spread / mid) * 100


@dataclass
class PriceEntry:
    """Internal structure for rolling window storage."""
    price: float
    timestamp: datetime


class BinanceOracle:
    """
    Async WebSocket client for Binance price feeds.
    
    Maintains rolling averages and provides instant price lookups
    with staleness detection for BTC/USDT and ETH/USDT pairs.
    """
    
    # Binance WebSocket endpoints
    BASE_WS_URL = "wss://stream.binance.com:9443"
    COMBINED_STREAMS = "btcusdt@ticker/ethusdt@ticker/solusdt@ticker/xrpusdt@ticker/dogeusdt@ticker/bnbusdt@ticker"
    
    # Symbol mapping from Binance format to internal format
    SYMBOL_MAP = {
        "BTCUSDT": "BTC",
        "ETHUSDT": "ETH",
        "SOLUSDT": "SOL",
        "XRPUSDT": "XRP",
        "DOGEUSDT": "DOGE",
        "BNBUSDT": "BNB",
    }
    
    # Symbols not available on Binance (require alternative oracle)
    UNSUPPORTED_SYMBOLS = {"HYPE"}
    
    def __init__(
        self,
        rolling_window_seconds: int = 30,
        ws_url: Optional[str] = None,
        staleness_threshold: int = 5,
    ):
        """
        Initialize the Binance Oracle.
        
        Args:
            rolling_window_seconds: Time window for rolling average calculation
            ws_url: Optional override for WebSocket base URL
            staleness_threshold: Default threshold for staleness detection (seconds)
        """
        self._ws_url = ws_url or self.BASE_WS_URL
        self._rolling_window = rolling_window_seconds
        self._staleness_threshold = staleness_threshold
        
        # Price storage with thread-safe access
        self._lock = Lock()
        self._prices: Dict[str, Deque[PriceEntry]] = {
            "BTC": deque(),
            "ETH": deque(),
            "SOL": deque(),
            "XRP": deque(),
            "DOGE": deque(),
            "BNB": deque(),
        }
        self._latest: Dict[str, PriceData] = {}
        
        # Connection state
        self._connected = False
        self._running = False
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 10
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        
    @classmethod
    def from_config(cls) -> "BinanceOracle":
        """
        Create oracle instance from application config.
        
        Returns:
            Configured BinanceOracle instance
        """
        from config import get_config
        
        config = get_config()
        return cls(
            rolling_window_seconds=config.oracle.rolling_window,
            ws_url=config.oracle.ws_url,
            staleness_threshold=config.oracle.staleness_threshold,
        )
    
    async def connect(self) -> None:
        """
        Connect to Binance WebSocket and start streaming.
        
        Handles auto-reconnection with exponential backoff on connection failures.
        """
        self._running = True
        
        while self._running:
            try:
                await self._connect_and_stream()
            except ConnectionClosed as e:
                logger.warning(f"WebSocket connection closed: {e.code} - {e.reason}")
                self._connected = False
                if self._running:
                    await self._handle_reconnect()
            except WebSocketException as e:
                logger.error(f"WebSocket error: {e}")
                self._connected = False
                if self._running:
                    await self._handle_reconnect()
            except asyncio.CancelledError:
                logger.info("Oracle connection cancelled")
                self._running = False
                break
            except Exception as e:
                logger.exception(f"Unexpected error in oracle: {e}")
                self._connected = False
                if self._running:
                    await self._handle_reconnect()
    
    async def _connect_and_stream(self) -> None:
        """Establish connection and process messages."""
        # Use combined stream endpoint with proper Binance format:
        # wss://stream.binance.com:9443/stream?streams=btcusdt@ticker/ethusdt@ticker/...
        url = f"{self._ws_url}/stream?streams={self.COMBINED_STREAMS}"
        logger.info(f"Connecting to Binance WebSocket: {url}")
        
        async with websockets.connect(
            url,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5,
        ) as ws:
            self._ws = ws
            self._connected = True
            self._reconnect_attempts = 0
            logger.info("Connected to Binance WebSocket")
            
            async for message in ws:
                await self._handle_message(message)
    
    async def _handle_message(self, raw_message: str) -> None:
        """
        Parse and process incoming WebSocket message.
        
        Args:
            raw_message: Raw JSON message from Binance
        """
        try:
            data = json.loads(raw_message)
            
            # Combined stream wraps data in 'data' field
            if "data" in data:
                ticker = data["data"]
            else:
                ticker = data
            
            # Validate message type
            event_type = ticker.get("e")
            if event_type != "24hrTicker":
                return
            
            symbol_raw = ticker.get("s")
            if symbol_raw not in self.SYMBOL_MAP:
                return
            
            symbol = self.SYMBOL_MAP[symbol_raw]
            
            # Parse price data
            price_data = PriceData(
                symbol=symbol,
                price=float(ticker.get("c", 0)),  # Current price
                timestamp=datetime.now(timezone.utc),
                bid=float(ticker.get("b", 0)),    # Best bid
                ask=float(ticker.get("a", 0)),    # Best ask
            )
            
            self._update_price(price_data)
            
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse message: {e}")
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Malformed ticker data: {e}")
    
    def _update_price(self, price_data: PriceData) -> None:
        """
        Thread-safe price update with rolling window management.
        
        Args:
            price_data: New price data to store
        """
        with self._lock:
            symbol = price_data.symbol
            
            # Update latest price
            self._latest[symbol] = price_data
            
            # Add to rolling window
            entry = PriceEntry(
                price=price_data.price,
                timestamp=price_data.timestamp,
            )
            self._prices[symbol].append(entry)
            
            # Prune old entries
            self._prune_old_entries(symbol)
    
    def _prune_old_entries(self, symbol: str) -> None:
        """
        Remove entries older than rolling window.
        
        Args:
            symbol: Symbol to prune (BTC or ETH)
        """
        cutoff = datetime.now(timezone.utc).timestamp() - self._rolling_window
        prices = self._prices[symbol]
        
        while prices and prices[0].timestamp.timestamp() < cutoff:
            prices.popleft()
    
    async def _handle_reconnect(self) -> None:
        """Handle reconnection with exponential backoff."""
        self._reconnect_attempts += 1
        
        if self._reconnect_attempts > self._max_reconnect_attempts:
            logger.error("Max reconnection attempts reached, stopping oracle")
            self._running = False
            return
        
        # Exponential backoff: 1s, 2s, 4s, 8s, ... max 60s
        delay = min(2 ** (self._reconnect_attempts - 1), 60)
        logger.info(f"Reconnecting in {delay}s (attempt {self._reconnect_attempts})")
        await asyncio.sleep(delay)
    
    def get_price(self, symbol: str) -> Optional[PriceData]:
        """
        Get latest price for symbol.
        
        Args:
            symbol: BTC or ETH
            
        Returns:
            Latest PriceData or None if not available
        """
        symbol = symbol.upper()
        if symbol not in self._prices:
            if symbol in self.UNSUPPORTED_SYMBOLS:
                logger.warning(f"Symbol {symbol} not available on Binance")
            else:
                logger.warning(f"Invalid symbol requested: {symbol}")
            return None
        
        with self._lock:
            return self._latest.get(symbol)
    
    def get_rolling_average(self, symbol: str) -> Optional[float]:
        """
        Get rolling average price over configured window.
        
        Args:
            symbol: BTC or ETH
            
        Returns:
            Average price or None if no data available
        """
        symbol = symbol.upper()
        if symbol not in self._prices:
            return None
        
        with self._lock:
            # First prune old entries
            self._prune_old_entries(symbol)
            
            prices = self._prices[symbol]
            if not prices:
                return None
            
            total = sum(entry.price for entry in prices)
            return total / len(prices)
    
    def is_stale(self, symbol: str, threshold_seconds: Optional[int] = None) -> bool:
        """
        Check if price data is stale.
        
        Args:
            symbol: BTC or ETH
            threshold_seconds: Optional override for staleness threshold
            
        Returns:
            True if data is stale or unavailable
        """
        threshold = threshold_seconds or self._staleness_threshold
        
        price_data = self.get_price(symbol)
        if price_data is None:
            return True
        
        age = (datetime.now(timezone.utc) - price_data.timestamp).total_seconds()
        return age > threshold
    
    def get_direction(self, symbol: str, current_market_price: float) -> str:
        """
        Determine if price is going UP or DOWN based on oracle data.
        
        Compares current market price against rolling average to determine
        likely direction for prediction market validation.
        
        Args:
            symbol: BTC or ETH
            current_market_price: Current market price to compare
            
        Returns:
            "UP" if price above rolling average, "DOWN" otherwise
        """
        rolling_avg = self.get_rolling_average(symbol)
        
        if rolling_avg is None:
            logger.warning(f"No rolling average available for {symbol}, defaulting to UP")
            return "UP"
        
        return "UP" if current_market_price >= rolling_avg else "DOWN"
    
    def get_price_momentum(self, symbol: str) -> Optional[float]:
        """
        Calculate price momentum as percentage change from oldest to newest.
        
        Args:
            symbol: BTC or ETH
            
        Returns:
            Momentum percentage or None if insufficient data
        """
        symbol = symbol.upper()
        
        with self._lock:
            self._prune_old_entries(symbol)
            prices = self._prices[symbol]
            
            if len(prices) < 2:
                return None
            
            oldest = prices[0].price
            newest = prices[-1].price
            
            if oldest == 0:
                return None
            
            return ((newest - oldest) / oldest) * 100
    
    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is currently connected."""
        return self._connected
    
    @property
    def supported_symbols(self) -> list[str]:
        """Get list of supported symbols."""
        return ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB"]
    
    async def disconnect(self) -> None:
        """Gracefully disconnect from WebSocket."""
        logger.info("Disconnecting from Binance WebSocket")
        self._running = False
        
        if self._ws and not self._ws.closed:
            try:
                await self._ws.close()
            except Exception as e:
                logger.debug(f"Error closing WebSocket: {e}")
        
        self._connected = False
        logger.info("Disconnected from Binance WebSocket")
    
    async def close(self) -> None:
        """Alias for disconnect() for consistency with other components."""
        await self.disconnect()
    
    def get_status(self) -> dict:
        """
        Get current oracle status for monitoring.
        
        Returns:
            Status dictionary with connection and data info
        """
        status = {
            "connected": self._connected,
            "reconnect_attempts": self._reconnect_attempts,
            "symbols": {},
        }
        
        for symbol in self.supported_symbols:
            price = self.get_price(symbol)
            status["symbols"][symbol] = {
                "has_data": price is not None,
                "latest_price": price.price if price else None,
                "is_stale": self.is_stale(symbol),
                "rolling_average": self.get_rolling_average(symbol),
                "momentum_pct": self.get_price_momentum(symbol),
                "data_points": len(self._prices[symbol]),
            }
        
        return status


# Convenience function for standalone usage
async def run_oracle(duration_seconds: Optional[int] = None) -> None:
    """
    Run oracle as standalone service.
    
    Args:
        duration_seconds: Optional duration to run (None for indefinite)
    """
    oracle = BinanceOracle.from_config()
    
    async def status_reporter():
        """Periodically report oracle status."""
        while oracle._running:
            await asyncio.sleep(10)
            if oracle.is_connected:
                status = oracle.get_status()
                for symbol, data in status["symbols"].items():
                    if data["has_data"]:
                        logger.info(
                            f"{symbol}: ${data['latest_price']:.2f} "
                            f"(avg: ${data['rolling_average']:.2f}, "
                            f"momentum: {data['momentum_pct']:.3f}%)"
                        )
    
    try:
        # Start status reporter
        reporter_task = asyncio.create_task(status_reporter())
        
        if duration_seconds:
            # Run for specified duration
            connect_task = asyncio.create_task(oracle.connect())
            await asyncio.sleep(duration_seconds)
            await oracle.disconnect()
            reporter_task.cancel()
            connect_task.cancel()
        else:
            # Run indefinitely
            await asyncio.gather(
                oracle.connect(),
                reporter_task,
            )
    except asyncio.CancelledError:
        await oracle.disconnect()


if __name__ == "__main__":
    # Standalone execution for testing
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )
    
    print("Starting Binance Oracle (Ctrl+C to stop)...")
    try:
        asyncio.run(run_oracle())
    except KeyboardInterrupt:
        print("\nOracle stopped.")
