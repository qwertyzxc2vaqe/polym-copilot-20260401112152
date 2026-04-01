"""
ZeroMQ Publisher Module - Async Messaging for Data Decoupling.

Phase 2 - Task 56: ZMQ Publisher to decouple data-ingestion loop from
execution loop, preventing blocking I/O during simulated network spikes.

Educational purpose only - paper trading simulation.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any, Callable, List
from enum import Enum

logger = logging.getLogger(__name__)

# Try to import ZMQ
try:
    import zmq
    import zmq.asyncio
    ZMQ_AVAILABLE = True
except ImportError:
    ZMQ_AVAILABLE = False
    logger.warning("ZeroMQ not available, using in-process queue fallback")


class MessageType(Enum):
    """Types of messages published via ZMQ."""
    TICK = "tick"
    DEPTH = "depth"
    TRADE = "trade"
    OFI = "ofi"
    SIGNAL = "signal"
    FUNDING = "funding"
    ALERT = "alert"


@dataclass
class ZMQMessage:
    """Wrapper for ZMQ messages."""
    msg_type: str
    symbol: str
    data: Dict[str, Any]
    timestamp: float
    sequence: int = 0
    
    def to_json(self) -> str:
        return json.dumps({
            'type': self.msg_type,
            'symbol': self.symbol,
            'data': self.data,
            'timestamp': self.timestamp,
            'sequence': self.sequence,
        })
    
    @classmethod
    def from_json(cls, raw: str) -> 'ZMQMessage':
        data = json.loads(raw)
        return cls(
            msg_type=data['type'],
            symbol=data['symbol'],
            data=data['data'],
            timestamp=data['timestamp'],
            sequence=data.get('sequence', 0),
        )


class InProcessQueue:
    """Fallback queue when ZMQ is not available."""
    
    def __init__(self, maxsize: int = 10000):
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._subscribers: List[Callable] = []
    
    async def publish(self, message: ZMQMessage) -> None:
        """Publish message to queue."""
        try:
            self._queue.put_nowait(message)
        except asyncio.QueueFull:
            # Drop oldest message
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(message)
            except:
                pass
    
    async def subscribe(self, callback: Callable) -> None:
        """Add a subscriber callback."""
        self._subscribers.append(callback)
    
    async def consume(self) -> Optional[ZMQMessage]:
        """Consume next message from queue."""
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=0.1)
        except asyncio.TimeoutError:
            return None
    
    async def consume_all(self) -> List[ZMQMessage]:
        """Consume all available messages."""
        messages = []
        while not self._queue.empty():
            try:
                messages.append(self._queue.get_nowait())
            except:
                break
        return messages


class ZMQPublisher:
    """
    ZeroMQ Publisher for async message distribution.
    
    Publishes market data to multiple subscribers without blocking.
    """
    
    DEFAULT_PORT = 5555
    DEFAULT_ADDRESS = "tcp://127.0.0.1"
    
    def __init__(
        self,
        address: str = None,
        port: int = None,
        high_water_mark: int = 10000,
    ):
        """
        Initialize ZMQ publisher.
        
        Args:
            address: ZMQ address (default: tcp://127.0.0.1)
            port: ZMQ port (default: 5555)
            high_water_mark: Max queued messages before dropping
        """
        self.address = address or self.DEFAULT_ADDRESS
        self.port = port or self.DEFAULT_PORT
        self.high_water_mark = high_water_mark
        
        self._context: Optional[zmq.asyncio.Context] = None
        self._socket: Optional[zmq.asyncio.Socket] = None
        self._running = False
        self._sequence = 0
        self._fallback_queue: Optional[InProcessQueue] = None
    
    @property
    def endpoint(self) -> str:
        return f"{self.address}:{self.port}"
    
    async def start(self) -> bool:
        """Start the ZMQ publisher."""
        if not ZMQ_AVAILABLE:
            logger.warning("ZMQ not available, using in-process queue")
            self._fallback_queue = InProcessQueue()
            self._running = True
            return True
        
        try:
            self._context = zmq.asyncio.Context()
            self._socket = self._context.socket(zmq.PUB)
            self._socket.setsockopt(zmq.SNDHWM, self.high_water_mark)
            self._socket.setsockopt(zmq.LINGER, 0)
            self._socket.bind(self.endpoint)
            
            self._running = True
            logger.info(f"ZMQ Publisher started on {self.endpoint}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start ZMQ publisher: {e}")
            # Fall back to in-process queue
            self._fallback_queue = InProcessQueue()
            self._running = True
            return True
    
    async def stop(self) -> None:
        """Stop the ZMQ publisher."""
        self._running = False
        
        if self._socket:
            self._socket.close()
            self._socket = None
        
        if self._context:
            self._context.term()
            self._context = None
        
        logger.info("ZMQ Publisher stopped")
    
    async def publish(
        self,
        msg_type: MessageType,
        symbol: str,
        data: Dict[str, Any],
    ) -> bool:
        """
        Publish a message.
        
        Args:
            msg_type: Type of message
            symbol: Symbol the message relates to
            data: Message payload
        
        Returns:
            True if published successfully
        """
        if not self._running:
            return False
        
        self._sequence += 1
        message = ZMQMessage(
            msg_type=msg_type.value,
            symbol=symbol,
            data=data,
            timestamp=time.time() * 1000,
            sequence=self._sequence,
        )
        
        # Use fallback if ZMQ not available
        if self._fallback_queue:
            await self._fallback_queue.publish(message)
            return True
        
        try:
            # Topic-based routing: topic is "TYPE.SYMBOL"
            topic = f"{msg_type.value}.{symbol}"
            await self._socket.send_multipart([
                topic.encode('utf-8'),
                message.to_json().encode('utf-8'),
            ])
            return True
        except Exception as e:
            logger.error(f"Failed to publish message: {e}")
            return False
    
    async def publish_tick(self, symbol: str, tick_data: dict) -> bool:
        """Convenience method for tick data."""
        return await self.publish(MessageType.TICK, symbol, tick_data)
    
    async def publish_depth(self, symbol: str, depth_data: dict) -> bool:
        """Convenience method for order book depth."""
        return await self.publish(MessageType.DEPTH, symbol, depth_data)
    
    async def publish_ofi(self, symbol: str, ofi_data: dict) -> bool:
        """Convenience method for OFI updates."""
        return await self.publish(MessageType.OFI, symbol, ofi_data)
    
    async def publish_signal(self, symbol: str, signal_data: dict) -> bool:
        """Convenience method for trading signals."""
        return await self.publish(MessageType.SIGNAL, symbol, signal_data)
    
    @property
    def is_running(self) -> bool:
        return self._running
    
    @property
    def message_count(self) -> int:
        return self._sequence


class ZMQSubscriber:
    """
    ZeroMQ Subscriber for receiving market data.
    
    Consumes messages from publisher asynchronously.
    """
    
    def __init__(
        self,
        address: str = "tcp://127.0.0.1",
        port: int = 5555,
        topics: List[str] = None,
        on_message: Optional[Callable] = None,
    ):
        """
        Initialize ZMQ subscriber.
        
        Args:
            address: Publisher address
            port: Publisher port
            topics: Topics to subscribe to (e.g., ['tick.BTC', 'ofi.*'])
            on_message: Callback for received messages
        """
        self.address = address
        self.port = port
        self.topics = topics or ['']  # Empty string = all topics
        self.on_message = on_message
        
        self._context: Optional[zmq.asyncio.Context] = None
        self._socket: Optional[zmq.asyncio.Socket] = None
        self._running = False
        self._message_count = 0
        self._fallback_queue: Optional[InProcessQueue] = None
    
    @property
    def endpoint(self) -> str:
        return f"{self.address}:{self.port}"
    
    async def start(self) -> bool:
        """Start the ZMQ subscriber."""
        if not ZMQ_AVAILABLE:
            logger.warning("ZMQ not available, subscriber in fallback mode")
            self._running = True
            return True
        
        try:
            self._context = zmq.asyncio.Context()
            self._socket = self._context.socket(zmq.SUB)
            self._socket.connect(self.endpoint)
            
            # Subscribe to topics
            for topic in self.topics:
                self._socket.setsockopt_string(zmq.SUBSCRIBE, topic)
            
            self._running = True
            logger.info(f"ZMQ Subscriber connected to {self.endpoint}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start ZMQ subscriber: {e}")
            return False
    
    async def stop(self) -> None:
        """Stop the subscriber."""
        self._running = False
        
        if self._socket:
            self._socket.close()
            self._socket = None
        
        if self._context:
            self._context.term()
            self._context = None
        
        logger.info("ZMQ Subscriber stopped")
    
    async def receive(self, timeout_ms: int = 100) -> Optional[ZMQMessage]:
        """
        Receive a single message.
        
        Args:
            timeout_ms: Timeout in milliseconds
        
        Returns:
            Message if received, None if timeout
        """
        if not self._running or not self._socket:
            return None
        
        try:
            if self._socket.poll(timeout_ms, zmq.POLLIN):
                parts = await self._socket.recv_multipart()
                if len(parts) >= 2:
                    topic = parts[0].decode('utf-8')
                    raw_message = parts[1].decode('utf-8')
                    message = ZMQMessage.from_json(raw_message)
                    self._message_count += 1
                    return message
        except Exception as e:
            logger.error(f"Error receiving message: {e}")
        
        return None
    
    async def run_loop(self) -> None:
        """
        Run continuous receive loop.
        
        Calls on_message callback for each received message.
        """
        if not self.on_message:
            logger.warning("No on_message callback set")
            return
        
        logger.info("Starting ZMQ subscriber loop")
        
        while self._running:
            message = await self.receive(timeout_ms=100)
            if message:
                try:
                    if asyncio.iscoroutinefunction(self.on_message):
                        await self.on_message(message)
                    else:
                        self.on_message(message)
                except Exception as e:
                    logger.error(f"Callback error: {e}")
    
    @property
    def is_running(self) -> bool:
        return self._running
    
    @property
    def message_count(self) -> int:
        return self._message_count


def set_fallback_queue(publisher: ZMQPublisher, subscriber: ZMQSubscriber) -> None:
    """
    Connect publisher and subscriber via fallback queue.
    
    Used when ZMQ is not available.
    """
    queue = InProcessQueue()
    publisher._fallback_queue = queue
    subscriber._fallback_queue = queue


# Factory functions
def create_publisher(
    address: str = "tcp://127.0.0.1",
    port: int = 5555,
) -> ZMQPublisher:
    """Create and return a ZMQ publisher."""
    return ZMQPublisher(address=address, port=port)


def create_subscriber(
    address: str = "tcp://127.0.0.1",
    port: int = 5555,
    topics: List[str] = None,
    on_message: Optional[Callable] = None,
) -> ZMQSubscriber:
    """Create and return a ZMQ subscriber."""
    return ZMQSubscriber(
        address=address,
        port=port,
        topics=topics,
        on_message=on_message,
    )
