"""
High-Frequency WebSocket Sniper for Polymarket Order Book Streaming.

Provides microsecond-accurate Best Bid/Ask state tracking with non-blocking
async message processing for low-latency arbitrage operations.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Callable, Dict, List, Set, Any
from enum import Enum

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from config import get_config

logger = logging.getLogger(__name__)


class ConnectionState(Enum):
    """WebSocket connection states."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


@dataclass
class OrderBookLevel:
    """Represents a single price level in the order book."""
    price: float
    size: float
    timestamp: datetime
    
    def __post_init__(self):
        if isinstance(self.timestamp, str):
            self.timestamp = datetime.fromisoformat(self.timestamp.replace('Z', '+00:00'))


@dataclass
class OrderBookState:
    """Current state of order book for a single token."""
    token_id: str
    best_bid: Optional[OrderBookLevel] = None
    best_ask: Optional[OrderBookLevel] = None
    last_update: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    update_count: int = 0
    
    @property
    def spread(self) -> Optional[float]:
        """Calculate current spread if both sides available."""
        if self.best_bid and self.best_ask:
            return self.best_ask.price - self.best_bid.price
        return None
    
    @property
    def mid_price(self) -> Optional[float]:
        """Calculate mid price if both sides available."""
        if self.best_bid and self.best_ask:
            return (self.best_bid.price + self.best_ask.price) / 2
        return None


@dataclass
class SniperStats:
    """Statistics for sniper performance monitoring."""
    messages_received: int = 0
    messages_processed: int = 0
    updates_applied: int = 0
    reconnect_count: int = 0
    last_message_time: Optional[datetime] = None
    start_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    @property
    def uptime_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.start_time).total_seconds()
    
    @property
    def messages_per_second(self) -> float:
        uptime = self.uptime_seconds
        if uptime > 0:
            return self.messages_processed / uptime
        return 0.0


