"""
Pytest fixtures and shared mocks for TDD testing.

This module provides reusable fixtures that enable isolated unit testing
without real network connections to Binance or Polymarket.

Usage:
    def test_something(mock_oracle, mock_sniper):
        # mock_oracle and mock_sniper are pre-configured AsyncMocks
        pass
"""

import asyncio
import pytest
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from unittest.mock import AsyncMock, MagicMock, patch
from enum import Enum

import sys
from pathlib import Path

# Add both tests and src to path for imports
tests_path = Path(__file__).parent
src_path = Path(__file__).parent.parent / "src"
if str(tests_path) not in sys.path:
    sys.path.insert(0, str(tests_path))
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))


# =============================================================================
# MOCK DATA CLASSES
# =============================================================================

@dataclass
class MockPriceData:
    """Mock price data from oracle."""
    symbol: str
    price: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    bid: float = 0.0
    ask: float = 0.0


@dataclass
class MockOrderBookEntry:
    """Mock order book entry."""
    price: float
    size: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class MockMarket5Min:
    """Mock 5-minute market for testing."""
    condition_id: str
    question: str
    yes_token_id: str
    no_token_id: str
    end_time: datetime
    asset: str
    slug: str = ""
    market_id: str = ""
    
    @property
    def seconds_to_expiry(self) -> float:
        """Seconds remaining until market expires."""
        return (self.end_time - datetime.now(timezone.utc)).total_seconds()
    
    @property
    def time_to_expiry(self) -> timedelta:
        """Time remaining until market expires."""
        return self.end_time - datetime.now(timezone.utc)


# =============================================================================
# MARKET BUILDERS (Test Data Factories)
# =============================================================================

class MarketBuilder:
    """
    Builder pattern for creating test markets.
    
    Usage:
        market = (MarketBuilder()
            .with_asset("BTC")
            .with_question("Will BTC go up?")
            .expiring_in_seconds(0.5)
            .build())
    """
    
    def __init__(self):
        self._condition_id = "test-market-001"
        self._question = "Will BTC go up in the next 5 minutes?"
        self._yes_token_id = "token-yes-001"
        self._no_token_id = "token-no-001"
        self._end_time = datetime.now(timezone.utc) + timedelta(seconds=60)
        self._asset = "BTC"
        self._slug = "btc-5min"
    
    def with_condition_id(self, condition_id: str) -> "MarketBuilder":
        self._condition_id = condition_id
        return self
    
    def with_question(self, question: str) -> "MarketBuilder":
        self._question = question
        return self
    
    def with_asset(self, asset: str) -> "MarketBuilder":
        self._asset = asset
        return self
    
    def with_tokens(self, yes_token: str, no_token: str) -> "MarketBuilder":
        self._yes_token_id = yes_token
        self._no_token_id = no_token
        return self
    
    def expiring_in_seconds(self, seconds: float) -> "MarketBuilder":
        self._end_time = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        return self
    
    def already_expired(self) -> "MarketBuilder":
        self._end_time = datetime.now(timezone.utc) - timedelta(seconds=1)
        return self
    
    def build(self) -> MockMarket5Min:
        return MockMarket5Min(
            condition_id=self._condition_id,
            question=self._question,
            yes_token_id=self._yes_token_id,
            no_token_id=self._no_token_id,
            end_time=self._end_time,
            asset=self._asset,
            slug=self._slug,
        )


# =============================================================================
# MOCK ORACLE
# =============================================================================

