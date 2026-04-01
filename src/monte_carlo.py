"""
Monte Carlo Position Simulator.

Phase 2 - Task 80: Run 10,000 PnL-path permutations for Sharpe CI
and tail-risk estimation.

Educational purpose only - paper trading simulation.
"""

import logging
import random
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
import math
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)


@dataclass
class MonteCarloResult:
    """Results from a Monte Carlo simulation run."""
    mean_pnl: float
    std_pnl: float
    median_pnl: float
    percentile_5: float   # VaR at 5%
    percentile_1: float   # VaR at 1%
    percentile_95: float  # Upper bound
    percentile_99: float
    max_pnl: float
    min_pnl: float
    sharpe_mean: float
    sharpe_std: float
    sharpe_ci_lower: float  # 95% CI lower
    sharpe_ci_upper: float  # 95% CI upper
    max_drawdown_mean: float
    max_drawdown_worst: float
    paths_simulated: int
    
    def to_dict(self) -> dict:
        return {
            'mean_pnl': self.mean_pnl,
            'std_pnl': self.std_pnl,
            'median_pnl': self.median_pnl,
            'var_5pct': self.percentile_5,
            'var_1pct': self.percentile_1,
            'percentile_95': self.percentile_95,
            'percentile_99': self.percentile_99,
            'max_pnl': self.max_pnl,
            'min_pnl': self.min_pnl,
            'sharpe_mean': self.sharpe_mean,
            'sharpe_std': self.sharpe_std,
            'sharpe_ci_95': [self.sharpe_ci_lower, self.sharpe_ci_upper],
            'max_drawdown_mean': self.max_drawdown_mean,
            'max_drawdown_worst': self.max_drawdown_worst,
            'paths_simulated': self.paths_simulated,
        }


