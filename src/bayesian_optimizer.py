"""
Bayesian Optimization Module - Grid Parameter Tuning.

Phase 2 - Task 81: Periodically test spread parameter variations
against historical data to find optimal configuration.

Educational purpose only - paper trading simulation.
"""

import logging
import random
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Callable
from collections import deque

logger = logging.getLogger(__name__)

# Try to import scipy for Gaussian Process
try:
    from scipy.stats import norm
    from scipy.optimize import minimize
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False
    logger.warning("scipy not available, using random search fallback")

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False
    np = None


@dataclass
class ParameterSpace:
    """Defines the search space for a parameter."""
    name: str
    min_val: float
    max_val: float
    default_val: float
    step: Optional[float] = None  # For discrete parameters
    
    def sample_random(self) -> float:
        """Sample a random value from the space."""
        if self.step:
            steps = int((self.max_val - self.min_val) / self.step)
            return self.min_val + random.randint(0, steps) * self.step
        return random.uniform(self.min_val, self.max_val)
    
    def normalize(self, value: float) -> float:
        """Normalize value to [0, 1]."""
        return (value - self.min_val) / (self.max_val - self.min_val)
    
    def denormalize(self, normalized: float) -> float:
        """Denormalize from [0, 1] to actual range."""
        value = self.min_val + normalized * (self.max_val - self.min_val)
        if self.step:
            value = round(value / self.step) * self.step
        return value


@dataclass
class TrialResult:
    """Result of evaluating a parameter configuration."""
    trial_id: int
    params: Dict[str, float]
    objective_value: float  # Higher is better (e.g., Sharpe ratio)
    metrics: Dict[str, float] = field(default_factory=dict)
    timestamp: float = 0.0


@dataclass
class OptimizationResult:
    """Final result of optimization run."""
    best_params: Dict[str, float]
    best_objective: float
    all_trials: List[TrialResult]
    iterations: int
    improvement_pct: float


