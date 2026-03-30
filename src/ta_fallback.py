"""
Technical Analysis Fallback Module

Provides technical analysis signals when the primary 1-second latency window
yields no fills. Uses micro-RSI and momentum detection for early entry opportunities.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, List, Callable, TYPE_CHECKING
from collections import deque
from pathlib import Path

if TYPE_CHECKING:
    from oracle import BinanceOracle

logger = logging.getLogger(__name__)


@dataclass
class Candle:
    """Represents a single candlestick."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    
    @property
    def is_bullish(self) -> bool:
        """Returns True if candle closed higher than it opened."""
        return self.close > self.open
    
    @property
    def body_size(self) -> float:
        """Returns the absolute size of the candle body."""
        return abs(self.close - self.open)
    
    @property
    def upper_wick(self) -> float:
        """Returns the size of the upper wick."""
        return self.high - max(self.open, self.close)
    
    @property
    def lower_wick(self) -> float:
        """Returns the size of the lower wick."""
        return min(self.open, self.close) - self.low


@dataclass
class MomentumSignal:
    """Represents a detected momentum signal."""
    symbol: str
    timestamp: datetime
    signal_type: str  # "bullish_divergence", "oversold_bounce", "momentum_shift", "overbought_reversal"
    rsi: float
    price: float
    confidence: float  # 0.0 to 1.0
    metadata: dict = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        """Convert signal to dictionary for JSON serialization."""
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "signal_type": self.signal_type,
            "rsi": self.rsi,
            "price": self.price,
            "confidence": self.confidence,
            "metadata": self.metadata,
        }


def _calculate_rsi(closes: List[float], period: int = 14) -> float:
    """
    Calculate RSI (Relative Strength Index) from closing prices.
    
    RSI = 100 - (100 / (1 + RS))
    RS = Average Gain / Average Loss over period
    
    Args:
        closes: List of closing prices (oldest to newest)
        period: RSI calculation period
        
    Returns:
        RSI value between 0 and 100
    """
    if len(closes) < period + 1:
        return 50.0  # Neutral when insufficient data
    
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    
    if avg_loss == 0:
        return 100.0
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


