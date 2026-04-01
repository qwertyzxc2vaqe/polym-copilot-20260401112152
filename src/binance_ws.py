"""
Binance WebSocket Module - Extended Order Book Support.

Phase 2 - Task 54: Track Top 20 levels of the order book for deeper
Order Flow Imbalance (OFI) matrix calculation.

Educational purpose only - paper trading simulation.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, List, Callable, Any, Deque
from collections import deque
from enum import Enum

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

logger = logging.getLogger(__name__)

# Try to import optimized parser
try:
    from parser import parse_ticker_fast, parse_depth_update, TickerData, calculate_order_flow_imbalance
    CYTHON_AVAILABLE = True
    logger.info("Using Cython-optimized parser")
except ImportError:
    CYTHON_AVAILABLE = False
    logger.info("Using pure Python parser (Cython not compiled)")


class StreamType(Enum):
    """Binance stream types."""
    TICKER = "ticker"
    DEPTH = "depth"
    TRADE = "trade"
    KLINE = "kline"


@dataclass
class OrderBookLevel:
    """Single order book level."""
    price: float
    quantity: float
    side: str  # 'bid' or 'ask'


@dataclass
class OrderBook:
    """Full order book snapshot with 20 levels."""
    symbol: str
    bids: List[OrderBookLevel] = field(default_factory=list)  # Sorted highest to lowest
    asks: List[OrderBookLevel] = field(default_factory=list)  # Sorted lowest to highest
    timestamp: float = 0.0
    last_update_id: int = 0
    
    @property
    def best_bid(self) -> float:
        return self.bids[0].price if self.bids else 0.0
    
    @property
    def best_ask(self) -> float:
        return self.asks[0].price if self.asks else 0.0
    
    @property
    def mid_price(self) -> float:
        if self.best_bid > 0 and self.best_ask > 0:
            return (self.best_bid + self.best_ask) / 2.0
        return 0.0
    
    @property
    def spread(self) -> float:
        return self.best_ask - self.best_bid
    
    @property
    def spread_bps(self) -> float:
        mid = self.mid_price
        if mid == 0:
            return 0.0
        return (self.spread / mid) * 10000
    
    def calculate_ofi_matrix(self, levels: int = 20) -> Dict[str, float]:
        """
        Calculate Order Flow Imbalance at various depth levels.
        
        Returns OFI values for 1, 3, 5, 10, and 20 level depths.
        """
        ofi_matrix = {}
        
        for depth in [1, 3, 5, 10, 20]:
            if depth > levels:
                break
            
            bid_volume = sum(level.quantity for level in self.bids[:depth])
            ask_volume = sum(level.quantity for level in self.asks[:depth])
            total_volume = bid_volume + ask_volume
            
            if total_volume > 0:
                ofi = (bid_volume - ask_volume) / total_volume
            else:
                ofi = 0.0
            
            ofi_matrix[f'ofi_{depth}'] = ofi
            ofi_matrix[f'bid_vol_{depth}'] = bid_volume
            ofi_matrix[f'ask_vol_{depth}'] = ask_volume
            ofi_matrix[f'total_vol_{depth}'] = total_volume
        
        return ofi_matrix
    
    def calculate_vwap_bid_ask(self, depth: int = 10) -> Dict[str, float]:
        """
        Calculate volume-weighted average bid/ask prices.
        """
        bid_vwap = 0.0
        ask_vwap = 0.0
        bid_total = 0.0
        ask_total = 0.0
        
        for level in self.bids[:depth]:
            bid_vwap += level.price * level.quantity
            bid_total += level.quantity
        
        for level in self.asks[:depth]:
            ask_vwap += level.price * level.quantity
            ask_total += level.quantity
        
        return {
            'vwap_bid': bid_vwap / bid_total if bid_total > 0 else 0.0,
            'vwap_ask': ask_vwap / ask_total if ask_total > 0 else 0.0,
            'bid_depth': bid_total,
            'ask_depth': ask_total,
        }


@dataclass
class TradeData:
    """Represents a single trade from the public tape."""
    symbol: str
    price: float
    quantity: float
    timestamp: float
    is_buyer_maker: bool  # True if buyer was maker (sell aggressor)
    trade_id: int = 0


class BinanceWebSocket:
    """
    Async WebSocket client for Binance with 20-level order book support.
    
    Maintains real-time order books and calculates OFI matrices.
    """
    
    BASE_WS_URL = "wss://stream.binance.com:9443"
    DEPTH_LEVELS = 20
    
    SYMBOL_MAP = {
        "BTCUSDT": "BTC",
        "ETHUSDT": "ETH",
        "SOLUSDT": "SOL",
        "XRPUSDT": "XRP",
        "DOGEUSDT": "DOGE",
        "BNBUSDT": "BNB",
    }
    
    def __init__(
        self,
        symbols: List[str] = None,
        on_ticker: Optional[Callable] = None,
        on_depth: Optional[Callable] = None,
        on_trade: Optional[Callable] = None,
        on_ofi_update: Optional[Callable] = None,
    ):
        """
        Initialize WebSocket client.
        
        Args:
            symbols: List of symbols to track (e.g., ['BTC', 'ETH'])
            on_ticker: Callback for ticker updates
            on_depth: Callback for depth/order book updates
            on_trade: Callback for trade updates
            on_ofi_update: Callback for OFI matrix updates
        """
        self.symbols = symbols or ['BTC', 'ETH']
        self.on_ticker = on_ticker
        self.on_depth = on_depth
        self.on_trade = on_trade
        self.on_ofi_update = on_ofi_update
        
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._running = False
        self._order_books: Dict[str, OrderBook] = {}
        self._trade_buffer: Dict[str, Deque[TradeData]] = {}
        self._last_ofi: Dict[str, Dict[str, float]] = {}
        
        # Initialize order books
        for symbol in self.symbols:
            self._order_books[symbol] = OrderBook(symbol=symbol)
            self._trade_buffer[symbol] = deque(maxlen=1000)
    
    def _build_stream_url(self) -> str:
        """Build combined stream URL for all symbols."""
        streams = []
        for symbol in self.symbols:
            binance_symbol = f"{symbol.lower()}usdt"
            streams.append(f"{binance_symbol}@ticker")
            streams.append(f"{binance_symbol}@depth{self.DEPTH_LEVELS}@100ms")
            streams.append(f"{binance_symbol}@trade")
        
        stream_path = "/".join(streams)
        return f"{self.BASE_WS_URL}/stream?streams={stream_path}"
    
    async def connect(self) -> None:
        """Connect to Binance WebSocket and start processing messages."""
        url = self._build_stream_url()
        logger.info(f"Connecting to Binance WebSocket: {url[:100]}...")
        
        self._running = True
        
        while self._running:
            try:
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    logger.info("Connected to Binance WebSocket")
                    
                    async for message in ws:
                        if not self._running:
                            break
                        await self._process_message(message)
                        
            except ConnectionClosed as e:
                logger.warning(f"WebSocket connection closed: {e}")
                if self._running:
                    await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                if self._running:
                    await asyncio.sleep(5)
    
    async def close(self) -> None:
        """Close WebSocket connection."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        logger.info("WebSocket closed")
    
    async def _process_message(self, raw_message: str) -> None:
        """Process incoming WebSocket message."""
        try:
            message = json.loads(raw_message)
            stream = message.get('stream', '')
            data = message.get('data', {})
            
            if '@ticker' in stream:
                await self._handle_ticker(data)
            elif '@depth' in stream:
                await self._handle_depth(data)
            elif '@trade' in stream:
                await self._handle_trade(data)
                
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse message: {e}")
        except Exception as e:
            logger.error(f"Error processing message: {e}")
    
    async def _handle_ticker(self, data: dict) -> None:
        """Handle ticker update."""
        if CYTHON_AVAILABLE:
            ticker = parse_ticker_fast(data)
            ticker_dict = ticker.to_dict()
        else:
            ticker_dict = self._parse_ticker_python(data)
        
        if self.on_ticker:
            await self._safe_callback(self.on_ticker, ticker_dict)
    
    async def _handle_depth(self, data: dict) -> None:
        """Handle order book depth update."""
        raw_symbol = data.get('s', '')
        symbol = self.SYMBOL_MAP.get(raw_symbol, raw_symbol.replace('USDT', ''))
        
        if symbol not in self._order_books:
            return
        
        book = self._order_books[symbol]
        
        # Parse bids and asks
        bids_raw = data.get('bids', data.get('b', []))
        asks_raw = data.get('asks', data.get('a', []))
        
        book.bids = [
            OrderBookLevel(price=float(b[0]), quantity=float(b[1]), side='bid')
            for b in bids_raw[:self.DEPTH_LEVELS]
        ]
        book.asks = [
            OrderBookLevel(price=float(a[0]), quantity=float(a[1]), side='ask')
            for a in asks_raw[:self.DEPTH_LEVELS]
        ]
        book.timestamp = time.time() * 1000
        book.last_update_id = data.get('lastUpdateId', data.get('u', 0))
        
        # Calculate OFI matrix
        ofi_matrix = book.calculate_ofi_matrix(self.DEPTH_LEVELS)
        self._last_ofi[symbol] = ofi_matrix
        
        if self.on_depth:
            await self._safe_callback(self.on_depth, {
                'symbol': symbol,
                'book': book,
                'ofi': ofi_matrix,
            })
        
        if self.on_ofi_update:
            await self._safe_callback(self.on_ofi_update, {
                'symbol': symbol,
                'ofi': ofi_matrix,
                'mid_price': book.mid_price,
                'spread_bps': book.spread_bps,
            })
    
    async def _handle_trade(self, data: dict) -> None:
        """Handle trade update."""
        raw_symbol = data.get('s', '')
        symbol = self.SYMBOL_MAP.get(raw_symbol, raw_symbol.replace('USDT', ''))
        
        trade = TradeData(
            symbol=symbol,
            price=float(data.get('p', 0)),
            quantity=float(data.get('q', 0)),
            timestamp=int(data.get('T', 0)),
            is_buyer_maker=data.get('m', False),
            trade_id=int(data.get('t', 0)),
        )
        
        if symbol in self._trade_buffer:
            self._trade_buffer[symbol].append(trade)
        
        if self.on_trade:
            await self._safe_callback(self.on_trade, trade)
    
    def _parse_ticker_python(self, data: dict) -> dict:
        """Pure Python ticker parser fallback."""
        raw_symbol = data.get('s', '')
        symbol = self.SYMBOL_MAP.get(raw_symbol, raw_symbol.replace('USDT', ''))
        
        return {
            'symbol': symbol,
            'price': float(data.get('c', 0)),
            'bid': float(data.get('b', 0)),
            'ask': float(data.get('a', 0)),
            'volume': float(data.get('v', 0)),
            'timestamp': int(data.get('E', 0)),
            'source': 'binance',
        }
    
    async def _safe_callback(self, callback: Callable, data: Any) -> None:
        """Execute callback safely."""
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(data)
            else:
                callback(data)
        except Exception as e:
            logger.error(f"Callback error: {e}")
    
    def get_order_book(self, symbol: str) -> Optional[OrderBook]:
        """Get current order book for symbol."""
        return self._order_books.get(symbol)
    
    def get_ofi(self, symbol: str) -> Dict[str, float]:
        """Get latest OFI matrix for symbol."""
        return self._last_ofi.get(symbol, {})
    
    def get_recent_trades(self, symbol: str, count: int = 100) -> List[TradeData]:
        """Get recent trades for symbol."""
        if symbol in self._trade_buffer:
            return list(self._trade_buffer[symbol])[-count:]
        return []
    
    def get_trade_flow(self, symbol: str, window_ms: int = 5000) -> Dict[str, float]:
        """
        Analyze recent trade flow direction.
        
        Returns buy/sell pressure metrics.
        """
        trades = self.get_recent_trades(symbol, 500)
        if not trades:
            return {'buy_pressure': 0.5, 'sell_pressure': 0.5, 'net_flow': 0.0}
        
        now = time.time() * 1000
        recent = [t for t in trades if now - t.timestamp <= window_ms]
        
        if not recent:
            return {'buy_pressure': 0.5, 'sell_pressure': 0.5, 'net_flow': 0.0}
        
        buy_volume = sum(t.quantity for t in recent if not t.is_buyer_maker)
        sell_volume = sum(t.quantity for t in recent if t.is_buyer_maker)
        total_volume = buy_volume + sell_volume
        
        if total_volume == 0:
            return {'buy_pressure': 0.5, 'sell_pressure': 0.5, 'net_flow': 0.0}
        
        return {
            'buy_pressure': buy_volume / total_volume,
            'sell_pressure': sell_volume / total_volume,
            'net_flow': (buy_volume - sell_volume) / total_volume,
            'buy_volume': buy_volume,
            'sell_volume': sell_volume,
            'trade_count': len(recent),
        }


# Factory function
def create_binance_ws(
    symbols: List[str] = None,
    on_ticker: Optional[Callable] = None,
    on_depth: Optional[Callable] = None,
    on_trade: Optional[Callable] = None,
    on_ofi_update: Optional[Callable] = None,
) -> BinanceWebSocket:
    """Create and return a BinanceWebSocket instance."""
    return BinanceWebSocket(
        symbols=symbols,
        on_ticker=on_ticker,
        on_depth=on_depth,
        on_trade=on_trade,
        on_ofi_update=on_ofi_update,
    )