@pytest.fixture
def mock_oracle():
    """
    Create a mock BinanceOracle with configurable price data.
    
    The mock provides:
    - get_price(symbol) -> MockPriceData
    - get_rolling_average(symbol) -> float
    - is_stale(symbol, threshold) -> bool
    
    Configure with:
        mock_oracle.set_price("BTC", 50000.0)
        mock_oracle.set_rolling_average("BTC", 49900.0)
        mock_oracle.set_stale("BTC", True)
    """
    oracle = MagicMock()
    
    # Internal state
    oracle._prices: Dict[str, float] = {"BTC": 50000.0, "ETH": 3000.0}
    oracle._rolling_averages: Dict[str, float] = {"BTC": 49900.0, "ETH": 2990.0}
    oracle._stale: Dict[str, bool] = {"BTC": False, "ETH": False}
    
    def get_price(symbol: str):
        price = oracle._prices.get(symbol)
        if price is None:
            return None
        return MockPriceData(symbol=symbol, price=price)
    
    def get_rolling_average(symbol: str):
        return oracle._rolling_averages.get(symbol)
    
    def is_stale(symbol: str, threshold: int = 5):
        return oracle._stale.get(symbol, False)
    
    # Helper methods for test setup
    def set_price(symbol: str, price: float):
        oracle._prices[symbol] = price
    
    def set_rolling_average(symbol: str, avg: float):
        oracle._rolling_averages[symbol] = avg
    
    def set_stale(symbol: str, stale: bool):
        oracle._stale[symbol] = stale
    
    oracle.get_price = MagicMock(side_effect=get_price)
    oracle.get_rolling_average = MagicMock(side_effect=get_rolling_average)
    oracle.is_stale = MagicMock(side_effect=is_stale)
    oracle.set_price = set_price
    oracle.set_rolling_average = set_rolling_average
    oracle.set_stale = set_stale
    
    return oracle


# =============================================================================
# MOCK SNIPER (Order Book)
# =============================================================================

@pytest.fixture
def mock_sniper():
    """
    Create a mock PolymarketSniper with configurable order book.
    
    The mock provides:
    - get_best_ask(token_id) -> MockOrderBookEntry
    - get_best_bid(token_id) -> MockOrderBookEntry
    - subscribe(token_ids)
    - unsubscribe(token_ids)
    
    Configure with:
        mock_sniper.set_best_ask("token-yes", 0.95)
        mock_sniper.set_best_bid("token-yes", 0.94)
    """
    sniper = AsyncMock()
    
    # Internal state
    sniper._best_asks: Dict[str, float] = {}
    sniper._best_bids: Dict[str, float] = {}
    
    def get_best_ask(token_id: str):
        price = sniper._best_asks.get(token_id)
        if price is None:
            return None
        return MockOrderBookEntry(price=price, size=100.0)
    
    def get_best_bid(token_id: str):
        price = sniper._best_bids.get(token_id)
        if price is None:
            return None
        return MockOrderBookEntry(price=price, size=100.0)
    
    # Helper methods
    def set_best_ask(token_id: str, price: float):
        sniper._best_asks[token_id] = price
    
    def set_best_bid(token_id: str, price: float):
        sniper._best_bids[token_id] = price
    
    def clear_order_book():
        sniper._best_asks.clear()
        sniper._best_bids.clear()
    
    sniper.get_best_ask = MagicMock(side_effect=get_best_ask)
    sniper.get_best_bid = MagicMock(side_effect=get_best_bid)
    sniper.set_best_ask = set_best_ask
    sniper.set_best_bid = set_best_bid
    sniper.clear_order_book = clear_order_book
    sniper.subscribe = AsyncMock()
    sniper.unsubscribe = AsyncMock()
    
    return sniper


# =============================================================================
# MOCK EXECUTOR
# =============================================================================

@pytest.fixture
def mock_executor():
    """
    Create a mock ZeroFeeExecutor for testing portfolio operations.
    
    The mock provides:
    - execute_fok_order(token_id, side, price, size) -> ExecutionResult
    - get_balance() -> float
    
    Configure with:
        mock_executor.set_balance(100.0)
        mock_executor.set_execution_success(True)
    """
    executor = AsyncMock()
    
    # Internal state
    executor._balance = 100.0
    executor._execution_success = True
    executor._order_count = 0
    
    @dataclass
    class MockExecutionResult:
        success: bool
        order_id: str
        fill_price: float
        fill_size: float
    
    async def execute_fok_order(token_id: str, side: str, price: float, size: float):
        executor._order_count += 1
        if executor._execution_success:
            return MockExecutionResult(
                success=True,
                order_id=f"order-{executor._order_count}",
                fill_price=price,
                fill_size=size,
            )
        raise RuntimeError("Execution failed")
    
    async def get_balance():
        return executor._balance
    
    def set_balance(balance: float):
        executor._balance = balance
    
    def set_execution_success(success: bool):
        executor._execution_success = success
    
    executor.execute_fok_order = execute_fok_order
    executor.get_balance = get_balance
    executor.set_balance = set_balance
    executor.set_execution_success = set_execution_success
    
    return executor


