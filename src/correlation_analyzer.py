"""
Correlation Analyzer - Cross-Pair Statistical Analysis.

Phase 2 - Task 70: Cross-pair correlation with BTC/ETH spread tracking.

Educational purpose only - paper trading simulation.
"""

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from collections import deque

logger = logging.getLogger(__name__)


@dataclass
class CorrelationResult:
    """Result of correlation analysis between two series."""
    pair1: str
    pair2: str
    correlation: float
    rolling_window: int
    timestamp: float
    spread_mean: float
    spread_std: float
    z_score: float
    
    def to_dict(self) -> dict:
        return {
            'pair1': self.pair1,
            'pair2': self.pair2,
            'correlation': self.correlation,
            'rolling_window': self.rolling_window,
            'spread_mean': self.spread_mean,
            'spread_std': self.spread_std,
            'z_score': self.z_score,
        }


class CorrelationAnalyzer:
    """
    Analyzes correlations between trading pairs.
    
    Features:
    - Rolling correlation calculation
    - Spread tracking (BTC/ETH, etc.)
    - Z-score deviation alerts
    - Cointegration hints
    """
    
    DEFAULT_WINDOW = 100
    
    def __init__(
        self,
        pairs: List[str] = None,
        window_size: int = None,
        z_score_threshold: float = 2.0,
    ):
        """
        Initialize correlation analyzer.
        
        Args:
            pairs: Trading pairs to track
            window_size: Rolling window size
            z_score_threshold: Alert threshold for spread deviation
        """
        self.pairs = pairs or ['BTC', 'ETH', 'SOL']
        self.window_size = window_size or self.DEFAULT_WINDOW
        self.z_score_threshold = z_score_threshold
        
        # Price histories
        self._prices: Dict[str, deque] = {}
        self._returns: Dict[str, deque] = {}
        
        # Spread tracking
        self._spread_history: Dict[str, deque] = {}
        
        # Initialize
        for pair in self.pairs:
            self._prices[pair] = deque(maxlen=self.window_size + 1)
            self._returns[pair] = deque(maxlen=self.window_size)
    
    def add_price(self, pair: str, price: float) -> None:
        """Add a price observation for a pair."""
        if pair not in self._prices:
            self._prices[pair] = deque(maxlen=self.window_size + 1)
            self._returns[pair] = deque(maxlen=self.window_size)
        
        prev_prices = self._prices[pair]
        if prev_prices:
            prev_price = prev_prices[-1]
            if prev_price > 0:
                ret = (price - prev_price) / prev_price
                self._returns[pair].append(ret)
        
        prev_prices.append(price)
    
    def calculate_correlation(
        self,
        pair1: str,
        pair2: str,
    ) -> Optional[CorrelationResult]:
        """
        Calculate rolling correlation between two pairs.
        
        Args:
            pair1: First pair symbol
            pair2: Second pair symbol
        
        Returns:
            CorrelationResult or None if insufficient data
        """
        returns1 = list(self._returns.get(pair1, []))
        returns2 = list(self._returns.get(pair2, []))
        
        min_len = min(len(returns1), len(returns2))
        
        if min_len < 10:
            return None
        
        # Align lengths
        r1 = returns1[-min_len:]
        r2 = returns2[-min_len:]
        
        # Calculate correlation
        corr = self._pearson_correlation(r1, r2)
        
        # Calculate spread statistics
        prices1 = list(self._prices.get(pair1, []))
        prices2 = list(self._prices.get(pair2, []))
        
        spread_mean = 0.0
        spread_std = 0.0
        z_score = 0.0
        
        if prices1 and prices2:
            # Calculate log price spread (ratio)
            aligned_len = min(len(prices1), len(prices2), min_len)
            p1 = prices1[-aligned_len:]
            p2 = prices2[-aligned_len:]
            
            spreads = []
            for i in range(aligned_len):
                if p2[i] > 0:
                    spreads.append(p1[i] / p2[i])
            
            if spreads:
                spread_mean = sum(spreads) / len(spreads)
                spread_std = self._std(spreads)
                
                if spread_std > 0:
                    current_spread = spreads[-1]
                    z_score = (current_spread - spread_mean) / spread_std
        
        result = CorrelationResult(
            pair1=pair1,
            pair2=pair2,
            correlation=corr,
            rolling_window=min_len,
            timestamp=datetime.now().timestamp(),
            spread_mean=spread_mean,
            spread_std=spread_std,
            z_score=z_score,
        )
        
        # Log if z-score exceeds threshold
        if abs(z_score) > self.z_score_threshold:
            direction = "above" if z_score > 0 else "below"
            logger.warning(
                f"SPREAD ALERT: {pair1}/{pair2} z-score {z_score:.2f} "
                f"({direction} mean by {abs(z_score):.1f} std)"
            )
        
        return result
    
    def _pearson_correlation(
        self,
        x: List[float],
        y: List[float],
    ) -> float:
        """Calculate Pearson correlation coefficient."""
        n = len(x)
        if n < 2:
            return 0.0
        
        mean_x = sum(x) / n
        mean_y = sum(y) / n
        
        cov = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n)) / n
        std_x = self._std(x)
        std_y = self._std(y)
        
        if std_x == 0 or std_y == 0:
            return 0.0
        
        return cov / (std_x * std_y)
    
    def _std(self, values: List[float]) -> float:
        """Calculate standard deviation."""
        if len(values) < 2:
            return 0.0
        
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        return math.sqrt(variance)
    
    def get_correlation_matrix(self) -> Dict[str, Dict[str, float]]:
        """Get full correlation matrix for all tracked pairs."""
        matrix = {}
        
        for i, pair1 in enumerate(self.pairs):
            matrix[pair1] = {}
            
            for j, pair2 in enumerate(self.pairs):
                if i == j:
                    matrix[pair1][pair2] = 1.0
                elif j < i:
                    # Already calculated
                    matrix[pair1][pair2] = matrix.get(pair2, {}).get(pair1, 0.0)
                else:
                    result = self.calculate_correlation(pair1, pair2)
                    matrix[pair1][pair2] = result.correlation if result else 0.0
        
        return matrix
    
    def track_spread(
        self,
        pair1: str,
        pair2: str,
    ) -> Optional[float]:
        """
        Track and return current spread ratio.
        
        Args:
            pair1: Numerator pair
            pair2: Denominator pair
        
        Returns:
            Current spread ratio or None
        """
        spread_key = f"{pair1}/{pair2}"
        
        if spread_key not in self._spread_history:
            self._spread_history[spread_key] = deque(maxlen=self.window_size)
        
        prices1 = self._prices.get(pair1, [])
        prices2 = self._prices.get(pair2, [])
        
        if not prices1 or not prices2:
            return None
        
        current1 = prices1[-1]
        current2 = prices2[-1]
        
        if current2 == 0:
            return None
        
        spread = current1 / current2
        self._spread_history[spread_key].append(spread)
        
        return spread
    
    def get_spread_statistics(
        self,
        pair1: str,
        pair2: str,
    ) -> Optional[Dict]:
        """Get statistics for a spread pair."""
        spread_key = f"{pair1}/{pair2}"
        history = list(self._spread_history.get(spread_key, []))
        
        if len(history) < 2:
            return None
        
        mean = sum(history) / len(history)
        std = self._std(history)
        current = history[-1]
        
        z_score = (current - mean) / std if std > 0 else 0.0
        
        return {
            'pair1': pair1,
            'pair2': pair2,
            'current': current,
            'mean': mean,
            'std': std,
            'z_score': z_score,
            'min': min(history),
            'max': max(history),
            'observations': len(history),
        }


