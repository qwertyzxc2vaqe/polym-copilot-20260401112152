"""
Portfolio Compounding Engine Module
====================================
Implements progressive capital allocation with three operational states:
1. DRY_RUN - Simulated trades with no real execution
2. LIVE_TEST - $1 risk limit for network latency testing
3. AUTONOMOUS - 5% of rolling balance per trade

State transitions occur automatically based on trading performance.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, TYPE_CHECKING

from enum import Enum

if TYPE_CHECKING:
    from executor import ZeroFeeExecutor
    from arbitrage import ArbitrageOpportunity
    from security import DailyLossLimiter

logger = logging.getLogger(__name__)


class TradingMode(Enum):
    """Trading operation mode."""
    DRY_RUN = "dry_run"
    LIVE_TEST = "live_test"
    AUTONOMOUS = "autonomous"


@dataclass
class Trade:
    """Record of a single trade execution."""
    timestamp: datetime
    mode: TradingMode
    token_id: str
    side: str
    price: float
    size: float
    cost: float
    market_question: str = ""
    outcome: Optional[str] = None  # "win", "loss", None (pending)
    payout: float = 0.0
    profit: float = 0.0

    def to_dict(self) -> dict:
        """Serialize trade to dictionary."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "mode": self.mode.value,
            "token_id": self.token_id,
            "side": self.side,
            "price": self.price,
            "size": self.size,
            "cost": self.cost,
            "market_question": self.market_question,
            "outcome": self.outcome,
            "payout": self.payout,
            "profit": self.profit,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Trade":
        """Deserialize trade from dictionary."""
        return cls(
            timestamp=datetime.fromisoformat(data["timestamp"]),
            mode=TradingMode(data["mode"]),
            token_id=data["token_id"],
            side=data["side"],
            price=data["price"],
            size=data["size"],
            cost=data["cost"],
            market_question=data.get("market_question", ""),
            outcome=data.get("outcome"),
            payout=data.get("payout", 0.0),
            profit=data.get("profit", 0.0),
        )


