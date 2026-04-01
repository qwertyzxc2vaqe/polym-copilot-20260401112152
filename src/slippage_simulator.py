"""
Slippage Simulator - Realistic Order Book Impact Modeling.

Phase 2 - Task 68: Calculate exactly how many ticks of the order book
a mock Market order would consume, applying realistic slippage to paper PnL.

Educational purpose only - paper trading simulation.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple
from collections import deque

logger = logging.getLogger(__name__)


@dataclass
class OrderBookLevel:
    """A single price level in the order book."""
    price: float
    quantity: float
    order_count: int = 1


@dataclass
class SlippageResult:
    """Result of slippage calculation."""
    symbol: str
    side: str  # 'buy' or 'sell'
    requested_quantity: float
    
    # Execution details
    levels_consumed: int
    total_filled: float
    average_price: float
    worst_price: float
    
    # Slippage metrics
    slippage_bps: float  # Total slippage in basis points
    slippage_absolute: float  # Absolute slippage amount
    
    # Cost breakdown
    base_cost: float  # Cost at mid-price
    actual_cost: float  # Actual execution cost
    slippage_cost: float  # Additional cost from slippage
    
    # Fill details
    fills: List[Dict] = field(default_factory=list)
    unfilled_quantity: float = 0.0
    
    def to_dict(self) -> dict:
        return {
            'symbol': self.symbol,
            'side': self.side,
            'requested_quantity': self.requested_quantity,
            'levels_consumed': self.levels_consumed,
            'total_filled': self.total_filled,
            'average_price': self.average_price,
            'worst_price': self.worst_price,
            'slippage_bps': self.slippage_bps,
            'slippage_absolute': self.slippage_absolute,
            'base_cost': self.base_cost,
            'actual_cost': self.actual_cost,
            'slippage_cost': self.slippage_cost,
            'unfilled_quantity': self.unfilled_quantity,
            'fills': self.fills,
        }


@dataclass
class MarketImpactModel:
    """Parameters for market impact estimation."""
    # Linear impact coefficient (price moves linearly with size)
    linear_impact: float = 0.0001
    
    # Square root impact coefficient (institutional flow model)
    sqrt_impact: float = 0.001
    
    # Temporary vs permanent impact ratio
    temporary_ratio: float = 0.7  # 70% temporary, 30% permanent
    
    # Decay rate for temporary impact (per second)
    decay_rate: float = 0.1


class SlippageSimulator:
    """
    Simulates slippage for market orders.
    
    Models:
    1. Order book consumption (walk the book)
    2. Market impact (price moves against large orders)
    3. Temporary vs permanent impact
    """
    
    def __init__(
        self,
        symbols: List[str] = None,
        impact_model: MarketImpactModel = None,
    ):
        """
        Initialize slippage simulator.
        
        Args:
            symbols: Symbols to track
            impact_model: Market impact parameters
        """
        self.symbols = symbols or ['BTC', 'ETH']
        self.impact_model = impact_model or MarketImpactModel()
        
        # Order book snapshots per symbol
        self._order_books: Dict[str, Dict[str, List[OrderBookLevel]]] = {}
        
        # Historical slippage for calibration
        self._slippage_history: Dict[str, deque] = {}
        
        # Initialize
        for symbol in self.symbols:
            self._order_books[symbol] = {'bids': [], 'asks': []}
            self._slippage_history[symbol] = deque(maxlen=1000)
    
    def update_order_book(
        self,
        symbol: str,
        bids: List[Tuple[float, float]],  # [(price, qty), ...]
        asks: List[Tuple[float, float]],
    ) -> None:
        """
        Update order book snapshot.
        
        Args:
            symbol: Symbol
            bids: Bid levels (price, quantity) sorted high to low
            asks: Ask levels (price, quantity) sorted low to high
        """
        if symbol not in self._order_books:
            self._order_books[symbol] = {'bids': [], 'asks': []}
            self._slippage_history[symbol] = deque(maxlen=1000)
        
        self._order_books[symbol]['bids'] = [
            OrderBookLevel(price=p, quantity=q) for p, q in bids
        ]
        self._order_books[symbol]['asks'] = [
            OrderBookLevel(price=p, quantity=q) for p, q in asks
        ]
    
    def calculate_slippage(
        self,
        symbol: str,
        side: str,
        quantity: float,
        mid_price: float = None,
    ) -> SlippageResult:
        """
        Calculate slippage for a market order.
        
        Args:
            symbol: Symbol to trade
            side: 'buy' or 'sell'
            quantity: Order quantity
            mid_price: Current mid price (optional, calculated if not provided)
        
        Returns:
            SlippageResult with detailed breakdown
        """
        book = self._order_books.get(symbol, {'bids': [], 'asks': []})
        
        # Determine which side of book to consume
        if side == 'buy':
            levels = book['asks']  # Buy orders consume asks
        else:
            levels = book['bids']  # Sell orders consume bids
        
        # Calculate mid price if not provided
        if mid_price is None:
            if book['bids'] and book['asks']:
                mid_price = (book['bids'][0].price + book['asks'][0].price) / 2
            elif levels:
                mid_price = levels[0].price
            else:
                mid_price = 0.0
        
        # Walk the book
        fills = []
        remaining = quantity
        levels_consumed = 0
        total_cost = 0.0
        worst_price = None
        
        for level in levels:
            if remaining <= 0:
                break
            
            fill_qty = min(remaining, level.quantity)
            fill_cost = fill_qty * level.price
            
            fills.append({
                'price': level.price,
                'quantity': fill_qty,
                'cost': fill_cost,
            })
            
            total_cost += fill_cost
            remaining -= fill_qty
            levels_consumed += 1
            worst_price = level.price
        
        # Calculate metrics
        total_filled = quantity - remaining
        
        if total_filled > 0:
            average_price = total_cost / total_filled
        else:
            average_price = mid_price
        
        if worst_price is None:
            worst_price = average_price
        
        # Calculate slippage
        base_cost = mid_price * total_filled
        
        if side == 'buy':
            # For buys, slippage is paying more than mid
            slippage_absolute = total_cost - base_cost
            slippage_bps = (average_price - mid_price) / mid_price * 10000 if mid_price > 0 else 0
        else:
            # For sells, slippage is receiving less than mid
            slippage_absolute = base_cost - total_cost
            slippage_bps = (mid_price - average_price) / mid_price * 10000 if mid_price > 0 else 0
        
        result = SlippageResult(
            symbol=symbol,
            side=side,
            requested_quantity=quantity,
            levels_consumed=levels_consumed,
            total_filled=total_filled,
            average_price=average_price,
            worst_price=worst_price,
            slippage_bps=slippage_bps,
            slippage_absolute=slippage_absolute,
            base_cost=base_cost,
            actual_cost=total_cost,
            slippage_cost=abs(slippage_absolute),
            fills=fills,
            unfilled_quantity=remaining,
        )
        
        # Store for history
        self._slippage_history[symbol].append(result)
        
        return result
    
    def estimate_market_impact(
        self,
        symbol: str,
        side: str,
        quantity: float,
        mid_price: float,
        daily_volume: float = 1000000.0,
    ) -> Dict[str, float]:
        """
        Estimate total market impact (beyond order book slippage).
        
        Uses square-root impact model common in institutional trading.
        
        Args:
            symbol: Symbol
            side: 'buy' or 'sell'
            quantity: Order quantity
            mid_price: Current mid price
            daily_volume: Average daily volume for sizing
        
        Returns:
            Dictionary with impact estimates
        """
        # Participation rate
        participation = quantity / daily_volume if daily_volume > 0 else 0
        
        # Square root impact model: impact = sigma * sqrt(participation)
        # Simplified: use coefficient from impact model
        base_impact = self.impact_model.sqrt_impact * (participation ** 0.5)
        
        # Linear component for very small orders
        linear_impact = self.impact_model.linear_impact * participation
        
        # Total expected impact
        total_impact = base_impact + linear_impact
        
        # Split into temporary and permanent
        temporary_impact = total_impact * self.impact_model.temporary_ratio
        permanent_impact = total_impact * (1 - self.impact_model.temporary_ratio)
        
        # Convert to price and basis points
        impact_bps = total_impact * 10000
        impact_price = mid_price * total_impact
        
        return {
            'total_impact_bps': impact_bps,
            'total_impact_price': impact_price,
            'temporary_impact_bps': temporary_impact * 10000,
            'permanent_impact_bps': permanent_impact * 10000,
            'participation_rate': participation,
            'estimated_execution_price': mid_price * (1 + total_impact) if side == 'buy' else mid_price * (1 - total_impact),
        }
    
    def get_optimal_execution_size(
        self,
        symbol: str,
        target_slippage_bps: float,
        side: str,
    ) -> float:
        """
        Calculate optimal order size for target slippage.
        
        Args:
            symbol: Symbol
            target_slippage_bps: Maximum acceptable slippage
            side: 'buy' or 'sell'
        
        Returns:
            Maximum order size to stay within slippage target
        """
        book = self._order_books.get(symbol, {'bids': [], 'asks': []})
        
        if side == 'buy':
            levels = book['asks']
        else:
            levels = book['bids']
        
        if not levels:
            return 0.0
        
        best_price = levels[0].price
        max_price = best_price * (1 + target_slippage_bps / 10000)
        
        if side == 'sell':
            max_price = best_price * (1 - target_slippage_bps / 10000)
        
        # Sum quantity within slippage limit
        total_qty = 0.0
        
        for level in levels:
            if side == 'buy' and level.price > max_price:
                break
            if side == 'sell' and level.price < max_price:
                break
            total_qty += level.quantity
        
        return total_qty
    
    def get_depth_at_price(
        self,
        symbol: str,
        side: str,
        price_levels: int = 5,
    ) -> Dict[str, float]:
        """
        Get cumulative depth at various price levels.
        
        Args:
            symbol: Symbol
            side: 'bids' or 'asks'
            price_levels: Number of levels to analyze
        
        Returns:
            Dictionary with depth at each level
        """
        book = self._order_books.get(symbol, {'bids': [], 'asks': []})
        levels = book.get(side, [])
        
        result = {}
        cumulative = 0.0
        
        for i, level in enumerate(levels[:price_levels]):
            cumulative += level.quantity
            result[f'level_{i+1}_price'] = level.price
            result[f'level_{i+1}_qty'] = level.quantity
            result[f'level_{i+1}_cumulative'] = cumulative
        
        return result
    
    def get_slippage_statistics(self, symbol: str) -> Dict[str, float]:
        """Get historical slippage statistics."""
        history = list(self._slippage_history.get(symbol, []))
        
        if not history:
            return {
                'avg_slippage_bps': 0.0,
                'max_slippage_bps': 0.0,
                'min_slippage_bps': 0.0,
                'total_slippage_cost': 0.0,
                'sample_count': 0,
            }
        
        slippages = [r.slippage_bps for r in history]
        costs = [r.slippage_cost for r in history]
        
        return {
            'avg_slippage_bps': sum(slippages) / len(slippages),
            'max_slippage_bps': max(slippages),
            'min_slippage_bps': min(slippages),
            'total_slippage_cost': sum(costs),
            'sample_count': len(history),
        }
    
    def apply_slippage_to_pnl(
        self,
        paper_pnl: float,
        slippage_result: SlippageResult,
    ) -> float:
        """
        Adjust paper PnL by slippage.
        
        Args:
            paper_pnl: Original paper PnL
            slippage_result: Calculated slippage
        
        Returns:
            Adjusted PnL after slippage
        """
        return paper_pnl - slippage_result.slippage_cost


# Factory function
def create_slippage_simulator(
    symbols: List[str] = None,
) -> SlippageSimulator:
    """Create and return a SlippageSimulator instance."""
    return SlippageSimulator(symbols=symbols)
