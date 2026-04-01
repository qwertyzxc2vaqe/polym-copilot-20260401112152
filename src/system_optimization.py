"""
Memory and CPU Optimization Utilities.

Phase 2 - Task 86-87: tracemalloc monitoring and CPU affinity binding.

Educational purpose only - paper trading simulation.
"""

import asyncio
import logging
import os
import sys
import tracemalloc
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from collections import deque

logger = logging.getLogger(__name__)


@dataclass
class MemorySnapshot:
    """Memory usage snapshot."""
    timestamp: datetime
    current_bytes: int
    peak_bytes: int
    top_allocations: List[Tuple[str, int]]


class MemoryMonitor:
    """
    Memory monitoring using tracemalloc.
    
    Features:
    - Start/stop memory tracking
    - Snapshot comparisons
    - Top allocation analysis
    - Memory leak detection
    """
    
    def __init__(
        self,
        top_n: int = 10,
        history_size: int = 100,
    ):
        """
        Initialize memory monitor.
        
        Args:
            top_n: Number of top allocations to track
            history_size: Number of snapshots to keep
        """
        self.top_n = top_n
        self._snapshots: deque = deque(maxlen=history_size)
        self._is_tracking = False
        self._baseline_snapshot = None
    
    def start_tracking(self) -> None:
        """Start memory tracking."""
        if not self._is_tracking:
            tracemalloc.start()
            self._is_tracking = True
            self._baseline_snapshot = tracemalloc.take_snapshot()
            logger.info("Memory tracking started")
    
    def stop_tracking(self) -> None:
        """Stop memory tracking."""
        if self._is_tracking:
            tracemalloc.stop()
            self._is_tracking = False
            logger.info("Memory tracking stopped")
    
    def take_snapshot(self) -> MemorySnapshot:
        """Take memory snapshot."""
        if not self._is_tracking:
            self.start_tracking()
        
        current, peak = tracemalloc.get_traced_memory()
        snapshot = tracemalloc.take_snapshot()
        
        # Get top allocations
        top_stats = snapshot.statistics('lineno')[:self.top_n]
        top_allocations = [
            (str(stat.traceback), stat.size)
            for stat in top_stats
        ]
        
        mem_snapshot = MemorySnapshot(
            timestamp=datetime.now(),
            current_bytes=current,
            peak_bytes=peak,
            top_allocations=top_allocations,
        )
        
        self._snapshots.append(mem_snapshot)
        
        return mem_snapshot
    
    def get_memory_diff(self) -> Optional[Dict]:
        """Get memory difference from baseline."""
        if not self._baseline_snapshot or not self._is_tracking:
            return None
        
        current_snapshot = tracemalloc.take_snapshot()
        diff = current_snapshot.compare_to(self._baseline_snapshot, 'lineno')
        
        return {
            'top_increases': [
                {'location': str(stat.traceback), 'size_diff': stat.size_diff}
                for stat in diff[:self.top_n]
                if stat.size_diff > 0
            ],
            'total_diff': sum(stat.size_diff for stat in diff),
        }
    
    def get_current_usage(self) -> Dict:
        """Get current memory usage."""
        if not self._is_tracking:
            return {'error': 'Not tracking'}
        
        current, peak = tracemalloc.get_traced_memory()
        
        return {
            'current_mb': current / (1024 * 1024),
            'peak_mb': peak / (1024 * 1024),
            'current_bytes': current,
            'peak_bytes': peak,
        }
    
    def detect_leaks(
        self,
        threshold_mb: float = 10.0,
    ) -> List[Dict]:
        """
        Detect potential memory leaks.
        
        Args:
            threshold_mb: Growth threshold in MB
        
        Returns:
            List of potential leak locations
        """
        if len(self._snapshots) < 2:
            return []
        
        first = self._snapshots[0]
        last = self._snapshots[-1]
        
        growth = last.current_bytes - first.current_bytes
        growth_mb = growth / (1024 * 1024)
        
        if growth_mb < threshold_mb:
            return []
        
        leaks = []
        
        for location, size in last.top_allocations:
            leaks.append({
                'location': location,
                'size_mb': size / (1024 * 1024),
                'likely_leak': size > threshold_mb * 1024 * 1024 * 0.1,
            })
        
        return leaks
    
    def get_history(self) -> List[Dict]:
        """Get snapshot history."""
        return [
            {
                'timestamp': s.timestamp.isoformat(),
                'current_mb': s.current_bytes / (1024 * 1024),
                'peak_mb': s.peak_bytes / (1024 * 1024),
            }
            for s in self._snapshots
        ]


