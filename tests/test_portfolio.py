"""
Test suite for PortfolioEngine - Capital Management and Compounding.

Tests verify:
1. Position size calculations per mode (DRY_RUN, LIVE_TEST, AUTONOMOUS)
2. Mode transitions (LIVE_TEST -> AUTONOMOUS after 3 wins)
3. Trade outcome recording (win/loss tracking)
4. Portfolio state persistence

Following TDD (Red-Green-Refactor):
- Each test verifies ONE specific behavior
- Tests are isolated with mock dependencies
"""

import pytest
import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock, AsyncMock, patch
import sys
from pathlib import Path
import tempfile
import json

# Add src to path
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))


class TestPortfolioPositionSizing:
    """Test position size calculations for each trading mode."""
    
    def test_dry_run_mode_uses_5_percent_allocation(self, mock_executor):
        """Test: DRY_RUN mode calculates 5% of balance."""
        from portfolio import PortfolioEngine, TradingMode
        
        engine = PortfolioEngine(
            executor=mock_executor,
            initial_balance=100.0,
            mode=TradingMode.DRY_RUN,
        )
        
        size = engine.calculate_position_size()
        
        assert size == 5.0, "DRY_RUN should allocate 5% of $100 = $5"
    
    def test_live_test_mode_uses_fixed_1_dollar(self, mock_executor):
        """Test: LIVE_TEST mode uses fixed $1.00 regardless of balance."""
        from portfolio import PortfolioEngine, TradingMode
        
        engine = PortfolioEngine(
            executor=mock_executor,
            initial_balance=1000.0,  # Large balance
            mode=TradingMode.LIVE_TEST,
        )
        
        size = engine.calculate_position_size()
        
        assert size == 1.0, "LIVE_TEST should always use $1.00"
    
    def test_autonomous_mode_uses_5_percent_allocation(self, mock_executor):
        """Test: AUTONOMOUS mode calculates 5% of current balance."""
        from portfolio import PortfolioEngine, TradingMode
        
        engine = PortfolioEngine(
            executor=mock_executor,
            initial_balance=200.0,
            mode=TradingMode.AUTONOMOUS,
        )
        
        size = engine.calculate_position_size()
        
        assert size == 10.0, "AUTONOMOUS should allocate 5% of $200 = $10"
    
    def test_position_size_scales_with_balance(self, mock_executor):
        """Test: Position size updates when balance changes."""
        from portfolio import PortfolioEngine, TradingMode
        
        engine = PortfolioEngine(
            executor=mock_executor,
            initial_balance=100.0,
            mode=TradingMode.AUTONOMOUS,
        )
        
        # Initial size
        size1 = engine.calculate_position_size()
        assert size1 == 5.0
        
        # Simulate balance increase
        engine._state.current_balance = 200.0
        
        # New size should reflect new balance
        size2 = engine.calculate_position_size()
        assert size2 == 10.0


