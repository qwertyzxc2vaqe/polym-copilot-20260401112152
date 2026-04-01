"""
Local Matching Engine - Polymarket CLOB Reconstruction.

Phase 2 - Task 69: Reconstruct Polymarket's exact matching algorithm
locally to calculate instantaneous theoretical fills.

Educational purpose only - paper trading simulation.
"""

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple
from collections import defaultdict
from enum import Enum
import heapq

logger = logging.getLogger(__name__)


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    LIMIT = "limit"
    MARKET = "market"
    IOC = "ioc"  # Immediate or cancel
    FOK = "fok"  # Fill or kill


class OrderStatus(Enum):
    NEW = "new"
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class Order:
    """Represents an order in the matching engine."""
    order_id: str
    market_id: str
    side: OrderSide
    price: float  # 0.00 to 1.00 for Polymarket
    quantity: float
    order_type: OrderType = OrderType.LIMIT
    status: OrderStatus = OrderStatus.NEW
    
    # Tracking
    filled_quantity: float = 0.0
    average_fill_price: float = 0.0
    created_at: float = field(default_factory=lambda: time.time() * 1000)
    updated_at: float = 0.0
    
    # Optional metadata
    maker_address: str = ""
    taker_fee_bps: float = 0.0
    maker_fee_bps: float = 0.0
    
    @property
    def remaining_quantity(self) -> float:
        return self.quantity - self.filled_quantity
    
    @property
    def is_complete(self) -> bool:
        return self.status in [OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED]
    
    def to_dict(self) -> dict:
        return {
            'order_id': self.order_id,
            'market_id': self.market_id,
            'side': self.side.value,
            'price': self.price,
            'quantity': self.quantity,
            'order_type': self.order_type.value,
            'status': self.status.value,
            'filled_quantity': self.filled_quantity,
            'remaining_quantity': self.remaining_quantity,
            'average_fill_price': self.average_fill_price,
            'created_at': self.created_at,
        }


@dataclass
class Fill:
    """Represents a fill (trade execution)."""
    fill_id: str
    market_id: str
    maker_order_id: str
    taker_order_id: str
    price: float
    quantity: float
    side: str  # Taker's side
    timestamp: float = field(default_factory=lambda: time.time() * 1000)
    maker_fee: float = 0.0
    taker_fee: float = 0.0
    
    def to_dict(self) -> dict:
        return {
            'fill_id': self.fill_id,
            'market_id': self.market_id,
            'maker_order_id': self.maker_order_id,
            'taker_order_id': self.taker_order_id,
            'price': self.price,
            'quantity': self.quantity,
            'side': self.side,
            'timestamp': self.timestamp,
            'maker_fee': self.maker_fee,
            'taker_fee': self.taker_fee,
        }


@dataclass
class MatchResult:
    """Result of matching an incoming order."""
    order: Order
    fills: List[Fill] = field(default_factory=list)
    total_filled: float = 0.0
    average_price: float = 0.0
    remaining: float = 0.0
    total_fees: float = 0.0
    
    @property
    def is_fully_filled(self) -> bool:
        return self.remaining == 0 and self.total_filled > 0
    
    @property
    def is_partial_fill(self) -> bool:
        return self.total_filled > 0 and self.remaining > 0
    
    def to_dict(self) -> dict:
        return {
            'order': self.order.to_dict(),
            'fills': [f.to_dict() for f in self.fills],
            'total_filled': self.total_filled,
            'average_price': self.average_price,
            'remaining': self.remaining,
            'total_fees': self.total_fees,
            'is_fully_filled': self.is_fully_filled,
        }


class PriceLevel:
    """A price level in the order book with FIFO queue."""
    
    def __init__(self, price: float):
        self.price = price
        self.orders: List[Order] = []
        self.total_quantity = 0.0
    
    def add_order(self, order: Order) -> None:
        """Add order to end of queue (FIFO)."""
        self.orders.append(order)
        self.total_quantity += order.remaining_quantity
    
    def remove_order(self, order_id: str) -> Optional[Order]:
        """Remove order by ID."""
        for i, order in enumerate(self.orders):
            if order.order_id == order_id:
                self.total_quantity -= order.remaining_quantity
                return self.orders.pop(i)
        return None
    
    def get_front_order(self) -> Optional[Order]:
        """Get order at front of queue."""
        return self.orders[0] if self.orders else None
    
    def is_empty(self) -> bool:
        return len(self.orders) == 0


