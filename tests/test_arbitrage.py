"""
Test suite for ArbitrageEngine - Core Business Logic.

These tests verify the 3-condition arbitrage system:
1. Time <= 1 second to resolution
2. Oracle alignment with market direction
3. Best ask < $0.99 on winning side

Following TDD (Red-Green-Refactor):
- Tests are written FIRST to define expected behavior
- Each test focuses on ONE specific condition or scenario
- Tests are isolated and independent
"""

import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock, patch
import sys
from pathlib import Path

# Add tests and src to path
tests_path = Path(__file__).parent
src_path = Path(__file__).parent.parent / "src"
if str(tests_path) not in sys.path:
    sys.path.insert(0, str(tests_path))
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

# Import from conftest
from conftest import MarketBuilder, MockMarket5Min, assert_opportunity_valid


class TestArbitrageEngineConditions:
    """Test the three core arbitrage conditions."""
    
    # =========================================================================
    # CONDITION 1: TIME THRESHOLD
    # =========================================================================
    
    @pytest.mark.asyncio
    async def test_time_condition_met_within_threshold(self, mock_oracle, mock_sniper):
        """Test: Market within 1 second of expiry should pass time condition."""
        # Import here to allow mocking
        from arbitrage import ArbitrageEngine, ArbitrageSignal
        
        # ARRANGE
        engine = ArbitrageEngine(
            oracle=mock_oracle,
            sniper=mock_sniper,
            max_entry_price=0.99,
            time_threshold_seconds=1,
        )
        
        market = (MarketBuilder()
            .with_asset("BTC")
            .expiring_in_seconds(0.5)  # Within threshold
            .with_tokens("yes-token", "no-token")
            .build())
        
        # Configure mocks for success
        mock_oracle.set_price("BTC", 50000.0)
        mock_oracle.set_rolling_average("BTC", 49900.0)  # Price going UP
        mock_sniper.set_best_ask("yes-token", 0.95)
        
        # ACT
        result = engine._check_time_condition(market)
        
        # ASSERT
        assert result is True, "Time condition should be met when market expires within threshold"
    
    @pytest.mark.asyncio
    async def test_time_condition_not_met_outside_threshold(self, mock_oracle, mock_sniper):
        """Test: Market more than 1 second from expiry should fail time condition."""
        from arbitrage import ArbitrageEngine
        
        engine = ArbitrageEngine(
            oracle=mock_oracle,
            sniper=mock_sniper,
            max_entry_price=0.99,
            time_threshold_seconds=1,
        )
        
        market = (MarketBuilder()
            .with_asset("BTC")
            .expiring_in_seconds(5.0)  # Outside threshold
            .build())
        
        result = engine._check_time_condition(market)
        
        assert result is False, "Time condition should NOT be met when market has >1s remaining"
    
    @pytest.mark.asyncio
    async def test_time_condition_not_met_for_expired_market(self, mock_oracle, mock_sniper):
        """Test: Already expired market should fail time condition."""
        from arbitrage import ArbitrageEngine
        
        engine = ArbitrageEngine(
            oracle=mock_oracle,
            sniper=mock_sniper,
        )
        
        market = MarketBuilder().already_expired().build()
        
        result = engine._check_time_condition(market)
        
        assert result is False, "Time condition should NOT be met for expired markets"
    
    # =========================================================================
    # CONDITION 2: ORACLE ALIGNMENT
    # =========================================================================
    
    @pytest.mark.asyncio
    async def test_oracle_alignment_price_going_up(self, mock_oracle, mock_sniper):
        """Test: Oracle shows price going UP (above rolling average)."""
        from arbitrage import ArbitrageEngine
        
        engine = ArbitrageEngine(oracle=mock_oracle, sniper=mock_sniper)
        
        market = (MarketBuilder()
            .with_asset("BTC")
            .with_question("Will Bitcoin go up?")
            .build())
        
        # Price above rolling average = UP direction
        mock_oracle.set_price("BTC", 50100.0)
        mock_oracle.set_rolling_average("BTC", 50000.0)
        
        aligned, direction, confidence, price = engine._check_oracle_alignment(market)
        
        assert aligned is True
        assert direction == "UP"
        assert confidence >= 0.5
        assert price == 50100.0
    
    @pytest.mark.asyncio
    async def test_oracle_alignment_price_going_down(self, mock_oracle, mock_sniper):
        """Test: Oracle shows price going DOWN (below rolling average)."""
        from arbitrage import ArbitrageEngine
        
        engine = ArbitrageEngine(oracle=mock_oracle, sniper=mock_sniper)
        
        market = MarketBuilder().with_asset("ETH").build()
        
        # Price below rolling average = DOWN direction
        mock_oracle.set_price("ETH", 2950.0)
        mock_oracle.set_rolling_average("ETH", 3000.0)
        
        aligned, direction, confidence, price = engine._check_oracle_alignment(market)
        
        assert aligned is True
        assert direction == "DOWN"
        assert price == 2950.0
    
    @pytest.mark.asyncio
    async def test_oracle_stale_data_fails_alignment(self, mock_oracle, mock_sniper):
        """Test: Stale oracle data should fail alignment check."""
        from arbitrage import ArbitrageEngine
        
        engine = ArbitrageEngine(
            oracle=mock_oracle, 
            sniper=mock_sniper,
            oracle_staleness_threshold=5,
        )
        
        market = MarketBuilder().with_asset("BTC").build()
        
        mock_oracle.set_stale("BTC", True)  # Mark as stale
        
        aligned, direction, confidence, price = engine._check_oracle_alignment(market)
        
        assert aligned is False, "Stale oracle data should fail alignment"
    
    @pytest.mark.asyncio
    async def test_oracle_no_data_fails_alignment(self, mock_oracle, mock_sniper):
        """Test: Missing oracle data should fail alignment."""
        from arbitrage import ArbitrageEngine
        
        engine = ArbitrageEngine(oracle=mock_oracle, sniper=mock_sniper)
        
        # Create market for unsupported asset
        market = MarketBuilder().with_asset("HYPE").build()
        
        aligned, direction, confidence, price = engine._check_oracle_alignment(market)
        
        assert aligned is False, "Missing oracle data should fail alignment"
    
    # =========================================================================
    # CONDITION 3: PRICE THRESHOLD
    # =========================================================================
    
    @pytest.mark.asyncio
    async def test_price_condition_met_below_threshold(self, mock_oracle, mock_sniper):
        """Test: Best ask below $0.99 should pass price condition."""
        from arbitrage import ArbitrageEngine
        
        engine = ArbitrageEngine(
            oracle=mock_oracle,
            sniper=mock_sniper,
            max_entry_price=0.99,
        )
        
        token_id = "test-token"
        mock_sniper.set_best_ask(token_id, 0.95)
        
        available, price = engine._check_price_condition(token_id)
        
        assert available is True
        assert price == 0.95
    
    @pytest.mark.asyncio
    async def test_price_condition_not_met_above_threshold(self, mock_oracle, mock_sniper):
        """Test: Best ask at or above $0.99 should fail price condition."""
        from arbitrage import ArbitrageEngine
        
        engine = ArbitrageEngine(
            oracle=mock_oracle,
            sniper=mock_sniper,
            max_entry_price=0.99,
        )
        
        token_id = "test-token"
        mock_sniper.set_best_ask(token_id, 0.995)  # Above threshold
        
        available, price = engine._check_price_condition(token_id)
        
        assert available is False
        assert price == 0.995
    
    @pytest.mark.asyncio
    async def test_price_condition_no_order_book(self, mock_oracle, mock_sniper):
        """Test: Missing order book data should fail price condition."""
        from arbitrage import ArbitrageEngine
        
        engine = ArbitrageEngine(oracle=mock_oracle, sniper=mock_sniper)
        
        # Don't set any order book data
        available, price = engine._check_price_condition("nonexistent-token")
        
        assert available is False
        assert price == 0.0