# =============================================================================
# MARKET FIXTURES
# =============================================================================

@pytest.fixture
def btc_market_expiring_soon():
    """BTC market expiring in 0.5 seconds (within time threshold)."""
    return (MarketBuilder()
        .with_asset("BTC")
        .with_question("Will Bitcoin go up in the next 5 minutes?")
        .with_tokens("btc-yes-token", "btc-no-token")
        .expiring_in_seconds(0.5)
        .build())


@pytest.fixture
def btc_market_not_expiring():
    """BTC market expiring in 2 minutes (outside time threshold)."""
    return (MarketBuilder()
        .with_asset("BTC")
        .with_question("Will Bitcoin go up in the next 5 minutes?")
        .with_tokens("btc-yes-token", "btc-no-token")
        .expiring_in_seconds(120)
        .build())


@pytest.fixture
def eth_market_expiring_soon():
    """ETH market expiring in 0.5 seconds."""
    return (MarketBuilder()
        .with_asset("ETH")
        .with_question("Will Ethereum go up or down?")
        .with_tokens("eth-yes-token", "eth-no-token")
        .expiring_in_seconds(0.5)
        .build())


@pytest.fixture
def expired_market():
    """Already expired market."""
    return MarketBuilder().already_expired().build()


# =============================================================================
# ASYNC EVENT LOOP
# =============================================================================

@pytest.fixture
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# =============================================================================
# PORTFOLIO FIXTURES
# =============================================================================

@pytest.fixture
def mock_loss_limiter():
    """
    Create a mock DailyLossLimiter.
    
    Configure with:
        mock_loss_limiter.set_trading_allowed(True)
    """
    limiter = MagicMock()
    limiter._trading_allowed = True
    limiter._total_loss = 0.0
    limiter._limit = 10.0
    
    def is_trading_allowed():
        return limiter._trading_allowed
    
    def record_loss(amount: float):
        limiter._total_loss += amount
    
    def record_profit(amount: float):
        pass  # Profits don't affect loss limit
    
    def get_status():
        return MagicMock(
            limit=limiter._limit,
            total_loss=limiter._total_loss,
            remaining=limiter._limit - limiter._total_loss,
        )
    
    def set_trading_allowed(allowed: bool):
        limiter._trading_allowed = allowed
    
    limiter.is_trading_allowed = MagicMock(side_effect=is_trading_allowed)
    limiter.record_loss = MagicMock(side_effect=record_loss)
    limiter.record_profit = MagicMock(side_effect=record_profit)
    limiter.get_status = MagicMock(side_effect=get_status)
    limiter.set_trading_allowed = set_trading_allowed
    
    return limiter


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def assert_opportunity_valid(opportunity, expected_signal=None, expected_token_id=None):
    """Helper to assert opportunity is valid and matches expectations."""
    assert opportunity is not None, "Expected opportunity but got None"
    assert opportunity.entry_price > 0, "Entry price should be positive"
    assert opportunity.entry_price < 1.0, "Entry price should be less than 1.0"
    assert opportunity.profit_margin > 0, "Profit margin should be positive"
    
    if expected_signal:
        assert opportunity.signal == expected_signal, f"Expected signal {expected_signal}, got {opportunity.signal}"
    
    if expected_token_id:
        assert opportunity.token_id == expected_token_id, f"Expected token {expected_token_id}, got {opportunity.token_id}"
