"""
Reinforcement Learning Gym Environment - Market Making Simulation.

Phase 2 - Tasks 66-67: RL environment with order book as State, spread
pricing as Action, and simulated PnL as Reward. Includes shadow execution.

Educational purpose only - paper trading simulation.
"""

import asyncio
import logging
import time
import random
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple, Any
from collections import deque
from enum import Enum
import math

logger = logging.getLogger(__name__)

# Try to import numpy
try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False
    np = None


class ActionType(Enum):
    """Available actions for the RL agent."""
    HOLD = 0           # No change
    TIGHTEN_SPREAD = 1  # Reduce spread (more aggressive)
    WIDEN_SPREAD = 2    # Increase spread (more conservative)
    INCREASE_SIZE = 3   # Increase order size
    DECREASE_SIZE = 4   # Decrease order size
    PULL_BIDS = 5       # Remove bid orders
    PULL_ASKS = 6       # Remove ask orders


@dataclass
class MarketState:
    """
    Observable market state for the RL agent.
    
    This is the "observation" in RL terminology.
    """
    # Price data
    mid_price: float = 0.0
    bid_price: float = 0.0
    ask_price: float = 0.0
    spread_bps: float = 0.0
    
    # Order flow
    ofi_1: float = 0.0    # Order flow imbalance (top 1 level)
    ofi_5: float = 0.0    # Order flow imbalance (top 5 levels)
    ofi_10: float = 0.0   # Order flow imbalance (top 10 levels)
    
    # Volume
    bid_volume: float = 0.0
    ask_volume: float = 0.0
    recent_volume: float = 0.0
    
    # Momentum
    price_return_1s: float = 0.0
    price_return_5s: float = 0.0
    price_return_1m: float = 0.0
    
    # Volatility
    volatility: float = 0.0
    
    # Position
    current_position: float = 0.0
    unrealized_pnl: float = 0.0
    
    # Our orders
    our_bid_price: float = 0.0
    our_ask_price: float = 0.0
    our_spread_bps: float = 0.0
    
    def to_array(self) -> np.ndarray:
        """Convert to numpy array for model input."""
        if not NUMPY_AVAILABLE:
            return []
        
        return np.array([
            self.spread_bps / 100,  # Normalize
            self.ofi_1,
            self.ofi_5,
            self.ofi_10,
            self.bid_volume / 1000,
            self.ask_volume / 1000,
            self.price_return_1s * 100,
            self.price_return_5s * 100,
            self.price_return_1m * 100,
            self.volatility / 100,
            self.current_position / 1000,
            self.unrealized_pnl / 100,
            self.our_spread_bps / 100,
        ], dtype=np.float32)
    
    @property
    def observation_size(self) -> int:
        return 13


@dataclass
class AgentAction:
    """Action taken by the RL agent."""
    action_type: ActionType
    spread_adjustment: float = 0.0  # Basis points
    size_adjustment: float = 0.0    # Percentage
    timestamp: float = 0.0


@dataclass
class StepResult:
    """Result of taking a step in the environment."""
    next_state: MarketState
    reward: float
    done: bool
    info: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Episode:
    """Records a complete episode."""
    episode_id: int
    states: List[MarketState] = field(default_factory=list)
    actions: List[AgentAction] = field(default_factory=list)
    rewards: List[float] = field(default_factory=list)
    total_reward: float = 0.0
    total_pnl: float = 0.0
    steps: int = 0