class TestArbitrageEngineSignalDetermination:
    """Test signal determination logic (BUY_YES vs BUY_NO)."""
    
    @pytest.mark.asyncio
    async def test_neutral_market_up_direction_buys_yes(self, mock_oracle, mock_sniper):
        """Test: Neutral market 'Up or Down?' with UP oracle -> BUY_YES."""
        from arbitrage import ArbitrageEngine, ArbitrageSignal
        
        engine = ArbitrageEngine(oracle=mock_oracle, sniper=mock_sniper)
        
        # Neutral market phrasing
        market = (MarketBuilder()
            .with_question("Will BTC go up or down in the next 5 minutes?")
            .build())
        
        signal = engine._determine_winning_side(market, "UP")
        
        assert signal == ArbitrageSignal.BUY_YES
    
    @pytest.mark.asyncio
    async def test_neutral_market_down_direction_buys_no(self, mock_oracle, mock_sniper):
        """Test: Neutral market 'Up or Down?' with DOWN oracle -> BUY_NO."""
        from arbitrage import ArbitrageEngine, ArbitrageSignal
        
        engine = ArbitrageEngine(oracle=mock_oracle, sniper=mock_sniper)
        
        market = (MarketBuilder()
            .with_question("Will ETH rise or fall?")
            .build())
        
        signal = engine._determine_winning_side(market, "DOWN")
        
        assert signal == ArbitrageSignal.BUY_NO
    
    @pytest.mark.asyncio
    async def test_directional_market_oracle_agrees(self, mock_oracle, mock_sniper):
        """Test: Directional market 'Will BTC go UP?' with UP oracle -> BUY_YES."""
        from arbitrage import ArbitrageEngine, ArbitrageSignal
        
        engine = ArbitrageEngine(oracle=mock_oracle, sniper=mock_sniper)
        
        market = (MarketBuilder()
            .with_question("Will Bitcoin increase in price?")  # UP bias
            .build())
        
        signal = engine._determine_winning_side(market, "UP")
        
        assert signal == ArbitrageSignal.BUY_YES
    
    @pytest.mark.asyncio
    async def test_directional_market_oracle_disagrees(self, mock_oracle, mock_sniper):
        """Test: Directional market 'Will BTC go UP?' with DOWN oracle -> BUY_NO."""
        from arbitrage import ArbitrageEngine, ArbitrageSignal
        
        engine = ArbitrageEngine(oracle=mock_oracle, sniper=mock_sniper)
        
        market = (MarketBuilder()
            .with_question("Will Bitcoin rise in the next 5 minutes?")  # UP bias
            .build())
        
        signal = engine._determine_winning_side(market, "DOWN")
        
        assert signal == ArbitrageSignal.BUY_NO