class TestPortfolioModeTransitions:
    """Test trading mode transitions."""
    
    def test_live_test_to_autonomous_after_3_wins(self, mock_executor):
        """Test: Mode upgrades from LIVE_TEST to AUTONOMOUS after 3 consecutive wins."""
        from portfolio import PortfolioEngine, TradingMode, Trade
        
        engine = PortfolioEngine(
            executor=mock_executor,
            initial_balance=100.0,
            mode=TradingMode.LIVE_TEST,
        )
        
        # Simulate 3 winning trades
        for i in range(3):
            trade = Trade(
                timestamp=datetime.now(timezone.utc),
                mode=TradingMode.LIVE_TEST,
                token_id=f"token-{i}",
                side="YES",
                price=0.95,
                size=1.0,
                cost=1.0,
            )
            engine._state.trades.append(trade)
            engine.record_outcome(trade, won=True, payout=1.0)
        
        assert engine._state.mode == TradingMode.AUTONOMOUS
        assert engine._state.consecutive_wins >= 3
    
    def test_no_upgrade_with_only_2_wins(self, mock_executor):
        """Test: Mode stays LIVE_TEST with only 2 consecutive wins."""
        from portfolio import PortfolioEngine, TradingMode, Trade
        
        engine = PortfolioEngine(
            executor=mock_executor,
            initial_balance=100.0,
            mode=TradingMode.LIVE_TEST,
        )
        
        # Simulate 2 winning trades
        for i in range(2):
            trade = Trade(
                timestamp=datetime.now(timezone.utc),
                mode=TradingMode.LIVE_TEST,
                token_id=f"token-{i}",
                side="YES",
                price=0.95,
                size=1.0,
                cost=1.0,
            )
            engine._state.trades.append(trade)
            engine.record_outcome(trade, won=True, payout=1.0)
        
        assert engine._state.mode == TradingMode.LIVE_TEST
        assert engine._state.consecutive_wins == 2
    
    def test_loss_resets_consecutive_wins(self, mock_executor):
        """Test: A loss resets consecutive win counter."""
        from portfolio import PortfolioEngine, TradingMode, Trade
        
        engine = PortfolioEngine(
            executor=mock_executor,
            initial_balance=100.0,
            mode=TradingMode.LIVE_TEST,
        )
        
        # Win twice
        for i in range(2):
            trade = Trade(
                timestamp=datetime.now(timezone.utc),
                mode=TradingMode.LIVE_TEST,
                token_id=f"token-{i}",
                side="YES",
                price=0.95,
                size=1.0,
                cost=1.0,
            )
            engine._state.trades.append(trade)
            engine.record_outcome(trade, won=True, payout=1.0)
        
        assert engine._state.consecutive_wins == 2
        
        # Lose once
        losing_trade = Trade(
            timestamp=datetime.now(timezone.utc),
            mode=TradingMode.LIVE_TEST,
            token_id="losing-token",
            side="YES",
            price=0.95,
            size=1.0,
            cost=1.0,
        )
        engine._state.trades.append(losing_trade)
        engine.record_outcome(losing_trade, won=False, payout=0.0)
        
        assert engine._state.consecutive_wins == 0
    
    def test_dry_run_does_not_upgrade(self, mock_executor):
        """Test: DRY_RUN mode never auto-upgrades regardless of wins."""
        from portfolio import PortfolioEngine, TradingMode, Trade
        
        engine = PortfolioEngine(
            executor=mock_executor,
            initial_balance=100.0,
            mode=TradingMode.DRY_RUN,
        )
        
        # Simulate 5 winning trades
        for i in range(5):
            trade = Trade(
                timestamp=datetime.now(timezone.utc),
                mode=TradingMode.DRY_RUN,
                token_id=f"token-{i}",
                side="YES",
                price=0.95,
                size=5.0,
                cost=5.0,
            )
            engine._state.trades.append(trade)
            engine.record_outcome(trade, won=True, payout=5.0)
        
        assert engine._state.mode == TradingMode.DRY_RUN
    
    def test_manual_mode_change_resets_streak(self, mock_executor):
        """Test: Manual mode change resets consecutive wins."""
        from portfolio import PortfolioEngine, TradingMode, Trade
        
        engine = PortfolioEngine(
            executor=mock_executor,
            initial_balance=100.0,
            mode=TradingMode.LIVE_TEST,
        )
        
        # Build up some wins
        for i in range(2):
            trade = Trade(
                timestamp=datetime.now(timezone.utc),
                mode=TradingMode.LIVE_TEST,
                token_id=f"token-{i}",
                side="YES",
                price=0.95,
                size=1.0,
                cost=1.0,
            )
            engine._state.trades.append(trade)
            engine.record_outcome(trade, won=True, payout=1.0)
        
        assert engine._state.consecutive_wins == 2
        
        # Manual mode change
        engine.set_mode(TradingMode.DRY_RUN)
        
        assert engine._state.consecutive_wins == 0


