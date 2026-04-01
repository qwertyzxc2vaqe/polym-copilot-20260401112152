"""
Auto-Hedging Simulator - Delta-Neutral Arbitrage Modeling.

Phase 2 - Task 71: Simulate opening inversely correlated positions
on Binance Perpetuals when Polymarket positions are filled.

Educational purpose only - paper trading simulation.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, List
from enum import Enum

logger = logging.getLogger(__name__)


class HedgeType(Enum):
    """Types of hedge positions."""
    LONG_PERP = "long_perp"    # Long perpetual futures
    SHORT_PERP = "short_perp"  # Short perpetual futures


class HedgeStatus(Enum):
    """Status of a hedge position."""
    PENDING = "pending"
    OPEN = "open"
    CLOSED = "closed"
    FAILED = "failed"


@dataclass
class HedgePosition:
    """Represents a simulated hedge position."""
    hedge_id: str
    polymarket_order_id: str
    symbol: str
    hedge_type: HedgeType
    entry_price: float
    quantity: float
    status: HedgeStatus = HedgeStatus.PENDING
    
    # Timing
    created_at: float = field(default_factory=lambda: time.time() * 1000)
    opened_at: Optional[float] = None
    closed_at: Optional[float] = None
    
    # PnL tracking
    exit_price: Optional[float] = None
    realized_pnl: float = 0.0
    fees_simulated: float = 0.0
    
    @property
    def unrealized_pnl(self) -> float:
        """Calculate unrealized PnL (requires current price)."""
        return 0.0  # Set externally
    
    @property
    def is_long(self) -> bool:
        return self.hedge_type == HedgeType.LONG_PERP
    
    def to_dict(self) -> dict:
        return {
            'hedge_id': self.hedge_id,
            'polymarket_order_id': self.polymarket_order_id,
            'symbol': self.symbol,
            'hedge_type': self.hedge_type.value,
            'entry_price': self.entry_price,
            'quantity': self.quantity,
            'status': self.status.value,
            'created_at': self.created_at,
            'realized_pnl': self.realized_pnl,
        }


@dataclass
class HedgeMetrics:
    """Aggregate metrics for hedging performance."""
    total_hedges: int = 0
    successful_hedges: int = 0
    failed_hedges: int = 0
    total_pnl: float = 0.0
    total_fees: float = 0.0
    avg_hedge_duration_ms: float = 0.0
    net_delta: float = 0.0


class AutoHedgingSimulator:
    """
    Simulates delta-neutral hedging with Binance Perpetuals.
    
    When a Polymarket YES position is filled:
    - YES bet on BTC going UP → Short BTC-PERP to hedge
    - YES bet on BTC going DOWN → Long BTC-PERP to hedge
    
    This creates a delta-neutral position where profit comes
    from the spread between Polymarket odds and oracle price.
    """
    
    # Simulated Binance Perp fee (0.02% maker, 0.04% taker)
    TAKER_FEE_RATE = 0.0004
    MAKER_FEE_RATE = 0.0002
    
    SYMBOL_MAP = {
        'BTC': 'BTCUSDT',
        'ETH': 'ETHUSDT',
        'SOL': 'SOLUSDT',
        'XRP': 'XRPUSDT',
        'DOGE': 'DOGEUSDT',
        'BNB': 'BNBUSDT',
    }
    
    def __init__(
        self,
        use_maker_fees: bool = True,
        max_position_usd: float = 1000.0,
    ):
        """
        Initialize auto-hedging simulator.
        
        Args:
            use_maker_fees: Use maker fee rate (lower)
            max_position_usd: Maximum hedge position size
        """
        self.use_maker_fees = use_maker_fees
        self.max_position_usd = max_position_usd
        self.fee_rate = self.MAKER_FEE_RATE if use_maker_fees else self.TAKER_FEE_RATE
        
        self._positions: Dict[str, HedgePosition] = {}
        self._closed_positions: List[HedgePosition] = []
        self._metrics = HedgeMetrics()
        self._hedge_counter = 0
    
    def create_hedge(
        self,
        polymarket_order_id: str,
        symbol: str,
        polymarket_direction: str,  # 'up' or 'down'
        notional_usd: float,
        oracle_price: float,
    ) -> Optional[HedgePosition]:
        """
        Create a simulated hedge position.
        
        Args:
            polymarket_order_id: ID of the Polymarket order being hedged
            symbol: Asset symbol (e.g., 'BTC')
            polymarket_direction: Direction of Polymarket bet
            notional_usd: USD value to hedge
            oracle_price: Current oracle price for position sizing
        
        Returns:
            HedgePosition if created successfully
        """
        # Validate
        if notional_usd > self.max_position_usd:
            logger.warning(f"Hedge size ${notional_usd} exceeds max ${self.max_position_usd}")
            notional_usd = self.max_position_usd
        
        # Determine hedge direction (opposite of Polymarket bet)
        # If betting on price going UP, we SHORT to hedge
        # If betting on price going DOWN, we LONG to hedge
        if polymarket_direction.lower() == 'up':
            hedge_type = HedgeType.SHORT_PERP
        else:
            hedge_type = HedgeType.LONG_PERP
        
        # Calculate quantity
        quantity = notional_usd / oracle_price if oracle_price > 0 else 0
        
        # Create position
        self._hedge_counter += 1
        hedge_id = f"hedge_{self._hedge_counter}_{int(time.time() * 1000)}"
        
        position = HedgePosition(
            hedge_id=hedge_id,
            polymarket_order_id=polymarket_order_id,
            symbol=symbol,
            hedge_type=hedge_type,
            entry_price=oracle_price,
            quantity=quantity,
            status=HedgeStatus.OPEN,
            opened_at=time.time() * 1000,
        )
        
        # Calculate entry fees
        position.fees_simulated = notional_usd * self.fee_rate
        
        self._positions[hedge_id] = position
        self._metrics.total_hedges += 1
        
        logger.info(
            f"Opened hedge {hedge_id}: {hedge_type.value} {quantity:.6f} {symbol} "
            f"@ ${oracle_price:.2f} (notional: ${notional_usd:.2f})"
        )
        
        return position
    
    def close_hedge(
        self,
        hedge_id: str,
        exit_price: float,
    ) -> Optional[HedgePosition]:
        """
        Close a hedge position.
        
        Args:
            hedge_id: ID of the hedge to close
            exit_price: Price at which to close
        
        Returns:
            Closed HedgePosition if successful
        """
        if hedge_id not in self._positions:
            logger.warning(f"Hedge {hedge_id} not found")
            return None
        
        position = self._positions[hedge_id]
        
        # Calculate PnL
        price_change = exit_price - position.entry_price
        
        if position.is_long:
            # Long: profit when price goes up
            pnl_per_unit = price_change
        else:
            # Short: profit when price goes down
            pnl_per_unit = -price_change
        
        raw_pnl = pnl_per_unit * position.quantity
        
        # Subtract exit fees
        exit_notional = exit_price * position.quantity
        exit_fee = exit_notional * self.fee_rate
        position.fees_simulated += exit_fee
        
        position.realized_pnl = raw_pnl - position.fees_simulated
        position.exit_price = exit_price
        position.closed_at = time.time() * 1000
        position.status = HedgeStatus.CLOSED
        
        # Move to closed
        del self._positions[hedge_id]
        self._closed_positions.append(position)
        
        # Update metrics
        self._metrics.total_pnl += position.realized_pnl
        self._metrics.total_fees += position.fees_simulated
        self._metrics.successful_hedges += 1
        
        if position.opened_at and position.closed_at:
            duration = position.closed_at - position.opened_at
            # Running average
            n = self._metrics.successful_hedges
            self._metrics.avg_hedge_duration_ms = (
                (self._metrics.avg_hedge_duration_ms * (n - 1) + duration) / n
            )
        
        logger.info(
            f"Closed hedge {hedge_id}: PnL ${position.realized_pnl:.4f} "
            f"(fees: ${position.fees_simulated:.4f})"
        )
        
        return position
    
    def close_all_hedges(self, current_prices: Dict[str, float]) -> List[HedgePosition]:
        """Close all open hedge positions."""
        closed = []
        for hedge_id in list(self._positions.keys()):
            position = self._positions[hedge_id]
            price = current_prices.get(position.symbol, position.entry_price)
            result = self.close_hedge(hedge_id, price)
            if result:
                closed.append(result)
        return closed
    
    def get_net_delta(self, current_prices: Dict[str, float]) -> Dict[str, float]:
        """
        Calculate net delta exposure per symbol.
        
        Returns dict of symbol -> delta (positive = long, negative = short)
        """
        deltas = {}
        for position in self._positions.values():
            if position.symbol not in deltas:
                deltas[position.symbol] = 0.0
            
            notional = position.quantity * current_prices.get(
                position.symbol, position.entry_price
            )
            
            if position.is_long:
                deltas[position.symbol] += notional
            else:
                deltas[position.symbol] -= notional
        
        return deltas
    
    def get_unrealized_pnl(self, current_prices: Dict[str, float]) -> float:
        """Calculate total unrealized PnL across all positions."""
        total_pnl = 0.0
        
        for position in self._positions.values():
            current_price = current_prices.get(position.symbol, position.entry_price)
            price_change = current_price - position.entry_price
            
            if position.is_long:
                pnl = price_change * position.quantity
            else:
                pnl = -price_change * position.quantity
            
            total_pnl += pnl
        
        return total_pnl
    
    def get_position(self, hedge_id: str) -> Optional[HedgePosition]:
        """Get a specific hedge position."""
        return self._positions.get(hedge_id)
    
    def get_open_positions(self) -> List[HedgePosition]:
        """Get all open hedge positions."""
        return list(self._positions.values())
    
    def get_metrics(self) -> HedgeMetrics:
        """Get aggregate hedging metrics."""
        return self._metrics
    
    def analyze_hedge_effectiveness(self) -> Dict:
        """
        Analyze how effective the hedging has been.
        
        Returns metrics on hedge performance vs unhedged exposure.
        """
        if not self._closed_positions:
            return {'status': 'no_data', 'message': 'No closed positions to analyze'}
        
        total_notional = sum(
            p.entry_price * p.quantity for p in self._closed_positions
        )
        total_pnl = sum(p.realized_pnl for p in self._closed_positions)
        total_fees = sum(p.fees_simulated for p in self._closed_positions)
        
        win_count = sum(1 for p in self._closed_positions if p.realized_pnl > 0)
        
        return {
            'total_hedges': len(self._closed_positions),
            'total_notional': total_notional,
            'total_pnl': total_pnl,
            'total_fees': total_fees,
            'net_pnl': total_pnl,
            'win_rate': win_count / len(self._closed_positions) * 100,
            'avg_pnl_per_hedge': total_pnl / len(self._closed_positions),
            'pnl_to_fees_ratio': total_pnl / total_fees if total_fees > 0 else 0,
        }


def create_auto_hedger(
    use_maker_fees: bool = True,
    max_position_usd: float = 1000.0,
) -> AutoHedgingSimulator:
    """Factory function to create auto-hedger."""
    return AutoHedgingSimulator(
        use_maker_fees=use_maker_fees,
        max_position_usd=max_position_usd,
    )
