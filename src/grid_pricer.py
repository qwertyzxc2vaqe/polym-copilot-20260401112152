"""
Gabagool Grid Market Making Pricing Engine.

Implements the core market-making logic:
1. Calculate fair value spread based on Polymarket odds
2. Place bids $0.01 below best bid on both YES and NO sides
3. Dynamically tilt spreads based on Binance OFI
4. Override on 79% directional bias during price spikes

This is a MAKER-ONLY strategy - all orders use post_only=True.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class GridMode(Enum):
    """Grid operating mode."""
    SYMMETRIC = "symmetric"       # Equal bids on both sides
    TILTED = "tilted"            # OFI-based asymmetric sizing
    DIRECTIONAL = "directional"  # 79% bias override - one side only


@dataclass
class GridQuote:
    """Single quote in the grid."""
    side: str              # "YES" or "NO"
    price: float           # Quote price
    size: float            # Quote size in USDC
    is_maker: bool = True  # Always True for this strategy
    
    @property
    def shares(self) -> float:
        """Number of shares at this price."""
        return self.size / self.price if self.price > 0 else 0


@dataclass
class GridState:
    """Current state of the pricing grid."""
    mode: GridMode
    yes_quote: Optional[GridQuote]
    no_quote: Optional[GridQuote]
    ofi_bias: float                # -1 to +1
    fair_value_yes: float          # Calculated fair value
    fair_value_no: float
    spread_bps: float              # Spread in basis points
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    @property
    def total_exposure(self) -> float:
        """Total capital committed to quotes."""
        exposure = 0.0
        if self.yes_quote:
            exposure += self.yes_quote.size
        if self.no_quote:
            exposure += self.no_quote.size
        return exposure


class GabagoolGridPricer:
    """
    Market-making pricing engine using the Gabagool Grid strategy.
    
    Strategy:
    - Place maker-only limit orders $0.01 below best bid on YES and NO
    - Tilt size allocation based on Binance OFI signal
    - On spike detection, go 79% directional (cancel one side)
    - Automatically refresh quotes every 10-15 seconds
    
    Configuration:
    - base_spread_bps: Base spread in basis points (default 100 = 1%)
    - tick_size: Minimum price increment (default $0.01)
    - max_position_size: Maximum USDC per side
    - ofi_tilt_factor: How much to tilt on OFI (0-1)
    """
    
    # Pricing constants
    DEFAULT_TICK_SIZE = 0.01
    DEFAULT_SPREAD_BPS = 100  # 1% base spread
    
    # Directional bias threshold
    DIRECTIONAL_BIAS_THRESHOLD = 0.79
    SPIKE_FULL_ALLOCATION = 0.90  # 90% to one side on spike
    
    def __init__(
        self,
        base_allocation: float = 100.0,
        tick_size: float = DEFAULT_TICK_SIZE,
        base_spread_bps: float = DEFAULT_SPREAD_BPS,
        ofi_tilt_factor: float = 0.3,
        max_position_per_side: float = 50.0,
    ):
        """
        Initialize Gabagool Grid pricer.
        
        Args:
            base_allocation: Total capital to allocate ($100 default)
            tick_size: Minimum price increment ($0.01)
            base_spread_bps: Base spread in basis points
            ofi_tilt_factor: OFI tilt multiplier (0-1)
            max_position_per_side: Max USDC per side
        """
        self.base_allocation = base_allocation
        self.tick_size = tick_size
        self.base_spread_bps = base_spread_bps
        self.ofi_tilt_factor = ofi_tilt_factor
        self.max_position_per_side = max_position_per_side
        
        # Current state
        self._current_state: Dict[str, GridState] = {}
    
    def calculate_grid(
        self,
        market_id: str,
        best_bid_yes: float,
        best_bid_no: float,
        ofi_bias: float = 0.0,
        spike_detected: bool = False,
        available_capital: float = None,
    ) -> GridState:
        """
        Calculate optimal grid quotes for a market.
        
        Args:
            market_id: Unique market identifier
            best_bid_yes: Current best bid for YES token
            best_bid_no: Current best bid for NO token
            ofi_bias: OFI normalized value (-1 to +1)
            spike_detected: Whether price spike detected
            available_capital: Available capital (defaults to base_allocation)
        
        Returns:
            GridState with calculated quotes
        """
        capital = available_capital or self.base_allocation
        
        # Determine grid mode
        if spike_detected and abs(ofi_bias) > self.DIRECTIONAL_BIAS_THRESHOLD:
            mode = GridMode.DIRECTIONAL
        elif abs(ofi_bias) > 0.2:
            mode = GridMode.TILTED
        else:
            mode = GridMode.SYMMETRIC
        
        # Calculate fair values (YES + NO should = 1.0 in perfect market)
        # Use mid between best bids as reference
        implied_yes = best_bid_yes + self.tick_size  # Best bid + tick ≈ fair value
        implied_no = best_bid_no + self.tick_size
        
        # Normalize to sum to 1.0
        total = implied_yes + implied_no
        if total > 0:
            fair_value_yes = implied_yes / total
            fair_value_no = implied_no / total
        else:
            fair_value_yes = 0.5
            fair_value_no = 0.5
        
        # Calculate our quote prices ($0.01 below best bid)
        our_price_yes = max(0.01, best_bid_yes - self.tick_size)
        our_price_no = max(0.01, best_bid_no - self.tick_size)
        
        # Calculate spread
        spread_bps = self._calculate_spread(fair_value_yes, our_price_yes)
        
        # Calculate size allocation based on mode
        yes_quote, no_quote = self._allocate_sizes(
            mode=mode,
            our_price_yes=our_price_yes,
            our_price_no=our_price_no,
            ofi_bias=ofi_bias,
            capital=capital,
        )
        
        # Create state
        state = GridState(
            mode=mode,
            yes_quote=yes_quote,
            no_quote=no_quote,
            ofi_bias=ofi_bias,
            fair_value_yes=fair_value_yes,
            fair_value_no=fair_value_no,
            spread_bps=spread_bps,
        )
        
        self._current_state[market_id] = state
        
        logger.debug(
            f"Grid [{market_id}]: mode={mode.value}, "
            f"YES=${our_price_yes:.2f}x${yes_quote.size if yes_quote else 0:.2f}, "
            f"NO=${our_price_no:.2f}x${no_quote.size if no_quote else 0:.2f}, "
            f"OFI={ofi_bias:+.2f}"
        )
        
        return state
    
    def _calculate_spread(self, fair_value: float, our_price: float) -> float:
        """Calculate spread in basis points."""
        if fair_value <= 0:
            return 0.0
        spread = (fair_value - our_price) / fair_value * 10000
        return max(0, spread)
    
    def _allocate_sizes(
        self,
        mode: GridMode,
        our_price_yes: float,
        our_price_no: float,
        ofi_bias: float,
        capital: float,
    ) -> Tuple[Optional[GridQuote], Optional[GridQuote]]:
        """
        Allocate capital between YES and NO sides.
        
        Returns:
            Tuple of (yes_quote, no_quote)
        """
        # Cap per-side allocation
        max_per_side = min(capital / 2, self.max_position_per_side)
        
        if mode == GridMode.SYMMETRIC:
            # Equal allocation
            yes_size = max_per_side * 0.5
            no_size = max_per_side * 0.5
            
        elif mode == GridMode.TILTED:
            # Tilt based on OFI
            # Positive OFI = more YES, Negative OFI = more NO
            tilt = ofi_bias * self.ofi_tilt_factor
            
            # Base 50/50 with tilt
            yes_pct = 0.5 + (tilt / 2)  # 0.5 ± tilt/2
            yes_pct = max(0.2, min(0.8, yes_pct))  # Clamp to 20-80%
            
            yes_size = max_per_side * yes_pct
            no_size = max_per_side * (1 - yes_pct)
            
        elif mode == GridMode.DIRECTIONAL:
            # 79% directional bias - heavily favor one side
            if ofi_bias > 0:
                # Bullish - go heavy YES
                yes_size = max_per_side * self.SPIKE_FULL_ALLOCATION
                no_size = max_per_side * (1 - self.SPIKE_FULL_ALLOCATION)
            else:
                # Bearish - go heavy NO
                yes_size = max_per_side * (1 - self.SPIKE_FULL_ALLOCATION)
                no_size = max_per_side * self.SPIKE_FULL_ALLOCATION
        else:
            yes_size = max_per_side * 0.5
            no_size = max_per_side * 0.5
        
        # Create quotes (only if size > minimum)
        min_size = 0.50  # Minimum $0.50 per quote
        
        yes_quote = None
        no_quote = None
        
        if yes_size >= min_size and our_price_yes > 0:
            yes_quote = GridQuote(
                side="YES",
                price=our_price_yes,
                size=round(yes_size, 2),
            )
        
        if no_size >= min_size and our_price_no > 0:
            no_quote = GridQuote(
                side="NO",
                price=our_price_no,
                size=round(no_size, 2),
            )
        
        return yes_quote, no_quote
    
    def get_state(self, market_id: str) -> Optional[GridState]:
        """Get current grid state for market."""
        return self._current_state.get(market_id)
    
    def should_refresh(
        self,
        market_id: str,
        current_best_bid_yes: float,
        current_best_bid_no: float,
        refresh_threshold_ticks: int = 2,
    ) -> bool:
        """
        Check if grid needs refresh due to market movement.
        
        Args:
            market_id: Market identifier
            current_best_bid_yes: Current best bid for YES
            current_best_bid_no: Current best bid for NO
            refresh_threshold_ticks: Number of ticks before refresh
        
        Returns:
            True if refresh needed
        """
        state = self._current_state.get(market_id)
        if not state:
            return True
        
        threshold = self.tick_size * refresh_threshold_ticks
        
        # Check if our quotes are now too far from best bids
        if state.yes_quote:
            expected_price = current_best_bid_yes - self.tick_size
            if abs(state.yes_quote.price - expected_price) > threshold:
                return True
        
        if state.no_quote:
            expected_price = current_best_bid_no - self.tick_size
            if abs(state.no_quote.price - expected_price) > threshold:
                return True
        
        return False
    
    def generate_orders(
        self,
        market_id: str,
        condition_id: str,
        yes_token_id: str,
        no_token_id: str,
    ) -> List[Dict]:
        """
        Generate order parameters for py-clob-client.
        
        Args:
            market_id: Market identifier
            condition_id: Polymarket condition ID
            yes_token_id: YES token ID
            no_token_id: NO token ID
        
        Returns:
            List of order parameter dictionaries
        """
        state = self._current_state.get(market_id)
        if not state:
            return []
        
        orders = []
        
        if state.yes_quote:
            orders.append({
                "token_id": yes_token_id,
                "side": "BUY",
                "price": state.yes_quote.price,
                "size": state.yes_quote.shares,  # Size in shares
                "type": "GTC",  # Good Till Cancelled
                "post_only": True,  # MAKER ONLY
            })
        
        if state.no_quote:
            orders.append({
                "token_id": no_token_id,
                "side": "BUY",
                "price": state.no_quote.price,
                "size": state.no_quote.shares,
                "type": "GTC",
                "post_only": True,
            })
        
        return orders


class GridManager:
    """
    Manages grid pricing across multiple markets with auto-refresh.
    
    Coordinates:
    - OFI signal integration
    - Quote refresh every 10-15 seconds
    - Cross-market exposure limits
    """
    
    REFRESH_INTERVAL_MIN = 10  # Minimum seconds between refreshes
    REFRESH_INTERVAL_MAX = 15  # Maximum seconds between refreshes
    MAX_CONCURRENT_MARKETS = 2  # Cross-market exposure limit
    
    def __init__(
        self,
        pricer: GabagoolGridPricer,
        ofi_engine=None,  # OFIEngine instance
    ):
        """
        Initialize grid manager.
        
        Args:
            pricer: GabagoolGridPricer instance
            ofi_engine: Optional OFIEngine for directional signals
        """
        self.pricer = pricer
        self.ofi_engine = ofi_engine
        
        # Active markets
        self._active_markets: Dict[str, dict] = {}
        self._last_refresh: Dict[str, float] = {}
        self._running = False
    
    async def add_market(
        self,
        market_id: str,
        condition_id: str,
        yes_token_id: str,
        no_token_id: str,
        asset_symbol: str,  # e.g., "BTC" or "ETH"
    ) -> bool:
        """
        Add market to grid management.
        
        Returns:
            True if added, False if at capacity
        """
        # Check exposure limit
        if len(self._active_markets) >= self.MAX_CONCURRENT_MARKETS:
            logger.warning(f"Cannot add market {market_id}: at capacity ({self.MAX_CONCURRENT_MARKETS})")
            return False
        
        self._active_markets[market_id] = {
            "condition_id": condition_id,
            "yes_token_id": yes_token_id,
            "no_token_id": no_token_id,
            "asset_symbol": asset_symbol.lower(),
        }
        
        logger.info(f"Grid: Added market {market_id} ({asset_symbol})")
        return True
    
    def remove_market(self, market_id: str):
        """Remove market from grid management."""
        if market_id in self._active_markets:
            del self._active_markets[market_id]
            logger.info(f"Grid: Removed market {market_id}")
    
    def get_ofi_bias(self, asset_symbol: str) -> Tuple[float, bool]:
        """
        Get OFI bias for asset.
        
        Returns:
            Tuple of (ofi_bias, spike_detected)
        """
        if not self.ofi_engine:
            return 0.0, False
        
        symbol = f"{asset_symbol.lower()}usdt"
        state = self.ofi_engine.get_state(symbol)
        
        if not state:
            return 0.0, False
        
        return state.ofi_normalized, state.spike_detected
    
    async def refresh_all_grids(
        self,
        get_best_bids: callable,  # async fn(token_id) -> float
        available_capital: float,
    ) -> Dict[str, GridState]:
        """
        Refresh all active market grids.
        
        Args:
            get_best_bids: Async function to get best bid for token
            available_capital: Total available capital
        
        Returns:
            Dict of market_id -> GridState
        """
        results = {}
        
        # Divide capital across active markets
        per_market_capital = available_capital / max(1, len(self._active_markets))
        
        for market_id, market_info in self._active_markets.items():
            try:
                # Get current best bids
                best_bid_yes = await get_best_bids(market_info["yes_token_id"])
                best_bid_no = await get_best_bids(market_info["no_token_id"])
                
                # Get OFI bias
                ofi_bias, spike = self.get_ofi_bias(market_info["asset_symbol"])
                
                # Calculate grid
                state = self.pricer.calculate_grid(
                    market_id=market_id,
                    best_bid_yes=best_bid_yes or 0.5,
                    best_bid_no=best_bid_no or 0.5,
                    ofi_bias=ofi_bias,
                    spike_detected=spike,
                    available_capital=per_market_capital,
                )
                
                results[market_id] = state
                self._last_refresh[market_id] = asyncio.get_event_loop().time()
                
            except Exception as e:
                logger.error(f"Error refreshing grid for {market_id}: {e}")
        
        return results


# Factory function
def create_gabagool_pricer(
    base_allocation: float = 100.0,
    ofi_engine=None,
) -> Tuple[GabagoolGridPricer, GridManager]:
    """
    Create Gabagool pricing system.
    
    Args:
        base_allocation: Total capital to allocate
        ofi_engine: Optional OFIEngine for directional signals
    
    Returns:
        Tuple of (pricer, manager)
    """
    pricer = GabagoolGridPricer(base_allocation=base_allocation)
    manager = GridManager(pricer=pricer, ofi_engine=ofi_engine)
    
    return pricer, manager


# Test
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    
    # Create pricer
    pricer = GabagoolGridPricer(base_allocation=100.0)
    
    # Test symmetric mode
    state = pricer.calculate_grid(
        market_id="test-btc-5min",
        best_bid_yes=0.55,
        best_bid_no=0.44,
        ofi_bias=0.0,
    )
    print(f"Symmetric: {state}")
    
    # Test tilted mode
    state = pricer.calculate_grid(
        market_id="test-btc-5min",
        best_bid_yes=0.55,
        best_bid_no=0.44,
        ofi_bias=0.5,  # Bullish
    )
    print(f"Tilted (bullish): {state}")
    
    # Test directional mode
    state = pricer.calculate_grid(
        market_id="test-btc-5min",
        best_bid_yes=0.55,
        best_bid_no=0.44,
        ofi_bias=0.85,  # Strong bullish
        spike_detected=True,
    )
    print(f"Directional (spike): {state}")