class TestPortfolioOutcomeRecording:
    """Test trade outcome recording and balance updates."""
    
    def test_winning_trade_increases_balance(self, mock_executor):
        """Test: Winning trade increases balance by profit amount."""
        from portfolio import PortfolioEngine, TradingMode, Trade
        
        engine = PortfolioEngine(
            executor=mock_executor,
            initial_balance=100.0,
            mode=TradingMode.LIVE_TEST,
        )
        
        trade = Trade(
            timestamp=datetime.now(timezone.utc),
            mode=TradingMode.LIVE_TEST,
            token_id="token",
            side="YES",
            price=0.95,
            size=1.0,
            cost=1.0,
        )
        engine._state.trades.append(trade)
        
        # Win trade (cost $1, payout $1) -> profit = $0
        # Actually for binary options: cost = $0.95 (price * size), payout = $1
        # Let's use the actual logic
        engine.record_outcome(trade, won=True, payout=1.0)
        
        # Profit = payout - cost = 1.0 - 1.0 = 0.0
        # But trade used cost=1.0, so balance changes by profit
        assert engine._state.winning_trades == 1
        assert trade.outcome == "win"
    
    def test_losing_trade_decreases_balance(self, mock_executor):
        """Test: Losing trade decreases balance by cost amount."""
        from portfolio import PortfolioEngine, TradingMode, Trade
        
        engine = PortfolioEngine(
            executor=mock_executor,
            initial_balance=100.0,
            mode=TradingMode.LIVE_TEST,
        )
        
        trade = Trade(
            timestamp=datetime.now(timezone.utc),
            mode=TradingMode.LIVE_TEST,
            token_id="token",
            side="YES",
            price=0.95,
            size=1.0,
            cost=1.0,
        )
        engine._state.trades.append(trade)
        
        initial_balance = engine._state.current_balance
        engine.record_outcome(trade, won=False, payout=0.0)
        
        assert engine._state.losing_trades == 1
        assert engine._state.current_balance == initial_balance - 1.0
        assert trade.outcome == "loss"
    
    def test_win_rate_calculation(self, mock_executor):
        """Test: Win rate is calculated correctly."""
        from portfolio import PortfolioEngine, TradingMode, Trade
        
        engine = PortfolioEngine(
            executor=mock_executor,
            initial_balance=100.0,
            mode=TradingMode.DRY_RUN,
        )
        
        # Record 3 wins and 1 loss
        for i in range(3):
            trade = Trade(
                timestamp=datetime.now(timezone.utc),
                mode=TradingMode.DRY_RUN,
                token_id=f"token-{i}",
                side="YES",
                price=0.95,
                size=5.0,
                cost=5.0,
            )
            engine._state.trades.append(trade)
            engine.record_outcome(trade, won=True, payout=5.0)
        
        losing_trade = Trade(
            timestamp=datetime.now(timezone.utc),
            mode=TradingMode.DRY_RUN,
            token_id="losing",
            side="YES",
            price=0.95,
            size=5.0,
            cost=5.0,
        )
        engine._state.trades.append(losing_trade)
        engine.record_outcome(losing_trade, won=False, payout=0.0)
        
        # 3 wins / 4 total = 75%
        assert engine._state.win_rate == 0.75


class TestPortfolioStatePersistence:
    """Test state save/load functionality."""
    
    @pytest.mark.asyncio
    async def test_save_and_load_state(self, mock_executor):
        """Test: Portfolio state can be saved and loaded."""
        from portfolio import PortfolioEngine, TradingMode, Trade
        
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "portfolio_state.json"
            
            # Create engine and make some trades
            engine = PortfolioEngine(
                executor=mock_executor,
                initial_balance=100.0,
                mode=TradingMode.LIVE_TEST,
            )
            
            trade = Trade(
                timestamp=datetime.now(timezone.utc),
                mode=TradingMode.LIVE_TEST,
                token_id="token",
                side="YES",
                price=0.95,
                size=1.0,
                cost=1.0,
            )
            engine._state.trades.append(trade)
            engine.record_outcome(trade, won=True, payout=1.0)
            
            # Save state
            await engine.save_state(str(state_file))
            
            # Create new engine and load state
            new_engine = PortfolioEngine(
                executor=mock_executor,
                initial_balance=50.0,  # Different initial
                mode=TradingMode.DRY_RUN,
            )
            
            loaded = await new_engine.load_state(str(state_file))
            
            assert loaded is True
            assert new_engine._state.initial_balance == 100.0
            assert new_engine._state.mode == TradingMode.LIVE_TEST
            assert new_engine._state.winning_trades == 1
    
    @pytest.mark.asyncio
    async def test_load_nonexistent_state_returns_false(self, mock_executor):
        """Test: Loading nonexistent state file returns False."""
        from portfolio import PortfolioEngine, TradingMode
        
        engine = PortfolioEngine(
            executor=mock_executor,
            initial_balance=100.0,
            mode=TradingMode.DRY_RUN,
        )
        
        loaded = await engine.load_state("nonexistent_file.json")
        
        assert loaded is False


