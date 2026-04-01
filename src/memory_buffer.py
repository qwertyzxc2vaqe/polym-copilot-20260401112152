"""
Memory Buffer Module - Redis-backed IPC for Educational Simulation.

Phase 2: Transitions from local RAM to Redis for ultra-fast
Inter-Process Communication between data ingestors and simulation engine.

Educational purpose only - paper trading simulation.
"""

import json
import logging
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Deque
from collections import deque
import asyncio

logger = logging.getLogger(__name__)

# Try to import Redis, fall back to in-memory if unavailable
try:
    import redis.asyncio as aioredis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.warning("Redis not available, using in-memory buffer")


@dataclass
class TickData:
    """Represents a single market tick."""
    symbol: str
    price: float
    bid: float
    ask: float
    volume: float
    timestamp: float  # Unix timestamp in milliseconds
    source: str = "binance"
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TickData':
        return cls(**data)
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict())
    
    @classmethod
    def from_json(cls, data: str) -> 'TickData':
        return cls.from_dict(json.loads(data))


class InMemoryBuffer:
    """Fallback in-memory buffer when Redis is unavailable."""
    
    def __init__(self, max_size: int = 100000):
        self.max_size = max_size
        self._buffers: Dict[str, Deque[TickData]] = {}
        self._lock = asyncio.Lock()
    
    async def push_tick(self, tick: TickData) -> None:
        """Push a tick to the buffer."""
        async with self._lock:
            if tick.symbol not in self._buffers:
                self._buffers[tick.symbol] = deque(maxlen=self.max_size)
            self._buffers[tick.symbol].append(tick)
    
    async def get_recent_ticks(self, symbol: str, count: int = 100) -> List[TickData]:
        """Get recent ticks for a symbol."""
        async with self._lock:
            if symbol not in self._buffers:
                return []
            buffer = self._buffers[symbol]
            return list(buffer)[-count:]
    
    async def get_tick_count(self, symbol: str) -> int:
        """Get number of ticks for a symbol."""
        async with self._lock:
            if symbol not in self._buffers:
                return 0
            return len(self._buffers[symbol])
    
    async def clear(self, symbol: Optional[str] = None) -> None:
        """Clear buffer for a symbol or all symbols."""
        async with self._lock:
            if symbol:
                if symbol in self._buffers:
                    self._buffers[symbol].clear()
            else:
                self._buffers.clear()
    
    async def get_price_series(self, symbol: str, count: int = 100) -> List[float]:
        """Get price series for calculations."""
        ticks = await self.get_recent_ticks(symbol, count)
        return [t.price for t in ticks]
    
    async def get_ohlcv(self, symbol: str, interval_ms: int = 1000, periods: int = 60) -> List[Dict]:
        """Aggregate ticks into OHLCV candles."""
        ticks = await self.get_recent_ticks(symbol, count=periods * 100)
        if not ticks:
            return []
        
        candles = []
        current_candle = None
        candle_start = None
        
        for tick in ticks:
            tick_time = int(tick.timestamp)
            candle_time = (tick_time // interval_ms) * interval_ms
            
            if candle_start is None or candle_time != candle_start:
                if current_candle:
                    candles.append(current_candle)
                candle_start = candle_time
                current_candle = {
                    'timestamp': candle_time,
                    'open': tick.price,
                    'high': tick.price,
                    'low': tick.price,
                    'close': tick.price,
                    'volume': tick.volume
                }
            else:
                current_candle['high'] = max(current_candle['high'], tick.price)
                current_candle['low'] = min(current_candle['low'], tick.price)
                current_candle['close'] = tick.price
                current_candle['volume'] += tick.volume
        
        if current_candle:
            candles.append(current_candle)
        
        return candles[-periods:]


class RedisBuffer:
    """Redis-backed buffer for high-performance IPC."""
    
    TICK_KEY_PREFIX = "tick:"
    TICK_LIST_PREFIX = "ticks:"
    PRICE_SERIES_PREFIX = "prices:"
    MAX_TICKS_PER_SYMBOL = 100000
    
    def __init__(self, host: str = "localhost", port: int = 6379, db: int = 0):
        self.host = host
        self.port = port
        self.db = db
        self._redis: Optional[aioredis.Redis] = None
        self._connected = False
    
    async def connect(self) -> bool:
        """Connect to Redis server."""
        if not REDIS_AVAILABLE:
            logger.error("Redis library not available")
            return False
        
        try:
            self._redis = aioredis.Redis(
                host=self.host,
                port=self.port,
                db=self.db,
                decode_responses=True
            )
            await self._redis.ping()
            self._connected = True
            logger.info(f"Connected to Redis at {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            self._connected = False
            return False
    
    async def disconnect(self) -> None:
        """Disconnect from Redis."""
        if self._redis:
            await self._redis.close()
            self._connected = False
            logger.info("Disconnected from Redis")
    
    @property
    def is_connected(self) -> bool:
        return self._connected and self._redis is not None
    
    async def push_tick(self, tick: TickData) -> None:
        """Push a tick to Redis list."""
        if not self.is_connected:
            raise ConnectionError("Not connected to Redis")
        
        key = f"{self.TICK_LIST_PREFIX}{tick.symbol}"
        
        # Push tick as JSON
        await self._redis.lpush(key, tick.to_json())
        
        # Trim to max size
        await self._redis.ltrim(key, 0, self.MAX_TICKS_PER_SYMBOL - 1)
        
        # Also store latest price for fast lookup
        await self._redis.set(f"{self.TICK_KEY_PREFIX}{tick.symbol}:latest", tick.to_json())
    
    async def get_recent_ticks(self, symbol: str, count: int = 100) -> List[TickData]:
        """Get recent ticks from Redis."""
        if not self.is_connected:
            raise ConnectionError("Not connected to Redis")
        
        key = f"{self.TICK_LIST_PREFIX}{symbol}"
        raw_ticks = await self._redis.lrange(key, 0, count - 1)
        
        ticks = []
        for raw in raw_ticks:
            try:
                ticks.append(TickData.from_json(raw))
            except Exception as e:
                logger.warning(f"Failed to parse tick: {e}")
        
        # Reverse to get chronological order
        return list(reversed(ticks))
    
    async def get_latest_tick(self, symbol: str) -> Optional[TickData]:
        """Get the most recent tick for a symbol."""
        if not self.is_connected:
            raise ConnectionError("Not connected to Redis")
        
        raw = await self._redis.get(f"{self.TICK_KEY_PREFIX}{symbol}:latest")
        if raw:
            return TickData.from_json(raw)
        return None
    
    async def get_tick_count(self, symbol: str) -> int:
        """Get number of ticks stored for a symbol."""
        if not self.is_connected:
            raise ConnectionError("Not connected to Redis")
        
        key = f"{self.TICK_LIST_PREFIX}{symbol}"
        return await self._redis.llen(key)
    
    async def clear(self, symbol: Optional[str] = None) -> None:
        """Clear buffer for a symbol or all symbols."""
        if not self.is_connected:
            raise ConnectionError("Not connected to Redis")
        
        if symbol:
            key = f"{self.TICK_LIST_PREFIX}{symbol}"
            await self._redis.delete(key)
            await self._redis.delete(f"{self.TICK_KEY_PREFIX}{symbol}:latest")
        else:
            # Clear all tick keys
            keys = await self._redis.keys(f"{self.TICK_LIST_PREFIX}*")
            if keys:
                await self._redis.delete(*keys)
            keys = await self._redis.keys(f"{self.TICK_KEY_PREFIX}*")
            if keys:
                await self._redis.delete(*keys)
    
    async def get_price_series(self, symbol: str, count: int = 100) -> List[float]:
        """Get price series for calculations."""
        ticks = await self.get_recent_ticks(symbol, count)
        return [t.price for t in ticks]
    
    async def publish_tick(self, channel: str, tick: TickData) -> None:
        """Publish tick to Redis pub/sub channel."""
        if not self.is_connected:
            raise ConnectionError("Not connected to Redis")
        
        await self._redis.publish(channel, tick.to_json())
    
    async def subscribe(self, channel: str):
        """Subscribe to a Redis pub/sub channel."""
        if not self.is_connected:
            raise ConnectionError("Not connected to Redis")
        
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(channel)
        return pubsub


class MemoryBufferManager:
    """
    Manages memory buffer with automatic fallback.
    
    Attempts Redis connection first, falls back to in-memory if unavailable.
    """
    
    def __init__(self, redis_host: str = "localhost", redis_port: int = 6379):
        self.redis_host = redis_host
        self.redis_port = redis_port
        self._redis_buffer: Optional[RedisBuffer] = None
        self._memory_buffer: Optional[InMemoryBuffer] = None
        self._use_redis = False
    
    async def initialize(self) -> str:
        """Initialize buffer, trying Redis first then falling back to memory."""
        if REDIS_AVAILABLE:
            self._redis_buffer = RedisBuffer(self.redis_host, self.redis_port)
            if await self._redis_buffer.connect():
                self._use_redis = True
                logger.info("Using Redis buffer for IPC")
                return "redis"
        
        # Fall back to in-memory
        self._memory_buffer = InMemoryBuffer()
        self._use_redis = False
        logger.info("Using in-memory buffer (Redis unavailable)")
        return "memory"
    
    async def close(self) -> None:
        """Close buffer connections."""
        if self._redis_buffer and self._use_redis:
            await self._redis_buffer.disconnect()
    
    @property
    def buffer_type(self) -> str:
        return "redis" if self._use_redis else "memory"
    
    async def push_tick(self, tick: TickData) -> None:
        """Push tick to active buffer."""
        if self._use_redis and self._redis_buffer:
            await self._redis_buffer.push_tick(tick)
        elif self._memory_buffer:
            await self._memory_buffer.push_tick(tick)
    
    async def get_recent_ticks(self, symbol: str, count: int = 100) -> List[TickData]:
        """Get recent ticks from active buffer."""
        if self._use_redis and self._redis_buffer:
            return await self._redis_buffer.get_recent_ticks(symbol, count)
        elif self._memory_buffer:
            return await self._memory_buffer.get_recent_ticks(symbol, count)
        return []
    
    async def get_tick_count(self, symbol: str) -> int:
        """Get tick count from active buffer."""
        if self._use_redis and self._redis_buffer:
            return await self._redis_buffer.get_tick_count(symbol)
        elif self._memory_buffer:
            return await self._memory_buffer.get_tick_count(symbol)
        return 0
    
    async def get_price_series(self, symbol: str, count: int = 100) -> List[float]:
        """Get price series from active buffer."""
        if self._use_redis and self._redis_buffer:
            return await self._redis_buffer.get_price_series(symbol, count)
        elif self._memory_buffer:
            return await self._memory_buffer.get_price_series(symbol, count)
        return []
    
    async def clear(self, symbol: Optional[str] = None) -> None:
        """Clear active buffer."""
        if self._use_redis and self._redis_buffer:
            await self._redis_buffer.clear(symbol)
        elif self._memory_buffer:
            await self._memory_buffer.clear(symbol)


# Singleton instance
_buffer_manager: Optional[MemoryBufferManager] = None


async def get_buffer_manager(redis_host: str = "localhost", redis_port: int = 6379) -> MemoryBufferManager:
    """Get or create the global buffer manager."""
    global _buffer_manager
    if _buffer_manager is None:
        _buffer_manager = MemoryBufferManager(redis_host, redis_port)
        await _buffer_manager.initialize()
    return _buffer_manager
