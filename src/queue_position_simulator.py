"""
Queue Position Simulator - CLOB Queue Modeling.

Phase 2 - Task 60: Institutional-grade Queue Position Simulator modeling
where mock limit orders sit in Polymarket's CLOB based on submission
timestamp versus incoming public trades.

Educational purpose only - paper trading simulation.
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, List, Tuple
from collections import defaultdict
from enum import Enum

logger = logging.getLogger(__name__)


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(Enum):
    PENDING = "pending"
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


@dataclass
class MockOrder:
    """Represents a mock limit order in the queue."""
    order_id: str
    market_id: str
    side: OrderSide
    price: float  # Limit price (0.00 to 1.00 for Polymarket)
    quantity: float  # Shares
    status: OrderStatus = OrderStatus.PENDING
    
    # Timing
    created_at: float = field(default_factory=lambda: time.time() * 1000)
    submitted_at: Optional[float] = None
    
    # Queue position tracking
    queue_position: int = 0  # Position in the queue at our price level
    total_queue_size: int = 0  # Total shares ahead at our price level
    estimated_fill_time: Optional[float] = None
    
    # Fill tracking
    filled_quantity: float = 0.0
    average_fill_price: float = 0.0
    fills: List[Dict] = field(default_factory=list)
    
    # Simulation metadata
    latency_added_ms: float = 0.0  # Simulated network latency
    
    @property
    def remaining_quantity(self) -> float:
        return self.quantity - self.filled_quantity
    
    @property
    def is_complete(self) -> bool:
        return self.status in [OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.EXPIRED]
    
    @property
    def fill_rate(self) -> float:
        if self.quantity == 0:
            return 0.0
        return self.filled_quantity / self.quantity
    
    def to_dict(self) -> dict:
        return {
            'order_id': self.order_id,
            'market_id': self.market_id,
            'side': self.side.value,
            'price': self.price,
            'quantity': self.quantity,
            'status': self.status.value,
            'created_at': self.created_at,
            'submitted_at': self.submitted_at,
            'queue_position': self.queue_position,
            'total_queue_size': self.total_queue_size,
            'filled_quantity': self.filled_quantity,
            'remaining_quantity': self.remaining_quantity,
            'average_fill_price': self.average_fill_price,
            'fill_rate': self.fill_rate,
            'fills': self.fills,
        }


@dataclass
class PriceLevel:
    """Represents a single price level in the order book."""
    price: float
    total_quantity: float = 0.0
    order_count: int = 0
    orders: List[str] = field(default_factory=list)  # Order IDs in queue order


@dataclass
class Trade:
    """Represents a public trade from the tape."""
    trade_id: str
    market_id: str
    price: float
    quantity: float
    side: str  # 'buy' or 'sell' (aggressor side)
    timestamp: float
    is_maker: bool = False


class QueuePositionSimulator:
    """
    Simulates queue position in a Central Limit Order Book.
    
    Models:
    1. Queue position based on order timestamp
    2. Fill probability based on incoming trade flow
    3. Queue jumping from large orders
    4. Partial fills from matching trades
    """
    
    # Polymarket tick size
    TICK_SIZE = 0.01
    
    # Simulation parameters
    DEFAULT_LATENCY_MS = 50  # Simulated submission latency
    QUEUE_DECAY_RATE = 0.95  # How much queue size decays over time
    
    def __init__(
        self,
        default_latency_ms: float = 50,
        max_orders: int = 1000,
    ):
        """
        Initialize queue position simulator.
        
        Args:
            default_latency_ms: Default network latency to simulate
            max_orders: Maximum concurrent mock orders
        """
        self.default_latency_ms = default_latency_ms
        self.max_orders = max_orders
        
        # Order storage
        self._orders: Dict[str, MockOrder] = {}
        
        # Order book simulation (per market)
        # Structure: {market_id: {price: PriceLevel}}
        self._bid_books: Dict[str, Dict[float, PriceLevel]] = defaultdict(dict)
        self._ask_books: Dict[str, Dict[float, PriceLevel]] = defaultdict(dict)
        
        # Trade history for fill simulation
        self._trade_history: Dict[str, List[Trade]] = defaultdict(list)
        
        # Statistics
        self._stats = {
            'orders_created': 0,
            'orders_filled': 0,
            'orders_cancelled': 0,
            'total_filled_quantity': 0.0,
            'average_queue_time_ms': 0.0,
        }
    
    def create_order(
        self,
        market_id: str,
        side: OrderSide,
        price: float,
        quantity: float,
        latency_ms: float = None,
    ) -> MockOrder:
        """
        Create a new mock limit order.
        
        Args:
            market_id: Polymarket market ID
            side: BUY or SELL
            price: Limit price (0.00 to 1.00)
            quantity: Number of shares
            latency_ms: Simulated network latency (optional)
        
        Returns:
            MockOrder object
        """
        # Validate price
        price = round(price / self.TICK_SIZE) * self.TICK_SIZE
        price = max(0.01, min(0.99, price))
        
        order = MockOrder(
            order_id=str(uuid.uuid4())[:8],
            market_id=market_id,
            side=side,
            price=price,
            quantity=quantity,
            latency_added_ms=latency_ms or self.default_latency_ms,
        )
        
        # Add simulated latency to submission time
        order.submitted_at = order.created_at + order.latency_added_ms
        
        self._orders[order.order_id] = order
        self._stats['orders_created'] += 1
        
        # Add to simulated order book
        self._add_to_book(order)
        
        # Calculate initial queue position
        self._update_queue_position(order)
        
        order.status = OrderStatus.OPEN
        
        logger.debug(f"Created mock order {order.order_id}: {side.value} {quantity}@{price}")
        return order
    
    def cancel_order(self, order_id: str) -> bool:
        """Cancel a mock order."""
        if order_id not in self._orders:
            return False
        
        order = self._orders[order_id]
        if order.is_complete:
            return False
        
        # Remove from book
        self._remove_from_book(order)
        
        order.status = OrderStatus.CANCELLED
        self._stats['orders_cancelled'] += 1
        
        logger.debug(f"Cancelled mock order {order_id}")
        return True
    
    def process_trade(self, trade: Trade) -> List[MockOrder]:
        """
        Process an incoming public trade and simulate fills.
        
        Args:
            trade: Public trade from the tape
        
        Returns:
            List of orders that were filled (partially or fully)
        """
        self._trade_history[trade.market_id].append(trade)
        
        # Keep only recent trades
        if len(self._trade_history[trade.market_id]) > 10000:
            self._trade_history[trade.market_id] = self._trade_history[trade.market_id][-5000:]
        
        filled_orders = []
        
        # Find matching orders
        matching_orders = self._find_matching_orders(trade)
        
        remaining_quantity = trade.quantity
        
        for order in matching_orders:
            if remaining_quantity <= 0:
                break
            
            if order.is_complete:
                continue
            
            # Check if trade would match this order
            if self._would_fill(order, trade):
                fill_qty = min(remaining_quantity, order.remaining_quantity)
                
                # Apply fill based on queue position probability
                fill_probability = self._calculate_fill_probability(order, trade)
                
                # Simulate probabilistic fill
                if fill_probability > 0.5:  # Simplified: fill if >50% probability
                    actual_fill = fill_qty * fill_probability
                    actual_fill = max(1, round(actual_fill))  # At least 1 share
                    actual_fill = min(actual_fill, order.remaining_quantity)
                    
                    self._apply_fill(order, actual_fill, trade.price, trade.timestamp)
                    remaining_quantity -= actual_fill
                    filled_orders.append(order)
        
        return filled_orders
    
    def _add_to_book(self, order: MockOrder) -> None:
        """Add order to simulated order book."""
        book = self._bid_books if order.side == OrderSide.BUY else self._ask_books
        market_book = book[order.market_id]
        
        if order.price not in market_book:
            market_book[order.price] = PriceLevel(price=order.price)
        
        level = market_book[order.price]
        level.orders.append(order.order_id)
        level.order_count += 1
        level.total_quantity += order.quantity
    
    def _remove_from_book(self, order: MockOrder) -> None:
        """Remove order from simulated order book."""
        book = self._bid_books if order.side == OrderSide.BUY else self._ask_books
        market_book = book.get(order.market_id, {})
        
        if order.price in market_book:
            level = market_book[order.price]
            if order.order_id in level.orders:
                level.orders.remove(order.order_id)
                level.order_count -= 1
                level.total_quantity -= order.remaining_quantity
    
    def _update_queue_position(self, order: MockOrder) -> None:
        """Update queue position for an order."""
        book = self._bid_books if order.side == OrderSide.BUY else self._ask_books
        market_book = book.get(order.market_id, {})
        
        if order.price not in market_book:
            order.queue_position = 0
            order.total_queue_size = 0
            return
        
        level = market_book[order.price]
        
        # Find position in queue (orders submitted before us)
        position = 0
        size_ahead = 0.0
        
        for oid in level.orders:
            if oid == order.order_id:
                break
            if oid in self._orders:
                other = self._orders[oid]
                if other.submitted_at < order.submitted_at:
                    position += 1
                    size_ahead += other.remaining_quantity
        
        order.queue_position = position
        order.total_queue_size = size_ahead
    
    def _find_matching_orders(self, trade: Trade) -> List[MockOrder]:
        """Find orders that could be filled by this trade."""
        matching = []
        
        # Trade is a buy -> matches sell orders (asks)
        # Trade is a sell -> matches buy orders (bids)
        if trade.side == 'buy':
            book = self._ask_books.get(trade.market_id, {})
            # Match orders at or below trade price
            for price, level in sorted(book.items()):
                if price <= trade.price:
                    for oid in level.orders:
                        if oid in self._orders:
                            matching.append(self._orders[oid])
        else:
            book = self._bid_books.get(trade.market_id, {})
            # Match orders at or above trade price
            for price, level in sorted(book.items(), reverse=True):
                if price >= trade.price:
                    for oid in level.orders:
                        if oid in self._orders:
                            matching.append(self._orders[oid])
        
        return matching
    
    def _would_fill(self, order: MockOrder, trade: Trade) -> bool:
        """Check if a trade would theoretically fill this order."""
        if order.side == OrderSide.BUY:
            # Buy order fills when trade price <= order price
            return trade.price <= order.price
        else:
            # Sell order fills when trade price >= order price
            return trade.price >= order.price
    
    def _calculate_fill_probability(self, order: MockOrder, trade: Trade) -> float:
        """
        Calculate probability of fill based on queue position.
        
        Models that orders ahead of us get filled first.
        """
        if order.total_queue_size == 0:
            return 1.0  # Front of queue
        
        # Probability decreases with queue position
        # P(fill) = trade_qty / (trade_qty + queue_ahead)
        probability = trade.quantity / (trade.quantity + order.total_queue_size)
        
        # Adjust for price improvement
        if order.side == OrderSide.BUY and trade.price < order.price:
            probability *= 1.5
        elif order.side == OrderSide.SELL and trade.price > order.price:
            probability *= 1.5
        
        return min(1.0, probability)
    
    def _apply_fill(
        self,
        order: MockOrder,
        fill_quantity: float,
        fill_price: float,
        timestamp: float,
    ) -> None:
        """Apply a fill to an order."""
        # Calculate new average fill price
        old_value = order.filled_quantity * order.average_fill_price
        new_value = fill_quantity * fill_price
        total_qty = order.filled_quantity + fill_quantity
        
        if total_qty > 0:
            order.average_fill_price = (old_value + new_value) / total_qty
        
        order.filled_quantity += fill_quantity
        
        # Record fill
        order.fills.append({
            'quantity': fill_quantity,
            'price': fill_price,
            'timestamp': timestamp,
        })
        
        # Update status
        if order.filled_quantity >= order.quantity:
            order.status = OrderStatus.FILLED
            self._stats['orders_filled'] += 1
            self._remove_from_book(order)
        else:
            order.status = OrderStatus.PARTIALLY_FILLED
        
        self._stats['total_filled_quantity'] += fill_quantity
        
        # Update queue position for remaining orders
        self._update_queue_position(order)
        
        logger.debug(f"Filled {fill_quantity}@{fill_price} for order {order.order_id}")
    
    def get_order(self, order_id: str) -> Optional[MockOrder]:
        """Get an order by ID."""
        return self._orders.get(order_id)
    
    def get_open_orders(self, market_id: str = None) -> List[MockOrder]:
        """Get all open orders, optionally filtered by market."""
        orders = [
            o for o in self._orders.values()
            if not o.is_complete
        ]
        
        if market_id:
            orders = [o for o in orders if o.market_id == market_id]
        
        return orders
    
    def get_order_book_summary(self, market_id: str) -> dict:
        """Get summary of the simulated order book."""
        bids = self._bid_books.get(market_id, {})
        asks = self._ask_books.get(market_id, {})
        
        return {
            'market_id': market_id,
            'bid_levels': len(bids),
            'ask_levels': len(asks),
            'total_bid_quantity': sum(l.total_quantity for l in bids.values()),
            'total_ask_quantity': sum(l.total_quantity for l in asks.values()),
            'best_bid': max(bids.keys()) if bids else None,
            'best_ask': min(asks.keys()) if asks else None,
        }
    
    def get_statistics(self) -> dict:
        """Get simulator statistics."""
        return {
            **self._stats,
            'active_orders': len([o for o in self._orders.values() if not o.is_complete]),
            'total_orders': len(self._orders),
        }
    
    def estimate_fill_time(self, order: MockOrder) -> Optional[float]:
        """
        Estimate time until order would fill based on trade flow.
        
        Returns estimated time in milliseconds, or None if uncertain.
        """
        trades = self._trade_history.get(order.market_id, [])
        if len(trades) < 10:
            return None
        
        # Calculate average trade flow at this price level
        recent_trades = trades[-100:]
        matching_trades = [
            t for t in recent_trades
            if self._would_fill(order, t)
        ]
        
        if not matching_trades:
            return None
        
        # Average volume per second
        time_span = (matching_trades[-1].timestamp - matching_trades[0].timestamp) / 1000
        if time_span <= 0:
            return None
        
        total_volume = sum(t.quantity for t in matching_trades)
        volume_per_second = total_volume / time_span
        
        if volume_per_second <= 0:
            return None
        
        # Estimate time to fill based on queue position
        queue_to_fill = order.total_queue_size + order.remaining_quantity
        estimated_seconds = queue_to_fill / volume_per_second
        
        return estimated_seconds * 1000  # Return milliseconds


# Factory function
def create_queue_simulator(
    default_latency_ms: float = 50,
    max_orders: int = 1000,
) -> QueuePositionSimulator:
    """Create and return a QueuePositionSimulator instance."""
    return QueuePositionSimulator(
        default_latency_ms=default_latency_ms,
        max_orders=max_orders,
    )
