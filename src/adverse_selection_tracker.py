"""
Adverse Selection Tracker - Post-Trade Mark-to-Market Analysis.

Phase 2 - Task 61: Track theoretical "mark-to-market" PnL of mock trades
1 minute after fill to prove if grid is being run over by toxic flow.

Educational purpose only - paper trading simulation.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, List, Callable
from collections import deque
from enum import Enum
import statistics

logger = logging.getLogger(__name__)


class FlowToxicity(Enum):
    """Classification of trade flow toxicity."""
    BENIGN = "benign"       # Flow that doesn't adversely select us
    MODERATE = "moderate"   # Some adverse selection
    TOXIC = "toxic"         # Significant adverse selection
    HIGHLY_TOXIC = "highly_toxic"  # Extreme adverse selection


@dataclass
class FillRecord:
    """Records a fill and its subsequent price movement."""
    fill_id: str
    order_id: str
    symbol: str
    side: str  # 'buy' or 'sell'
    fill_price: float
    quantity: float
    fill_timestamp: float
    
    # Oracle prices at various intervals
    oracle_price_at_fill: float = 0.0
    oracle_price_10s: Optional[float] = None
    oracle_price_30s: Optional[float] = None
    oracle_price_1m: Optional[float] = None
    oracle_price_5m: Optional[float] = None
    
    # Mark-to-market PnL at various intervals (per share)
    mtm_10s: Optional[float] = None
    mtm_30s: Optional[float] = None
    mtm_1m: Optional[float] = None
    mtm_5m: Optional[float] = None
    
    # Adverse selection metrics
    adverse_selection_bps: Optional[float] = None
    toxicity_classification: FlowToxicity = FlowToxicity.BENIGN
    is_complete: bool = False
    
    def to_dict(self) -> dict:
        return {
            'fill_id': self.fill_id,
            'order_id': self.order_id,
            'symbol': self.symbol,
            'side': self.side,
            'fill_price': self.fill_price,
            'quantity': self.quantity,
            'fill_timestamp': self.fill_timestamp,
            'oracle_price_at_fill': self.oracle_price_at_fill,
            'oracle_price_10s': self.oracle_price_10s,
            'oracle_price_30s': self.oracle_price_30s,
            'oracle_price_1m': self.oracle_price_1m,
            'oracle_price_5m': self.oracle_price_5m,
            'mtm_10s': self.mtm_10s,
            'mtm_30s': self.mtm_30s,
            'mtm_1m': self.mtm_1m,
            'mtm_5m': self.mtm_5m,
            'adverse_selection_bps': self.adverse_selection_bps,
            'toxicity': self.toxicity_classification.value,
            'is_complete': self.is_complete,
        }


@dataclass
class AdverseSelectionStats:
    """Aggregate adverse selection statistics."""
    total_fills: int = 0
    completed_fills: int = 0
    
    # Average MTM by interval
    avg_mtm_10s: float = 0.0
    avg_mtm_30s: float = 0.0
    avg_mtm_1m: float = 0.0
    avg_mtm_5m: float = 0.0
    
    # Adverse selection by side
    buy_avg_mtm_1m: float = 0.0
    sell_avg_mtm_1m: float = 0.0
    
    # Toxicity distribution
    benign_count: int = 0
    moderate_count: int = 0
    toxic_count: int = 0
    highly_toxic_count: int = 0
    
    # Win rates (MTM > 0)
    win_rate_10s: float = 0.0
    win_rate_30s: float = 0.0
    win_rate_1m: float = 0.0
    
    # Total PnL impact
    total_adverse_selection_pnl: float = 0.0
    
    def to_dict(self) -> dict:
        return {
            'total_fills': self.total_fills,
            'completed_fills': self.completed_fills,
            'avg_mtm_10s': self.avg_mtm_10s,
            'avg_mtm_30s': self.avg_mtm_30s,
            'avg_mtm_1m': self.avg_mtm_1m,
            'avg_mtm_5m': self.avg_mtm_5m,
            'buy_avg_mtm_1m': self.buy_avg_mtm_1m,
            'sell_avg_mtm_1m': self.sell_avg_mtm_1m,
            'benign_pct': self.benign_count / max(1, self.completed_fills) * 100,
            'moderate_pct': self.moderate_count / max(1, self.completed_fills) * 100,
            'toxic_pct': self.toxic_count / max(1, self.completed_fills) * 100,
            'highly_toxic_pct': self.highly_toxic_count / max(1, self.completed_fills) * 100,
            'win_rate_10s': self.win_rate_10s,
            'win_rate_30s': self.win_rate_30s,
            'win_rate_1m': self.win_rate_1m,
            'total_adverse_selection_pnl': self.total_adverse_selection_pnl,
        }


class AdverseSelectionTracker:
    """
    Tracks adverse selection by monitoring price movement after fills.
    
    Key metrics:
    - Mark-to-market PnL 10s, 30s, 1m, 5m after fill
    - Classification of flow toxicity
    - Aggregate statistics on adverse selection
    
    Positive MTM = good fill (price moved in our favor)
    Negative MTM = adverse selection (price moved against us)
    """
    
    # Time intervals for MTM calculation (milliseconds)
    INTERVAL_10S = 10_000
    INTERVAL_30S = 30_000
    INTERVAL_1M = 60_000
    INTERVAL_5M = 300_000
    
    # Toxicity thresholds (in basis points of adverse price movement)
    BENIGN_THRESHOLD = 5      # < 5 bps
    MODERATE_THRESHOLD = 15   # 5-15 bps
    TOXIC_THRESHOLD = 30      # 15-30 bps
    # > 30 bps = highly toxic
    
    def __init__(
        self,
        get_oracle_price: Callable[[str], float],
        on_fill_complete: Optional[Callable] = None,
        on_toxic_flow_detected: Optional[Callable] = None,
        max_pending_fills: int = 10000,
    ):
        """
        Initialize adverse selection tracker.
        
        Args:
            get_oracle_price: Callback to get current oracle price for a symbol
            on_fill_complete: Callback when fill MTM analysis is complete
            on_toxic_flow_detected: Callback when toxic flow is detected
            max_pending_fills: Maximum fills to track concurrently
        """
        self.get_oracle_price = get_oracle_price
        self.on_fill_complete = on_fill_complete
        self.on_toxic_flow_detected = on_toxic_flow_detected
        self.max_pending_fills = max_pending_fills
        
        # Fill storage
        self._pending_fills: Dict[str, FillRecord] = {}
        self._completed_fills: deque = deque(maxlen=10000)
        
        # Background task
        self._running = False
        self._task: Optional[asyncio.Task] = None
        
        # Statistics
        self._stats = AdverseSelectionStats()
    
    async def start(self) -> None:
        """Start the background MTM tracking loop."""
        self._running = True
        self._task = asyncio.create_task(self._tracking_loop())
        logger.info("Adverse selection tracker started")
    
    async def stop(self) -> None:
        """Stop the tracker."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Adverse selection tracker stopped")
    
    def record_fill(
        self,
        fill_id: str,
        order_id: str,
        symbol: str,
        side: str,
        fill_price: float,
        quantity: float,
        oracle_price: Optional[float] = None,
    ) -> FillRecord:
        """
        Record a new fill for MTM tracking.
        
        Args:
            fill_id: Unique fill identifier
            order_id: Associated order ID
            symbol: Trading symbol
            side: 'buy' or 'sell'
            fill_price: Price at which fill occurred
            quantity: Fill quantity
            oracle_price: Oracle price at fill time (optional)
        
        Returns:
            FillRecord object
        """
        if oracle_price is None:
            oracle_price = self.get_oracle_price(symbol)
        
        record = FillRecord(
            fill_id=fill_id,
            order_id=order_id,
            symbol=symbol,
            side=side,
            fill_price=fill_price,
            quantity=quantity,
            fill_timestamp=time.time() * 1000,
            oracle_price_at_fill=oracle_price,
        )
        
        self._pending_fills[fill_id] = record
        self._stats.total_fills += 1
        
        # Cleanup if too many pending
        if len(self._pending_fills) > self.max_pending_fills:
            self._cleanup_old_fills()
        
        logger.debug(f"Recording fill {fill_id}: {side} {quantity}@{fill_price}")
        return record
    
    async def _tracking_loop(self) -> None:
        """Background loop to update MTM values."""
        while self._running:
            try:
                now = time.time() * 1000
                fills_to_complete = []
                
                for fill_id, record in list(self._pending_fills.items()):
                    elapsed = now - record.fill_timestamp
                    
                    # Update MTM at each interval
                    current_price = self.get_oracle_price(record.symbol)
                    
                    if elapsed >= self.INTERVAL_10S and record.oracle_price_10s is None:
                        record.oracle_price_10s = current_price
                        record.mtm_10s = self._calculate_mtm(record, current_price)
                    
                    if elapsed >= self.INTERVAL_30S and record.oracle_price_30s is None:
                        record.oracle_price_30s = current_price
                        record.mtm_30s = self._calculate_mtm(record, current_price)
                    
                    if elapsed >= self.INTERVAL_1M and record.oracle_price_1m is None:
                        record.oracle_price_1m = current_price
                        record.mtm_1m = self._calculate_mtm(record, current_price)
                        
                        # Calculate adverse selection at 1m
                        record.adverse_selection_bps = self._calculate_adverse_selection_bps(record)
                        record.toxicity_classification = self._classify_toxicity(record)
                        
                        # Check for toxic flow
                        if record.toxicity_classification in [FlowToxicity.TOXIC, FlowToxicity.HIGHLY_TOXIC]:
                            await self._handle_toxic_flow(record)
                    
                    if elapsed >= self.INTERVAL_5M and record.oracle_price_5m is None:
                        record.oracle_price_5m = current_price
                        record.mtm_5m = self._calculate_mtm(record, current_price)
                        record.is_complete = True
                        fills_to_complete.append(fill_id)
                
                # Move completed fills
                for fill_id in fills_to_complete:
                    record = self._pending_fills.pop(fill_id)
                    self._completed_fills.append(record)
                    self._stats.completed_fills += 1
                    self._update_stats(record)
                    
                    if self.on_fill_complete:
                        await self._safe_callback(self.on_fill_complete, record)
                
                await asyncio.sleep(1)  # Check every second
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in tracking loop: {e}")
                await asyncio.sleep(5)
    
    def _calculate_mtm(self, record: FillRecord, current_price: float) -> float:
        """
        Calculate mark-to-market PnL per share.
        
        For buys: current_price - fill_price (positive if price went up)
        For sells: fill_price - current_price (positive if price went down)
        """
        if record.side == 'buy':
            return current_price - record.fill_price
        else:
            return record.fill_price - current_price
    
    def _calculate_adverse_selection_bps(self, record: FillRecord) -> float:
        """
        Calculate adverse selection in basis points.
        
        Adverse selection = how much the market moved against us after fill.
        """
        if record.oracle_price_1m is None or record.oracle_price_at_fill == 0:
            return 0.0
        
        price_change = record.oracle_price_1m - record.oracle_price_at_fill
        
        # For buys, negative price change is adverse
        # For sells, positive price change is adverse
        if record.side == 'buy':
            adverse_movement = -price_change
        else:
            adverse_movement = price_change
        
        # Convert to basis points
        return (adverse_movement / record.oracle_price_at_fill) * 10000
    
    def _classify_toxicity(self, record: FillRecord) -> FlowToxicity:
        """Classify flow toxicity based on adverse selection."""
        if record.adverse_selection_bps is None:
            return FlowToxicity.BENIGN
        
        abs_adverse = abs(record.adverse_selection_bps)
        
        # Only count as adverse if MTM is negative
        if record.mtm_1m is not None and record.mtm_1m >= 0:
            return FlowToxicity.BENIGN
        
        if abs_adverse < self.BENIGN_THRESHOLD:
            return FlowToxicity.BENIGN
        elif abs_adverse < self.MODERATE_THRESHOLD:
            return FlowToxicity.MODERATE
        elif abs_adverse < self.TOXIC_THRESHOLD:
            return FlowToxicity.TOXIC
        else:
            return FlowToxicity.HIGHLY_TOXIC
    
    async def _handle_toxic_flow(self, record: FillRecord) -> None:
        """Handle detection of toxic flow."""
        logger.warning(
            f"Toxic flow detected: {record.symbol} {record.side} fill "
            f"adverse selection: {record.adverse_selection_bps:.1f} bps"
        )
        
        if self.on_toxic_flow_detected:
            await self._safe_callback(self.on_toxic_flow_detected, record)
    
    def _update_stats(self, record: FillRecord) -> None:
        """Update aggregate statistics."""
        completed = list(self._completed_fills)
        n = len(completed)
        
        if n == 0:
            return
        
        # Calculate averages
        mtm_10s = [r.mtm_10s for r in completed if r.mtm_10s is not None]
        mtm_30s = [r.mtm_30s for r in completed if r.mtm_30s is not None]
        mtm_1m = [r.mtm_1m for r in completed if r.mtm_1m is not None]
        mtm_5m = [r.mtm_5m for r in completed if r.mtm_5m is not None]
        
        if mtm_10s:
            self._stats.avg_mtm_10s = sum(mtm_10s) / len(mtm_10s)
        if mtm_30s:
            self._stats.avg_mtm_30s = sum(mtm_30s) / len(mtm_30s)
        if mtm_1m:
            self._stats.avg_mtm_1m = sum(mtm_1m) / len(mtm_1m)
        if mtm_5m:
            self._stats.avg_mtm_5m = sum(mtm_5m) / len(mtm_5m)
        
        # By side
        buy_mtm = [r.mtm_1m for r in completed if r.side == 'buy' and r.mtm_1m is not None]
        sell_mtm = [r.mtm_1m for r in completed if r.side == 'sell' and r.mtm_1m is not None]
        
        if buy_mtm:
            self._stats.buy_avg_mtm_1m = sum(buy_mtm) / len(buy_mtm)
        if sell_mtm:
            self._stats.sell_avg_mtm_1m = sum(sell_mtm) / len(sell_mtm)
        
        # Toxicity counts
        self._stats.benign_count = sum(1 for r in completed if r.toxicity_classification == FlowToxicity.BENIGN)
        self._stats.moderate_count = sum(1 for r in completed if r.toxicity_classification == FlowToxicity.MODERATE)
        self._stats.toxic_count = sum(1 for r in completed if r.toxicity_classification == FlowToxicity.TOXIC)
        self._stats.highly_toxic_count = sum(1 for r in completed if r.toxicity_classification == FlowToxicity.HIGHLY_TOXIC)
        
        # Win rates
        if mtm_10s:
            self._stats.win_rate_10s = sum(1 for m in mtm_10s if m > 0) / len(mtm_10s) * 100
        if mtm_30s:
            self._stats.win_rate_30s = sum(1 for m in mtm_30s if m > 0) / len(mtm_30s) * 100
        if mtm_1m:
            self._stats.win_rate_1m = sum(1 for m in mtm_1m if m > 0) / len(mtm_1m) * 100
        
        # Total PnL impact
        self._stats.total_adverse_selection_pnl = sum(
            (r.mtm_1m or 0) * r.quantity
            for r in completed
        )
    
    def _cleanup_old_fills(self) -> None:
        """Remove oldest pending fills if over limit."""
        if len(self._pending_fills) <= self.max_pending_fills:
            return
        
        # Sort by timestamp and remove oldest
        sorted_fills = sorted(
            self._pending_fills.items(),
            key=lambda x: x[1].fill_timestamp
        )
        
        to_remove = len(sorted_fills) - self.max_pending_fills
        for fill_id, record in sorted_fills[:to_remove]:
            record.is_complete = True
            self._completed_fills.append(record)
            del self._pending_fills[fill_id]
    
    async def _safe_callback(self, callback: Callable, data) -> None:
        """Execute callback safely."""
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(data)
            else:
                callback(data)
        except Exception as e:
            logger.error(f"Callback error: {e}")
    
    def get_fill(self, fill_id: str) -> Optional[FillRecord]:
        """Get a fill record by ID."""
        if fill_id in self._pending_fills:
            return self._pending_fills[fill_id]
        
        for record in self._completed_fills:
            if record.fill_id == fill_id:
                return record
        
        return None
    
    def get_pending_fills(self) -> List[FillRecord]:
        """Get all pending fill records."""
        return list(self._pending_fills.values())
    
    def get_completed_fills(self, limit: int = 100) -> List[FillRecord]:
        """Get completed fill records."""
        return list(self._completed_fills)[-limit:]
    
    def get_statistics(self) -> AdverseSelectionStats:
        """Get aggregate statistics."""
        return self._stats
    
    def is_flow_toxic(self, window_minutes: int = 5) -> bool:
        """
        Check if recent flow is toxic.
        
        Returns True if >30% of recent fills are toxic.
        """
        now = time.time() * 1000
        window_ms = window_minutes * 60 * 1000
        
        recent = [
            r for r in self._completed_fills
            if now - r.fill_timestamp <= window_ms
        ]
        
        if len(recent) < 5:
            return False
        
        toxic_count = sum(
            1 for r in recent
            if r.toxicity_classification in [FlowToxicity.TOXIC, FlowToxicity.HIGHLY_TOXIC]
        )
        
        return toxic_count / len(recent) > 0.30
    
    def get_toxicity_report(self) -> dict:
        """Generate a toxicity analysis report."""
        stats = self.get_statistics()
        
        return {
            'summary': {
                'total_fills_analyzed': stats.completed_fills,
                'avg_mtm_1m': stats.avg_mtm_1m,
                'win_rate_1m': stats.win_rate_1m,
                'total_pnl_impact': stats.total_adverse_selection_pnl,
            },
            'toxicity_distribution': {
                'benign': stats.benign_count,
                'moderate': stats.moderate_count,
                'toxic': stats.toxic_count,
                'highly_toxic': stats.highly_toxic_count,
            },
            'by_side': {
                'buy_avg_mtm': stats.buy_avg_mtm_1m,
                'sell_avg_mtm': stats.sell_avg_mtm_1m,
            },
            'intervals': {
                'mtm_10s': stats.avg_mtm_10s,
                'mtm_30s': stats.avg_mtm_30s,
                'mtm_1m': stats.avg_mtm_1m,
                'mtm_5m': stats.avg_mtm_5m,
            },
            'is_currently_toxic': self.is_flow_toxic(),
        }


# Factory function
def create_adverse_selection_tracker(
    get_oracle_price: Callable[[str], float],
    on_fill_complete: Optional[Callable] = None,
    on_toxic_flow_detected: Optional[Callable] = None,
) -> AdverseSelectionTracker:
    """Create and return an AdverseSelectionTracker instance."""
    return AdverseSelectionTracker(
        get_oracle_price=get_oracle_price,
        on_fill_complete=on_fill_complete,
        on_toxic_flow_detected=on_toxic_flow_detected,
    )