class TestArbitrageEngineFullAnalysis:
    """Test full market analysis with all conditions."""
    
    @pytest.mark.asyncio
    async def test_all_conditions_met_returns_opportunity(self, mock_oracle, mock_sniper):
        """Test: When all 3 conditions met, return valid opportunity."""
        from arbitrage import ArbitrageEngine, ArbitrageSignal
        
        engine = ArbitrageEngine(
            oracle=mock_oracle,
            sniper=mock_sniper,
            max_entry_price=0.99,
            time_threshold_seconds=1,
        )
        
        # Market expiring in 0.5 seconds
        market = (MarketBuilder()
            .with_asset("BTC")
            .with_question("Will Bitcoin go up or down?")
            .with_tokens("btc-yes", "btc-no")
            .expiring_in_seconds(0.5)
            .build())
        
        # Oracle shows UP (price above rolling average)
        mock_oracle.set_price("BTC", 50100.0)
        mock_oracle.set_rolling_average("BTC", 50000.0)
        
        # Order book has good price for YES token
        mock_sniper.set_best_ask("btc-yes", 0.95)
        
        # Analyze
        with patch('arbitrage.rate_limited', lambda *a, **k: lambda f: f):
            with patch('arbitrage.secure_error_handler', lambda f: f):
                opportunity = await engine.analyze_market(market)
        
        # Assert
        assert opportunity is not None
        assert opportunity.signal == ArbitrageSignal.BUY_YES
        assert opportunity.token_id == "btc-yes"
        assert opportunity.entry_price == 0.95
        assert abs(opportunity.profit_margin - 0.05) < 0.001  # Floating point comparison
    
    @pytest.mark.asyncio
    async def test_time_condition_fails_returns_none(self, mock_oracle, mock_sniper):
        """Test: When time condition fails, return None."""
        from arbitrage import ArbitrageEngine
        
        engine = ArbitrageEngine(
            oracle=mock_oracle,
            sniper=mock_sniper,
            time_threshold_seconds=1,
        )
        
        # Market NOT expiring soon
        market = (MarketBuilder()
            .with_asset("BTC")
            .expiring_in_seconds(60)  # Too far out
            .build())
        
        with patch('arbitrage.rate_limited', lambda *a, **k: lambda f: f):
            with patch('arbitrage.secure_error_handler', lambda f: f):
                opportunity = await engine.analyze_market(market)
        
        assert opportunity is None
    
    @pytest.mark.asyncio
    async def test_price_too_high_returns_none(self, mock_oracle, mock_sniper):
        """Test: When price >= max_entry, return None."""
        from arbitrage import ArbitrageEngine
        
        engine = ArbitrageEngine(
            oracle=mock_oracle,
            sniper=mock_sniper,
            max_entry_price=0.99,
            time_threshold_seconds=1,
        )
        
        market = (MarketBuilder()
            .with_asset("BTC")
            .with_tokens("btc-yes", "btc-no")
            .expiring_in_seconds(0.5)
            .build())
        
        mock_oracle.set_price("BTC", 50100.0)
        mock_oracle.set_rolling_average("BTC", 50000.0)
        
        # Price too high
        mock_sniper.set_best_ask("btc-yes", 0.995)
        
        with patch('arbitrage.rate_limited', lambda *a, **k: lambda f: f):
            with patch('arbitrage.secure_error_handler', lambda f: f):
                opportunity = await engine.analyze_market(market)
        
        assert opportunity is None