class MarketMakingEnv:
    """
    Reinforcement Learning environment for market making.
    
    State: Market conditions (OFI, volatility, position, etc.)
    Action: Spread/size adjustments
    Reward: PnL from market making activity
    """
    
    # Environment parameters
    INITIAL_CAPITAL = 100.0
    MAX_POSITION = 1000.0
    MAX_SPREAD_BPS = 100.0
    MIN_SPREAD_BPS = 5.0
    
    # Reward shaping
    PNL_WEIGHT = 1.0
    INVENTORY_PENALTY = 0.001  # Penalty per unit of inventory
    SPREAD_REWARD = 0.0001    # Small reward for maintaining spread
    
    def __init__(
        self,
        symbol: str = "BTC",
        tick_size: float = 0.01,
        initial_spread_bps: float = 20.0,
        initial_size: float = 10.0,
    ):
        """
        Initialize market making environment.
        
        Args:
            symbol: Trading symbol
            tick_size: Minimum price increment
            initial_spread_bps: Starting spread in basis points
            initial_size: Starting order size
        """
        self.symbol = symbol
        self.tick_size = tick_size
        self.initial_spread_bps = initial_spread_bps
        self.initial_size = initial_size
        
        # State
        self._state: Optional[MarketState] = None
        self._position = 0.0
        self._cash = self.INITIAL_CAPITAL
        self._realized_pnl = 0.0
        self._unrealized_pnl = 0.0
        
        # Our orders
        self._current_spread_bps = initial_spread_bps
        self._current_size = initial_size
        self._bid_price = 0.0
        self._ask_price = 0.0
        
        # Episode tracking
        self._step_count = 0
        self._episode_count = 0
        self._current_episode: Optional[Episode] = None
        
        # History
        self._price_history: deque = deque(maxlen=1000)
        self._pnl_history: deque = deque(maxlen=1000)
    
    def reset(self, initial_price: float = 100.0) -> MarketState:
        """
        Reset environment for new episode.
        
        Args:
            initial_price: Starting mid price
        
        Returns:
            Initial state
        """
        self._position = 0.0
        self._cash = self.INITIAL_CAPITAL
        self._realized_pnl = 0.0
        self._unrealized_pnl = 0.0
        self._current_spread_bps = self.initial_spread_bps
        self._current_size = self.initial_size
        self._step_count = 0
        
        # Calculate initial bid/ask
        half_spread = initial_price * self._current_spread_bps / 10000 / 2
        self._bid_price = initial_price - half_spread
        self._ask_price = initial_price + half_spread
        
        # Create initial state
        self._state = MarketState(
            mid_price=initial_price,
            bid_price=initial_price - half_spread,
            ask_price=initial_price + half_spread,
            spread_bps=self._current_spread_bps,
            our_bid_price=self._bid_price,
            our_ask_price=self._ask_price,
            our_spread_bps=self._current_spread_bps,
        )
        
        # Start new episode
        self._episode_count += 1
        self._current_episode = Episode(episode_id=self._episode_count)
        self._current_episode.states.append(self._state)
        
        self._price_history.clear()
        self._price_history.append(initial_price)
        
        return self._state
    
    def step(
        self,
        action: AgentAction,
        market_update: Dict[str, float],
    ) -> StepResult:
        """
        Take a step in the environment.
        
        Args:
            action: Agent's action
            market_update: New market data (mid_price, ofi, volume, etc.)
        
        Returns:
            StepResult with new state, reward, done flag
        """
        self._step_count += 1
        prev_state = self._state
        prev_value = self._get_portfolio_value(prev_state.mid_price)
        
        # Apply action
        self._apply_action(action)
        
        # Update market state
        new_mid = market_update.get('mid_price', self._state.mid_price)
        self._price_history.append(new_mid)
        
        # Simulate fills based on market movement
        fills = self._simulate_fills(prev_state.mid_price, new_mid)
        
        # Update position and PnL
        for fill in fills:
            self._process_fill(fill)
        
        # Calculate new state
        self._state = self._create_state(market_update)
        
        # Calculate reward
        new_value = self._get_portfolio_value(new_mid)
        pnl_reward = (new_value - prev_value) * self.PNL_WEIGHT
        inventory_penalty = abs(self._position) * self.INVENTORY_PENALTY
        spread_reward = self._current_spread_bps * self.SPREAD_REWARD
        
        reward = pnl_reward - inventory_penalty + spread_reward
        
        # Check if done (e.g., max loss, max steps)
        done = self._check_done()
        
        # Record episode data
        if self._current_episode:
            self._current_episode.states.append(self._state)
            self._current_episode.actions.append(action)
            self._current_episode.rewards.append(reward)
            self._current_episode.total_reward += reward
            self._current_episode.steps += 1
        
        info = {
            'position': self._position,
            'cash': self._cash,
            'realized_pnl': self._realized_pnl,
            'unrealized_pnl': self._unrealized_pnl,
            'portfolio_value': new_value,
            'fills': fills,
        }
        
        return StepResult(
            next_state=self._state,
            reward=reward,
            done=done,
            info=info,
        )
    
    def _apply_action(self, action: AgentAction) -> None:
        """Apply agent's action to update orders."""
        if action.action_type == ActionType.TIGHTEN_SPREAD:
            self._current_spread_bps = max(
                self.MIN_SPREAD_BPS,
                self._current_spread_bps - 2
            )
        elif action.action_type == ActionType.WIDEN_SPREAD:
            self._current_spread_bps = min(
                self.MAX_SPREAD_BPS,
                self._current_spread_bps + 2
            )
        elif action.action_type == ActionType.INCREASE_SIZE:
            self._current_size *= 1.1
        elif action.action_type == ActionType.DECREASE_SIZE:
            self._current_size *= 0.9
        elif action.action_type == ActionType.PULL_BIDS:
            self._bid_price = 0.0
        elif action.action_type == ActionType.PULL_ASKS:
            self._ask_price = 0.0
        
        # Update bid/ask prices
        if self._state:
            half_spread = self._state.mid_price * self._current_spread_bps / 10000 / 2
            if action.action_type != ActionType.PULL_BIDS:
                self._bid_price = self._state.mid_price - half_spread
            if action.action_type != ActionType.PULL_ASKS:
                self._ask_price = self._state.mid_price + half_spread
    
    def _simulate_fills(
        self,
        prev_price: float,
        new_price: float,
    ) -> List[Dict]:
        """
        Simulate order fills based on price movement.
        
        Simple model: if price crosses our levels, we get filled.
        """
        fills = []
        
        # Check if bid was hit (price dropped to or below our bid)
        if self._bid_price > 0 and new_price <= self._bid_price:
            # Probability of fill based on how far price moved
            fill_prob = min(1.0, abs(new_price - prev_price) / (self._bid_price * 0.001))
            if random.random() < fill_prob:
                fills.append({
                    'side': 'buy',
                    'price': self._bid_price,
                    'quantity': self._current_size,
                })
        
        # Check if ask was hit (price rose to or above our ask)
        if self._ask_price > 0 and new_price >= self._ask_price:
            fill_prob = min(1.0, abs(new_price - prev_price) / (self._ask_price * 0.001))
            if random.random() < fill_prob:
                fills.append({
                    'side': 'sell',
                    'price': self._ask_price,
                    'quantity': self._current_size,
                })
        
        return fills
    
    def _process_fill(self, fill: Dict) -> None:
        """Process a fill and update position/cash."""
        side = fill['side']
        price = fill['price']
        quantity = fill['quantity']
        
        if side == 'buy':
            self._position += quantity
            self._cash -= price * quantity
        else:  # sell
            self._position -= quantity
            self._cash += price * quantity
    
    def _create_state(self, market_update: Dict[str, float]) -> MarketState:
        """Create new market state from market update."""
        mid_price = market_update.get('mid_price', self._state.mid_price if self._state else 100.0)
        
        # Calculate returns from history
        prices = list(self._price_history)
        return_1s = (mid_price - prices[-2]) / prices[-2] if len(prices) >= 2 else 0
        return_5s = (mid_price - prices[-5]) / prices[-5] if len(prices) >= 5 else 0
        return_1m = (mid_price - prices[-60]) / prices[-60] if len(prices) >= 60 else 0
        
        # Calculate unrealized PnL
        if self._position != 0:
            avg_entry = (self.INITIAL_CAPITAL - self._cash) / abs(self._position) if self._position != 0 else 0
            if self._position > 0:
                self._unrealized_pnl = (mid_price - avg_entry) * self._position
            else:
                self._unrealized_pnl = (avg_entry - mid_price) * abs(self._position)
        else:
            self._unrealized_pnl = 0
        
        return MarketState(
            mid_price=mid_price,
            bid_price=market_update.get('bid_price', mid_price * 0.999),
            ask_price=market_update.get('ask_price', mid_price * 1.001),
            spread_bps=market_update.get('spread_bps', 10.0),
            ofi_1=market_update.get('ofi_1', 0.0),
            ofi_5=market_update.get('ofi_5', 0.0),
            ofi_10=market_update.get('ofi_10', 0.0),
            bid_volume=market_update.get('bid_volume', 0.0),
            ask_volume=market_update.get('ask_volume', 0.0),
            recent_volume=market_update.get('recent_volume', 0.0),
            price_return_1s=return_1s,
            price_return_5s=return_5s,
            price_return_1m=return_1m,
            volatility=market_update.get('volatility', 0.0),
            current_position=self._position,
            unrealized_pnl=self._unrealized_pnl,
            our_bid_price=self._bid_price,
            our_ask_price=self._ask_price,
            our_spread_bps=self._current_spread_bps,
        )
    
    def _get_portfolio_value(self, mid_price: float) -> float:
        """Calculate total portfolio value."""
        position_value = self._position * mid_price
        return self._cash + position_value
    
    def _check_done(self) -> bool:
        """Check if episode should end."""
        # Max steps
        if self._step_count >= 10000:
            return True
        
        # Max loss (50% drawdown)
        if self._get_portfolio_value(self._state.mid_price) < self.INITIAL_CAPITAL * 0.5:
            return True
        
        # Max position
        if abs(self._position) > self.MAX_POSITION:
            return True
        
        return False
    
    def get_current_state(self) -> Optional[MarketState]:
        """Get current market state."""
        return self._state
    
    def get_episode_summary(self) -> Optional[Dict]:
        """Get summary of current episode."""
        if not self._current_episode:
            return None
        
        return {
            'episode_id': self._current_episode.episode_id,
            'steps': self._current_episode.steps,
            'total_reward': self._current_episode.total_reward,
            'total_pnl': self._get_portfolio_value(self._state.mid_price) - self.INITIAL_CAPITAL,
            'final_position': self._position,
            'avg_reward': self._current_episode.total_reward / max(1, self._current_episode.steps),
        }


