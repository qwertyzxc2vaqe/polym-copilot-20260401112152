"""
Multi-Conditional Latency Arbitrage Logic Module.

Implements Fill-or-Kill (FOK) order triggering at $0.99 when ALL THREE
conditions are simultaneously met:
1. Time Remaining <= 1 second to resolution
2. Binance Oracle Alignment with market direction
3. Best Ask < $0.99 on the winning side

Uses oracle price data to predict market direction and sniper for order book state.
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Callable, List, Awaitable
from enum import Enum

from oracle import BinanceOracle
from sniper import PolymarketSniper
from scanner import Market5Min
from security import rate_limited, secure_error_handler, Validator

logger = logging.getLogger(__name__)


class ArbitrageSignal(Enum):
    """Signal indicating which side to buy."""
    NO_SIGNAL = "no_signal"
    BUY_YES = "buy_yes"
    BUY_NO = "buy_no"


class ArbitrageCondition(Enum):
    """Individual condition status for debugging."""
    TIME_NOT_MET = "time_not_met"
    ORACLE_STALE = "oracle_stale"
    ORACLE_MISALIGNED = "oracle_misaligned"
    PRICE_TOO_HIGH = "price_too_high"
    NO_ORDER_BOOK = "no_order_book"
    MARKET_RESOLVED = "market_resolved"
    ASSET_UNKNOWN = "asset_unknown"
    ALL_CONDITIONS_MET = "all_conditions_met"


@dataclass
class ArbitrageOpportunity:
    """
    Represents a valid arbitrage opportunity with all conditions met.
    
    All fields are populated only when the opportunity is actionable.
    """
    market: "Market5Min"
    signal: ArbitrageSignal
    token_id: str
    entry_price: float
    expected_payout: float  # Always 1.00 if correct prediction
    profit_margin: float    # 1.00 - entry_price
    time_to_resolution: float  # seconds remaining
    oracle_direction: str   # UP or DOWN
    oracle_price: float     # Current oracle price
    confidence: float       # 0-1 based on alignment strength
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    @property
    def profit_percent(self) -> float:
        """Calculate profit as percentage."""
        if self.entry_price == 0:
            return 0.0
        return (self.profit_margin / self.entry_price) * 100
    
    def to_dict(self) -> dict:
        """Serialize to dictionary for logging/monitoring."""
        return {
            "market_id": self.market.condition_id,
            "question": self.market.question,
            "signal": self.signal.value,
            "token_id": self.token_id,
            "entry_price": self.entry_price,
            "profit_margin": self.profit_margin,
            "profit_percent": self.profit_percent,
            "time_to_resolution": self.time_to_resolution,
            "oracle_direction": self.oracle_direction,
            "oracle_price": self.oracle_price,
            "confidence": self.confidence,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class AnalysisResult:
    """Result of market analysis including failure reason if applicable."""
    opportunity: Optional[ArbitrageOpportunity] = None
    condition: ArbitrageCondition = ArbitrageCondition.NO_ORDER_BOOK
    details: str = ""
    
    @property
    def is_actionable(self) -> bool:
        """Check if this result represents an actionable opportunity."""
        return self.opportunity is not None and self.condition == ArbitrageCondition.ALL_CONDITIONS_MET


class ArbitrageEngine:
    """
    Multi-conditional arbitrage engine for Polymarket 5-minute markets.
    
    Monitors markets for the exact conditions required to execute a 
    Fill-or-Kill order at $0.99 when oracle alignment confirms direction.
    
    Features:
    - Real-time condition checking across time, price, and oracle
    - Confidence scoring based on oracle signal strength
    - Priority ranking when multiple opportunities exist
    - Callback system for opportunity notifications
    """
    
    # Asset detection patterns in market questions
    # Note: HYPE is not supported by oracle (not on Binance) - see oracle.py
    ASSET_PATTERNS = {
        "BTC": [
            re.compile(r'\bBitcoin\b', re.IGNORECASE),
            re.compile(r'\bBTC\b', re.IGNORECASE),
        ],
        "ETH": [
            re.compile(r'\bEthereum\b', re.IGNORECASE),
            re.compile(r'\bETH\b', re.IGNORECASE),
        ],
        "SOL": [
            re.compile(r'\bSolana\b', re.IGNORECASE),
            re.compile(r'\bSOL\b', re.IGNORECASE),
        ],
        "XRP": [
            re.compile(r'\bXRP\b', re.IGNORECASE),
            re.compile(r'\bRipple\b', re.IGNORECASE),
        ],
        "DOGE": [
            re.compile(r'\bDogecoin\b', re.IGNORECASE),
            re.compile(r'\bDOGE\b', re.IGNORECASE),
        ],
        "BNB": [
            re.compile(r'\bBNB\b', re.IGNORECASE),
            re.compile(r'\bBinance\s*Coin\b', re.IGNORECASE),
        ],
    }
    
    # Symbols supported by the oracle (excludes HYPE - not on Binance)
    ORACLE_SUPPORTED_SYMBOLS = {"BTC", "ETH", "SOL", "XRP", "DOGE", "BNB"}
    
    # Direction patterns in market questions
    DIRECTION_PATTERNS = {
        "UP": re.compile(r'\b(up|higher|above|rise|increase)\b', re.IGNORECASE),
        "DOWN": re.compile(r'\b(down|lower|below|fall|decrease)\b', re.IGNORECASE),
    }
    
    def __init__(
        self,
        oracle: BinanceOracle,
        sniper: PolymarketSniper,
        max_entry_price: float = 0.99,
        time_threshold_seconds: int = 1,
        min_confidence: float = 0.0,
        oracle_staleness_threshold: int = 5,
    ):
        """
        Initialize the arbitrage engine.
        
        Args:
            oracle: BinanceOracle instance for price data
            sniper: PolymarketSniper instance for order book state
            max_entry_price: Maximum price to pay for entry (default $0.99)
            time_threshold_seconds: Max seconds to resolution (default 1)
            min_confidence: Minimum confidence score to trigger (0-1)
            oracle_staleness_threshold: Max age of oracle data in seconds
        """
        self.oracle = oracle
        self.sniper = sniper
        self.max_entry_price = max_entry_price
        self.time_threshold_seconds = time_threshold_seconds
        self.min_confidence = min_confidence
        self.oracle_staleness_threshold = oracle_staleness_threshold
        
        # Running state
        self._running = False
        self._analysis_count = 0
        self._opportunities_found = 0
        
        # Validate configuration
        if not Validator.validate_price(max_entry_price):
            raise ValueError(f"Invalid max_entry_price: {max_entry_price}")
        if time_threshold_seconds < 0:
            raise ValueError(f"Invalid time_threshold_seconds: {time_threshold_seconds}")
    
    def _parse_asset_from_question(self, question: str) -> Optional[str]:
        """
        Extract asset symbol (BTC/ETH) from market question.
        
        Args:
            question: Market question text
            
        Returns:
            Asset symbol or None if not detected
        """
        for asset, patterns in self.ASSET_PATTERNS.items():
            for pattern in patterns:
                if pattern.search(question):
                    return asset
        return None
    
    def _parse_market_direction_bias(self, question: str) -> Optional[str]:
        """
        Detect if market question has inherent direction bias.
        
        Some markets are phrased as "Will BTC go UP?" where YES = UP.
        Others are neutral "Bitcoin Up or Down?" where we need oracle.
        
        Args:
            question: Market question text
            
        Returns:
            "UP", "DOWN", or None if neutral/ambiguous
        """
        up_match = self.DIRECTION_PATTERNS["UP"].search(question)
        down_match = self.DIRECTION_PATTERNS["DOWN"].search(question)
        
        # If both or neither, market is likely neutral "Up or Down?"
        if (up_match and down_match) or (not up_match and not down_match):
            return None
        
        return "UP" if up_match else "DOWN"
    
    def _check_time_condition(self, market: Market5Min) -> bool:
        """
        Check if market is within time threshold of resolution.
        
        Args:
            market: Market to check
            
        Returns:
            True if within threshold, False otherwise
        """
        seconds_remaining = market.seconds_to_expiry
        return 0 < seconds_remaining <= self.time_threshold_seconds
    
    def _check_oracle_alignment(
        self, 
        market: Market5Min
    ) -> tuple[bool, str, float, float]:
        """
        Check if oracle price aligns with market direction.
        
        Uses rolling average comparison to determine price direction,
        then calculates confidence based on deviation strength.
        
        Args:
            market: Market to analyze
            
        Returns:
            Tuple of (aligned, direction, confidence, current_price)
            - aligned: True if oracle data is available and fresh
            - direction: "UP" or "DOWN" based on oracle
            - confidence: 0-1 score based on deviation from rolling avg
            - current_price: Current oracle price
        """
        # Determine asset from market
        asset = market.asset.upper() if market.asset else self._parse_asset_from_question(market.question)
        
        if not asset or asset not in self.ORACLE_SUPPORTED_SYMBOLS:
            return False, "", 0.0, 0.0
        
        # Check for stale oracle data
        if self.oracle.is_stale(asset, self.oracle_staleness_threshold):
            return False, "", 0.0, 0.0
        
        # Get current price and rolling average
        price_data = self.oracle.get_price(asset)
        if price_data is None:
            return False, "", 0.0, 0.0
        
        rolling_avg = self.oracle.get_rolling_average(asset)
        if rolling_avg is None or rolling_avg == 0:
            return False, "", 0.0, 0.0
        
        current_price = price_data.price
        
        # Determine direction: current vs rolling average
        direction = "UP" if current_price >= rolling_avg else "DOWN"
        
        # Calculate confidence based on deviation from rolling average
        # Higher deviation = higher confidence in direction
        deviation_pct = abs(current_price - rolling_avg) / rolling_avg * 100
        
        # Map deviation to confidence (0-1)
        # 0% deviation = 0.5 confidence (uncertain)
        # 0.1% deviation = ~0.75 confidence
        # 0.2%+ deviation = ~1.0 confidence
        confidence = min(1.0, 0.5 + (deviation_pct * 2.5))
        
        return True, direction, confidence, current_price
    
    def _check_price_condition(self, token_id: str) -> tuple[bool, float]:
        """
        Check if best ask is below max entry price.
        
        Args:
            token_id: Token to check in order book
            
        Returns:
            Tuple of (available, price)
            - available: True if ask exists below max price
            - price: Best ask price or 0 if unavailable
        """
        best_ask = self.sniper.get_best_ask(token_id)
        
        if best_ask is None:
            return False, 0.0
        
        price = best_ask.price
        return price < self.max_entry_price, price
    
    def _determine_winning_side(
        self, 
        market: Market5Min, 
        oracle_direction: str
    ) -> ArbitrageSignal:
        """
        Determine which side (YES/NO) to buy based on oracle direction.
        
        Logic:
        - For "Up or Down" markets: UP → YES, DOWN → NO
        - For directional markets ("Will BTC go UP?"): match oracle to question
        
        Args:
            market: Market being analyzed
            oracle_direction: "UP" or "DOWN" from oracle
            
        Returns:
            ArbitrageSignal indicating which side to buy
        """
        market_bias = self._parse_market_direction_bias(market.question)
        
        if market_bias is None:
            # Neutral market "Up or Down?" - UP = YES, DOWN = NO
            if oracle_direction == "UP":
                return ArbitrageSignal.BUY_YES
            else:
                return ArbitrageSignal.BUY_NO
        else:
            # Directional market - match oracle to question
            if oracle_direction == market_bias:
                # Oracle agrees with market direction (e.g., both say UP)
                return ArbitrageSignal.BUY_YES
            else:
                # Oracle disagrees with market direction
                return ArbitrageSignal.BUY_NO
    
    def _get_token_for_signal(
        self, 
        market: Market5Min, 
        signal: ArbitrageSignal
    ) -> str:
        """
        Get the token ID for the determined signal.
        
        Args:
            market: Market being traded
            signal: Signal indicating YES or NO side
            
        Returns:
            Token ID for the appropriate side
        """
        if signal == ArbitrageSignal.BUY_YES:
            return market.yes_token_id
        elif signal == ArbitrageSignal.BUY_NO:
            return market.no_token_id
        else:
            return ""
    
    @secure_error_handler
    @rate_limited(max_calls=1000, time_window=1.0, name="arbitrage_analysis")
    async def analyze_market(self, market: Market5Min) -> Optional[ArbitrageOpportunity]:
        """
        Analyze a market for arbitrage opportunity.
        
        Checks all three conditions simultaneously:
        1. Time <= threshold
        2. Oracle alignment
        3. Price < max entry
        
        Returns opportunity if ALL conditions met, None otherwise.
        
        Args:
            market: Market to analyze
            
        Returns:
            ArbitrageOpportunity if actionable, None otherwise
        """
        result = await self._analyze_market_detailed(market)
        return result.opportunity
    
    async def _analyze_market_detailed(self, market: Market5Min) -> AnalysisResult:
        """
        Detailed market analysis with failure reasons.
        
        Args:
            market: Market to analyze
            
        Returns:
            AnalysisResult with opportunity or failure reason
        """
        self._analysis_count += 1
        
        # Check for already resolved market
        if market.seconds_to_expiry <= 0:
            return AnalysisResult(
                condition=ArbitrageCondition.MARKET_RESOLVED,
                details="Market has already resolved"
            )
        
        # Condition 1: Time threshold
        if not self._check_time_condition(market):
            return AnalysisResult(
                condition=ArbitrageCondition.TIME_NOT_MET,
                details=f"Time remaining: {market.seconds_to_expiry:.2f}s > {self.time_threshold_seconds}s threshold"
            )
        
        # Parse asset for oracle lookup
        # Note: HYPE markets cannot use oracle arbitrage (not available on Binance)
        asset = market.asset.upper() if market.asset else self._parse_asset_from_question(market.question)
        if not asset or asset not in self.ORACLE_SUPPORTED_SYMBOLS:
            return AnalysisResult(
                condition=ArbitrageCondition.ASSET_UNKNOWN,
                details=f"Could not determine asset from: {market.question[:50]}"
            )
        
        # Condition 2: Oracle alignment
        aligned, direction, confidence, oracle_price = self._check_oracle_alignment(market)
        
        if not aligned:
            if self.oracle.is_stale(asset, self.oracle_staleness_threshold):
                return AnalysisResult(
                    condition=ArbitrageCondition.ORACLE_STALE,
                    details=f"Oracle data for {asset} is stale"
                )
            return AnalysisResult(
                condition=ArbitrageCondition.ORACLE_MISALIGNED,
                details=f"No oracle data available for {asset}"
            )
        
        # Check minimum confidence threshold
        if confidence < self.min_confidence:
            return AnalysisResult(
                condition=ArbitrageCondition.ORACLE_MISALIGNED,
                details=f"Confidence {confidence:.2f} below minimum {self.min_confidence}"
            )
        
        # Determine winning side based on oracle direction
        signal = self._determine_winning_side(market, direction)
        
        if signal == ArbitrageSignal.NO_SIGNAL:
            return AnalysisResult(
                condition=ArbitrageCondition.ORACLE_MISALIGNED,
                details="Could not determine winning side"
            )
        
        # Get token ID for the winning side
        token_id = self._get_token_for_signal(market, signal)
        
        if not token_id:
            return AnalysisResult(
                condition=ArbitrageCondition.NO_ORDER_BOOK,
                details="No token ID available"
            )
        
        # Condition 3: Price below threshold
        price_available, entry_price = self._check_price_condition(token_id)
        
        if not price_available:
            if entry_price == 0:
                return AnalysisResult(
                    condition=ArbitrageCondition.NO_ORDER_BOOK,
                    details=f"No order book data for token {token_id[:20]}..."
                )
            return AnalysisResult(
                condition=ArbitrageCondition.PRICE_TOO_HIGH,
                details=f"Best ask {entry_price:.4f} >= max entry {self.max_entry_price}"
            )
        
        # All conditions met - create opportunity
        time_remaining = market.seconds_to_expiry
        profit_margin = 1.0 - entry_price
        
        opportunity = ArbitrageOpportunity(
            market=market,
            signal=signal,
            token_id=token_id,
            entry_price=entry_price,
            expected_payout=1.0,
            profit_margin=profit_margin,
            time_to_resolution=time_remaining,
            oracle_direction=direction,
            oracle_price=oracle_price,
            confidence=confidence,
        )
        
        self._opportunities_found += 1
        
        logger.info(
            f"OPPORTUNITY: {signal.value} on {asset} market | "
            f"Entry: ${entry_price:.4f} | Profit: {profit_margin * 100:.2f}% | "
            f"Time: {time_remaining:.2f}s | Confidence: {confidence:.2f}"
        )
        
        return AnalysisResult(
            opportunity=opportunity,
            condition=ArbitrageCondition.ALL_CONDITIONS_MET,
            details="All three conditions met"
        )
    
    async def analyze_markets_batch(
        self, 
        markets: List[Market5Min]
    ) -> List[ArbitrageOpportunity]:
        """
        Analyze multiple markets and return all valid opportunities.
        
        Runs analysis in parallel for efficiency.
        
        Args:
            markets: List of markets to analyze
            
        Returns:
            List of valid opportunities, sorted by profit margin (highest first)
        """
        if not markets:
            return []
        
        # Run analysis concurrently
        results = await asyncio.gather(
            *[self.analyze_market(market) for market in markets],
            return_exceptions=True
        )
        
        opportunities = []
        for result in results:
            if isinstance(result, ArbitrageOpportunity):
                opportunities.append(result)
            elif isinstance(result, Exception):
                logger.warning(f"Analysis error: {result}")
        
        # Sort by profit margin (highest first)
        opportunities.sort(key=lambda x: x.profit_margin, reverse=True)
        
        return opportunities
    
    async def run_analysis_loop(
        self,
        markets: List[Market5Min],
        on_opportunity: Callable[[ArbitrageOpportunity], Awaitable[None]],
        interval_ms: int = 100,
    ) -> None:
        """
        Continuously analyze markets for opportunities.
        
        Calls on_opportunity callback when valid signal found.
        Prioritizes highest profit margin when multiple opportunities exist.
        
        Args:
            markets: List of markets to monitor
            on_opportunity: Async callback for opportunity notifications
            interval_ms: Analysis interval in milliseconds (default 100ms)
        """
        self._running = True
        interval_seconds = interval_ms / 1000.0
        
        logger.info(
            f"Starting analysis loop with {len(markets)} markets | "
            f"Interval: {interval_ms}ms | Max entry: ${self.max_entry_price} | "
            f"Time threshold: {self.time_threshold_seconds}s"
        )
        
        while self._running:
            try:
                # Filter out expired markets
                active_markets = [
                    m for m in markets 
                    if m.seconds_to_expiry > 0
                ]
                
                if not active_markets:
                    logger.info("No active markets remaining, stopping loop")
                    break
                
                # Analyze all markets
                opportunities = await self.analyze_markets_batch(active_markets)
                
                # Process opportunities (highest profit first)
                for opp in opportunities:
                    try:
                        await on_opportunity(opp)
                    except Exception as e:
                        logger.error(f"Error in opportunity callback: {e}")
                
                await asyncio.sleep(interval_seconds)
                
            except asyncio.CancelledError:
                logger.info("Analysis loop cancelled")
                break
            except Exception as e:
                logger.error(f"Analysis loop error: {e}")
                await asyncio.sleep(interval_seconds)
        
        self._running = False
        logger.info(
            f"Analysis loop stopped | Total analyses: {self._analysis_count} | "
            f"Opportunities found: {self._opportunities_found}"
        )
    
    def stop(self) -> None:
        """Stop the analysis loop."""
        self._running = False
    
    def get_stats(self) -> dict:
        """
        Get engine statistics.
        
        Returns:
            Dictionary with analysis stats
        """
        return {
            "running": self._running,
            "analysis_count": self._analysis_count,
            "opportunities_found": self._opportunities_found,
            "max_entry_price": self.max_entry_price,
            "time_threshold_seconds": self.time_threshold_seconds,
            "min_confidence": self.min_confidence,
            "oracle_connected": self.oracle.is_connected,
            "sniper_stats": self.sniper.stats.__dict__ if hasattr(self.sniper, 'stats') else {},
        }


# Factory function for easy instantiation
def create_arbitrage_engine(
    oracle: Optional[BinanceOracle] = None,
    sniper: Optional[PolymarketSniper] = None,
    max_entry_price: float = 0.99,
    time_threshold_seconds: int = 1,
) -> ArbitrageEngine:
    """
    Create an arbitrage engine with default or provided components.
    
    Args:
        oracle: BinanceOracle instance (created from config if None)
        sniper: PolymarketSniper instance (created if None)
        max_entry_price: Maximum entry price (default $0.99)
        time_threshold_seconds: Time threshold (default 1 second)
        
    Returns:
        Configured ArbitrageEngine instance
    """
    if oracle is None:
        oracle = BinanceOracle.from_config()
    
    if sniper is None:
        sniper = PolymarketSniper()
    
    return ArbitrageEngine(
        oracle=oracle,
        sniper=sniper,
        max_entry_price=max_entry_price,
        time_threshold_seconds=time_threshold_seconds,
    )


if __name__ == "__main__":
    # Basic test/demo
    import sys
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )
    
    async def demo():
        """Demo the arbitrage engine with mock data."""
        print("Arbitrage Engine Demo")
        print("=" * 50)
        
        # Would need real oracle/sniper connections for full demo
        print("\nTo run the full arbitrage engine:")
        print("1. Connect BinanceOracle for price feeds")
        print("2. Connect PolymarketSniper for order book")
        print("3. Scan for markets using MarketScanner")
        print("4. Call engine.analyze_market() or run_analysis_loop()")
        print("\nAll three conditions must be met:")
        print("  - Time <= 1 second to resolution")
        print("  - Oracle alignment (price direction)")
        print("  - Best ask < $0.99")
    
    asyncio.run(demo())
