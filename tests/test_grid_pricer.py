"""
Tests for Gabagool Grid Pricing Engine.

Tests cover:
1. Grid mode selection (symmetric, tilted, directional)
2. Quote price calculation ($0.01 below best bid)
3. OFI-based size tilting
4. 79% directional bias on spikes
5. Cross-market exposure limits
6. Order generation for py-clob-client
"""

import pytest
from datetime import datetime, timezone
from src.grid_pricer import (
    GabagoolGridPricer,
    GridManager,
    GridState,
    GridQuote,
    GridMode,
    create_gabagool_pricer,
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def pricer():
    """Create default Gabagool pricer."""
    return GabagoolGridPricer(
        base_allocation=100.0,
        tick_size=0.01,
        ofi_tilt_factor=0.3,
        max_position_per_side=50.0,
    )


@pytest.fixture
def tight_pricer():
    """Pricer with tight position limits."""
    return GabagoolGridPricer(
        base_allocation=20.0,
        tick_size=0.01,
        ofi_tilt_factor=0.5,
        max_position_per_side=10.0,
    )


# ============================================================================
# Grid Mode Selection Tests
# ============================================================================

class TestGridModeSelection:
    """Tests for grid mode determination."""
    
    def test_symmetric_mode_on_zero_ofi(self, pricer):
        """No OFI bias should result in symmetric mode."""
        state = pricer.calculate_grid(
            market_id="test",
            best_bid_yes=0.50,
            best_bid_no=0.49,
            ofi_bias=0.0,
        )
        assert state.mode == GridMode.SYMMETRIC
    
    def test_symmetric_mode_on_low_ofi(self, pricer):
        """Low OFI (< 0.2) should remain symmetric."""
        state = pricer.calculate_grid(
            market_id="test",
            best_bid_yes=0.50,
            best_bid_no=0.49,
            ofi_bias=0.15,
        )
        assert state.mode == GridMode.SYMMETRIC
    
    def test_tilted_mode_on_moderate_ofi(self, pricer):
        """Moderate OFI (0.2-0.79) should use tilted mode."""
        state = pricer.calculate_grid(
            market_id="test",
            best_bid_yes=0.50,
            best_bid_no=0.49,
            ofi_bias=0.5,
        )
        assert state.mode == GridMode.TILTED
    
    def test_tilted_mode_on_negative_ofi(self, pricer):
        """Negative moderate OFI should also tilt."""
        state = pricer.calculate_grid(
            market_id="test",
            best_bid_yes=0.50,
            best_bid_no=0.49,
            ofi_bias=-0.4,
        )
        assert state.mode == GridMode.TILTED
    
    def test_directional_mode_requires_spike(self, pricer):
        """High OFI alone should not trigger directional (needs spike)."""
        state = pricer.calculate_grid(
            market_id="test",
            best_bid_yes=0.50,
            best_bid_no=0.49,
            ofi_bias=0.85,
            spike_detected=False,
        )
        # Without spike, it's tilted not directional
        assert state.mode == GridMode.TILTED
    
    def test_directional_mode_on_spike_with_high_ofi(self, pricer):
        """Spike + high OFI (>79%) should trigger directional."""
        state = pricer.calculate_grid(
            market_id="test",
            best_bid_yes=0.50,
            best_bid_no=0.49,
            ofi_bias=0.85,
            spike_detected=True,
        )
        assert state.mode == GridMode.DIRECTIONAL
    
    def test_directional_requires_79_percent_threshold(self, pricer):
        """Spike + OFI below 79% should not trigger directional."""
        state = pricer.calculate_grid(
            market_id="test",
            best_bid_yes=0.50,
            best_bid_no=0.49,
            ofi_bias=0.78,  # Just below threshold
            spike_detected=True,
        )
        assert state.mode == GridMode.TILTED


# ============================================================================
# Quote Price Tests
# ============================================================================

class TestQuotePrices:
    """Tests for quote price calculation."""
    
    def test_yes_quote_one_tick_below_best_bid(self, pricer):
        """YES quote should be $0.01 below best bid."""
        state = pricer.calculate_grid(
            market_id="test",
            best_bid_yes=0.55,
            best_bid_no=0.44,
            ofi_bias=0.0,
        )
        assert state.yes_quote is not None
        assert state.yes_quote.price == 0.54
    
    def test_no_quote_one_tick_below_best_bid(self, pricer):
        """NO quote should be $0.01 below best bid."""
        state = pricer.calculate_grid(
            market_id="test",
            best_bid_yes=0.55,
            best_bid_no=0.44,
            ofi_bias=0.0,
        )
        assert state.no_quote is not None
        assert state.no_quote.price == 0.43
    
    def test_minimum_price_floor(self, pricer):
        """Price should not go below $0.01."""
        state = pricer.calculate_grid(
            market_id="test",
            best_bid_yes=0.01,  # Already at minimum
            best_bid_no=0.98,
            ofi_bias=0.0,
        )
        # YES quote should be clamped to $0.01 (not $0.00)
        assert state.yes_quote is not None
        assert state.yes_quote.price >= 0.01
    
    def test_quotes_are_maker_only(self, pricer):
        """All quotes should be marked as maker-only."""
        state = pricer.calculate_grid(
            market_id="test",
            best_bid_yes=0.55,
            best_bid_no=0.44,
            ofi_bias=0.0,
        )
        assert state.yes_quote.is_maker is True
        assert state.no_quote.is_maker is True


# ============================================================================
# Size Allocation Tests
# ============================================================================

class TestSizeAllocation:
    """Tests for capital allocation between YES and NO."""
    
    def test_symmetric_equal_allocation(self, pricer):
        """Symmetric mode should split evenly."""
        state = pricer.calculate_grid(
            market_id="test",
            best_bid_yes=0.50,
            best_bid_no=0.49,
            ofi_bias=0.0,
            available_capital=100.0,
        )
        # Should be roughly equal (within $0.50)
        yes_size = state.yes_quote.size if state.yes_quote else 0
        no_size = state.no_quote.size if state.no_quote else 0
        assert abs(yes_size - no_size) < 0.50
    
    def test_tilted_favors_yes_on_positive_ofi(self, pricer):
        """Positive OFI should allocate more to YES."""
        state = pricer.calculate_grid(
            market_id="test",
            best_bid_yes=0.50,
            best_bid_no=0.49,
            ofi_bias=0.6,
            available_capital=100.0,
        )
        yes_size = state.yes_quote.size if state.yes_quote else 0
        no_size = state.no_quote.size if state.no_quote else 0
        assert yes_size > no_size
    
    def test_tilted_favors_no_on_negative_ofi(self, pricer):
        """Negative OFI should allocate more to NO."""
        state = pricer.calculate_grid(
            market_id="test",
            best_bid_yes=0.50,
            best_bid_no=0.49,
            ofi_bias=-0.6,
            available_capital=100.0,
        )
        yes_size = state.yes_quote.size if state.yes_quote else 0
        no_size = state.no_quote.size if state.no_quote else 0
        assert no_size > yes_size
    
    def test_directional_90_percent_to_yes_on_bullish_spike(self, pricer):
        """Bullish spike should put 90% on YES side."""
        state = pricer.calculate_grid(
            market_id="test",
            best_bid_yes=0.50,
            best_bid_no=0.49,
            ofi_bias=0.85,
            spike_detected=True,
            available_capital=100.0,
        )
        yes_size = state.yes_quote.size if state.yes_quote else 0
        no_size = state.no_quote.size if state.no_quote else 0
        total = yes_size + no_size
        
        if total > 0:
            yes_ratio = yes_size / total
            assert yes_ratio > 0.85  # Should be ~90%
    
    def test_directional_90_percent_to_no_on_bearish_spike(self, pricer):
        """Bearish spike should put 90% on NO side."""
        state = pricer.calculate_grid(
            market_id="test",
            best_bid_yes=0.50,
            best_bid_no=0.49,
            ofi_bias=-0.85,
            spike_detected=True,
            available_capital=100.0,
        )
        yes_size = state.yes_quote.size if state.yes_quote else 0
        no_size = state.no_quote.size if state.no_quote else 0
        total = yes_size + no_size
        
        if total > 0:
            no_ratio = no_size / total
            assert no_ratio > 0.85  # Should be ~90%
    
    def test_respects_max_position_per_side(self, tight_pricer):
        """Should not exceed max position per side."""
        state = tight_pricer.calculate_grid(
            market_id="test",
            best_bid_yes=0.50,
            best_bid_no=0.49,
            ofi_bias=0.0,
            available_capital=1000.0,  # Way more than max
        )
        yes_size = state.yes_quote.size if state.yes_quote else 0
        no_size = state.no_quote.size if state.no_quote else 0
        
        assert yes_size <= tight_pricer.max_position_per_side
        assert no_size <= tight_pricer.max_position_per_side


# ============================================================================
# State Tracking Tests
# ============================================================================

class TestStateTracking:
    """Tests for grid state management."""
    
    def test_state_includes_ofi_bias(self, pricer):
        """State should record OFI bias."""
        state = pricer.calculate_grid(
            market_id="test",
            best_bid_yes=0.50,
            best_bid_no=0.49,
            ofi_bias=0.42,
        )
        assert abs(state.ofi_bias - 0.42) < 0.001
    
    def test_state_includes_fair_values(self, pricer):
        """State should include calculated fair values."""
        state = pricer.calculate_grid(
            market_id="test",
            best_bid_yes=0.55,
            best_bid_no=0.44,
            ofi_bias=0.0,
        )
        # Fair values should sum close to 1.0
        assert abs((state.fair_value_yes + state.fair_value_no) - 1.0) < 0.01
    
    def test_state_has_timestamp(self, pricer):
        """State should have UTC timestamp."""
        state = pricer.calculate_grid(
            market_id="test",
            best_bid_yes=0.50,
            best_bid_no=0.49,
            ofi_bias=0.0,
        )
        assert state.timestamp is not None
        assert state.timestamp.tzinfo == timezone.utc
    
    def test_get_state_returns_cached(self, pricer):
        """get_state should return last calculated state."""
        pricer.calculate_grid(
            market_id="market-123",
            best_bid_yes=0.60,
            best_bid_no=0.39,
            ofi_bias=0.0,
        )
        
        state = pricer.get_state("market-123")
        assert state is not None
        assert state.yes_quote.price == 0.59
    
    def test_get_state_returns_none_for_unknown(self, pricer):
        """get_state should return None for unknown market."""
        state = pricer.get_state("unknown-market")
        assert state is None
    
    def test_total_exposure_calculation(self, pricer):
        """Total exposure should sum both sides."""
        state = pricer.calculate_grid(
            market_id="test",
            best_bid_yes=0.50,
            best_bid_no=0.49,
            ofi_bias=0.0,
            available_capital=100.0,
        )
        
        yes_size = state.yes_quote.size if state.yes_quote else 0
        no_size = state.no_quote.size if state.no_quote else 0
        
        assert abs(state.total_exposure - (yes_size + no_size)) < 0.01


# ============================================================================
# Refresh Logic Tests
# ============================================================================

class TestRefreshLogic:
    """Tests for quote refresh decisions."""
    
    def test_should_refresh_when_no_state(self, pricer):
        """Should refresh if no state exists."""
        should = pricer.should_refresh(
            market_id="unknown",
            current_best_bid_yes=0.50,
            current_best_bid_no=0.49,
        )
        assert should is True
    
    def test_should_not_refresh_on_small_move(self, pricer):
        """Should not refresh on move smaller than threshold."""
        pricer.calculate_grid(
            market_id="test",
            best_bid_yes=0.50,
            best_bid_no=0.49,
            ofi_bias=0.0,
        )
        
        # Small move - only 1 tick
        should = pricer.should_refresh(
            market_id="test",
            current_best_bid_yes=0.51,
            current_best_bid_no=0.48,
            refresh_threshold_ticks=2,
        )
        assert should is False
    
    def test_should_refresh_on_large_move(self, pricer):
        """Should refresh when bid moves more than threshold."""
        pricer.calculate_grid(
            market_id="test",
            best_bid_yes=0.50,
            best_bid_no=0.49,
            ofi_bias=0.0,
        )
        
        # Large move - 3 ticks
        should = pricer.should_refresh(
            market_id="test",
            current_best_bid_yes=0.53,  # Moved 3 cents
            current_best_bid_no=0.49,
            refresh_threshold_ticks=2,
        )
        assert should is True


# ============================================================================
# Order Generation Tests
# ============================================================================

class TestOrderGeneration:
    """Tests for py-clob-client order generation."""
    
    def test_generates_both_orders(self, pricer):
        """Should generate orders for both YES and NO."""
        pricer.calculate_grid(
            market_id="test",
            best_bid_yes=0.55,
            best_bid_no=0.44,
            ofi_bias=0.0,
        )
        
        orders = pricer.generate_orders(
            market_id="test",
            condition_id="0xabc123",
            yes_token_id="0xyes",
            no_token_id="0xno",
        )
        
        assert len(orders) == 2
    
    def test_order_includes_post_only(self, pricer):
        """All orders should have post_only=True."""
        pricer.calculate_grid(
            market_id="test",
            best_bid_yes=0.55,
            best_bid_no=0.44,
            ofi_bias=0.0,
        )
        
        orders = pricer.generate_orders(
            market_id="test",
            condition_id="0xabc123",
            yes_token_id="0xyes",
            no_token_id="0xno",
        )
        
        for order in orders:
            assert order["post_only"] is True
    
    def test_order_has_correct_token_ids(self, pricer):
        """Orders should use correct token IDs."""
        pricer.calculate_grid(
            market_id="test",
            best_bid_yes=0.55,
            best_bid_no=0.44,
            ofi_bias=0.0,
        )
        
        orders = pricer.generate_orders(
            market_id="test",
            condition_id="0xabc123",
            yes_token_id="0xyes123",
            no_token_id="0xno456",
        )
        
        token_ids = {o["token_id"] for o in orders}
        assert "0xyes123" in token_ids
        assert "0xno456" in token_ids
    
    def test_order_type_is_gtc(self, pricer):
        """Orders should be Good Till Cancelled."""
        pricer.calculate_grid(
            market_id="test",
            best_bid_yes=0.55,
            best_bid_no=0.44,
            ofi_bias=0.0,
        )
        
        orders = pricer.generate_orders(
            market_id="test",
            condition_id="0xabc123",
            yes_token_id="0xyes",
            no_token_id="0xno",
        )
        
        for order in orders:
            assert order["type"] == "GTC"
    
    def test_order_side_is_buy(self, pricer):
        """All grid orders should be BUY (we're bidding)."""
        pricer.calculate_grid(
            market_id="test",
            best_bid_yes=0.55,
            best_bid_no=0.44,
            ofi_bias=0.0,
        )
        
        orders = pricer.generate_orders(
            market_id="test",
            condition_id="0xabc123",
            yes_token_id="0xyes",
            no_token_id="0xno",
        )
        
        for order in orders:
            assert order["side"] == "BUY"
    
    def test_no_orders_for_unknown_market(self, pricer):
        """Should return empty list for unknown market."""
        orders = pricer.generate_orders(
            market_id="unknown",
            condition_id="0xabc",
            yes_token_id="0xyes",
            no_token_id="0xno",
        )
        assert orders == []


# ============================================================================
# Grid Manager Tests
# ============================================================================

class TestGridManager:
    """Tests for GridManager cross-market coordination."""
    
    @pytest.fixture
    def manager(self, pricer):
        """Create grid manager without OFI engine."""
        return GridManager(pricer=pricer, ofi_engine=None)
    
    @pytest.mark.asyncio
    async def test_add_market_succeeds(self, manager):
        """Should successfully add first market."""
        result = await manager.add_market(
            market_id="btc-5min",
            condition_id="0xcond1",
            yes_token_id="0xyes1",
            no_token_id="0xno1",
            asset_symbol="BTC",
        )
        assert result is True
    
    @pytest.mark.asyncio
    async def test_respects_max_concurrent_markets(self, manager):
        """Should reject markets beyond capacity."""
        # Add up to capacity
        await manager.add_market(
            market_id="btc-5min",
            condition_id="0xcond1",
            yes_token_id="0xyes1",
            no_token_id="0xno1",
            asset_symbol="BTC",
        )
        await manager.add_market(
            market_id="eth-5min",
            condition_id="0xcond2",
            yes_token_id="0xyes2",
            no_token_id="0xno2",
            asset_symbol="ETH",
        )
        
        # Third should be rejected
        result = await manager.add_market(
            market_id="sol-5min",
            condition_id="0xcond3",
            yes_token_id="0xyes3",
            no_token_id="0xno3",
            asset_symbol="SOL",
        )
        assert result is False
    
    @pytest.mark.asyncio
    async def test_remove_market_frees_capacity(self, manager):
        """Removing market should free up capacity."""
        await manager.add_market(
            market_id="btc-5min",
            condition_id="0xcond1",
            yes_token_id="0xyes1",
            no_token_id="0xno1",
            asset_symbol="BTC",
        )
        await manager.add_market(
            market_id="eth-5min",
            condition_id="0xcond2",
            yes_token_id="0xyes2",
            no_token_id="0xno2",
            asset_symbol="ETH",
        )
        
        # Remove one
        manager.remove_market("btc-5min")
        
        # Now SOL should be accepted
        result = await manager.add_market(
            market_id="sol-5min",
            condition_id="0xcond3",
            yes_token_id="0xyes3",
            no_token_id="0xno3",
            asset_symbol="SOL",
        )
        assert result is True
    
    def test_ofi_bias_zero_without_engine(self, manager):
        """Without OFI engine, bias should be 0."""
        bias, spike = manager.get_ofi_bias("BTC")
        assert bias == 0.0
        assert spike is False


# ============================================================================
# Quote Shares Calculation Tests
# ============================================================================

class TestSharesCalculation:
    """Tests for GridQuote shares calculation."""
    
    def test_shares_from_size_and_price(self):
        """Shares = size / price."""
        quote = GridQuote(side="YES", price=0.50, size=25.0)
        assert quote.shares == 50.0  # $25 at $0.50 = 50 shares
    
    def test_shares_at_low_price(self):
        """More shares at lower prices."""
        quote = GridQuote(side="NO", price=0.10, size=10.0)
        assert quote.shares == 100.0  # $10 at $0.10 = 100 shares
    
    def test_shares_zero_on_zero_price(self):
        """Zero price should return 0 shares (not divide by zero)."""
        quote = GridQuote(side="YES", price=0.0, size=10.0)
        assert quote.shares == 0.0


# ============================================================================
# Factory Function Tests
# ============================================================================

class TestFactory:
    """Tests for create_gabagool_pricer factory."""
    
    def test_creates_pricer_and_manager(self):
        """Factory should return both pricer and manager."""
        pricer, manager = create_gabagool_pricer(base_allocation=50.0)
        
        assert isinstance(pricer, GabagoolGridPricer)
        assert isinstance(manager, GridManager)
    
    def test_manager_uses_same_pricer(self):
        """Manager should use the same pricer instance."""
        pricer, manager = create_gabagool_pricer()
        assert manager.pricer is pricer
    
    def test_respects_base_allocation(self):
        """Factory should set base allocation on pricer."""
        pricer, _ = create_gabagool_pricer(base_allocation=200.0)
        assert pricer.base_allocation == 200.0