class ShadowExecutor:
    """
    Shadow execution mode for RL agent.
    
    Runs RL agent alongside primary heuristic algorithm,
    tracking which generates higher theoretical alpha.
    """
    
    def __init__(
        self,
        env: MarketMakingEnv,
        agent_name: str = "RL_Agent",
    ):
        """
        Initialize shadow executor.
        
        Args:
            env: Market making environment
            agent_name: Name for this agent
        """
        self.env = env
        self.agent_name = agent_name
        
        # Tracking
        self._rl_pnl: float = 0.0
        self._heuristic_pnl: float = 0.0
        self._rl_trades: List[Dict] = []
        self._heuristic_trades: List[Dict] = []
        self._comparison_history: deque = deque(maxlen=10000)
    
    def record_rl_action(
        self,
        action: AgentAction,
        result: StepResult,
    ) -> None:
        """Record RL agent's action and result."""
        self._rl_pnl += result.reward
        
        for fill in result.info.get('fills', []):
            self._rl_trades.append({
                'timestamp': time.time() * 1000,
                'fill': fill,
                'cumulative_pnl': self._rl_pnl,
            })
    
    def record_heuristic_action(
        self,
        pnl_delta: float,
        fill: Optional[Dict] = None,
    ) -> None:
        """Record heuristic algorithm's action and result."""
        self._heuristic_pnl += pnl_delta
        
        if fill:
            self._heuristic_trades.append({
                'timestamp': time.time() * 1000,
                'fill': fill,
                'cumulative_pnl': self._heuristic_pnl,
            })
    
    def record_comparison(self, timestamp: float = None) -> Dict:
        """Record a comparison point between RL and heuristic."""
        timestamp = timestamp or time.time() * 1000
        
        comparison = {
            'timestamp': timestamp,
            'rl_pnl': self._rl_pnl,
            'heuristic_pnl': self._heuristic_pnl,
            'rl_trades': len(self._rl_trades),
            'heuristic_trades': len(self._heuristic_trades),
            'rl_winning': self._rl_pnl > self._heuristic_pnl,
        }
        
        self._comparison_history.append(comparison)
        return comparison
    
    def get_performance_summary(self) -> Dict:
        """Get performance comparison summary."""
        if not self._comparison_history:
            return {
                'rl_total_pnl': self._rl_pnl,
                'heuristic_total_pnl': self._heuristic_pnl,
                'rl_outperformance': self._rl_pnl - self._heuristic_pnl,
                'rl_win_rate': 0.0,
            }
        
        rl_wins = sum(1 for c in self._comparison_history if c['rl_winning'])
        total = len(self._comparison_history)
        
        return {
            'rl_total_pnl': self._rl_pnl,
            'heuristic_total_pnl': self._heuristic_pnl,
            'rl_outperformance': self._rl_pnl - self._heuristic_pnl,
            'rl_win_rate': rl_wins / total * 100 if total > 0 else 0.0,
            'rl_trade_count': len(self._rl_trades),
            'heuristic_trade_count': len(self._heuristic_trades),
            'comparison_points': total,
        }
    
    def reset(self) -> None:
        """Reset shadow executor state."""
        self._rl_pnl = 0.0
        self._heuristic_pnl = 0.0
        self._rl_trades.clear()
        self._heuristic_trades.clear()
        self._comparison_history.clear()


# Factory functions
def create_market_making_env(
    symbol: str = "BTC",
    initial_spread_bps: float = 20.0,
) -> MarketMakingEnv:
    """Create and return a MarketMakingEnv instance."""
    return MarketMakingEnv(symbol=symbol, initial_spread_bps=initial_spread_bps)


def create_shadow_executor(
    env: MarketMakingEnv,
    agent_name: str = "RL_Agent",
) -> ShadowExecutor:
    """Create and return a ShadowExecutor instance."""
    return ShadowExecutor(env=env, agent_name=agent_name)
