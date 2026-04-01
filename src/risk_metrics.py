"""
Risk Metrics Module - Sharpe, Sortino, VaR Calculations.

Phase 2 - Tasks 72-74: Real-time Sharpe Ratio, Sortino Ratio, VaR
calculator, and drawdown circuit breaker.

Educational purpose only - paper trading simulation.
"""

import asyncio
import json
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Callable
from collections import deque

logger = logging.getLogger(__name__)

# Try to import numpy/scipy
try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False
    np = None

try:
    from scipy import stats
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False


@dataclass
class PortfolioSnapshot:
    """Point-in-time portfolio snapshot."""
    timestamp: float
    total_equity: float
    cash_balance: float
    positions_value: float
    unrealized_pnl: float
    realized_pnl: float
    daily_return: float = 0.0
    cumulative_return: float = 0.0


@dataclass
class RiskMetrics:
    """Comprehensive risk metrics snapshot."""
    timestamp: float
    
    # Performance metrics
    total_return: float = 0.0
    daily_return: float = 0.0
    annualized_return: float = 0.0
    
    # Risk-adjusted returns
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    
    # Volatility
    daily_volatility: float = 0.0
    annualized_volatility: float = 0.0
    downside_volatility: float = 0.0
    
    # Drawdown
    current_drawdown: float = 0.0
    max_drawdown: float = 0.0
    drawdown_duration_days: int = 0
    
    # VaR metrics
    var_95: float = 0.0  # 95% VaR
    var_99: float = 0.0  # 99% VaR
    expected_shortfall_95: float = 0.0  # CVaR 95%
    expected_shortfall_99: float = 0.0  # CVaR 99%
    
    # Win/Loss
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    
    def to_dict(self) -> dict:
        return {
            'timestamp': self.timestamp,
            'total_return': self.total_return,
            'daily_return': self.daily_return,
            'annualized_return': self.annualized_return,
            'sharpe_ratio': self.sharpe_ratio,
            'sortino_ratio': self.sortino_ratio,
            'calmar_ratio': self.calmar_ratio,
            'daily_volatility': self.daily_volatility,
            'annualized_volatility': self.annualized_volatility,
            'downside_volatility': self.downside_volatility,
            'current_drawdown': self.current_drawdown,
            'max_drawdown': self.max_drawdown,
            'drawdown_duration_days': self.drawdown_duration_days,
            'var_95': self.var_95,
            'var_99': self.var_99,
            'expected_shortfall_95': self.expected_shortfall_95,
            'expected_shortfall_99': self.expected_shortfall_99,
            'win_rate': self.win_rate,
            'profit_factor': self.profit_factor,
        }


@dataclass
class CircuitBreakerState:
    """Circuit breaker status."""
    is_triggered: bool = False
    trigger_reason: str = ""
    trigger_time: Optional[float] = None
    equity_at_trigger: float = 0.0
    threshold_violated: str = ""