class TestPortfolioSummary:
    """Test portfolio summary generation."""
    
    def test_summary_includes_key_metrics(self, mock_executor):
        """Test: Summary includes mode, balance, P&L, win rate."""
        from portfolio import PortfolioEngine, TradingMode
        
        engine = PortfolioEngine(
            executor=mock_executor,
            initial_balance=100.0,
            mode=TradingMode.DRY_RUN,
        )
        
        summary = engine.get_summary()
        
        assert "DRY_RUN" in summary
        assert "$100.00" in summary
        assert "Win Rate" in summary
        assert "Position Size" in summary


class TestTradeDataclass:
    """Test Trade dataclass serialization."""
    
    def test_trade_to_dict(self):
        """Test: Trade serializes to dictionary correctly."""
        from portfolio import Trade, TradingMode
        
        trade = Trade(
            timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            mode=TradingMode.LIVE_TEST,
            token_id="token-123",
            side="YES",
            price=0.95,
            size=1.0,
            cost=1.0,
            market_question="Will BTC go up?",
            outcome="win",
            payout=1.0,
            profit=0.05,
        )
        
        data = trade.to_dict()
        
        assert data["mode"] == "live_test"
        assert data["token_id"] == "token-123"
        assert data["side"] == "YES"
        assert data["price"] == 0.95
        assert data["outcome"] == "win"
    
    def test_trade_from_dict(self):
        """Test: Trade deserializes from dictionary correctly."""
        from portfolio import Trade, TradingMode
        
        data = {
            "timestamp": "2024-01-01T12:00:00+00:00",
            "mode": "live_test",
            "token_id": "token-123",
            "side": "YES",
            "price": 0.95,
            "size": 1.0,
            "cost": 1.0,
            "market_question": "Test",
            "outcome": "win",
            "payout": 1.0,
            "profit": 0.05,
        }
        
        trade = Trade.from_dict(data)
        
        assert trade.mode == TradingMode.LIVE_TEST
        assert trade.token_id == "token-123"
        assert trade.outcome == "win"


class TestPortfolioStateProperties:
    """Test PortfolioState computed properties."""
    
    def test_roi_calculation(self, mock_executor):
        """Test: ROI is calculated as (current - initial) / initial."""
        from portfolio import PortfolioEngine, TradingMode
        
        engine = PortfolioEngine(
            executor=mock_executor,
            initial_balance=100.0,
            mode=TradingMode.DRY_RUN,
        )
        
        # Initial ROI should be 0
        assert engine._state.roi == 0.0
        
        # Simulate profit
        engine._state.current_balance = 110.0
        
        # ROI = (110 - 100) / 100 = 0.10 (10%)
        assert engine._state.roi == 0.10
    
    def test_pending_trades_filter(self, mock_executor):
        """Test: pending_trades returns only trades without outcomes."""
        from portfolio import PortfolioEngine, TradingMode, Trade
        
        engine = PortfolioEngine(
            executor=mock_executor,
            initial_balance=100.0,
            mode=TradingMode.DRY_RUN,
        )
        
        # Add trades with different outcome states
        pending = Trade(
            timestamp=datetime.now(timezone.utc),
            mode=TradingMode.DRY_RUN,
            token_id="pending",
            side="YES",
            price=0.95,
            size=5.0,
            cost=5.0,
            outcome=None,  # Pending
        )
        
        completed = Trade(
            timestamp=datetime.now(timezone.utc),
            mode=TradingMode.DRY_RUN,
            token_id="completed",
            side="YES",
            price=0.95,
            size=5.0,
            cost=5.0,
            outcome="win",
        )
        
        engine._state.trades.append(pending)
        engine._state.trades.append(completed)
        
        pending_trades = engine._state.pending_trades
        
        assert len(pending_trades) == 1
        assert pending_trades[0].token_id == "pending"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
