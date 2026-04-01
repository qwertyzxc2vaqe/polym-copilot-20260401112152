"""
Funding Rate Ingestor - Binance Perpetuals Funding Rate Tracker.

Phase 2 - Task 55: Connect to public Binance Perpetuals REST API to
fetch real-time funding rates for spot market momentum prediction.

Educational purpose only - paper trading simulation.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, List, Callable
from collections import deque

import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class FundingRate:
    """Represents a funding rate snapshot."""
    symbol: str
    funding_rate: float  # Decimal (e.g., 0.0001 = 0.01%)
    funding_rate_bps: float  # Basis points
    next_funding_time: int  # Unix timestamp ms
    mark_price: float
    index_price: float
    timestamp: float
    
    @property
    def funding_rate_pct(self) -> float:
        """Funding rate as percentage."""
        return self.funding_rate * 100
    
    @property
    def time_to_funding_seconds(self) -> float:
        """Seconds until next funding."""
        return max(0, (self.next_funding_time - time.time() * 1000) / 1000)
    
    @property
    def annualized_rate(self) -> float:
        """Annualized funding rate (3 fundings per day * 365 days)."""
        return self.funding_rate * 3 * 365 * 100  # As percentage
    
    @property
    def mark_index_spread(self) -> float:
        """Spread between mark and index price."""
        if self.index_price == 0:
            return 0.0
        return (self.mark_price - self.index_price) / self.index_price * 100
    
    def to_dict(self) -> dict:
        return {
            'symbol': self.symbol,
            'funding_rate': self.funding_rate,
            'funding_rate_bps': self.funding_rate_bps,
            'funding_rate_pct': self.funding_rate_pct,
            'annualized_rate': self.annualized_rate,
            'next_funding_time': self.next_funding_time,
            'time_to_funding_seconds': self.time_to_funding_seconds,
            'mark_price': self.mark_price,
            'index_price': self.index_price,
            'mark_index_spread': self.mark_index_spread,
            'timestamp': self.timestamp,
        }


@dataclass
class FundingRateHistory:
    """Historical funding rate entry."""
    symbol: str
    funding_rate: float
    funding_time: int
    mark_price: float


class FundingRateIngestor:
    """
    Binance Perpetuals Funding Rate Tracker.
    
    Fetches real-time funding rates to predict spot market momentum.
    High positive funding → longs paying shorts → potential spot dump
    High negative funding → shorts paying longs → potential spot pump
    """
    
    PERP_BASE_URL = "https://fapi.binance.com"
    FUNDING_ENDPOINT = "/fapi/v1/premiumIndex"
    FUNDING_HISTORY_ENDPOINT = "/fapi/v1/fundingRate"
    
    SYMBOL_MAP = {
        'BTC': 'BTCUSDT',
        'ETH': 'ETHUSDT',
        'SOL': 'SOLUSDT',
        'XRP': 'XRPUSDT',
        'DOGE': 'DOGEUSDT',
        'BNB': 'BNBUSDT',
    }
    
    # Funding rate thresholds for signal generation
    HIGH_POSITIVE_THRESHOLD = 0.0005  # 0.05% - extreme long bias
    HIGH_NEGATIVE_THRESHOLD = -0.0005  # -0.05% - extreme short bias
    NEUTRAL_THRESHOLD = 0.0001  # 0.01% - neutral zone
    
    def __init__(
        self,
        symbols: List[str] = None,
        poll_interval: float = 60.0,
        on_funding_update: Optional[Callable] = None,
        on_signal: Optional[Callable] = None,
    ):
        """
        Initialize funding rate ingestor.
        
        Args:
            symbols: Symbols to track (e.g., ['BTC', 'ETH'])
            poll_interval: Seconds between API polls
            on_funding_update: Callback for funding rate updates
            on_signal: Callback for momentum signals
        """
        self.symbols = symbols or ['BTC', 'ETH']
        self.poll_interval = poll_interval
        self.on_funding_update = on_funding_update
        self.on_signal = on_signal
        
        self._running = False
        self._session: Optional[aiohttp.ClientSession] = None
        self._current_rates: Dict[str, FundingRate] = {}
        self._rate_history: Dict[str, deque] = {}
        
        # Initialize history buffers
        for symbol in self.symbols:
            self._rate_history[symbol] = deque(maxlen=100)
    
    async def start(self) -> None:
        """Start the funding rate polling loop."""
        self._running = True
        self._session = aiohttp.ClientSession()
        
        logger.info(f"Starting funding rate ingestor for {self.symbols}")
        
        try:
            while self._running:
                await self._fetch_all_funding_rates()
                await asyncio.sleep(self.poll_interval)
        except asyncio.CancelledError:
            logger.info("Funding rate ingestor cancelled")
        finally:
            await self.stop()
    
    async def stop(self) -> None:
        """Stop the ingestor and cleanup."""
        self._running = False
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("Funding rate ingestor stopped")
    
    async def _fetch_all_funding_rates(self) -> None:
        """Fetch funding rates for all tracked symbols."""
        for symbol in self.symbols:
            try:
                rate = await self._fetch_funding_rate(symbol)
                if rate:
                    self._current_rates[symbol] = rate
                    self._rate_history[symbol].append(rate)
                    
                    if self.on_funding_update:
                        await self._safe_callback(self.on_funding_update, rate)
                    
                    # Generate momentum signal
                    signal = self._generate_signal(rate)
                    if signal and self.on_signal:
                        await self._safe_callback(self.on_signal, signal)
                        
            except Exception as e:
                logger.error(f"Error fetching funding rate for {symbol}: {e}")
    
    async def _fetch_funding_rate(self, symbol: str) -> Optional[FundingRate]:
        """Fetch funding rate for a single symbol."""
        if not self._session:
            return None
        
        perp_symbol = self.SYMBOL_MAP.get(symbol, f"{symbol}USDT")
        url = f"{self.PERP_BASE_URL}{self.FUNDING_ENDPOINT}"
        params = {'symbol': perp_symbol}
        
        try:
            async with self._session.get(url, params=params, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    return self._parse_funding_rate(symbol, data)
                else:
                    logger.warning(f"Funding rate API returned {response.status}")
                    return None
        except Exception as e:
            logger.error(f"HTTP error fetching funding rate: {e}")
            return None
    
    def _parse_funding_rate(self, symbol: str, data: dict) -> FundingRate:
        """Parse API response into FundingRate object."""
        funding_rate = float(data.get('lastFundingRate', 0))
        
        return FundingRate(
            symbol=symbol,
            funding_rate=funding_rate,
            funding_rate_bps=funding_rate * 10000,
            next_funding_time=int(data.get('nextFundingTime', 0)),
            mark_price=float(data.get('markPrice', 0)),
            index_price=float(data.get('indexPrice', 0)),
            timestamp=time.time() * 1000,
        )
    
    def _generate_signal(self, rate: FundingRate) -> Optional[dict]:
        """
        Generate momentum drag signal from funding rate.
        
        High positive funding → predict downward pressure on spot
        High negative funding → predict upward pressure on spot
        """
        signal_type = 'neutral'
        signal_strength = 0.0
        prediction = 'hold'
        
        if rate.funding_rate >= self.HIGH_POSITIVE_THRESHOLD:
            signal_type = 'bearish'
            signal_strength = min(1.0, rate.funding_rate / 0.001)
            prediction = 'expect_spot_weakness'
        elif rate.funding_rate <= self.HIGH_NEGATIVE_THRESHOLD:
            signal_type = 'bullish'
            signal_strength = min(1.0, abs(rate.funding_rate) / 0.001)
            prediction = 'expect_spot_strength'
        elif abs(rate.funding_rate) <= self.NEUTRAL_THRESHOLD:
            signal_type = 'neutral'
            signal_strength = 0.0
            prediction = 'no_momentum_drag'
        
        # Only return signal if meaningful
        if signal_type == 'neutral':
            return None
        
        return {
            'symbol': rate.symbol,
            'signal_type': signal_type,
            'signal_strength': signal_strength,
            'prediction': prediction,
            'funding_rate': rate.funding_rate,
            'funding_rate_bps': rate.funding_rate_bps,
            'annualized_rate': rate.annualized_rate,
            'time_to_funding': rate.time_to_funding_seconds,
            'timestamp': rate.timestamp,
        }
    
    async def _safe_callback(self, callback: Callable, data) -> None:
        """Execute callback safely."""
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(data)
            else:
                callback(data)
        except Exception as e:
            logger.error(f"Callback error: {e}")
    
    def get_current_rate(self, symbol: str) -> Optional[FundingRate]:
        """Get current funding rate for symbol."""
        return self._current_rates.get(symbol)
    
    def get_rate_history(self, symbol: str, count: int = 50) -> List[FundingRate]:
        """Get funding rate history for symbol."""
        if symbol in self._rate_history:
            return list(self._rate_history[symbol])[-count:]
        return []
    
    def get_funding_trend(self, symbol: str, periods: int = 10) -> dict:
        """
        Calculate funding rate trend over recent periods.
        
        Returns trend direction and magnitude.
        """
        history = self.get_rate_history(symbol, periods)
        if len(history) < 2:
            return {'trend': 'unknown', 'change': 0.0, 'avg_rate': 0.0}
        
        rates = [h.funding_rate for h in history]
        avg_rate = sum(rates) / len(rates)
        
        # Simple trend: compare recent vs older
        recent_avg = sum(rates[-3:]) / min(3, len(rates[-3:]))
        older_avg = sum(rates[:3]) / min(3, len(rates[:3]))
        
        if recent_avg > older_avg + 0.0001:
            trend = 'increasing'
        elif recent_avg < older_avg - 0.0001:
            trend = 'decreasing'
        else:
            trend = 'stable'
        
        return {
            'trend': trend,
            'change': recent_avg - older_avg,
            'avg_rate': avg_rate,
            'current_rate': rates[-1],
            'periods': len(rates),
        }
    
    async def fetch_funding_history(self, symbol: str, limit: int = 100) -> List[FundingRateHistory]:
        """
        Fetch historical funding rates from API.
        
        Useful for backtesting momentum predictions.
        """
        if not self._session:
            return []
        
        perp_symbol = self.SYMBOL_MAP.get(symbol, f"{symbol}USDT")
        url = f"{self.PERP_BASE_URL}{self.FUNDING_HISTORY_ENDPOINT}"
        params = {'symbol': perp_symbol, 'limit': limit}
        
        try:
            async with self._session.get(url, params=params, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    return [
                        FundingRateHistory(
                            symbol=symbol,
                            funding_rate=float(entry.get('fundingRate', 0)),
                            funding_time=int(entry.get('fundingTime', 0)),
                            mark_price=float(entry.get('markPrice', 0)),
                        )
                        for entry in data
                    ]
                return []
        except Exception as e:
            logger.error(f"Error fetching funding history: {e}")
            return []


def create_funding_ingestor(
    symbols: List[str] = None,
    poll_interval: float = 60.0,
    on_funding_update: Optional[Callable] = None,
    on_signal: Optional[Callable] = None,
) -> FundingRateIngestor:
    """Create and return a FundingRateIngestor instance."""
    return FundingRateIngestor(
        symbols=symbols,
        poll_interval=poll_interval,
        on_funding_update=on_funding_update,
        on_signal=on_signal,
    )
