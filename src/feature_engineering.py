"""
Feature Engineering Module - VWAP, TWAP, Micro-Price Calculations.

Phase 2 - Task 59: Calculate rolling Volume Weighted Average Price,
Time-Weighted Average Price, and Micro-Price from historical data.

Educational purpose only - paper trading simulation.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Tuple, Any
from collections import deque
import math

logger = logging.getLogger(__name__)

# Try to import numpy/pandas for vectorized operations
try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False
    logger.warning("NumPy not available, using pure Python calculations")

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False


@dataclass
class TickFeatures:
    """Calculated features for a single tick."""
    symbol: str
    timestamp: float
    price: float
    bid: float
    ask: float
    volume: float
    
    # Derived features
    mid_price: float = 0.0
    micro_price: float = 0.0
    spread: float = 0.0
    spread_bps: float = 0.0
    
    # Rolling calculations
    vwap_1m: float = 0.0
    vwap_5m: float = 0.0
    twap_1m: float = 0.0
    twap_5m: float = 0.0
    
    # Price relative to averages
    price_vs_vwap_1m: float = 0.0  # Percentage deviation
    price_vs_twap_1m: float = 0.0
    
    # Volatility features
    volatility_1m: float = 0.0
    volatility_5m: float = 0.0
    
    # Momentum features
    return_1s: float = 0.0
    return_5s: float = 0.0
    return_1m: float = 0.0
    
    # Volume features
    volume_imbalance: float = 0.0  # (buy_vol - sell_vol) / total
    relative_volume: float = 0.0  # Current vs average
    
    def to_dict(self) -> dict:
        return {
            'symbol': self.symbol,
            'timestamp': self.timestamp,
            'price': self.price,
            'bid': self.bid,
            'ask': self.ask,
            'volume': self.volume,
            'mid_price': self.mid_price,
            'micro_price': self.micro_price,
            'spread': self.spread,
            'spread_bps': self.spread_bps,
            'vwap_1m': self.vwap_1m,
            'vwap_5m': self.vwap_5m,
            'twap_1m': self.twap_1m,
            'twap_5m': self.twap_5m,
            'price_vs_vwap_1m': self.price_vs_vwap_1m,
            'price_vs_twap_1m': self.price_vs_twap_1m,
            'volatility_1m': self.volatility_1m,
            'volatility_5m': self.volatility_5m,
            'return_1s': self.return_1s,
            'return_5s': self.return_5s,
            'return_1m': self.return_1m,
            'volume_imbalance': self.volume_imbalance,
            'relative_volume': self.relative_volume,
        }


@dataclass
class OrderBookFeatures:
    """Features derived from order book data."""
    symbol: str
    timestamp: float
    
    # Level 1
    best_bid: float = 0.0
    best_ask: float = 0.0
    best_bid_size: float = 0.0
    best_ask_size: float = 0.0
    
    # Micro-price (weighted by top-of-book sizes)
    micro_price: float = 0.0
    
    # Depth-weighted prices
    vwap_bid_5: float = 0.0  # VWAP of top 5 bid levels
    vwap_ask_5: float = 0.0  # VWAP of top 5 ask levels
    
    # Order flow imbalance at various depths
    ofi_1: float = 0.0
    ofi_5: float = 0.0
    ofi_10: float = 0.0
    ofi_20: float = 0.0
    
    # Depth metrics
    bid_depth_5: float = 0.0
    ask_depth_5: float = 0.0
    depth_imbalance_5: float = 0.0
    
    # Price levels
    bid_levels_count: int = 0
    ask_levels_count: int = 0
    
    def to_dict(self) -> dict:
        return {
            'symbol': self.symbol,
            'timestamp': self.timestamp,
            'best_bid': self.best_bid,
            'best_ask': self.best_ask,
            'best_bid_size': self.best_bid_size,
            'best_ask_size': self.best_ask_size,
            'micro_price': self.micro_price,
            'vwap_bid_5': self.vwap_bid_5,
            'vwap_ask_5': self.vwap_ask_5,
            'ofi_1': self.ofi_1,
            'ofi_5': self.ofi_5,
            'ofi_10': self.ofi_10,
            'ofi_20': self.ofi_20,
            'bid_depth_5': self.bid_depth_5,
            'ask_depth_5': self.ask_depth_5,
            'depth_imbalance_5': self.depth_imbalance_5,
        }


class FeatureEngine:
    """
    Real-time feature engineering engine.
    
    Calculates VWAP, TWAP, Micro-Price, and other features
    from streaming market data.
    """
    
    # Window sizes in milliseconds
    WINDOW_1M = 60_000
    WINDOW_5M = 300_000
    WINDOW_1S = 1_000
    WINDOW_5S = 5_000
    
    def __init__(
        self,
        symbols: List[str] = None,
        max_history: int = 100000,
    ):
        """
        Initialize feature engine.
        
        Args:
            symbols: Symbols to track
            max_history: Maximum ticks to keep in memory
        """
        self.symbols = symbols or ['BTC', 'ETH']
        self.max_history = max_history
        
        # Price/volume history for each symbol
        self._tick_history: Dict[str, deque] = {}
        self._order_book_history: Dict[str, deque] = {}
        
        # Initialize buffers
        for symbol in self.symbols:
            self._tick_history[symbol] = deque(maxlen=max_history)
            self._order_book_history[symbol] = deque(maxlen=1000)
    
    def add_tick(
        self,
        symbol: str,
        price: float,
        bid: float,
        ask: float,
        volume: float,
        timestamp: float,
    ) -> TickFeatures:
        """
        Add a tick and calculate features.
        
        Returns fully populated TickFeatures object.
        """
        if symbol not in self._tick_history:
            self._tick_history[symbol] = deque(maxlen=self.max_history)
        
        # Store tick data
        tick = {
            'price': price,
            'bid': bid,
            'ask': ask,
            'volume': volume,
            'timestamp': timestamp,
        }
        self._tick_history[symbol].append(tick)
        
        # Calculate features
        features = TickFeatures(
            symbol=symbol,
            timestamp=timestamp,
            price=price,
            bid=bid,
            ask=ask,
            volume=volume,
        )
        
        # Basic derived features
        features.mid_price = (bid + ask) / 2 if bid > 0 and ask > 0 else price
        features.spread = ask - bid
        features.spread_bps = (features.spread / features.mid_price * 10000) if features.mid_price > 0 else 0
        
        # Calculate micro-price (requires order book data)
        features.micro_price = self._calculate_micro_price(symbol, bid, ask)
        
        # Rolling VWAP calculations
        features.vwap_1m = self._calculate_vwap(symbol, timestamp, self.WINDOW_1M)
        features.vwap_5m = self._calculate_vwap(symbol, timestamp, self.WINDOW_5M)
        
        # Rolling TWAP calculations
        features.twap_1m = self._calculate_twap(symbol, timestamp, self.WINDOW_1M)
        features.twap_5m = self._calculate_twap(symbol, timestamp, self.WINDOW_5M)
        
        # Price vs averages
        if features.vwap_1m > 0:
            features.price_vs_vwap_1m = (price - features.vwap_1m) / features.vwap_1m * 100
        if features.twap_1m > 0:
            features.price_vs_twap_1m = (price - features.twap_1m) / features.twap_1m * 100
        
        # Volatility
        features.volatility_1m = self._calculate_volatility(symbol, timestamp, self.WINDOW_1M)
        features.volatility_5m = self._calculate_volatility(symbol, timestamp, self.WINDOW_5M)
        
        # Returns
        features.return_1s = self._calculate_return(symbol, timestamp, self.WINDOW_1S)
        features.return_5s = self._calculate_return(symbol, timestamp, self.WINDOW_5S)
        features.return_1m = self._calculate_return(symbol, timestamp, self.WINDOW_1M)
        
        # Volume metrics
        features.relative_volume = self._calculate_relative_volume(symbol, timestamp)
        
        return features
    
    def add_order_book(
        self,
        symbol: str,
        bids: List[Tuple[float, float]],  # [(price, qty), ...]
        asks: List[Tuple[float, float]],
        timestamp: float,
    ) -> OrderBookFeatures:
        """
        Add order book snapshot and calculate features.
        
        Returns OrderBookFeatures object.
        """
        if symbol not in self._order_book_history:
            self._order_book_history[symbol] = deque(maxlen=1000)
        
        # Store order book
        book = {
            'bids': bids,
            'asks': asks,
            'timestamp': timestamp,
        }
        self._order_book_history[symbol].append(book)
        
        features = OrderBookFeatures(
            symbol=symbol,
            timestamp=timestamp,
        )
        
        if not bids or not asks:
            return features
        
        # Level 1 features
        features.best_bid = bids[0][0]
        features.best_ask = asks[0][0]
        features.best_bid_size = bids[0][1]
        features.best_ask_size = asks[0][1]
        
        # Micro-price: weighted by inverse of size at top
        total_top_size = features.best_bid_size + features.best_ask_size
        if total_top_size > 0:
            # Weight bid price by ask size and vice versa (larger size = more impact)
            features.micro_price = (
                features.best_bid * features.best_ask_size +
                features.best_ask * features.best_bid_size
            ) / total_top_size
        
        # Store micro-price for tick calculations
        self._last_micro_price = {symbol: features.micro_price}
        
        # VWAP of bid/ask levels
        features.vwap_bid_5 = self._calculate_level_vwap(bids[:5])
        features.vwap_ask_5 = self._calculate_level_vwap(asks[:5])
        
        # Order flow imbalance at various depths
        for depth in [1, 5, 10, 20]:
            bid_vol = sum(b[1] for b in bids[:depth])
            ask_vol = sum(a[1] for a in asks[:depth])
            total_vol = bid_vol + ask_vol
            
            if total_vol > 0:
                ofi = (bid_vol - ask_vol) / total_vol
            else:
                ofi = 0.0
            
            setattr(features, f'ofi_{depth}', ofi)
        
        # Depth metrics
        features.bid_depth_5 = sum(b[1] for b in bids[:5])
        features.ask_depth_5 = sum(a[1] for a in asks[:5])
        
        total_depth = features.bid_depth_5 + features.ask_depth_5
        if total_depth > 0:
            features.depth_imbalance_5 = (features.bid_depth_5 - features.ask_depth_5) / total_depth
        
        features.bid_levels_count = len(bids)
        features.ask_levels_count = len(asks)
        
        return features
    
    def _calculate_micro_price(self, symbol: str, bid: float, ask: float) -> float:
        """
        Calculate micro-price using order book data if available.
        
        Micro-price = (bid * ask_size + ask * bid_size) / (bid_size + ask_size)
        """
        # Try to use stored micro-price from order book
        if hasattr(self, '_last_micro_price') and symbol in self._last_micro_price:
            return self._last_micro_price[symbol]
        
        # Fall back to mid-price
        if bid > 0 and ask > 0:
            return (bid + ask) / 2
        return 0.0
    
    def _calculate_vwap(self, symbol: str, current_time: float, window_ms: int) -> float:
        """
        Calculate Volume Weighted Average Price over window.
        
        VWAP = Σ(Price × Volume) / Σ(Volume)
        """
        history = self._tick_history.get(symbol, [])
        if not history:
            return 0.0
        
        cutoff_time = current_time - window_ms
        
        total_pv = 0.0  # Price * Volume
        total_v = 0.0   # Volume
        
        for tick in history:
            if tick['timestamp'] >= cutoff_time:
                total_pv += tick['price'] * tick['volume']
                total_v += tick['volume']
        
        if total_v > 0:
            return total_pv / total_v
        return 0.0
    
    def _calculate_twap(self, symbol: str, current_time: float, window_ms: int) -> float:
        """
        Calculate Time Weighted Average Price over window.
        
        TWAP = Σ(Price × ΔTime) / Σ(ΔTime)
        For tick data, we use simple average as approximation.
        """
        history = self._tick_history.get(symbol, [])
        if not history:
            return 0.0
        
        cutoff_time = current_time - window_ms
        prices = []
        
        for tick in history:
            if tick['timestamp'] >= cutoff_time:
                prices.append(tick['price'])
        
        if prices:
            return sum(prices) / len(prices)
        return 0.0
    
    def _calculate_volatility(self, symbol: str, current_time: float, window_ms: int) -> float:
        """
        Calculate annualized volatility from returns in window.
        """
        history = self._tick_history.get(symbol, [])
        if len(history) < 2:
            return 0.0
        
        cutoff_time = current_time - window_ms
        prices = []
        
        for tick in history:
            if tick['timestamp'] >= cutoff_time:
                prices.append(tick['price'])
        
        if len(prices) < 2:
            return 0.0
        
        # Calculate log returns
        returns = []
        for i in range(1, len(prices)):
            if prices[i-1] > 0:
                ret = math.log(prices[i] / prices[i-1])
                returns.append(ret)
        
        if not returns:
            return 0.0
        
        # Standard deviation of returns
        mean_return = sum(returns) / len(returns)
        variance = sum((r - mean_return) ** 2 for r in returns) / len(returns)
        std_dev = math.sqrt(variance)
        
        # Annualize (assuming ~500 ticks per minute as approximation)
        # Scaling factor: sqrt(ticks_per_year)
        ticks_per_year = 500 * 60 * 24 * 365
        annualized = std_dev * math.sqrt(ticks_per_year)
        
        return annualized * 100  # Return as percentage
    
    def _calculate_return(self, symbol: str, current_time: float, window_ms: int) -> float:
        """Calculate return over window."""
        history = self._tick_history.get(symbol, [])
        if not history:
            return 0.0
        
        cutoff_time = current_time - window_ms
        
        # Find first tick in window and last tick
        first_price = None
        last_price = None
        
        for tick in history:
            if tick['timestamp'] >= cutoff_time:
                if first_price is None:
                    first_price = tick['price']
                last_price = tick['price']
        
        if first_price and last_price and first_price > 0:
            return (last_price - first_price) / first_price * 100
        return 0.0
    
    def _calculate_relative_volume(self, symbol: str, current_time: float) -> float:
        """Calculate current volume relative to average."""
        history = self._tick_history.get(symbol, [])
        if len(history) < 10:
            return 1.0
        
        # Compare last 10 ticks to average of last 100
        recent_volume = sum(t['volume'] for t in list(history)[-10:])
        all_volume = sum(t['volume'] for t in list(history)[-100:])
        
        if all_volume > 0:
            avg_volume = all_volume / min(100, len(history))
            recent_avg = recent_volume / 10
            return recent_avg / avg_volume
        return 1.0
    
    def _calculate_level_vwap(self, levels: List[Tuple[float, float]]) -> float:
        """Calculate VWAP across order book levels."""
        if not levels:
            return 0.0
        
        total_pq = sum(price * qty for price, qty in levels)
        total_q = sum(qty for _, qty in levels)
        
        if total_q > 0:
            return total_pq / total_q
        return 0.0
    
    def get_feature_vector(self, symbol: str) -> Optional[List[float]]:
        """
        Get latest features as a vector for ML model input.
        
        Returns None if insufficient data.
        """
        history = self._tick_history.get(symbol, [])
        if not history:
            return None
        
        last_tick = history[-1]
        features = self.add_tick(
            symbol=symbol,
            price=last_tick['price'],
            bid=last_tick['bid'],
            ask=last_tick['ask'],
            volume=last_tick['volume'],
            timestamp=last_tick['timestamp'],
        )
        
        # Return normalized feature vector
        return [
            features.price_vs_vwap_1m / 10,  # Normalize percentage
            features.price_vs_twap_1m / 10,
            features.spread_bps / 100,
            features.volatility_1m / 100,
            features.return_1s / 10,
            features.return_5s / 10,
            features.return_1m / 10,
            features.relative_volume - 1,  # Center around 0
        ]
    
    def get_history_dataframe(self, symbol: str, window_ms: int = None):
        """
        Get tick history as pandas DataFrame.
        
        Only available if pandas is installed.
        """
        if not PANDAS_AVAILABLE:
            logger.warning("Pandas not available for DataFrame conversion")
            return None
        
        history = self._tick_history.get(symbol, [])
        if not history:
            return None
        
        df = pd.DataFrame(list(history))
        
        if window_ms:
            current_time = time.time() * 1000
            df = df[df['timestamp'] >= current_time - window_ms]
        
        return df


# Factory function
def create_feature_engine(
    symbols: List[str] = None,
    max_history: int = 100000,
) -> FeatureEngine:
    """Create and return a FeatureEngine instance."""
    return FeatureEngine(symbols=symbols, max_history=max_history)