class MonteCarloSimulator:
    """
    Monte Carlo simulator for PnL distribution estimation.
    
    Features:
    - 10,000 permutation runs
    - Bootstrap resampling of historical returns
    - Sharpe ratio confidence intervals
    - VaR and CVaR estimation
    - Max drawdown distribution
    """
    
    DEFAULT_PATHS = 10000
    TRADING_DAYS = 252  # For annualization
    
    def __init__(
        self,
        n_paths: int = None,
        risk_free_rate: float = 0.05,
        random_seed: int = None,
    ):
        """
        Initialize Monte Carlo simulator.
        
        Args:
            n_paths: Number of simulation paths
            risk_free_rate: Annual risk-free rate for Sharpe
            random_seed: Optional seed for reproducibility
        """
        self.n_paths = n_paths or self.DEFAULT_PATHS
        self.risk_free_rate = risk_free_rate
        
        if random_seed is not None:
            random.seed(random_seed)
        
        self._historical_returns: List[float] = []
    
    def add_returns(self, returns: List[float]) -> None:
        """Add historical returns for bootstrap."""
        self._historical_returns.extend(returns)
    
    def simulate_paths(
        self,
        initial_capital: float = 10000.0,
        n_periods: int = 252,
        returns: List[float] = None,
    ) -> MonteCarloResult:
        """
        Run Monte Carlo simulation.
        
        Args:
            initial_capital: Starting capital
            n_periods: Number of periods per path
            returns: Historical returns to bootstrap from
        
        Returns:
            MonteCarloResult with distribution statistics
        """
        returns = returns or self._historical_returns
        
        if len(returns) < 10:
            raise ValueError("Need at least 10 historical returns")
        
        final_pnls = []
        sharpe_ratios = []
        max_drawdowns = []
        
        for _ in range(self.n_paths):
            path_result = self._simulate_single_path(
                returns=returns,
                initial_capital=initial_capital,
                n_periods=n_periods,
            )
            final_pnls.append(path_result['final_pnl'])
            sharpe_ratios.append(path_result['sharpe'])
            max_drawdowns.append(path_result['max_drawdown'])
        
        # Compute statistics
        final_pnls.sort()
        sharpe_ratios.sort()
        max_drawdowns.sort()
        
        n = len(final_pnls)
        
        result = MonteCarloResult(
            mean_pnl=sum(final_pnls) / n,
            std_pnl=self._std(final_pnls),
            median_pnl=final_pnls[n // 2],
            percentile_5=final_pnls[int(n * 0.05)],
            percentile_1=final_pnls[int(n * 0.01)],
            percentile_95=final_pnls[int(n * 0.95)],
            percentile_99=final_pnls[int(n * 0.99)],
            max_pnl=final_pnls[-1],
            min_pnl=final_pnls[0],
            sharpe_mean=sum(sharpe_ratios) / n,
            sharpe_std=self._std(sharpe_ratios),
            sharpe_ci_lower=sharpe_ratios[int(n * 0.025)],
            sharpe_ci_upper=sharpe_ratios[int(n * 0.975)],
            max_drawdown_mean=sum(max_drawdowns) / n,
            max_drawdown_worst=max_drawdowns[-1],
            paths_simulated=self.n_paths,
        )
        
        logger.info(
            f"Monte Carlo complete: {self.n_paths} paths, "
            f"Mean PnL: ${result.mean_pnl:.2f}, "
            f"Sharpe CI: [{result.sharpe_ci_lower:.3f}, {result.sharpe_ci_upper:.3f}]"
        )
        
        return result
    
    def _simulate_single_path(
        self,
        returns: List[float],
        initial_capital: float,
        n_periods: int,
    ) -> Dict:
        """Simulate a single path using bootstrap resampling."""
        # Bootstrap resample returns
        sampled_returns = random.choices(returns, k=n_periods)
        
        # Simulate equity curve
        equity = initial_capital
        equity_curve = [equity]
        peak = equity
        max_drawdown = 0.0
        
        for r in sampled_returns:
            equity *= (1 + r)
            equity_curve.append(equity)
            
            if equity > peak:
                peak = equity
            
            drawdown = (peak - equity) / peak if peak > 0 else 0
            max_drawdown = max(max_drawdown, drawdown)
        
        # Calculate path statistics
        final_pnl = equity - initial_capital
        
        # Calculate Sharpe for this path
        if len(sampled_returns) > 1:
            mean_return = sum(sampled_returns) / len(sampled_returns)
            std_return = self._std(sampled_returns)
            
            daily_rf = self.risk_free_rate / self.TRADING_DAYS
            
            if std_return > 0:
                sharpe = (mean_return - daily_rf) / std_return * math.sqrt(self.TRADING_DAYS)
            else:
                sharpe = 0.0
        else:
            sharpe = 0.0
        
        return {
            'final_pnl': final_pnl,
            'sharpe': sharpe,
            'max_drawdown': max_drawdown,
            'equity_curve': equity_curve,
        }
    
    def _std(self, values: List[float]) -> float:
        """Calculate standard deviation."""
        if len(values) < 2:
            return 0.0
        
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
        return math.sqrt(variance)
    
    def calculate_cvar(
        self,
        returns: List[float],
        alpha: float = 0.05,
    ) -> float:
        """
        Calculate Conditional VaR (Expected Shortfall).
        
        Args:
            returns: List of returns
            alpha: Tail percentile (0.05 = 5%)
        
        Returns:
            CVaR (average of worst alpha% returns)
        """
        sorted_returns = sorted(returns)
        cutoff = int(len(sorted_returns) * alpha)
        
        if cutoff < 1:
            return sorted_returns[0] if sorted_returns else 0.0
        
        tail = sorted_returns[:cutoff]
        return sum(tail) / len(tail) if tail else 0.0
    
    def parallel_simulate(
        self,
        initial_capital: float = 10000.0,
        n_periods: int = 252,
        returns: List[float] = None,
        n_workers: int = 4,
    ) -> MonteCarloResult:
        """
        Run Monte Carlo simulation in parallel.
        
        Uses ThreadPoolExecutor for parallel path generation.
        """
        returns = returns or self._historical_returns
        
        if len(returns) < 10:
            raise ValueError("Need at least 10 historical returns")
        
        paths_per_worker = self.n_paths // n_workers
        
        all_pnls = []
        all_sharpes = []
        all_drawdowns = []
        
        def run_batch(batch_size: int):
            results = []
            for _ in range(batch_size):
                result = self._simulate_single_path(
                    returns=returns,
                    initial_capital=initial_capital,
                    n_periods=n_periods,
                )
                results.append(result)
            return results
        
        with ThreadPoolExecutor(max_workers=n_workers) as executor:
            futures = [
                executor.submit(run_batch, paths_per_worker)
                for _ in range(n_workers)
            ]
            
            for future in as_completed(futures):
                batch_results = future.result()
                for r in batch_results:
                    all_pnls.append(r['final_pnl'])
                    all_sharpes.append(r['sharpe'])
                    all_drawdowns.append(r['max_drawdown'])
        
        # Compute statistics
        all_pnls.sort()
        all_sharpes.sort()
        all_drawdowns.sort()
        
        n = len(all_pnls)
        
        return MonteCarloResult(
            mean_pnl=sum(all_pnls) / n,
            std_pnl=self._std(all_pnls),
            median_pnl=all_pnls[n // 2],
            percentile_5=all_pnls[int(n * 0.05)],
            percentile_1=all_pnls[int(n * 0.01)],
            percentile_95=all_pnls[int(n * 0.95)],
            percentile_99=all_pnls[int(n * 0.99)],
            max_pnl=all_pnls[-1],
            min_pnl=all_pnls[0],
            sharpe_mean=sum(all_sharpes) / n,
            sharpe_std=self._std(all_sharpes),
            sharpe_ci_lower=all_sharpes[int(n * 0.025)],
            sharpe_ci_upper=all_sharpes[int(n * 0.975)],
            max_drawdown_mean=sum(all_drawdowns) / n,
            max_drawdown_worst=all_drawdowns[-1],
            paths_simulated=n,
        )


def create_monte_carlo_simulator(
    n_paths: int = 10000,
    risk_free_rate: float = 0.05,
    random_seed: int = None,
) -> MonteCarloSimulator:
    """Create and return a MonteCarloSimulator instance."""
    return MonteCarloSimulator(
        n_paths=n_paths,
        risk_free_rate=risk_free_rate,
        random_seed=random_seed,
    )