class TechnicalAnalyzer:
    """
    Technical analysis module for momentum detection and early entry signals.
    
    Acts as a fallback when the primary 1-second latency window yields no fills.
    Uses micro-RSI for faster signal detection and logs momentum shifts.
    """
    
    # RSI thresholds
    RSI_OVERSOLD = 30.0
    RSI_OVERBOUGHT = 70.0
    RSI_EXTREME_OVERSOLD = 20.0
    RSI_EXTREME_OVERBOUGHT = 80.0
    
    def __init__(
        self,
        oracle: "BinanceOracle",
        candle_interval_seconds: int = 30,
        rsi_period: int = 14,
        heuristics_file: str = "data/ta_heuristics.json"
    ):
        """
        Initialize the Technical Analyzer.
        
        Args:
            oracle: BinanceOracle instance for price data
            candle_interval_seconds: Candle aggregation interval
            rsi_period: Standard RSI calculation period
            heuristics_file: Path to heuristics storage file
        """
        self._oracle = oracle
        self._candle_interval = candle_interval_seconds
        self._rsi_period = rsi_period
        self._heuristics_file = Path(heuristics_file)
        
        # Candle storage per symbol (deque with max 100 candles)
        self._candles: dict[str, deque[Candle]] = {
            "BTC": deque(maxlen=100),
            "ETH": deque(maxlen=100),
        }
        
        # Current building candle per symbol
        self._building_candle: dict[str, Optional[Candle]] = {
            "BTC": None,
            "ETH": None,
        }
        
        # RSI cache
        self._rsi: dict[str, float] = {}
        self._micro_rsi: dict[str, float] = {}
        
        # Previous RSI for divergence detection
        self._prev_rsi: dict[str, float] = {}
        self._prev_price: dict[str, float] = {}
        
        # Heuristics storage
        self._heuristics: dict = {
            "signals": [],
            "stats": {}
        }
        
        # Running state
        self._running = False
        
        # Load existing heuristics
        self.load_heuristics()
    
    async def start(self):
        """Start candle aggregation from oracle ticks."""
        self._running = True
        logger.info(f"Starting TA analyzer with {self._candle_interval}s candles")
        
        while self._running:
            try:
                await self._aggregate_candles()
                await asyncio.sleep(1)  # Check every second
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in candle aggregation: {e}")
                await asyncio.sleep(1)
        
        # Save heuristics on shutdown
        self.save_heuristics()
    
    async def stop(self):
        """Stop the analyzer."""
        self._running = False
        self.save_heuristics()
        logger.info("TA analyzer stopped")
    
    async def _aggregate_candles(self):
        """Aggregate tick data into candles."""
        now = datetime.now(timezone.utc)
        
        for symbol in ["BTC", "ETH"]:
            price_data = self._oracle.get_price(symbol)
            if price_data is None:
                continue
            
            price = price_data.price
            building = self._building_candle[symbol]
            
            if building is None:
                # Start new candle
                self._building_candle[symbol] = Candle(
                    timestamp=now,
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    volume=0.0,
                )
            else:
                # Update building candle
                building.high = max(building.high, price)
                building.low = min(building.low, price)
                building.close = price
                
                # Check if candle is complete
                elapsed = (now - building.timestamp).total_seconds()
                if elapsed >= self._candle_interval:
                    # Finalize candle
                    self._candles[symbol].append(building)
                    
                    # Update RSI values
                    self._update_rsi(symbol)
                    
                    logger.debug(
                        f"{symbol} candle closed: O={building.open:.2f} "
                        f"H={building.high:.2f} L={building.low:.2f} "
                        f"C={building.close:.2f} RSI={self._rsi.get(symbol, 50):.1f}"
                    )
                    
                    # Start new candle
                    self._building_candle[symbol] = Candle(
                        timestamp=now,
                        open=price,
                        high=price,
                        low=price,
                        close=price,
                        volume=0.0,
                    )
    
    def _update_rsi(self, symbol: str):
        """Update RSI values for a symbol."""
        # Store previous values for divergence detection
        if symbol in self._rsi:
            self._prev_rsi[symbol] = self._rsi[symbol]
        if symbol in self._candles and self._candles[symbol]:
            candles = self._candles[symbol]
            if len(candles) >= 2:
                self._prev_price[symbol] = candles[-2].close
        
        # Calculate standard RSI
        self._rsi[symbol] = self.calculate_rsi(symbol)
        
        # Calculate micro RSI
        self._micro_rsi[symbol] = self.get_micro_rsi(symbol) or 50.0
    
    def calculate_rsi(self, symbol: str) -> Optional[float]:
        """
        Calculate RSI (Relative Strength Index) from candles.
        
        Args:
            symbol: BTC or ETH
            
        Returns:
            RSI value or None if insufficient data
        """
        candles = self._candles.get(symbol)
        if not candles or len(candles) < self._rsi_period + 1:
            return None
        
        closes = [c.close for c in candles]
        return _calculate_rsi(closes, self._rsi_period)
    
    def get_micro_rsi(self, symbol: str, window: int = 5) -> Optional[float]:
        """
        Calculate micro-RSI using smaller window for faster signals.
        
        Args:
            symbol: BTC or ETH
            window: Micro-RSI period (default 5)
            
        Returns:
            Micro-RSI value or None if insufficient data
        """
        candles = self._candles.get(symbol)
        if not candles or len(candles) < window + 1:
            return None
        
        closes = [c.close for c in candles]
        return _calculate_rsi(closes, window)
    
    def detect_momentum_shift(self, symbol: str) -> Optional[MomentumSignal]:
        """
        Detect momentum shifts based on:
        - RSI crossing 30 (oversold bounce)
        - RSI crossing 70 (overbought reversal)
        - RSI divergence from price
        - Micro-RSI vs standard RSI divergence
        
        Args:
            symbol: BTC or ETH
            
        Returns:
            MomentumSignal if detected, None otherwise
        """
        rsi = self._rsi.get(symbol)
        micro_rsi = self._micro_rsi.get(symbol)
        prev_rsi = self._prev_rsi.get(symbol)
        
        if rsi is None:
            return None
        
        candles = self._candles.get(symbol)
        if not candles:
            return None
        
        current_price = candles[-1].close
        now = datetime.now(timezone.utc)
        
        # Check for oversold bounce
        if prev_rsi is not None and prev_rsi < self.RSI_OVERSOLD <= rsi:
            confidence = min(1.0, (self.RSI_OVERSOLD - prev_rsi) / 10)
            signal = MomentumSignal(
                symbol=symbol,
                timestamp=now,
                signal_type="oversold_bounce",
                rsi=rsi,
                price=current_price,
                confidence=confidence,
                metadata={
                    "prev_rsi": prev_rsi,
                    "micro_rsi": micro_rsi,
                    "direction": "bullish",
                }
            )
            logger.info(f"Momentum signal: {symbol} oversold bounce RSI {prev_rsi:.1f} -> {rsi:.1f}")
            return signal
        
        # Check for overbought reversal
        if prev_rsi is not None and prev_rsi > self.RSI_OVERBOUGHT >= rsi:
            confidence = min(1.0, (prev_rsi - self.RSI_OVERBOUGHT) / 10)
            signal = MomentumSignal(
                symbol=symbol,
                timestamp=now,
                signal_type="overbought_reversal",
                rsi=rsi,
                price=current_price,
                confidence=confidence,
                metadata={
                    "prev_rsi": prev_rsi,
                    "micro_rsi": micro_rsi,
                    "direction": "bearish",
                }
            )
            logger.info(f"Momentum signal: {symbol} overbought reversal RSI {prev_rsi:.1f} -> {rsi:.1f}")
            return signal
        
        # Check for micro-RSI divergence (early signal)
        if micro_rsi is not None and rsi is not None:
            divergence = micro_rsi - rsi
            
            # Bullish divergence: micro-RSI rising faster than standard RSI
            if divergence > 10 and rsi < 50:
                signal = MomentumSignal(
                    symbol=symbol,
                    timestamp=now,
                    signal_type="bullish_divergence",
                    rsi=rsi,
                    price=current_price,
                    confidence=min(1.0, divergence / 20),
                    metadata={
                        "micro_rsi": micro_rsi,
                        "divergence": divergence,
                        "direction": "bullish",
                    }
                )
                logger.info(f"Momentum signal: {symbol} bullish divergence (micro={micro_rsi:.1f}, std={rsi:.1f})")
                return signal
            
            # Bearish divergence: micro-RSI falling faster than standard RSI
            if divergence < -10 and rsi > 50:
                signal = MomentumSignal(
                    symbol=symbol,
                    timestamp=now,
                    signal_type="bearish_divergence",
                    rsi=rsi,
                    price=current_price,
                    confidence=min(1.0, abs(divergence) / 20),
                    metadata={
                        "micro_rsi": micro_rsi,
                        "divergence": divergence,
                        "direction": "bearish",
                    }
                )
                logger.info(f"Momentum signal: {symbol} bearish divergence (micro={micro_rsi:.1f}, std={rsi:.1f})")
                return signal
        
        # Check for general momentum shift
        if prev_rsi is not None:
            rsi_change = rsi - prev_rsi
            if abs(rsi_change) > 5:  # Significant RSI movement
                direction = "bullish" if rsi_change > 0 else "bearish"
                signal = MomentumSignal(
                    symbol=symbol,
                    timestamp=now,
                    signal_type="momentum_shift",
                    rsi=rsi,
                    price=current_price,
                    confidence=min(1.0, abs(rsi_change) / 15),
                    metadata={
                        "prev_rsi": prev_rsi,
                        "rsi_change": rsi_change,
                        "micro_rsi": micro_rsi,
                        "direction": direction,
                    }
                )
                logger.info(f"Momentum signal: {symbol} shift {direction} (RSI delta={rsi_change:.1f})")
                return signal
        
        return None
    
    def get_early_entry_signal(self, symbol: str) -> Optional[MomentumSignal]:
        """
        Generate early entry signal when:
        - Micro-RSI shows momentum shift
        - Price action confirms direction
        - Volume supports move (if available)
        
        Args:
            symbol: BTC or ETH
            
        Returns:
            MomentumSignal for early entry or None
        """
        micro_rsi = self._micro_rsi.get(symbol)
        rsi = self._rsi.get(symbol)
        
        if micro_rsi is None:
            return None
        
        candles = self._candles.get(symbol)
        if not candles or len(candles) < 3:
            return None
        
        current_candle = candles[-1]
        prev_candle = candles[-2]
        current_price = current_candle.close
        now = datetime.now(timezone.utc)
        
        # Check for extreme oversold with bullish price action
        if micro_rsi < self.RSI_EXTREME_OVERSOLD and current_candle.is_bullish:
            # Confirm with bullish engulfing or hammer pattern
            if current_candle.body_size > prev_candle.body_size * 0.5:
                confidence = min(1.0, (self.RSI_EXTREME_OVERSOLD - micro_rsi) / 10 + 0.3)
                return MomentumSignal(
                    symbol=symbol,
                    timestamp=now,
                    signal_type="early_entry_long",
                    rsi=rsi or micro_rsi,
                    price=current_price,
                    confidence=confidence,
                    metadata={
                        "micro_rsi": micro_rsi,
                        "candle_pattern": "bullish_reversal",
                        "direction": "bullish",
                    }
                )
        
        # Check for extreme overbought with bearish price action
        if micro_rsi > self.RSI_EXTREME_OVERBOUGHT and not current_candle.is_bullish:
            # Confirm with bearish engulfing or shooting star
            if current_candle.body_size > prev_candle.body_size * 0.5:
                confidence = min(1.0, (micro_rsi - self.RSI_EXTREME_OVERBOUGHT) / 10 + 0.3)
                return MomentumSignal(
                    symbol=symbol,
                    timestamp=now,
                    signal_type="early_entry_short",
                    rsi=rsi or micro_rsi,
                    price=current_price,
                    confidence=confidence,
                    metadata={
                        "micro_rsi": micro_rsi,
                        "candle_pattern": "bearish_reversal",
                        "direction": "bearish",
                    }
                )
        
        # Check for momentum divergence early entry
        if rsi is not None and micro_rsi is not None:
            divergence = micro_rsi - rsi
            
            # Strong bullish setup
            if (divergence > 15 and 
                micro_rsi < 40 and 
                current_candle.is_bullish and
                current_candle.close > prev_candle.high):
                return MomentumSignal(
                    symbol=symbol,
                    timestamp=now,
                    signal_type="early_entry_long",
                    rsi=rsi,
                    price=current_price,
                    confidence=min(1.0, divergence / 25 + 0.2),
                    metadata={
                        "micro_rsi": micro_rsi,
                        "divergence": divergence,
                        "breakout": True,
                        "direction": "bullish",
                    }
                )
            
            # Strong bearish setup
            if (divergence < -15 and 
                micro_rsi > 60 and 
                not current_candle.is_bullish and
                current_candle.close < prev_candle.low):
                return MomentumSignal(
                    symbol=symbol,
                    timestamp=now,
                    signal_type="early_entry_short",
                    rsi=rsi,
                    price=current_price,
                    confidence=min(1.0, abs(divergence) / 25 + 0.2),
                    metadata={
                        "micro_rsi": micro_rsi,
                        "divergence": divergence,
                        "breakdown": True,
                        "direction": "bearish",
                    }
                )
        
        return None
    
    def record_heuristic(self, signal: MomentumSignal, outcome: str, profit: float):
        """
        Record signal outcome for future optimization.
        
        Args:
            signal: The original momentum signal
            outcome: "win" or "loss"
            profit: Profit/loss percentage
        """
        record = {
            "timestamp": signal.timestamp.isoformat(),
            "symbol": signal.symbol,
            "signal_type": signal.signal_type,
            "rsi": signal.rsi,
            "entry_price": signal.price,
            "confidence": signal.confidence,
            "outcome": outcome,
            "profit_pct": profit,
        }
        
        self._heuristics["signals"].append(record)
        
        # Update stats
        signal_type = signal.signal_type
        if signal_type not in self._heuristics["stats"]:
            self._heuristics["stats"][signal_type] = {"wins": 0, "losses": 0}
        
        if outcome == "win":
            self._heuristics["stats"][signal_type]["wins"] += 1
        else:
            self._heuristics["stats"][signal_type]["losses"] += 1
        
        logger.info(f"Recorded heuristic: {signal_type} -> {outcome} ({profit:+.2f}%)")
        
        # Auto-save after recording
        self.save_heuristics()
    
    def load_heuristics(self):
        """Load historical heuristics from file."""
        if not self._heuristics_file.exists():
            logger.info("No existing heuristics file, starting fresh")
            return
        
        try:
            with open(self._heuristics_file, "r") as f:
                self._heuristics = json.load(f)
            
            total_signals = len(self._heuristics.get("signals", []))
            logger.info(f"Loaded {total_signals} historical signals from heuristics")
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse heuristics file: {e}")
        except Exception as e:
            logger.error(f"Failed to load heuristics: {e}")
    
    def save_heuristics(self):
        """Save heuristics to file."""
        try:
            # Ensure directory exists
            self._heuristics_file.parent.mkdir(parents=True, exist_ok=True)
            
            with open(self._heuristics_file, "w") as f:
                json.dump(self._heuristics, f, indent=2)
            
            logger.debug(f"Saved heuristics to {self._heuristics_file}")
            
        except Exception as e:
            logger.error(f"Failed to save heuristics: {e}")
    
    def get_success_rate(self, signal_type: str) -> float:
        """
        Calculate historical success rate for signal type.
        
        Args:
            signal_type: Type of signal to check
            
        Returns:
            Success rate as float between 0.0 and 1.0
        """
        stats = self._heuristics.get("stats", {}).get(signal_type)
        if not stats:
            return 0.5  # Default to neutral if no data
        
        wins = stats.get("wins", 0)
        losses = stats.get("losses", 0)
        total = wins + losses
        
        if total == 0:
            return 0.5
        
        return wins / total
    
    def get_all_stats(self) -> dict:
        """Get all signal statistics."""
        result = {}
        for signal_type, stats in self._heuristics.get("stats", {}).items():
            wins = stats.get("wins", 0)
            losses = stats.get("losses", 0)
            total = wins + losses
            result[signal_type] = {
                "wins": wins,
                "losses": losses,
                "total": total,
                "win_rate": wins / total if total > 0 else 0.5,
            }
        return result
    
    async def run_background_analysis(self, callback: Callable[[MomentumSignal], None]):
        """
        Continuously analyze and emit momentum signals.
        
        Args:
            callback: Function to call when a signal is detected
        """
        logger.info("Starting background TA analysis")
        
        # Cooldown tracking to avoid signal spam
        last_signal_time: dict[str, datetime] = {}
        signal_cooldown_seconds = 60
        
        while self._running:
            try:
                for symbol in ["BTC", "ETH"]:
                    # Check cooldown
                    last = last_signal_time.get(symbol)
                    if last:
                        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
                        if elapsed < signal_cooldown_seconds:
                            continue
                    
                    # Check for momentum shift
                    signal = self.detect_momentum_shift(symbol)
                    if signal:
                        callback(signal)
                        last_signal_time[symbol] = datetime.now(timezone.utc)
                        continue
                    
                    # Check for early entry
                    signal = self.get_early_entry_signal(symbol)
                    if signal:
                        callback(signal)
                        last_signal_time[symbol] = datetime.now(timezone.utc)
                
                await asyncio.sleep(self._candle_interval / 2)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in background analysis: {e}")
                await asyncio.sleep(5)
        
        logger.info("Background TA analysis stopped")
    
    def get_current_state(self, symbol: str) -> dict:
        """
        Get current TA state for a symbol.
        
        Args:
            symbol: BTC or ETH
            
        Returns:
            Dictionary with current TA metrics
        """
        candles = self._candles.get(symbol, deque())
        
        return {
            "symbol": symbol,
            "candle_count": len(candles),
            "rsi": self._rsi.get(symbol),
            "micro_rsi": self._micro_rsi.get(symbol),
            "prev_rsi": self._prev_rsi.get(symbol),
            "last_candle": {
                "open": candles[-1].open if candles else None,
                "high": candles[-1].high if candles else None,
                "low": candles[-1].low if candles else None,
                "close": candles[-1].close if candles else None,
                "bullish": candles[-1].is_bullish if candles else None,
            } if candles else None,
            "building_candle": {
                "open": self._building_candle[symbol].open,
                "high": self._building_candle[symbol].high,
                "low": self._building_candle[symbol].low,
                "close": self._building_candle[symbol].close,
            } if self._building_candle.get(symbol) else None,
        }