class PolymarketSniper:
    """
    High-frequency WebSocket client for Polymarket order book streaming.
    
    Features:
    - Non-blocking async message processing
    - Microsecond-accurate state updates
    - Auto-reconnect with exponential backoff
    - Dynamic subscription management
    - Callback system for real-time updates
    """
    
    WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    
    # Reconnection settings
    RECONNECT_BASE_DELAY = 0.5  # 500ms initial delay
    RECONNECT_MAX_DELAY = 30.0  # Max 30 seconds
    RECONNECT_MULTIPLIER = 2.0
    
    # Heartbeat settings
    HEARTBEAT_INTERVAL = 30.0  # Send ping every 30s
    HEARTBEAT_TIMEOUT = 10.0   # Wait 10s for pong
    
    def __init__(self, max_queue_size: int = 10000):
        """
        Initialize the sniper.
        
        Args:
            max_queue_size: Maximum messages to buffer before dropping
        """
        self._books: Dict[str, OrderBookState] = {}
        self._callbacks: List[Callable[[str, OrderBookState], Any]] = []
        self._error_callbacks: List[Callable[[Exception], Any]] = []
        self._connection_callbacks: List[Callable[[ConnectionState], Any]] = []
        
        self._running = False
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._state = ConnectionState.DISCONNECTED
        
        # Subscription management
        self._subscribed_tokens: Set[str] = set()
        self._pending_subscriptions: Set[str] = set()
        self._pending_unsubscriptions: Set[str] = set()
        
        # Message queue for non-blocking processing
        self._message_queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)
        
        # Tasks
        self._receive_task: Optional[asyncio.Task] = None
        self._process_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        
        # Statistics
        self.stats = SniperStats()
        
        # Reconnection state
        self._reconnect_delay = self.RECONNECT_BASE_DELAY
        self._should_reconnect = True
        
        # Lock for thread-safe state updates
        self._state_lock = asyncio.Lock()
        self._subscription_lock = asyncio.Lock()
        
        logger.info("PolymarketSniper initialized", extra={
            "ws_url": self.WS_URL,
            "max_queue_size": max_queue_size
        })
    
    async def connect(self) -> bool:
        """
        Connect to Polymarket WebSocket.
        
        Returns:
            True if connection successful, False otherwise
        """
        if self._state == ConnectionState.CONNECTED:
            logger.warning("Already connected")
            return True
        
        self._set_state(ConnectionState.CONNECTING)
        
        try:
            self._ws = await websockets.connect(
                self.WS_URL,
                ping_interval=None,  # We handle our own heartbeat
                ping_timeout=None,
                close_timeout=5.0,
                max_size=10 * 1024 * 1024,  # 10MB max message
                compression=None,  # Disable for lower latency
            )
            
            self._set_state(ConnectionState.CONNECTED)
            self._reconnect_delay = self.RECONNECT_BASE_DELAY
            
            logger.info("Connected to Polymarket WebSocket")
            return True
            
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            self._set_state(ConnectionState.DISCONNECTED)
            await self._notify_error(e)
            return False
    
    async def disconnect(self):
        """Gracefully disconnect from WebSocket."""
        self._should_reconnect = False
        self._running = False
        
        # Cancel tasks
        for task in [self._receive_task, self._process_task, self._heartbeat_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        # Close WebSocket
        if self._ws:
            try:
                await self._ws.close()
            except Exception as e:
                logger.debug(f"Error closing WebSocket: {e}")
            self._ws = None
        
        self._set_state(ConnectionState.DISCONNECTED)
        logger.info("Disconnected from Polymarket WebSocket")
    
    async def subscribe(self, token_ids: List[str]):
        """
        Subscribe to order book updates for given tokens.
        
        Args:
            token_ids: List of token IDs to subscribe to
        """
        if not token_ids:
            return
        
        async with self._subscription_lock:
            new_tokens = set(token_ids) - self._subscribed_tokens
            if not new_tokens:
                logger.debug("All tokens already subscribed")
                return
            
            # Initialize order book state for new tokens
            for token_id in new_tokens:
                if token_id not in self._books:
                    self._books[token_id] = OrderBookState(token_id=token_id)
            
            if self._state == ConnectionState.CONNECTED and self._ws:
                await self._send_subscription(list(new_tokens))
                self._subscribed_tokens.update(new_tokens)
            else:
                # Queue for when connection is established
                self._pending_subscriptions.update(new_tokens)
            
            logger.info(f"Subscribed to {len(new_tokens)} tokens", extra={
                "tokens": list(new_tokens)[:5]  # Log first 5
            })
    
    async def unsubscribe(self, token_ids: List[str]):
        """
        Unsubscribe from tokens.
        
        Args:
            token_ids: List of token IDs to unsubscribe from
        """
        if not token_ids:
            return
        
        async with self._subscription_lock:
            tokens_to_remove = set(token_ids) & self._subscribed_tokens
            if not tokens_to_remove:
                return
            
            if self._state == ConnectionState.CONNECTED and self._ws:
                await self._send_unsubscription(list(tokens_to_remove))
            
            self._subscribed_tokens -= tokens_to_remove
            
            # Clean up order book state
            for token_id in tokens_to_remove:
                self._books.pop(token_id, None)
            
            logger.info(f"Unsubscribed from {len(tokens_to_remove)} tokens")
    
    async def _send_subscription(self, token_ids: List[str]):
        """Send subscription message to WebSocket."""
        if not self._ws:
            return
        
        message = {
            "type": "subscribe",
            "channel": "book",
            "assets": token_ids
        }
        
        try:
            await self._ws.send(json.dumps(message))
            logger.debug(f"Sent subscription for {len(token_ids)} tokens")
        except Exception as e:
            logger.error(f"Failed to send subscription: {e}")
            raise
    
    async def _send_unsubscription(self, token_ids: List[str]):
        """Send unsubscription message to WebSocket."""
        if not self._ws:
            return
        
        message = {
            "type": "unsubscribe",
            "channel": "book",
            "assets": token_ids
        }
        
        try:
            await self._ws.send(json.dumps(message))
            logger.debug(f"Sent unsubscription for {len(token_ids)} tokens")
        except Exception as e:
            logger.error(f"Failed to send unsubscription: {e}")
    
    def get_best_ask(self, token_id: str) -> Optional[OrderBookLevel]:
        """
        Get current best ask for token.
        
        Args:
            token_id: Token to query
            
        Returns:
            Best ask level or None if not available
        """
        book = self._books.get(token_id)
        return book.best_ask if book else None
    
    def get_best_bid(self, token_id: str) -> Optional[OrderBookLevel]:
        """
        Get current best bid for token.
        
        Args:
            token_id: Token to query
            
        Returns:
            Best bid level or None if not available
        """
        book = self._books.get(token_id)
        return book.best_bid if book else None
    
    def get_book_state(self, token_id: str) -> Optional[OrderBookState]:
        """
        Get full order book state for token.
        
        Args:
            token_id: Token to query
            
        Returns:
            Full order book state or None
        """
        return self._books.get(token_id)
    
    def get_all_books(self) -> Dict[str, OrderBookState]:
        """Get all tracked order book states."""
        return self._books.copy()
    
    def on_update(self, callback: Callable[[str, OrderBookState], Any]):
        """
        Register callback for order book updates.
        
        Callback signature: (token_id: str, state: OrderBookState) -> Any
        
        Args:
            callback: Function to call on each update
        """
        self._callbacks.append(callback)
        logger.debug(f"Registered update callback, total: {len(self._callbacks)}")
    
    def on_error(self, callback: Callable[[Exception], Any]):
        """
        Register callback for errors.
        
        Args:
            callback: Function to call on error
        """
        self._error_callbacks.append(callback)
    
    def on_connection_change(self, callback: Callable[[ConnectionState], Any]):
        """
        Register callback for connection state changes.
        
        Args:
            callback: Function to call on state change
        """
        self._connection_callbacks.append(callback)
    
    def remove_callback(self, callback: Callable):
        """Remove a previously registered callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)
        if callback in self._error_callbacks:
            self._error_callbacks.remove(callback)
        if callback in self._connection_callbacks:
            self._connection_callbacks.remove(callback)
    
    async def run(self):
        """
        Main event loop - process messages without blocking.
        
        This method runs indefinitely, handling:
        - Message receiving and queuing
        - Message processing
        - Heartbeat/keepalive
        - Auto-reconnection
        """
        self._running = True
        self._should_reconnect = True
        self.stats = SniperStats()
        
        logger.info("Starting sniper main loop")
        
        while self._running:
            try:
                # Connect if needed
                if self._state != ConnectionState.CONNECTED:
                    connected = await self.connect()
                    if not connected:
                        await self._handle_reconnect()
                        continue
                
                # Process pending subscriptions
                if self._pending_subscriptions:
                    async with self._subscription_lock:
                        pending = list(self._pending_subscriptions)
                        self._pending_subscriptions.clear()
                    await self._send_subscription(pending)
                    self._subscribed_tokens.update(pending)
                
                # Start worker tasks
                self._receive_task = asyncio.create_task(self._receive_loop())
                self._process_task = asyncio.create_task(self._process_loop())
                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
                
                # Wait for any task to complete (usually means error)
                done, pending = await asyncio.wait(
                    [self._receive_task, self._process_task, self._heartbeat_task],
                    return_when=asyncio.FIRST_COMPLETED
                )
                
                # Cancel remaining tasks
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                
                # Check what happened
                for task in done:
                    try:
                        task.result()
                    except asyncio.CancelledError:
                        pass
                    except Exception as e:
                        logger.error(f"Task error: {e}")
                        await self._notify_error(e)
                
            except asyncio.CancelledError:
                logger.info("Sniper cancelled")
                break
            except Exception as e:
                logger.error(f"Main loop error: {e}")
                await self._notify_error(e)
            
            if self._running and self._should_reconnect:
                await self._handle_reconnect()
        
        await self.disconnect()
        logger.info("Sniper stopped")
    
    async def _receive_loop(self):
        """Receive messages from WebSocket and queue them."""
        while self._running and self._ws:
            try:
                message = await self._ws.recv()
                self.stats.messages_received += 1
                self.stats.last_message_time = datetime.now(timezone.utc)
                
                # Non-blocking queue put
                try:
                    self._message_queue.put_nowait(message)
                except asyncio.QueueFull:
                    logger.warning("Message queue full, dropping oldest")
                    try:
                        self._message_queue.get_nowait()
                        self._message_queue.put_nowait(message)
                    except asyncio.QueueEmpty:
                        pass
                        
            except ConnectionClosed as e:
                logger.warning(f"WebSocket connection closed: {e}")
                self._set_state(ConnectionState.DISCONNECTED)
                raise
            except Exception as e:
                logger.error(f"Receive error: {e}")
                raise
    
    async def _process_loop(self):
        """Process queued messages without blocking."""
        while self._running:
            try:
                # Get message with short timeout for responsiveness
                try:
                    message = await asyncio.wait_for(
                        self._message_queue.get(),
                        timeout=0.1
                    )
                except asyncio.TimeoutError:
                    continue
                
                # Process message
                await self._process_message(message)
                self.stats.messages_processed += 1
                
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Process error: {e}")
    
    async def _heartbeat_loop(self):
        """Send periodic heartbeats to keep connection alive."""
        while self._running and self._ws:
            try:
                await asyncio.sleep(self.HEARTBEAT_INTERVAL)
                
                if self._ws and self._state == ConnectionState.CONNECTED:
                    pong = await self._ws.ping()
                    await asyncio.wait_for(pong, timeout=self.HEARTBEAT_TIMEOUT)
                    logger.debug("Heartbeat OK")
                    
            except asyncio.TimeoutError:
                logger.warning("Heartbeat timeout")
                raise
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")
                raise
    
    async def _process_message(self, raw_message: str):
        """
        Process a single WebSocket message.
        
        Args:
            raw_message: Raw JSON message string
        """
        # Skip empty messages (ping/pong frames, connection confirmations)
        if not raw_message or not raw_message.strip():
            return
        
        timestamp = datetime.now(timezone.utc)
        
        try:
            data = json.loads(raw_message)
        except json.JSONDecodeError as e:
            # Only log warning for non-empty messages that failed to parse
            if raw_message.strip():
                logger.debug(f"Non-JSON message received: {raw_message[:100]}")
            return
        
        msg_type = data.get("type") or data.get("event_type")
        
        if msg_type in ("book", "book_update", "price_change"):
            await self._handle_book_update(data, timestamp)
        elif msg_type == "subscribed":
            logger.debug(f"Subscription confirmed: {data}")
        elif msg_type == "unsubscribed":
            logger.debug(f"Unsubscription confirmed: {data}")
        elif msg_type == "error":
            logger.error(f"Server error: {data.get('message', data)}")
        elif msg_type in ("heartbeat", "ping", "pong"):
            pass  # Ignore heartbeat messages
        else:
            # Try to extract book data from unknown format
            if "asset_id" in data or "token_id" in data:
                await self._handle_book_update(data, timestamp)
            else:
                logger.debug(f"Unknown message type: {msg_type}")
    
    async def _handle_book_update(self, data: Dict, timestamp: datetime):
        """
        Handle order book update message.
        
        Supports multiple message formats from Polymarket:
        - Level 1 updates (best bid/ask)
        - Incremental book updates
        - Full book snapshots
        """
        # Extract token ID (various field names)
        token_id = (
            data.get("asset_id") or 
            data.get("token_id") or 
            data.get("market")
        )
        
        if not token_id:
            return
        
        # Get or create book state
        async with self._state_lock:
            if token_id not in self._books:
                self._books[token_id] = OrderBookState(token_id=token_id)
            
            book = self._books[token_id]
            updated = False
            
            # Handle different update formats
            
            # Format 1: Direct best bid/ask
            if "best_bid" in data or "best_ask" in data:
                if "best_bid" in data and data["best_bid"]:
                    bid_data = data["best_bid"]
                    if isinstance(bid_data, dict):
                        new_bid = OrderBookLevel(
                            price=float(bid_data.get("price", 0)),
                            size=float(bid_data.get("size", 0)),
                            timestamp=timestamp
                        )
                    else:
                        new_bid = OrderBookLevel(
                            price=float(bid_data),
                            size=float(data.get("best_bid_size", 0)),
                            timestamp=timestamp
                        )
                    if not book.best_bid or book.best_bid.price != new_bid.price:
                        book.best_bid = new_bid
                        updated = True
                
                if "best_ask" in data and data["best_ask"]:
                    ask_data = data["best_ask"]
                    if isinstance(ask_data, dict):
                        new_ask = OrderBookLevel(
                            price=float(ask_data.get("price", 0)),
                            size=float(ask_data.get("size", 0)),
                            timestamp=timestamp
                        )
                    else:
                        new_ask = OrderBookLevel(
                            price=float(ask_data),
                            size=float(data.get("best_ask_size", 0)),
                            timestamp=timestamp
                        )
                    if not book.best_ask or book.best_ask.price != new_ask.price:
                        book.best_ask = new_ask
                        updated = True
            
            # Format 2: Bids/Asks arrays (take best from each)
            elif "bids" in data or "asks" in data:
                bids = data.get("bids", [])
                asks = data.get("asks", [])
                
                if bids:
                    try:
                        # Best bid is highest price
                        best = max(bids, key=lambda x: float(x[0] if isinstance(x, list) else x.get("price", 0)))
                        if isinstance(best, list) and len(best) >= 2:
                            price, size = float(best[0]), float(best[1])
                        elif isinstance(best, dict):
                            price, size = float(best["price"]), float(best.get("size", best.get("quantity", 0)))
                        else:
                            raise ValueError(f"Unexpected bid format: {best}")
                        
                        new_bid = OrderBookLevel(price=price, size=size, timestamp=timestamp)
                        if not book.best_bid or book.best_bid.price != new_bid.price:
                            book.best_bid = new_bid
                            updated = True
                    except (ValueError, TypeError, KeyError, IndexError) as e:
                        logger.debug(f"Failed to parse bids: {e}")
                
                if asks:
                    try:
                        # Best ask is lowest price
                        best = min(asks, key=lambda x: float(x[0] if isinstance(x, list) else x.get("price", 0)))
                        if isinstance(best, list) and len(best) >= 2:
                            price, size = float(best[0]), float(best[1])
                        elif isinstance(best, dict):
                            price, size = float(best["price"]), float(best.get("size", best.get("quantity", 0)))
                        else:
                            raise ValueError(f"Unexpected ask format: {best}")
                        
                        new_ask = OrderBookLevel(price=price, size=size, timestamp=timestamp)
                        if not book.best_ask or book.best_ask.price != new_ask.price:
                            book.best_ask = new_ask
                            updated = True
                    except (ValueError, TypeError, KeyError, IndexError) as e:
                        logger.debug(f"Failed to parse asks: {e}")
            
            # Format 3: Single price/side update
            elif "price" in data and "side" in data:
                price = float(data["price"])
                size = float(data.get("size", data.get("quantity", 0)))
                side = data["side"].lower()
                
                level = OrderBookLevel(price=price, size=size, timestamp=timestamp)
                
                if side in ("buy", "bid"):
                    if not book.best_bid or price >= book.best_bid.price:
                        book.best_bid = level
                        updated = True
                elif side in ("sell", "ask"):
                    if not book.best_ask or price <= book.best_ask.price:
                        book.best_ask = level
                        updated = True
            
            if updated:
                book.last_update = timestamp
                book.update_count += 1
                self.stats.updates_applied += 1
        
        # Notify callbacks outside of lock
        if updated:
            await self._notify_update(token_id, book)
    
    async def _notify_update(self, token_id: str, state: OrderBookState):
        """Notify all registered callbacks of an update."""
        for callback in self._callbacks:
            try:
                result = callback(token_id, state)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"Callback error: {e}")
    
    async def _notify_error(self, error: Exception):
        """Notify error callbacks."""
        for callback in self._error_callbacks:
            try:
                result = callback(error)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"Error callback error: {e}")
    
    def _set_state(self, state: ConnectionState):
        """Update connection state and notify callbacks."""
        old_state = self._state
        self._state = state
        
        if old_state != state:
            logger.info(f"Connection state: {old_state.value} -> {state.value}")
            for callback in self._connection_callbacks:
                try:
                    result = callback(state)
                    if asyncio.iscoroutine(result):
                        asyncio.create_task(result)
                except Exception as e:
                    logger.error(f"Connection callback error: {e}")
    
    async def _handle_reconnect(self):
        """Handle reconnection with exponential backoff."""
        if not self._should_reconnect:
            return
        
        self._set_state(ConnectionState.RECONNECTING)
        self.stats.reconnect_count += 1
        
        logger.info(f"Reconnecting in {self._reconnect_delay:.1f}s...")
        await asyncio.sleep(self._reconnect_delay)
        
        # Exponential backoff
        self._reconnect_delay = min(
            self._reconnect_delay * self.RECONNECT_MULTIPLIER,
            self.RECONNECT_MAX_DELAY
        )
        
        # Reset WebSocket
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
    
    @property
    def is_connected(self) -> bool:
        """Check if currently connected."""
        return self._state == ConnectionState.CONNECTED
    
    @property
    def is_running(self) -> bool:
        """Check if sniper is running."""
        return self._running
    
    @property
    def subscribed_tokens(self) -> Set[str]:
        """Get set of currently subscribed tokens."""
        return self._subscribed_tokens.copy()
    
    async def close(self):
        """Alias for disconnect() for consistency with other components."""
        await self.disconnect()


async def main():
    """Example usage of the sniper."""
    sniper = PolymarketSniper()
    
    # Example callback
    def on_book_update(token_id: str, state: OrderBookState):
        if state.best_ask:
            print(f"[{token_id[:8]}...] Ask: {state.best_ask.price:.4f} x {state.best_ask.size:.2f}")
        if state.best_bid:
            print(f"[{token_id[:8]}...] Bid: {state.best_bid.price:.4f} x {state.best_bid.size:.2f}")
    
    sniper.on_update(on_book_update)
    
    # Example token IDs (would come from market discovery)
    example_tokens = [
        "21742633143463906290569050155826241533067272736897614950488156847949938836455"
    ]
    
    # Subscribe before connecting
    await sniper.subscribe(example_tokens)
    
    try:
        # Run the sniper
        await sniper.run()
    except KeyboardInterrupt:
        print("\nShutting down...")
        await sniper.disconnect()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)s | %(name)s | %(message)s'
    )
    asyncio.run(main())
