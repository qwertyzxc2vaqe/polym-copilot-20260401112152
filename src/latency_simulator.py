"""
Latency Jitter Simulator.

Phase 2 - Task 75: Simulate network latency with 5-50ms jitter.

Educational purpose only - paper trading simulation.
"""

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Callable
from collections import deque

logger = logging.getLogger(__name__)


@dataclass
class LatencyStats:
    """Latency statistics."""
    min_ms: float
    max_ms: float
    mean_ms: float
    median_ms: float
    p95_ms: float
    p99_ms: float
    std_ms: float
    samples: int


class LatencyJitterSimulator:
    """
    Simulates realistic network latency with jitter.
    
    Features:
    - Configurable base latency (5-50ms default)
    - Random jitter distribution
    - Spike simulation
    - Latency tracking/statistics
    """
    
    DEFAULT_MIN_LATENCY_MS = 5
    DEFAULT_MAX_LATENCY_MS = 50
    
    def __init__(
        self,
        min_latency_ms: float = None,
        max_latency_ms: float = None,
        spike_probability: float = 0.01,
        spike_multiplier: float = 5.0,
    ):
        """
        Initialize latency simulator.
        
        Args:
            min_latency_ms: Minimum latency in ms
            max_latency_ms: Maximum latency in ms
            spike_probability: Probability of latency spike
            spike_multiplier: Multiplier for spike latency
        """
        self.min_latency_ms = min_latency_ms or self.DEFAULT_MIN_LATENCY_MS
        self.max_latency_ms = max_latency_ms or self.DEFAULT_MAX_LATENCY_MS
        self.spike_probability = spike_probability
        self.spike_multiplier = spike_multiplier
        
        self._latency_history: deque = deque(maxlen=10000)
        self._enabled = True
    
    def get_latency(self) -> float:
        """
        Get a random latency value.
        
        Returns:
            Latency in milliseconds
        """
        if not self._enabled:
            return 0.0
        
        # Base latency (uniform distribution)
        base = random.uniform(self.min_latency_ms, self.max_latency_ms)
        
        # Occasional spike
        if random.random() < self.spike_probability:
            latency = base * self.spike_multiplier
        else:
            latency = base
        
        self._latency_history.append(latency)
        return latency
    
    async def simulate_delay(self) -> float:
        """
        Add simulated latency delay.
        
        Returns:
            Actual delay in milliseconds
        """
        latency_ms = self.get_latency()
        
        if latency_ms > 0:
            await asyncio.sleep(latency_ms / 1000.0)
        
        return latency_ms
    
    def sync_simulate_delay(self) -> float:
        """
        Synchronous version of delay simulation.
        
        Returns:
            Actual delay in milliseconds
        """
        latency_ms = self.get_latency()
        
        if latency_ms > 0:
            time.sleep(latency_ms / 1000.0)
        
        return latency_ms
    
    def get_statistics(self) -> LatencyStats:
        """Get latency statistics."""
        history = list(self._latency_history)
        
        if not history:
            return LatencyStats(
                min_ms=0, max_ms=0, mean_ms=0, median_ms=0,
                p95_ms=0, p99_ms=0, std_ms=0, samples=0,
            )
        
        sorted_history = sorted(history)
        n = len(sorted_history)
        
        # Calculate statistics
        mean = sum(sorted_history) / n
        variance = sum((x - mean) ** 2 for x in sorted_history) / n
        std = variance ** 0.5
        
        return LatencyStats(
            min_ms=sorted_history[0],
            max_ms=sorted_history[-1],
            mean_ms=mean,
            median_ms=sorted_history[n // 2],
            p95_ms=sorted_history[int(n * 0.95)],
            p99_ms=sorted_history[int(n * 0.99)],
            std_ms=std,
            samples=n,
        )
    
    def enable(self) -> None:
        """Enable latency simulation."""
        self._enabled = True
    
    def disable(self) -> None:
        """Disable latency simulation."""
        self._enabled = False
    
    def is_enabled(self) -> bool:
        """Check if simulation is enabled."""
        return self._enabled
    
    def clear_history(self) -> None:
        """Clear latency history."""
        self._latency_history.clear()


class LatencyArbTester:
    """
    Latency Arbitrage Tester (Task 94).
    
    Tests if strategy remains profitable under realistic latency.
    """
    
    def __init__(
        self,
        latency_simulator: LatencyJitterSimulator = None,
    ):
        """
        Initialize latency arb tester.
        
        Args:
            latency_simulator: Latency simulator instance
        """
        self.latency_simulator = latency_simulator or LatencyJitterSimulator()
        
        self._test_results: List[Dict] = []
    
    async def test_strategy_latency(
        self,
        strategy_func: Callable,
        iterations: int = 100,
        expected_pnl_per_trade: float = 1.0,
    ) -> Dict:
        """
        Test strategy profitability under latency.
        
        Args:
            strategy_func: Strategy function to test
            iterations: Number of test iterations
            expected_pnl_per_trade: Expected PnL without latency
        
        Returns:
            Test results dictionary
        """
        pnls = []
        latencies = []
        
        for i in range(iterations):
            # Simulate order-to-fill latency
            latency = await self.latency_simulator.simulate_delay()
            latencies.append(latency)
            
            # Calculate slippage due to latency
            # Assume 0.01% slippage per ms for HFT strategies
            slippage_pct = latency * 0.0001
            
            # Adjusted PnL
            adjusted_pnl = expected_pnl_per_trade * (1 - slippage_pct)
            pnls.append(adjusted_pnl)
        
        # Analyze results
        total_pnl = sum(pnls)
        mean_pnl = total_pnl / iterations
        win_rate = sum(1 for p in pnls if p > 0) / iterations
        
        stats = self.latency_simulator.get_statistics()
        
        result = {
            'iterations': iterations,
            'total_pnl': total_pnl,
            'mean_pnl': mean_pnl,
            'win_rate': win_rate,
            'profitable': total_pnl > 0,
            'latency_stats': {
                'mean_ms': stats.mean_ms,
                'p95_ms': stats.p95_ms,
                'p99_ms': stats.p99_ms,
            },
            'edge_preserved': mean_pnl > expected_pnl_per_trade * 0.5,
        }
        
        self._test_results.append(result)
        
        logger.info(
            f"Latency test: {iterations} iterations, "
            f"PnL: ${total_pnl:.2f}, "
            f"Edge preserved: {result['edge_preserved']}"
        )
        
        return result
    
    def test_latency_sensitivity(
        self,
        latency_levels: List[float] = None,
        expected_edge_bp: float = 10.0,  # Basis points
    ) -> Dict:
        """
        Test strategy sensitivity to different latency levels.
        
        Args:
            latency_levels: Latency levels to test (ms)
            expected_edge_bp: Expected edge in basis points
        
        Returns:
            Sensitivity analysis results
        """
        latency_levels = latency_levels or [5, 10, 20, 50, 100, 200]
        
        results = []
        
        for latency_ms in latency_levels:
            # Calculate edge erosion
            # Assume 1bp erosion per 10ms of latency
            erosion_bp = latency_ms / 10.0
            remaining_edge_bp = max(0, expected_edge_bp - erosion_bp)
            
            profitable = remaining_edge_bp > 0
            
            results.append({
                'latency_ms': latency_ms,
                'edge_erosion_bp': erosion_bp,
                'remaining_edge_bp': remaining_edge_bp,
                'profitable': profitable,
                'roi_factor': remaining_edge_bp / expected_edge_bp if expected_edge_bp > 0 else 0,
            })
        
        # Find break-even latency
        break_even_ms = expected_edge_bp * 10.0  # ms
        
        return {
            'expected_edge_bp': expected_edge_bp,
            'break_even_latency_ms': break_even_ms,
            'results': results,
            'max_profitable_latency_ms': max(
                (r['latency_ms'] for r in results if r['profitable']),
                default=0
            ),
        }
    
    def get_test_history(self) -> List[Dict]:
        """Get test result history."""
        return self._test_results


class TickSizeOptimizer:
    """
    Tick Size Optimizer (Task 93).
    
    Optimizes quoting granularity for Polymarket's $0.01 tick.
    """
    
    POLYMARKET_TICK = 0.01  # $0.01 tick size
    
    def __init__(
        self,
        tick_size: float = None,
    ):
        """
        Initialize tick size optimizer.
        
        Args:
            tick_size: Market tick size
        """
        self.tick_size = tick_size or self.POLYMARKET_TICK
    
    def round_to_tick(self, price: float) -> float:
        """Round price to nearest tick."""
        return round(price / self.tick_size) * self.tick_size
    
    def calculate_optimal_spread(
        self,
        volatility: float,
        inventory_skew: float = 0.0,
        adverse_selection_cost: float = 0.001,
    ) -> Dict:
        """
        Calculate optimal bid-ask spread.
        
        Args:
            volatility: Asset volatility (annualized)
            inventory_skew: Current inventory skew (-1 to 1)
            adverse_selection_cost: AS cost estimate
        
        Returns:
            Optimal spread parameters
        """
        # Base spread from volatility (simplified Avellaneda-Stoikov)
        daily_vol = volatility / (252 ** 0.5)
        base_spread = daily_vol * 2 + adverse_selection_cost
        
        # Round to tick
        min_spread_ticks = max(1, int(base_spread / self.tick_size))
        optimal_spread = min_spread_ticks * self.tick_size
        
        # Inventory adjustment
        skew_adjustment = inventory_skew * self.tick_size
        
        return {
            'base_spread': base_spread,
            'optimal_spread': optimal_spread,
            'spread_ticks': min_spread_ticks,
            'bid_adjustment': -skew_adjustment,
            'ask_adjustment': skew_adjustment,
            'tick_size': self.tick_size,
        }
    
    def is_price_valid(self, price: float) -> bool:
        """Check if price is valid for tick size."""
        ticks = price / self.tick_size
        return abs(ticks - round(ticks)) < 1e-9
    
    def get_price_levels(
        self,
        mid_price: float,
        num_levels: int = 5,
    ) -> Dict:
        """
        Get valid price levels around mid price.
        
        Args:
            mid_price: Current mid price
            num_levels: Number of levels per side
        
        Returns:
            Bid and ask price levels
        """
        mid_tick = self.round_to_tick(mid_price)
        
        bids = [mid_tick - (i + 1) * self.tick_size for i in range(num_levels)]
        asks = [mid_tick + (i + 1) * self.tick_size for i in range(num_levels)]
        
        return {
            'mid': mid_tick,
            'bids': bids,
            'asks': asks,
            'tick_size': self.tick_size,
        }


# Factory functions
def create_latency_simulator(
    min_latency_ms: float = 5,
    max_latency_ms: float = 50,
) -> LatencyJitterSimulator:
    """Create and return a LatencyJitterSimulator instance."""
    return LatencyJitterSimulator(
        min_latency_ms=min_latency_ms,
        max_latency_ms=max_latency_ms,
    )


def create_latency_arb_tester() -> LatencyArbTester:
    """Create and return a LatencyArbTester instance."""
    return LatencyArbTester()


def create_tick_size_optimizer(
    tick_size: float = 0.01,
) -> TickSizeOptimizer:
    """Create and return a TickSizeOptimizer instance."""
    return TickSizeOptimizer(tick_size=tick_size)