class RiskMetricsCalculator:
    """
    Real-time risk metrics calculator.
    
    Calculates:
    - Sharpe Ratio (risk-adjusted return vs risk-free rate)
    - Sortino Ratio (return vs downside risk)
    - VaR (Value at Risk) using Historical Simulation
    - Expected Shortfall (CVaR)
    - Maximum Drawdown
    """
    
    # Risk-free rate (annualized, e.g., 5% = 0.05)
    RISK_FREE_RATE = 0.05
    
    # Trading days per year (crypto = 365)
    TRADING_DAYS = 365
    
    # VaR confidence levels
    VAR_CONFIDENCE_95 = 0.05
    VAR_CONFIDENCE_99 = 0.01
    
    def __init__(
        self,
        initial_capital: float = 100.0,
        window_days: int = 30,
        update_interval_seconds: float = 60.0,
    ):
        """
        Initialize risk metrics calculator.
        
        Args:
            initial_capital: Starting capital
            window_days: Rolling window for calculations
            update_interval_seconds: How often to recalculate
        """
        self.initial_capital = initial_capital
        self.window_days = window_days
        self.update_interval_seconds = update_interval_seconds
        
        # Portfolio history
        self._snapshots: deque = deque(maxlen=window_days * 24 * 60)  # 1 per minute
        self._daily_returns: deque = deque(maxlen=window_days)
        
        # Trade history for win/loss calculations
        self._trades: List[Dict] = []
        
        # Current state
        self._peak_equity = initial_capital
        self._current_equity = initial_capital
        self._drawdown_start: Optional[float] = None
        
        # Latest metrics
        self._latest_metrics: Optional[RiskMetrics] = None
    
    def record_snapshot(
        self,
        equity: float,
        cash: float = None,
        positions_value: float = None,
        unrealized_pnl: float = 0.0,
        realized_pnl: float = 0.0,
    ) -> PortfolioSnapshot:
        """
        Record a portfolio snapshot.
        
        Args:
            equity: Total portfolio equity
            cash: Cash balance
            positions_value: Value of positions
            unrealized_pnl: Unrealized PnL
            realized_pnl: Realized PnL
        
        Returns:
            PortfolioSnapshot
        """
        timestamp = time.time() * 1000
        
        if cash is None:
            cash = equity
        if positions_value is None:
            positions_value = equity - cash
        
        # Calculate returns
        if self._snapshots:
            prev = self._snapshots[-1]
            if prev.total_equity > 0:
                daily_return = (equity - prev.total_equity) / prev.total_equity
            else:
                daily_return = 0.0
        else:
            daily_return = 0.0
        
        cumulative_return = (equity - self.initial_capital) / self.initial_capital
        
        snapshot = PortfolioSnapshot(
            timestamp=timestamp,
            total_equity=equity,
            cash_balance=cash,
            positions_value=positions_value,
            unrealized_pnl=unrealized_pnl,
            realized_pnl=realized_pnl,
            daily_return=daily_return,
            cumulative_return=cumulative_return,
        )
        
        self._snapshots.append(snapshot)
        self._current_equity = equity
        
        # Update peak for drawdown
        if equity > self._peak_equity:
            self._peak_equity = equity
            self._drawdown_start = None
        elif self._drawdown_start is None:
            self._drawdown_start = timestamp
        
        return snapshot
    
    def record_trade(
        self,
        pnl: float,
        timestamp: float = None,
    ) -> None:
        """Record a completed trade for win/loss tracking."""
        self._trades.append({
            'pnl': pnl,
            'timestamp': timestamp or time.time() * 1000,
        })
        
        # Keep last 1000 trades
        if len(self._trades) > 1000:
            self._trades = self._trades[-500:]
    
    def calculate_metrics(self) -> RiskMetrics:
        """
        Calculate comprehensive risk metrics.
        
        Returns:
            RiskMetrics object
        """
        metrics = RiskMetrics(timestamp=time.time() * 1000)
        
        if len(self._snapshots) < 2:
            return metrics
        
        # Get returns array
        returns = [s.daily_return for s in self._snapshots if s.daily_return != 0]
        
        if not returns:
            return metrics
        
        if NUMPY_AVAILABLE:
            returns_arr = np.array(returns)
        else:
            returns_arr = returns
        
        # Performance metrics
        metrics.total_return = (self._current_equity - self.initial_capital) / self.initial_capital * 100
        metrics.daily_return = returns[-1] * 100 if returns else 0
        
        # Volatility
        if NUMPY_AVAILABLE:
            metrics.daily_volatility = np.std(returns_arr) * 100
            metrics.annualized_volatility = metrics.daily_volatility * math.sqrt(self.TRADING_DAYS)
            
            # Downside volatility (negative returns only)
            negative_returns = returns_arr[returns_arr < 0]
            if len(negative_returns) > 0:
                metrics.downside_volatility = np.std(negative_returns) * 100 * math.sqrt(self.TRADING_DAYS)
        else:
            # Pure Python fallback
            mean_ret = sum(returns) / len(returns)
            variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
            metrics.daily_volatility = math.sqrt(variance) * 100
            metrics.annualized_volatility = metrics.daily_volatility * math.sqrt(self.TRADING_DAYS)
        
        # Annualized return
        days_elapsed = len(self._snapshots) / (24 * 60)  # Assuming 1 snapshot per minute
        if days_elapsed > 0:
            metrics.annualized_return = metrics.total_return * (365 / max(1, days_elapsed))
        
        # Sharpe Ratio
        excess_return = metrics.annualized_return - self.RISK_FREE_RATE * 100
        if metrics.annualized_volatility > 0:
            metrics.sharpe_ratio = excess_return / metrics.annualized_volatility
        
        # Sortino Ratio
        if metrics.downside_volatility > 0:
            metrics.sortino_ratio = excess_return / metrics.downside_volatility
        
        # Drawdown
        metrics.current_drawdown = (self._peak_equity - self._current_equity) / self._peak_equity * 100
        metrics.max_drawdown = self._calculate_max_drawdown()
        
        if self._drawdown_start:
            metrics.drawdown_duration_days = int((time.time() * 1000 - self._drawdown_start) / (24 * 60 * 60 * 1000))
        
        # Calmar Ratio
        if metrics.max_drawdown > 0:
            metrics.calmar_ratio = metrics.annualized_return / metrics.max_drawdown
        
        # VaR and Expected Shortfall
        var_metrics = self._calculate_var(returns)
        metrics.var_95 = var_metrics['var_95']
        metrics.var_99 = var_metrics['var_99']
        metrics.expected_shortfall_95 = var_metrics['es_95']
        metrics.expected_shortfall_99 = var_metrics['es_99']
        
        # Win/Loss metrics
        win_loss = self._calculate_win_loss()
        metrics.win_rate = win_loss['win_rate']
        metrics.profit_factor = win_loss['profit_factor']
        metrics.avg_win = win_loss['avg_win']
        metrics.avg_loss = win_loss['avg_loss']
        
        self._latest_metrics = metrics
        return metrics
    
    def _calculate_max_drawdown(self) -> float:
        """Calculate maximum drawdown from snapshots."""
        if not self._snapshots:
            return 0.0
        
        peak = self.initial_capital
        max_dd = 0.0
        
        for snapshot in self._snapshots:
            if snapshot.total_equity > peak:
                peak = snapshot.total_equity
            
            dd = (peak - snapshot.total_equity) / peak * 100
            max_dd = max(max_dd, dd)
        
        return max_dd
    
    def _calculate_var(self, returns: List[float]) -> Dict[str, float]:
        """
        Calculate VaR using Historical Simulation.
        
        Historical VaR: Sort returns and take percentile.
        """
        if len(returns) < 10:
            return {'var_95': 0.0, 'var_99': 0.0, 'es_95': 0.0, 'es_99': 0.0}
        
        if NUMPY_AVAILABLE:
            returns_arr = np.array(returns)
            
            # VaR (negative returns at percentile)
            var_95 = -np.percentile(returns_arr, 5) * self._current_equity
            var_99 = -np.percentile(returns_arr, 1) * self._current_equity
            
            # Expected Shortfall (average of returns below VaR)
            threshold_95 = np.percentile(returns_arr, 5)
            threshold_99 = np.percentile(returns_arr, 1)
            
            below_95 = returns_arr[returns_arr <= threshold_95]
            below_99 = returns_arr[returns_arr <= threshold_99]
            
            es_95 = -np.mean(below_95) * self._current_equity if len(below_95) > 0 else var_95
            es_99 = -np.mean(below_99) * self._current_equity if len(below_99) > 0 else var_99
        else:
            # Pure Python fallback
            sorted_returns = sorted(returns)
            n = len(sorted_returns)
            
            idx_95 = int(n * 0.05)
            idx_99 = int(n * 0.01)
            
            var_95 = -sorted_returns[idx_95] * self._current_equity if idx_95 < n else 0
            var_99 = -sorted_returns[idx_99] * self._current_equity if idx_99 < n else 0
            
            es_95 = -sum(sorted_returns[:idx_95 + 1]) / (idx_95 + 1) * self._current_equity if idx_95 > 0 else var_95
            es_99 = -sum(sorted_returns[:idx_99 + 1]) / (idx_99 + 1) * self._current_equity if idx_99 > 0 else var_99
        
        return {
            'var_95': max(0, var_95),
            'var_99': max(0, var_99),
            'es_95': max(0, es_95),
            'es_99': max(0, es_99),
        }
    
    def _calculate_win_loss(self) -> Dict[str, float]:
        """Calculate win/loss metrics from trades."""
        if not self._trades:
            return {
                'win_rate': 0.0,
                'profit_factor': 0.0,
                'avg_win': 0.0,
                'avg_loss': 0.0,
            }
        
        wins = [t['pnl'] for t in self._trades if t['pnl'] > 0]
        losses = [t['pnl'] for t in self._trades if t['pnl'] < 0]
        
        win_rate = len(wins) / len(self._trades) * 100 if self._trades else 0
        
        total_wins = sum(wins) if wins else 0
        total_losses = abs(sum(losses)) if losses else 0
        
        profit_factor = total_wins / total_losses if total_losses > 0 else float('inf')
        
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        
        return {
            'win_rate': win_rate,
            'profit_factor': min(profit_factor, 999.99),  # Cap for display
            'avg_win': avg_win,
            'avg_loss': avg_loss,
        }
    
    def get_latest_metrics(self) -> Optional[RiskMetrics]:
        """Get most recently calculated metrics."""
        return self._latest_metrics