class TestAssetParsing:
    """Test asset detection from market questions."""
    
    @pytest.mark.asyncio
    async def test_parse_btc_from_question(self, mock_oracle, mock_sniper):
        """Test: Detect BTC from various question phrasings."""
        from arbitrage import ArbitrageEngine
        
        engine = ArbitrageEngine(oracle=mock_oracle, sniper=mock_sniper)
        
        test_cases = [
            ("Will BTC go up?", "BTC"),
            ("Bitcoin price prediction", "BTC"),
            ("Will BITCOIN increase?", "BTC"),
        ]
        
        for question, expected in test_cases:
            result = engine._parse_asset_from_question(question)
            assert result == expected, f"Failed for question: {question}"
    
    @pytest.mark.asyncio
    async def test_parse_eth_from_question(self, mock_oracle, mock_sniper):
        """Test: Detect ETH from various question phrasings."""
        from arbitrage import ArbitrageEngine
        
        engine = ArbitrageEngine(oracle=mock_oracle, sniper=mock_sniper)
        
        test_cases = [
            ("Will ETH go up?", "ETH"),
            ("Ethereum price in 5 minutes", "ETH"),
            ("Will ETHEREUM rise?", "ETH"),
        ]
        
        for question, expected in test_cases:
            result = engine._parse_asset_from_question(question)
            assert result == expected, f"Failed for question: {question}"
    
    @pytest.mark.asyncio
    async def test_parse_unknown_asset_returns_none(self, mock_oracle, mock_sniper):
        """Test: Unknown asset returns None."""
        from arbitrage import ArbitrageEngine
        
        engine = ArbitrageEngine(oracle=mock_oracle, sniper=mock_sniper)
        
        result = engine._parse_asset_from_question("Will UNKNOWN coin go up?")
        
        assert result is None


class TestArbitrageOpportunityDataclass:
    """Test ArbitrageOpportunity calculations and serialization."""
    
    def test_profit_percent_calculation(self):
        """Test: profit_percent is calculated correctly."""
        from arbitrage import ArbitrageOpportunity, ArbitrageSignal
        
        market = MarketBuilder().build()
        
        opportunity = ArbitrageOpportunity(
            market=market,
            signal=ArbitrageSignal.BUY_YES,
            token_id="token",
            entry_price=0.95,
            expected_payout=1.0,
            profit_margin=0.05,
            time_to_resolution=0.5,
            oracle_direction="UP",
            oracle_price=50000.0,
            confidence=0.9,
        )
        
        # 0.05 / 0.95 * 100 = 5.26%
        assert abs(opportunity.profit_percent - 5.26) < 0.1
    
    def test_to_dict_serialization(self):
        """Test: to_dict produces valid dictionary."""
        from arbitrage import ArbitrageOpportunity, ArbitrageSignal
        
        market = (MarketBuilder()
            .with_condition_id("test-market")
            .with_question("Test question")
            .build())
        
        opportunity = ArbitrageOpportunity(
            market=market,
            signal=ArbitrageSignal.BUY_YES,
            token_id="token-123",
            entry_price=0.95,
            expected_payout=1.0,
            profit_margin=0.05,
            time_to_resolution=0.5,
            oracle_direction="UP",
            oracle_price=50000.0,
            confidence=0.9,
        )
        
        data = opportunity.to_dict()
        
        assert data["market_id"] == "test-market"
        assert data["signal"] == "buy_yes"
        assert data["token_id"] == "token-123"
        assert data["entry_price"] == 0.95


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