class AutoHedgingSimulator:
    """
    Auto-Hedging Simulator (Task 71).
    
    Simulates delta-neutral arbitrage strategies
    by tracking position exposure and suggesting hedges.
    """
    
    def __init__(
        self,
        base_pair: str = 'BTC',
        hedge_pairs: List[str] = None,
        correlation_threshold: float = 0.7,
    ):
        """
        Initialize auto-hedging simulator.
        
        Args:
            base_pair: Primary trading pair
            hedge_pairs: Pairs to use for hedging
            correlation_threshold: Min correlation for hedge consideration
        """
        self.base_pair = base_pair
        self.hedge_pairs = hedge_pairs or ['ETH', 'SOL']
        self.correlation_threshold = correlation_threshold
        
        self._positions: Dict[str, float] = {}
        self._correlations: Dict[str, float] = {}
        self._correlation_analyzer = CorrelationAnalyzer(
            pairs=[base_pair] + self.hedge_pairs
        )
    
    def update_position(self, pair: str, delta: float) -> None:
        """Update position for a pair."""
        current = self._positions.get(pair, 0.0)
        self._positions[pair] = current + delta
    
    def add_price(self, pair: str, price: float) -> None:
        """Add price for correlation tracking."""
        self._correlation_analyzer.add_price(pair, price)
    
    def update_correlations(self) -> Dict[str, float]:
        """Update correlation estimates."""
        for hedge_pair in self.hedge_pairs:
            result = self._correlation_analyzer.calculate_correlation(
                self.base_pair, hedge_pair
            )
            if result:
                self._correlations[hedge_pair] = result.correlation
        
        return self._correlations
    
    def calculate_hedge_requirements(
        self,
        base_position_value: float,
    ) -> Dict[str, Dict]:
        """
        Calculate hedge requirements for delta-neutral portfolio.
        
        Args:
            base_position_value: Value of base pair position
        
        Returns:
            Hedge recommendations per pair
        """
        recommendations = {}
        
        for hedge_pair in self.hedge_pairs:
            corr = self._correlations.get(hedge_pair, 0.0)
            
            if abs(corr) < self.correlation_threshold:
                recommendations[hedge_pair] = {
                    'recommendation': 'skip',
                    'reason': f'Correlation {corr:.2f} below threshold {self.correlation_threshold}',
                    'hedge_value': 0.0,
                    'correlation': corr,
                }
                continue
            
            # Calculate hedge ratio (beta-weighted)
            hedge_value = -base_position_value * corr
            
            recommendations[hedge_pair] = {
                'recommendation': 'hedge',
                'hedge_value': hedge_value,
                'correlation': corr,
                'direction': 'short' if hedge_value < 0 else 'long',
                'beta_weight': corr,
            }
        
        return recommendations
    
    def get_portfolio_delta(self) -> float:
        """Get total portfolio delta exposure."""
        total_delta = self._positions.get(self.base_pair, 0.0)
        
        for hedge_pair in self.hedge_pairs:
            corr = self._correlations.get(hedge_pair, 0.0)
            hedge_pos = self._positions.get(hedge_pair, 0.0)
            total_delta += hedge_pos * corr
        
        return total_delta
    
    def is_delta_neutral(self, threshold: float = 0.1) -> bool:
        """Check if portfolio is approximately delta-neutral."""
        delta = self.get_portfolio_delta()
        return abs(delta) < threshold


# Factory functions
def create_correlation_analyzer(
    pairs: List[str] = None,
    window_size: int = 100,
) -> CorrelationAnalyzer:
    """Create and return a CorrelationAnalyzer instance."""
    return CorrelationAnalyzer(
        pairs=pairs,
        window_size=window_size,
    )


def create_auto_hedging_simulator(
    base_pair: str = 'BTC',
    hedge_pairs: List[str] = None,
) -> AutoHedgingSimulator:
    """Create and return an AutoHedgingSimulator instance."""
    return AutoHedgingSimulator(
        base_pair=base_pair,
        hedge_pairs=hedge_pairs,
    )