@dataclass
class PortfolioState:
    """Current state of the trading portfolio."""
    initial_balance: float
    current_balance: float
    mode: TradingMode
    consecutive_wins: int
    total_trades: int
    winning_trades: int
    losing_trades: int
    total_profit: float
    trades: List[Trade] = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        """Calculate win rate as percentage."""
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades

    @property
    def roi(self) -> float:
        """Calculate return on investment."""
        if self.initial_balance == 0:
            return 0.0
        return (self.current_balance - self.initial_balance) / self.initial_balance

    @property
    def pending_trades(self) -> List[Trade]:
        """Get trades awaiting outcome resolution."""
        return [t for t in self.trades if t.outcome is None]

    def to_dict(self) -> dict:
        """Serialize state to dictionary."""
        return {
            "initial_balance": self.initial_balance,
            "current_balance": self.current_balance,
            "mode": self.mode.value,
            "consecutive_wins": self.consecutive_wins,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "total_profit": self.total_profit,
            "trades": [t.to_dict() for t in self.trades],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PortfolioState":
        """Deserialize state from dictionary."""
        return cls(
            initial_balance=data["initial_balance"],
            current_balance=data["current_balance"],
            mode=TradingMode(data["mode"]),
            consecutive_wins=data["consecutive_wins"],
            total_trades=data["total_trades"],
            winning_trades=data["winning_trades"],
            losing_trades=data["losing_trades"],
            total_profit=data["total_profit"],
            trades=[Trade.from_dict(t) for t in data.get("trades", [])],
        )


class PortfolioEngine:
    """
    Portfolio compounding engine with progressive risk management.

    Manages capital allocation across three modes:
    - DRY_RUN: Simulated 5% allocation, no real trades
    - LIVE_TEST: Fixed $1 per trade for network testing
    - AUTONOMOUS: 5% of current balance with auto-compounding

    Transitions:
    - DRY_RUN → LIVE_TEST: Manual switch
    - LIVE_TEST → AUTONOMOUS: 3 consecutive wins
    - AUTONOMOUS → LIVE_TEST: Daily loss limit hit (downgrade)
    """

    REQUIRED_CONSECUTIVE_WINS = 3
    ALLOCATION_PERCENT = 0.05  # 5% per trade
    LIVE_TEST_SIZE = 1.00      # $1 for live test

    def __init__(
        self,
        executor: "ZeroFeeExecutor",
        initial_balance: float = 100.0,
        mode: TradingMode = TradingMode.DRY_RUN,
        loss_limiter: Optional["DailyLossLimiter"] = None,
    ):
        """
        Initialize portfolio engine.

        Args:
            executor: ZeroFeeExecutor instance for order execution
            initial_balance: Starting portfolio balance in USDC
            mode: Initial trading mode
            loss_limiter: Optional DailyLossLimiter for risk management
        """
        self._executor = executor
        self._loss_limiter = loss_limiter
        self._state = PortfolioState(
            initial_balance=initial_balance,
            current_balance=initial_balance,
            mode=mode,
            consecutive_wins=0,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            total_profit=0.0,
        )

    async def refresh_balance(self) -> float:
        """
        Fetch current USDC balance from chain.

        Returns:
            Current balance in USDC
        """
        try:
            balance = await self._executor.get_balance()
            self._state.current_balance = balance
            logger.info(f"Refreshed balance: ${balance:.2f}")
            return balance
        except Exception as e:
            logger.error(f"Failed to refresh balance: {e}")
            return self._state.current_balance

    def calculate_position_size(self) -> float:
        """
        Calculate position size based on current mode.

        Returns:
            - DRY_RUN: Simulated 5% of balance
            - LIVE_TEST: Fixed $1.00
            - AUTONOMOUS: 5% of current balance
        """
        mode = self._state.mode

        if mode == TradingMode.LIVE_TEST:
            return self.LIVE_TEST_SIZE

        # Both DRY_RUN and AUTONOMOUS use 5% allocation
        size = self._state.current_balance * self.ALLOCATION_PERCENT
        return round(size, 2)

    async def execute_opportunity(
        self, opportunity: "ArbitrageOpportunity"
    ) -> Trade:
        """
        Execute trade based on opportunity and current mode.

        Args:
            opportunity: ArbitrageOpportunity with market and signal info

        Returns:
            Trade record with execution details
        """
        from arbitrage import ArbitrageSignal

        timestamp = datetime.now(timezone.utc)
        mode = self._state.mode

        # Refresh balance in autonomous mode
        if mode == TradingMode.AUTONOMOUS:
            await self.refresh_balance()

        # Calculate position size
        size = self.calculate_position_size()
        side = "YES" if opportunity.signal == ArbitrageSignal.BUY_YES else "NO"
        price = opportunity.entry_price
        cost = size  # Cost equals position size in binary options
        expected_profit = (1.0 - price) * size
        profit_pct = (expected_profit / cost) * 100 if cost > 0 else 0

        # Create trade record
        trade = Trade(
            timestamp=timestamp,
            mode=mode,
            token_id=opportunity.token_id,
            side=side,
            price=price,
            size=size,
            cost=cost,
            market_question=opportunity.market.question,
        )

        # DRY RUN: Print simulation and track
        if mode == TradingMode.DRY_RUN:
            self._print_dry_run(trade, opportunity, expected_profit, profit_pct)
            self._state.trades.append(trade)
            return trade

        # Check loss limiter before live execution
        if self._loss_limiter and not self._loss_limiter.is_trading_allowed():
            logger.warning("Daily loss limit reached - trade blocked")
            if mode == TradingMode.AUTONOMOUS:
                self._downgrade_to_live_test()
            raise RuntimeError("Daily loss limit exceeded")

        # LIVE execution (LIVE_TEST or AUTONOMOUS)
        try:
            result = await self._executor.execute_fok_order(
                token_id=opportunity.token_id,
                side=side.upper(),
                price=price,
                size=size,
            )

            # Log execution result
            logger.info(
                f"[{timestamp.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}] "
                f"{mode.value.upper()}: Executed {side} at ${price:.2f}"
            )
            logger.info(f"  Market: {opportunity.market.question}")
            logger.info(f"  Size: ${size:.2f} | Order ID: {result.order_id}")

            self._state.trades.append(trade)
            return trade

        except Exception as e:
            logger.error(f"Execution failed: {e}")
            trade.outcome = "error"
            self._state.trades.append(trade)
            raise

    def _print_dry_run(
        self,
        trade: Trade,
        opportunity: "ArbitrageOpportunity",
        expected_profit: float,
        profit_pct: float,
    ) -> None:
        """Print formatted dry run output."""
        ts = trade.timestamp.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        balance = self._state.current_balance
        alloc_pct = (trade.size / balance * 100) if balance > 0 else 0

        print(f"[{ts}] DRY RUN: WOULD BUY {trade.side} at ${trade.price:.2f}")
        print(f"  Market: {opportunity.market.question}")
        print(f"  Size: ${trade.size:.2f} ({alloc_pct:.0f}% of ${balance:.2f})")
        print(f"  Expected Profit: ${expected_profit:.2f} ({profit_pct:.2f}%)")
        print()

    def record_outcome(self, trade: Trade, won: bool, payout: float = 0.0) -> None:
        """
        Record trade outcome and update state.
        Potentially upgrade mode after consecutive wins.

        Args:
            trade: The trade to record outcome for
            won: Whether the trade was profitable
            payout: Payout received (1.0 per unit for winning trades)
        """
        trade.outcome = "win" if won else "loss"
        trade.payout = payout

        if won:
            # Winning trade: payout minus cost
            trade.profit = payout - trade.cost
            self._state.winning_trades += 1
            self._state.consecutive_wins += 1
            self._state.current_balance += trade.profit
            self._state.total_profit += trade.profit

            if self._loss_limiter:
                self._loss_limiter.record_profit(trade.profit)

            logger.info(
                f"Trade WON: +${trade.profit:.2f} "
                f"(streak: {self._state.consecutive_wins})"
            )
        else:
            # Losing trade: cost is lost
            trade.profit = -trade.cost
            self._state.losing_trades += 1
            self._state.consecutive_wins = 0
            self._state.current_balance -= trade.cost
            self._state.total_profit -= trade.cost

            if self._loss_limiter:
                self._loss_limiter.record_loss(trade.cost)

            logger.info(f"Trade LOST: -${trade.cost:.2f} (streak reset)")

        self._state.total_trades += 1

        # Check for mode upgrade
        self._check_mode_upgrade()

        # Check for downgrade on loss limit
        self._check_mode_downgrade()

    def _check_mode_upgrade(self) -> None:
        """
        Check if should upgrade from LIVE_TEST to AUTONOMOUS
        after REQUIRED_CONSECUTIVE_WINS consecutive wins.
        """
        if (
            self._state.mode == TradingMode.LIVE_TEST
            and self._state.consecutive_wins >= self.REQUIRED_CONSECUTIVE_WINS
        ):
            self._state.mode = TradingMode.AUTONOMOUS
            logger.info(
                f"MODE UPGRADE: LIVE_TEST → AUTONOMOUS "
                f"(after {self.REQUIRED_CONSECUTIVE_WINS} consecutive wins)"
            )
            print(f"\n{'='*50}")
            print(">> AUTONOMOUS MODE ACTIVATED")
            print(f"   Consecutive wins: {self._state.consecutive_wins}")
            print(f"   New position size: ${self.calculate_position_size():.2f}")
            print(f"{'='*50}\n")

    def _check_mode_downgrade(self) -> None:
        """Check if daily loss limit hit and downgrade to LIVE_TEST."""
        if (
            self._state.mode == TradingMode.AUTONOMOUS
            and self._loss_limiter
            and not self._loss_limiter.is_trading_allowed()
        ):
            self._downgrade_to_live_test()

    def _downgrade_to_live_test(self) -> None:
        """Downgrade from AUTONOMOUS to LIVE_TEST."""
        self._state.mode = TradingMode.LIVE_TEST
        self._state.consecutive_wins = 0
        logger.warning("MODE DOWNGRADE: AUTONOMOUS -> LIVE_TEST (loss limit hit)")
        print(f"\n{'='*50}")
        print("[WARN] DOWNGRADED TO LIVE_TEST MODE")
        print("   Reason: Daily loss limit reached")
        print(f"   Position size reset to: ${self.LIVE_TEST_SIZE:.2f}")
        print(f"{'='*50}\n")

    def set_mode(self, mode: TradingMode) -> None:
        """
        Manually set trading mode.

        Args:
            mode: New trading mode
        """
        old_mode = self._state.mode
        self._state.mode = mode

        # Reset consecutive wins on manual mode change
        if mode != old_mode:
            self._state.consecutive_wins = 0
            logger.info(f"Mode changed: {old_mode.value} → {mode.value}")

    def get_state(self) -> PortfolioState:
        """Return current portfolio state."""
        return self._state

    def get_summary(self) -> str:
        """Return human-readable portfolio summary."""
        s = self._state
        lines = [
            "=" * 50,
            "PORTFOLIO SUMMARY",
            "=" * 50,
            f"Mode:              {s.mode.value.upper()}",
            f"Initial Balance:   ${s.initial_balance:.2f}",
            f"Current Balance:   ${s.current_balance:.2f}",
            f"Total P&L:         ${s.total_profit:+.2f} ({s.roi*100:+.1f}%)",
            "-" * 50,
            f"Total Trades:      {s.total_trades}",
            f"Wins / Losses:     {s.winning_trades} / {s.losing_trades}",
            f"Win Rate:          {s.win_rate*100:.1f}%",
            f"Consecutive Wins:  {s.consecutive_wins}",
            "-" * 50,
            f"Position Size:     ${self.calculate_position_size():.2f}",
            f"Pending Trades:    {len(s.pending_trades)}",
        ]

        # Add mode-specific info
        if s.mode == TradingMode.LIVE_TEST:
            wins_needed = self.REQUIRED_CONSECUTIVE_WINS - s.consecutive_wins
            lines.append(f"Wins to Autonomous: {wins_needed}")

        if self._loss_limiter:
            status = self._loss_limiter.get_status()
            lines.append("-" * 50)
            lines.append(f"Daily Loss Limit:  ${status.limit:.2f}")
            lines.append(f"Today's Losses:    ${status.total_loss:.2f}")
            lines.append(f"Remaining Budget:  ${status.remaining:.2f}")

        lines.append("=" * 50)
        return "\n".join(lines)

    async def save_state(self, filepath: str = "data/portfolio_state.json") -> None:
        """
        Persist portfolio state to file.

        Args:
            filepath: Path to save state JSON
        """
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)

        state_dict = self._state.to_dict()
        state_dict["saved_at"] = datetime.now(timezone.utc).isoformat()

        with open(path, "w") as f:
            json.dump(state_dict, f, indent=2)

        logger.info(f"Portfolio state saved to {filepath}")

    async def load_state(self, filepath: str = "data/portfolio_state.json") -> bool:
        """
        Load portfolio state from file.

        Args:
            filepath: Path to state JSON file

        Returns:
            True if state loaded successfully, False otherwise
        """
        path = Path(filepath)

        if not path.exists():
            logger.warning(f"State file not found: {filepath}")
            return False

        try:
            with open(path, "r") as f:
                data = json.load(f)

            self._state = PortfolioState.from_dict(data)
            logger.info(
                f"Portfolio state loaded from {filepath} "
                f"(mode: {self._state.mode.value}, balance: ${self._state.current_balance:.2f})"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to load state: {e}")
            return False


async def create_portfolio_engine(
    executor: "ZeroFeeExecutor",
    initial_balance: float = 100.0,
    mode: TradingMode = TradingMode.DRY_RUN,
    daily_loss_limit: float = 10.0,
    load_existing: bool = True,
    state_file: str = "data/portfolio_state.json",
) -> PortfolioEngine:
    """
    Factory function to create a configured PortfolioEngine.

    Args:
        executor: ZeroFeeExecutor instance
        initial_balance: Starting balance if no saved state
        mode: Initial mode if no saved state
        daily_loss_limit: Maximum daily loss in USDC
        load_existing: Whether to load existing state file
        state_file: Path to state persistence file

    Returns:
        Configured PortfolioEngine instance
    """
    from security import DailyLossLimiter

    loss_limiter = DailyLossLimiter(daily_loss_limit)

    engine = PortfolioEngine(
        executor=executor,
        initial_balance=initial_balance,
        mode=mode,
        loss_limiter=loss_limiter,
    )

    if load_existing:
        await engine.load_state(state_file)

    return engine