class CPUAffinityManager:
    """
    CPU affinity management for process pinning.
    
    Features:
    - Pin process to specific CPU cores
    - NUMA-aware allocation
    - Performance optimization for latency-critical code
    """
    
    def __init__(self):
        """Initialize CPU affinity manager."""
        self._original_affinity: Optional[List[int]] = None
        self._has_psutil = False
        
        try:
            import psutil
            self._psutil = psutil
            self._has_psutil = True
        except ImportError:
            logger.warning("psutil not available, CPU affinity disabled")
    
    def get_cpu_count(self) -> int:
        """Get number of available CPUs."""
        return os.cpu_count() or 1
    
    def get_current_affinity(self) -> Optional[List[int]]:
        """Get current CPU affinity."""
        if not self._has_psutil:
            return None
        
        try:
            process = self._psutil.Process()
            return list(process.cpu_affinity())
        except Exception as e:
            logger.error(f"Failed to get CPU affinity: {e}")
            return None
    
    def set_affinity(self, cpus: List[int]) -> bool:
        """
        Set CPU affinity for current process.
        
        Args:
            cpus: List of CPU cores to pin to
        
        Returns:
            True if successful
        """
        if not self._has_psutil:
            logger.warning("Cannot set affinity: psutil not available")
            return False
        
        try:
            # Save original affinity
            if self._original_affinity is None:
                self._original_affinity = self.get_current_affinity()
            
            process = self._psutil.Process()
            process.cpu_affinity(cpus)
            
            logger.info(f"CPU affinity set to cores: {cpus}")
            return True
        
        except Exception as e:
            logger.error(f"Failed to set CPU affinity: {e}")
            return False
    
    def pin_to_single_core(self, core: int = 0) -> bool:
        """
        Pin process to a single CPU core.
        
        Args:
            core: Core number to pin to
        
        Returns:
            True if successful
        """
        return self.set_affinity([core])
    
    def pin_to_performance_cores(self) -> bool:
        """
        Pin to performance cores (first half on hybrid CPUs).
        
        Returns:
            True if successful
        """
        cpu_count = self.get_cpu_count()
        
        # Assume first half are performance cores on hybrid systems
        perf_cores = list(range(cpu_count // 2))
        
        if not perf_cores:
            perf_cores = [0]
        
        return self.set_affinity(perf_cores)
    
    def reset_affinity(self) -> bool:
        """
        Reset to original CPU affinity.
        
        Returns:
            True if successful
        """
        if self._original_affinity is None:
            return True
        
        return self.set_affinity(self._original_affinity)
    
    def get_cpu_info(self) -> Dict:
        """Get CPU information."""
        info = {
            'cpu_count': self.get_cpu_count(),
            'affinity_available': self._has_psutil,
        }
        
        if self._has_psutil:
            info['current_affinity'] = self.get_current_affinity()
            
            try:
                freq = self._psutil.cpu_freq()
                if freq:
                    info['frequency_mhz'] = {
                        'current': freq.current,
                        'min': freq.min,
                        'max': freq.max,
                    }
            except Exception:
                pass
        
        return info


class FeeTierSimulator:
    """
    Fee Tier Simulator (Task 92).
    
    Simulates different exchange fee structures.
    """
    
    # Common fee tiers (in basis points)
    FEE_TIERS = {
        'retail': {'maker': 10, 'taker': 20},  # 0.10% / 0.20%
        'vip1': {'maker': 8, 'taker': 16},
        'vip2': {'maker': 6, 'taker': 12},
        'vip3': {'maker': 4, 'taker': 8},
        'market_maker': {'maker': 0, 'taker': 4},
        'polymarket': {'maker': 0, 'taker': 0},  # Zero fees
    }
    
    def __init__(
        self,
        tier: str = "polymarket",
    ):
        """
        Initialize fee simulator.
        
        Args:
            tier: Fee tier name
        """
        self.tier = tier
        self._fees = self.FEE_TIERS.get(tier, self.FEE_TIERS['retail'])
        self._total_fees_paid = 0.0
        self._trade_count = 0
    
    def calculate_fee(
        self,
        notional: float,
        is_maker: bool = False,
    ) -> float:
        """
        Calculate fee for a trade.
        
        Args:
            notional: Trade notional value
            is_maker: True if maker order
        
        Returns:
            Fee amount
        """
        fee_bp = self._fees['maker'] if is_maker else self._fees['taker']
        fee = notional * fee_bp / 10000.0
        
        self._total_fees_paid += fee
        self._trade_count += 1
        
        return fee
    
    def get_effective_spread_cost(
        self,
        spread_bp: float,
        is_maker: bool = False,
    ) -> float:
        """
        Get total cost including spread and fees.
        
        Args:
            spread_bp: Spread in basis points
            is_maker: True if maker order
        
        Returns:
            Total cost in basis points
        """
        fee_bp = self._fees['maker'] if is_maker else self._fees['taker']
        
        # Makers typically capture spread, takers pay it
        if is_maker:
            return fee_bp - spread_bp / 2
        else:
            return fee_bp + spread_bp / 2
    
    def get_statistics(self) -> Dict:
        """Get fee statistics."""
        return {
            'tier': self.tier,
            'maker_fee_bp': self._fees['maker'],
            'taker_fee_bp': self._fees['taker'],
            'total_fees_paid': self._total_fees_paid,
            'trade_count': self._trade_count,
            'avg_fee_per_trade': (
                self._total_fees_paid / self._trade_count
                if self._trade_count > 0 else 0
            ),
        }
    
    def set_tier(self, tier: str) -> None:
        """Change fee tier."""
        if tier in self.FEE_TIERS:
            self.tier = tier
            self._fees = self.FEE_TIERS[tier]
            logger.info(f"Fee tier changed to: {tier}")
        else:
            logger.warning(f"Unknown fee tier: {tier}")


# Factory functions
def create_memory_monitor(
    top_n: int = 10,
) -> MemoryMonitor:
    """Create and return a MemoryMonitor instance."""
    return MemoryMonitor(top_n=top_n)


def create_cpu_affinity_manager() -> CPUAffinityManager:
    """Create and return a CPUAffinityManager instance."""
    return CPUAffinityManager()


def create_fee_simulator(
    tier: str = "polymarket",
) -> FeeTierSimulator:
    """Create and return a FeeTierSimulator instance."""
    return FeeTierSimulator(tier=tier)