# Convenience function for standalone usage
async def run_analyzer(oracle: "BinanceOracle", duration_seconds: Optional[int] = None):
    """
    Run analyzer as standalone service.
    
    Args:
        oracle: BinanceOracle instance
        duration_seconds: Optional duration to run (None for indefinite)
    """
    analyzer = TechnicalAnalyzer(oracle)
    
    def signal_callback(signal: MomentumSignal):
        print(f"\n{'='*50}")
        print(f"SIGNAL: {signal.signal_type}")
        print(f"Symbol: {signal.symbol}")
        print(f"Price: ${signal.price:,.2f}")
        print(f"RSI: {signal.rsi:.1f}")
        print(f"Confidence: {signal.confidence:.1%}")
        print(f"Direction: {signal.metadata.get('direction', 'unknown')}")
        print(f"{'='*50}\n")
    
    try:
        # Start analyzer tasks
        aggregation_task = asyncio.create_task(analyzer.start())
        analysis_task = asyncio.create_task(analyzer.run_background_analysis(signal_callback))
        
        if duration_seconds:
            await asyncio.sleep(duration_seconds)
            await analyzer.stop()
            aggregation_task.cancel()
            analysis_task.cancel()
        else:
            await asyncio.gather(aggregation_task, analysis_task)
            
    except asyncio.CancelledError:
        await analyzer.stop()


if __name__ == "__main__":
    # Standalone execution for testing
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    
    from oracle import BinanceOracle
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )
    
    async def main():
        oracle = BinanceOracle()
        
        # Start oracle and analyzer together
        oracle_task = asyncio.create_task(oracle.connect())
        
        # Wait for oracle to connect
        await asyncio.sleep(3)
        
        if oracle.is_connected:
            print("Oracle connected, starting TA analyzer...")
            await run_analyzer(oracle)
        else:
            print("Oracle failed to connect")
        
        await oracle.disconnect()
        oracle_task.cancel()
    
    print("Starting Technical Analyzer (Ctrl+C to stop)...")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nAnalyzer stopped.")