class DrawdownCircuitBreaker:
    """
    Circuit breaker that halts trading on excessive drawdown.
    
    Triggers when equity falls below threshold, dumps state to file.
    """
    
    def __init__(
        self,
        threshold_equity: float = 90.0,
        threshold_drawdown_pct: float = 10.0,
        on_trigger: Optional[Callable] = None,
        post_mortem_file: str = "post_mortem.json",
    ):
        """
        Initialize circuit breaker.
        
        Args:
            threshold_equity: Minimum equity before trigger
            threshold_drawdown_pct: Maximum drawdown percentage
            on_trigger: Callback when triggered
            post_mortem_file: File to dump state on trigger
        """
        self.threshold_equity = threshold_equity
        self.threshold_drawdown_pct = threshold_drawdown_pct
        self.on_trigger = on_trigger
        self.post_mortem_file = post_mortem_file
        
        self._state = CircuitBreakerState()
        self._initial_equity = 100.0
        self._peak_equity = 100.0
    
    def check(
        self,
        current_equity: float,
        metrics: RiskMetrics = None,
    ) -> CircuitBreakerState:
        """
        Check if circuit breaker should trigger.
        
        Args:
            current_equity: Current portfolio equity
            metrics: Optional risk metrics
        
        Returns:
            CircuitBreakerState
        """
        if self._state.is_triggered:
            return self._state
        
        # Update peak
        if current_equity > self._peak_equity:
            self._peak_equity = current_equity
        
        # Check equity threshold
        if current_equity < self.threshold_equity:
            self._trigger(
                reason=f"Equity below threshold: ${current_equity:.2f} < ${self.threshold_equity:.2f}",
                threshold="equity",
                equity=current_equity,
                metrics=metrics,
            )
            return self._state
        
        # Check drawdown threshold
        drawdown = (self._peak_equity - current_equity) / self._peak_equity * 100
        if drawdown >= self.threshold_drawdown_pct:
            self._trigger(
                reason=f"Drawdown exceeded: {drawdown:.2f}% >= {self.threshold_drawdown_pct:.2f}%",
                threshold="drawdown",
                equity=current_equity,
                metrics=metrics,
            )
            return self._state
        
        return self._state
    
    def _trigger(
        self,
        reason: str,
        threshold: str,
        equity: float,
        metrics: RiskMetrics = None,
    ) -> None:
        """Trigger the circuit breaker."""
        self._state.is_triggered = True
        self._state.trigger_reason = reason
        self._state.trigger_time = time.time() * 1000
        self._state.equity_at_trigger = equity
        self._state.threshold_violated = threshold
        
        logger.warning(f"CIRCUIT BREAKER TRIGGERED: {reason}")
        
        # Dump post-mortem
        self._dump_post_mortem(metrics)
        
        # Call callback
        if self.on_trigger:
            try:
                if asyncio.iscoroutinefunction(self.on_trigger):
                    asyncio.create_task(self.on_trigger(self._state))
                else:
                    self.on_trigger(self._state)
            except Exception as e:
                logger.error(f"Circuit breaker callback error: {e}")
    
    def _dump_post_mortem(self, metrics: RiskMetrics = None) -> None:
        """Dump state to post-mortem file."""
        data = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'circuit_breaker': {
                'is_triggered': self._state.is_triggered,
                'trigger_reason': self._state.trigger_reason,
                'trigger_time': self._state.trigger_time,
                'equity_at_trigger': self._state.equity_at_trigger,
                'threshold_violated': self._state.threshold_violated,
            },
            'thresholds': {
                'equity': self.threshold_equity,
                'drawdown_pct': self.threshold_drawdown_pct,
            },
            'peak_equity': self._peak_equity,
            'metrics': metrics.to_dict() if metrics else None,
        }
        
        try:
            with open(self.post_mortem_file, 'w') as f:
                json.dump(data, f, indent=2)
            logger.info(f"Post-mortem dumped to {self.post_mortem_file}")
        except Exception as e:
            logger.error(f"Failed to dump post-mortem: {e}")
    
    def reset(self) -> None:
        """Reset circuit breaker state."""
        self._state = CircuitBreakerState()
        logger.info("Circuit breaker reset")
    
    @property
    def is_triggered(self) -> bool:
        return self._state.is_triggered
    
    @property
    def state(self) -> CircuitBreakerState:
        return self._state


# Factory functions
def create_risk_calculator(
    initial_capital: float = 100.0,
    window_days: int = 30,
) -> RiskMetricsCalculator:
    """Create and return a RiskMetricsCalculator instance."""
    return RiskMetricsCalculator(
        initial_capital=initial_capital,
        window_days=window_days,
    )


def create_circuit_breaker(
    threshold_equity: float = 90.0,
    threshold_drawdown_pct: float = 10.0,
    on_trigger: Optional[Callable] = None,
) -> DrawdownCircuitBreaker:
    """Create and return a DrawdownCircuitBreaker instance."""
    return DrawdownCircuitBreaker(
        threshold_equity=threshold_equity,
        threshold_drawdown_pct=threshold_drawdown_pct,
        on_trigger=on_trigger,
    )