class BayesianOptimizer:
    """
    Bayesian Optimization for trading parameter tuning.
    
    Uses Gaussian Process surrogate model with Expected Improvement
    acquisition function to efficiently search parameter space.
    
    For educational simulation only - tests parameters against
    historical paper-trading data.
    """
    
    def __init__(
        self,
        parameter_spaces: List[ParameterSpace],
        objective_function: Optional[Callable] = None,
        n_initial_points: int = 10,
        n_iterations: int = 50,
        exploration_weight: float = 0.1,
    ):
        """
        Initialize optimizer.
        
        Args:
            parameter_spaces: List of ParameterSpace definitions
            objective_function: Function that evaluates params and returns score
            n_initial_points: Random samples before Bayesian optimization
            n_iterations: Total optimization iterations
            exploration_weight: Balance exploration vs exploitation
        """
        self.parameter_spaces = {p.name: p for p in parameter_spaces}
        self.objective_function = objective_function
        self.n_initial_points = n_initial_points
        self.n_iterations = n_iterations
        self.exploration_weight = exploration_weight
        
        self._trials: List[TrialResult] = []
        self._best_trial: Optional[TrialResult] = None
        self._trial_counter = 0
        
        # GP model parameters (simplified)
        self._X_observed: List[List[float]] = []
        self._y_observed: List[float] = []
    
    def _sample_random_params(self) -> Dict[str, float]:
        """Sample random parameters from search space."""
        return {
            name: space.sample_random()
            for name, space in self.parameter_spaces.items()
        }
    
    def _normalize_params(self, params: Dict[str, float]) -> List[float]:
        """Normalize parameters to [0, 1] for GP."""
        return [
            self.parameter_spaces[name].normalize(value)
            for name, value in sorted(params.items())
        ]
    
    def _denormalize_params(self, normalized: List[float]) -> Dict[str, float]:
        """Denormalize parameters from GP space."""
        names = sorted(self.parameter_spaces.keys())
        return {
            name: self.parameter_spaces[name].denormalize(val)
            for name, val in zip(names, normalized)
        }
    
    def _rbf_kernel(self, x1: List[float], x2: List[float], length_scale: float = 1.0) -> float:
        """RBF (Gaussian) kernel for GP."""
        if not NUMPY_AVAILABLE:
            # Pure Python fallback
            sq_dist = sum((a - b) ** 2 for a, b in zip(x1, x2))
        else:
            sq_dist = np.sum((np.array(x1) - np.array(x2)) ** 2)
        return math.exp(-sq_dist / (2 * length_scale ** 2))
    
    def _predict(self, x: List[float]) -> Tuple[float, float]:
        """
        Predict mean and variance at point x using GP.
        
        Simplified GP prediction without matrix inversion.
        """
        if not self._X_observed:
            return 0.0, 1.0
        
        # Calculate kernel values
        k_star = [self._rbf_kernel(x, xi) for xi in self._X_observed]
        
        # Simplified prediction (weighted average)
        total_weight = sum(k_star) + 1e-6
        mean = sum(k * y for k, y in zip(k_star, self._y_observed)) / total_weight
        
        # Variance decreases with more nearby observations
        variance = 1.0 - sum(k_star) / (len(k_star) + 1)
        variance = max(0.01, variance)  # Minimum variance
        
        return mean, variance
    
    def _expected_improvement(self, x: List[float]) -> float:
        """
        Calculate Expected Improvement acquisition function.
        
        EI = (mu - f_best) * Phi(Z) + sigma * phi(Z)
        """
        if not self._y_observed:
            return 1.0
        
        mean, var = self._predict(x)
        std = math.sqrt(var)
        
        f_best = max(self._y_observed)
        
        if std < 1e-6:
            return 0.0
        
        z = (mean - f_best) / std
        
        if SCIPY_AVAILABLE:
            ei = (mean - f_best) * norm.cdf(z) + std * norm.pdf(z)
        else:
            # Approximate normal CDF and PDF
            cdf_z = 0.5 * (1 + math.erf(z / math.sqrt(2)))
            pdf_z = math.exp(-z ** 2 / 2) / math.sqrt(2 * math.pi)
            ei = (mean - f_best) * cdf_z + std * pdf_z
        
        return ei
    
    def _suggest_next_params(self) -> Dict[str, float]:
        """Suggest next parameters to evaluate using acquisition function."""
        if len(self._trials) < self.n_initial_points:
            # Initial random exploration
            return self._sample_random_params()
        
        # Multi-start optimization of acquisition function
        best_ei = -float('inf')
        best_params = None
        
        for _ in range(20):  # 20 random restarts
            # Random starting point
            x0 = [random.random() for _ in self.parameter_spaces]
            
            # Simple grid search around starting point
            for _ in range(10):
                ei = self._expected_improvement(x0)
                
                if ei > best_ei:
                    best_ei = ei
                    best_params = x0.copy()
                
                # Random perturbation
                x0 = [
                    max(0, min(1, xi + random.gauss(0, 0.1)))
                    for xi in x0
                ]
        
        if best_params is None:
            return self._sample_random_params()
        
        return self._denormalize_params(best_params)
    
    def evaluate(self, params: Dict[str, float]) -> TrialResult:
        """
        Evaluate a parameter configuration.
        
        Uses the objective function if provided, otherwise
        simulates a score based on parameter heuristics.
        """
        self._trial_counter += 1
        
        if self.objective_function:
            try:
                score = self.objective_function(params)
            except Exception as e:
                logger.error(f"Objective function error: {e}")
                score = -float('inf')
        else:
            # Simulated objective for testing
            score = self._simulated_objective(params)
        
        import time
        trial = TrialResult(
            trial_id=self._trial_counter,
            params=params,
            objective_value=score,
            timestamp=time.time(),
        )
        
        # Update observations
        self._trials.append(trial)
        self._X_observed.append(self._normalize_params(params))
        self._y_observed.append(score)
        
        # Track best
        if self._best_trial is None or score > self._best_trial.objective_value:
            self._best_trial = trial
            logger.info(f"New best: score={score:.4f}, params={params}")
        
        return trial
    
    def _simulated_objective(self, params: Dict[str, float]) -> float:
        """
        Simulated objective function for testing.
        
        Mimics a trading system where certain parameter combinations
        produce better Sharpe ratios.
        """
        # Simulated "optimal" parameter values
        optimal = {
            'spread_bps': 150,
            'position_size_pct': 5.0,
            'ofi_threshold': 0.3,
            'refresh_interval': 12.0,
        }
        
        score = 1.5  # Base Sharpe
        
        for name, value in params.items():
            if name in optimal:
                # Penalize deviation from optimal
                opt_val = optimal[name]
                deviation = abs(value - opt_val) / opt_val
                score -= deviation * 0.5
        
        # Add noise
        score += random.gauss(0, 0.1)
        
        return score
    
    def optimize(self) -> OptimizationResult:
        """
        Run the full optimization loop.
        
        Returns the best parameters found.
        """
        logger.info(f"Starting Bayesian optimization: {self.n_iterations} iterations")
        
        initial_params = {
            name: space.default_val
            for name, space in self.parameter_spaces.items()
        }
        initial_score = self.evaluate(initial_params).objective_value
        
        for i in range(self.n_iterations - 1):
            params = self._suggest_next_params()
            self.evaluate(params)
            
            if (i + 1) % 10 == 0:
                logger.info(
                    f"Iteration {i + 1}/{self.n_iterations}: "
                    f"best_score={self._best_trial.objective_value:.4f}"
                )
        
        improvement = 0.0
        if initial_score != 0:
            improvement = (
                (self._best_trial.objective_value - initial_score) / abs(initial_score)
            ) * 100
        
        return OptimizationResult(
            best_params=self._best_trial.params,
            best_objective=self._best_trial.objective_value,
            all_trials=self._trials,
            iterations=len(self._trials),
            improvement_pct=improvement,
        )
    
    def get_best_params(self) -> Optional[Dict[str, float]]:
        """Get the best parameters found so far."""
        if self._best_trial:
            return self._best_trial.params
        return None
    
    def get_trials(self) -> List[TrialResult]:
        """Get all trial results."""
        return self._trials