class LocalMatchingEngine:
    """
    Local reconstruction of Polymarket's CLOB matching engine.
    
    Implements:
    - Price-time priority (best price first, then FIFO)
    - Continuous matching
    - Polymarket tick size ($0.01)
    - Maker/taker fee calculation
    """
    
    # Polymarket parameters
    TICK_SIZE = 0.01
    MIN_PRICE = 0.01
    MAX_PRICE = 0.99
    
    # Default fees (Polymarket is zero-fee but we model for simulation)
    DEFAULT_MAKER_FEE_BPS = 0.0
    DEFAULT_TAKER_FEE_BPS = 0.0
    
    def __init__(
        self,
        market_id: str,
        maker_fee_bps: float = 0.0,
        taker_fee_bps: float = 0.0,
    ):
        """
        Initialize matching engine for a market.
        
        Args:
            market_id: Polymarket market ID
            maker_fee_bps: Maker fee in basis points
            taker_fee_bps: Taker fee in basis points
        """
        self.market_id = market_id
        self.maker_fee_bps = maker_fee_bps
        self.taker_fee_bps = taker_fee_bps
        
        # Order books: price -> PriceLevel
        # Bids: sorted high to low (best bid = highest)
        # Asks: sorted low to high (best ask = lowest)
        self._bids: Dict[float, PriceLevel] = {}
        self._asks: Dict[float, PriceLevel] = {}
        
        # Order lookup
        self._orders: Dict[str, Order] = {}
        
        # Fill history
        self._fills: List[Fill] = []
        
        # Statistics
        self._stats = {
            'orders_received': 0,
            'orders_filled': 0,
            'orders_cancelled': 0,
            'total_volume': 0.0,
            'total_fills': 0,
        }
    
    def submit_order(self, order: Order) -> MatchResult:
        """
        Submit an order to the matching engine.
        
        Args:
            order: Order to submit
        
        Returns:
            MatchResult with fills and remaining quantity
        """
        # Validate order
        if not self._validate_order(order):
            order.status = OrderStatus.REJECTED
            return MatchResult(order=order, remaining=order.quantity)
        
        self._stats['orders_received'] += 1
        order.status = OrderStatus.OPEN
        
        # Match against resting orders
        result = self._match_order(order)
        
        # Handle remaining quantity based on order type
        if result.remaining > 0:
            if order.order_type == OrderType.MARKET:
                # Market orders don't rest
                order.status = OrderStatus.FILLED if result.total_filled > 0 else OrderStatus.CANCELLED
            elif order.order_type == OrderType.IOC:
                # IOC cancels unfilled portion
                order.status = OrderStatus.FILLED if result.total_filled > 0 else OrderStatus.CANCELLED
            elif order.order_type == OrderType.FOK:
                # FOK requires full fill or nothing
                if result.total_filled > 0 and result.remaining > 0:
                    # Undo fills (in real engine, this would be atomic)
                    order.status = OrderStatus.CANCELLED
                    result.fills = []
                    result.total_filled = 0
                    result.remaining = order.quantity
            else:
                # Limit order - add to book
                self._add_to_book(order)
        else:
            order.status = OrderStatus.FILLED
            self._stats['orders_filled'] += 1
        
        return result
    
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order."""
        if order_id not in self._orders:
            return False
        
        order = self._orders[order_id]
        if order.is_complete:
            return False
        
        # Remove from book
        self._remove_from_book(order)
        
        order.status = OrderStatus.CANCELLED
        order.updated_at = time.time() * 1000
        self._stats['orders_cancelled'] += 1
        
        return True
    
    def _validate_order(self, order: Order) -> bool:
        """Validate order parameters."""
        # Price within bounds
        if order.price < self.MIN_PRICE or order.price > self.MAX_PRICE:
            logger.warning(f"Order price {order.price} out of bounds")
            return False
        
        # Price on tick
        rounded = round(order.price / self.TICK_SIZE) * self.TICK_SIZE
        if abs(order.price - rounded) > 1e-9:
            logger.warning(f"Order price {order.price} not on tick")
            return False
        
        # Quantity positive
        if order.quantity <= 0:
            logger.warning(f"Order quantity {order.quantity} must be positive")
            return False
        
        return True
    
    def _match_order(self, order: Order) -> MatchResult:
        """
        Match incoming order against resting orders.
        
        Implements price-time priority.
        """
        fills = []
        total_filled = 0.0
        total_cost = 0.0
        remaining = order.quantity
        
        # Determine which side of book to match against
        if order.side == OrderSide.BUY:
            opposite_book = self._asks
            price_check = lambda level_price: level_price <= order.price
            price_order = sorted(opposite_book.keys())  # Low to high
        else:
            opposite_book = self._bids
            price_check = lambda level_price: level_price >= order.price
            price_order = sorted(opposite_book.keys(), reverse=True)  # High to low
        
        # Match at each price level
        levels_to_remove = []
        
        for level_price in price_order:
            if remaining <= 0:
                break
            
            # Check price crossing
            if order.order_type != OrderType.MARKET and not price_check(level_price):
                break
            
            level = opposite_book[level_price]
            
            # Match against orders at this level (FIFO)
            while not level.is_empty() and remaining > 0:
                maker_order = level.get_front_order()
                
                # Calculate fill quantity
                fill_qty = min(remaining, maker_order.remaining_quantity)
                fill_price = level_price  # Match at maker's price
                
                # Calculate fees
                maker_fee = fill_qty * fill_price * self.maker_fee_bps / 10000
                taker_fee = fill_qty * fill_price * self.taker_fee_bps / 10000
                
                # Create fill
                fill = Fill(
                    fill_id=str(uuid.uuid4())[:8],
                    market_id=self.market_id,
                    maker_order_id=maker_order.order_id,
                    taker_order_id=order.order_id,
                    price=fill_price,
                    quantity=fill_qty,
                    side=order.side.value,
                    maker_fee=maker_fee,
                    taker_fee=taker_fee,
                )
                
                fills.append(fill)
                self._fills.append(fill)
                
                # Update quantities
                remaining -= fill_qty
                total_filled += fill_qty
                total_cost += fill_qty * fill_price
                
                # Update maker order
                maker_order.filled_quantity += fill_qty
                maker_order.updated_at = time.time() * 1000
                
                if maker_order.remaining_quantity <= 0:
                    maker_order.status = OrderStatus.FILLED
                    level.orders.pop(0)  # Remove from front
                    self._stats['orders_filled'] += 1
                else:
                    maker_order.status = OrderStatus.PARTIALLY_FILLED
                
                level.total_quantity -= fill_qty
                self._stats['total_volume'] += fill_qty
                self._stats['total_fills'] += 1
            
            if level.is_empty():
                levels_to_remove.append(level_price)
        
        # Clean up empty levels
        for price in levels_to_remove:
            del opposite_book[price]
        
        # Update taker order
        order.filled_quantity = total_filled
        if total_filled > 0:
            order.average_fill_price = total_cost / total_filled
        order.updated_at = time.time() * 1000
        
        if remaining == 0 and total_filled > 0:
            order.status = OrderStatus.FILLED
        elif total_filled > 0:
            order.status = OrderStatus.PARTIALLY_FILLED
        
        total_fees = sum(f.maker_fee + f.taker_fee for f in fills)
        
        return MatchResult(
            order=order,
            fills=fills,
            total_filled=total_filled,
            average_price=order.average_fill_price,
            remaining=remaining,
            total_fees=total_fees,
        )
    
    def _add_to_book(self, order: Order) -> None:
        """Add order to the order book."""
        book = self._bids if order.side == OrderSide.BUY else self._asks
        
        if order.price not in book:
            book[order.price] = PriceLevel(order.price)
        
        book[order.price].add_order(order)
        self._orders[order.order_id] = order
    
    def _remove_from_book(self, order: Order) -> None:
        """Remove order from the order book."""
        book = self._bids if order.side == OrderSide.BUY else self._asks
        
        if order.price in book:
            book[order.price].remove_order(order.order_id)
            if book[order.price].is_empty():
                del book[order.price]
        
        if order.order_id in self._orders:
            del self._orders[order.order_id]
    
    def get_order(self, order_id: str) -> Optional[Order]:
        """Get order by ID."""
        return self._orders.get(order_id)
    
    def get_order_book(self, levels: int = 10) -> Dict[str, List[Dict]]:
        """
        Get order book snapshot.
        
        Returns top N levels on each side.
        """
        bid_prices = sorted(self._bids.keys(), reverse=True)[:levels]
        ask_prices = sorted(self._asks.keys())[:levels]
        
        bids = [
            {
                'price': p,
                'quantity': self._bids[p].total_quantity,
                'orders': len(self._bids[p].orders),
            }
            for p in bid_prices
        ]
        
        asks = [
            {
                'price': p,
                'quantity': self._asks[p].total_quantity,
                'orders': len(self._asks[p].orders),
            }
            for p in ask_prices
        ]
        
        return {
            'bids': bids,
            'asks': asks,
            'best_bid': bids[0]['price'] if bids else None,
            'best_ask': asks[0]['price'] if asks else None,
            'spread': (asks[0]['price'] - bids[0]['price']) if (bids and asks) else None,
        }
    
    def get_best_bid(self) -> Optional[float]:
        """Get best (highest) bid price."""
        if not self._bids:
            return None
        return max(self._bids.keys())
    
    def get_best_ask(self) -> Optional[float]:
        """Get best (lowest) ask price."""
        if not self._asks:
            return None
        return min(self._asks.keys())
    
    def get_mid_price(self) -> Optional[float]:
        """Get mid price."""
        best_bid = self.get_best_bid()
        best_ask = self.get_best_ask()
        if best_bid and best_ask:
            return (best_bid + best_ask) / 2
        return None
    
    def get_statistics(self) -> Dict:
        """Get matching engine statistics."""
        return {
            **self._stats,
            'open_orders': len(self._orders),
            'bid_levels': len(self._bids),
            'ask_levels': len(self._asks),
            'total_bid_depth': sum(l.total_quantity for l in self._bids.values()),
            'total_ask_depth': sum(l.total_quantity for l in self._asks.values()),
        }
    
    def simulate_fill(
        self,
        side: str,
        price: float,
        quantity: float,
    ) -> MatchResult:
        """
        Simulate what would happen if order was submitted.
        
        Does not actually modify the order book.
        
        Args:
            side: 'buy' or 'sell'
            price: Limit price
            quantity: Order quantity
        
        Returns:
            MatchResult showing expected fills
        """
        # Create temporary order
        order = Order(
            order_id=f"sim_{uuid.uuid4().hex[:8]}",
            market_id=self.market_id,
            side=OrderSide.BUY if side == 'buy' else OrderSide.SELL,
            price=price,
            quantity=quantity,
            order_type=OrderType.LIMIT,
        )
        
        # Calculate potential fills without modifying book
        fills = []
        total_filled = 0.0
        total_cost = 0.0
        remaining = quantity
        
        if order.side == OrderSide.BUY:
            opposite_book = self._asks
            price_check = lambda lp: lp <= price
            price_order = sorted(opposite_book.keys())
        else:
            opposite_book = self._bids
            price_check = lambda lp: lp >= price
            price_order = sorted(opposite_book.keys(), reverse=True)
        
        for level_price in price_order:
            if remaining <= 0 or not price_check(level_price):
                break
            
            level = opposite_book[level_price]
            available = level.total_quantity
            fill_qty = min(remaining, available)
            
            if fill_qty > 0:
                fills.append({
                    'price': level_price,
                    'quantity': fill_qty,
                })
                remaining -= fill_qty
                total_filled += fill_qty
                total_cost += fill_qty * level_price
        
        avg_price = total_cost / total_filled if total_filled > 0 else price
        
        return MatchResult(
            order=order,
            fills=[Fill(
                fill_id='sim',
                market_id=self.market_id,
                maker_order_id='sim',
                taker_order_id=order.order_id,
                price=f['price'],
                quantity=f['quantity'],
                side=side,
            ) for f in fills],
            total_filled=total_filled,
            average_price=avg_price,
            remaining=remaining,
        )


# Factory function
def create_matching_engine(
    market_id: str,
    maker_fee_bps: float = 0.0,
    taker_fee_bps: float = 0.0,
) -> LocalMatchingEngine:
    """Create and return a LocalMatchingEngine instance."""
    return LocalMatchingEngine(
        market_id=market_id,
        maker_fee_bps=maker_fee_bps,
        taker_fee_bps=taker_fee_bps,
    )