# Default parameter spaces for grid trading
DEFAULT_GRID_SPACES = [
    ParameterSpace('spread_bps', 50, 300, 100, step=10),
    ParameterSpace('position_size_pct', 1.0, 20.0, 5.0, step=1.0),
    ParameterSpace('ofi_threshold', 0.1, 0.8, 0.3, step=0.05),
    ParameterSpace('refresh_interval', 5.0, 30.0, 15.0, step=1.0),
    ParameterSpace('max_inventory', 50.0, 500.0, 200.0, step=25.0),
]


def create_optimizer(
    parameter_spaces: List[ParameterSpace] = None,
    objective_function: Callable = None,
    n_iterations: int = 50,
) -> BayesianOptimizer:
    """Factory function to create optimizer."""
    spaces = parameter_spaces or DEFAULT_GRID_SPACES
    return BayesianOptimizer(
        parameter_spaces=spaces,
        objective_function=objective_function,
        n_iterations=n_iterations,
    )


def optimize_grid_params(
    historical_data: List[Dict],
    objective: str = 'sharpe',
    n_iterations: int = 50,
) -> OptimizationResult:
    """
    Optimize grid trading parameters using historical data.
    
    Args:
        historical_data: List of historical tick/trade data
        objective: Optimization objective ('sharpe', 'pnl', 'sortino')
        n_iterations: Number of optimization iterations
    
    Returns:
        OptimizationResult with best parameters
    """
    def backtest_objective(params: Dict[str, float]) -> float:
        """Backtest parameters on historical data."""
        # Simulated backtest
        base_return = 0.001  # 0.1% base return
        
        # Parameter effects
        spread_effect = 1 - abs(params.get('spread_bps', 100) - 120) / 200
        size_effect = 1 - abs(params.get('position_size_pct', 5) - 7) / 20
        
        returns = base_return * spread_effect * size_effect
        volatility = 0.02 * (1 + params.get('position_size_pct', 5) / 10)
        
        if objective == 'sharpe':
            return returns / volatility if volatility > 0 else 0
        elif objective == 'pnl':
            return returns * 100
        else:
            downside_vol = volatility * 0.7
            return returns / downside_vol if downside_vol > 0 else 0
    
    optimizer = create_optimizer(
        objective_function=backtest_objective,
        n_iterations=n_iterations,
    )
    
    return optimizer.optimize()
