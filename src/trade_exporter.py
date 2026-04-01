"""
Trade History Exporter for post-session quantitative analysis.
Logs all simulated orders, merges, and latency metrics to CSV.
"""
import csv
import logging
from pathlib import Path
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional, List

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    """Represents a filled order in the paper trading system."""
    timestamp: datetime
    market_id: str
    condition_id: str
    side: str  # YES/NO
    price: float
    size: float
    order_type: str  # LIMIT/FOK
    is_maker: bool
    fill_status: str  # FILLED/PARTIAL/CANCELLED
    latency_ms: float
    theoretical_rebate: float


@dataclass
class MergeRecord:
    """Represents a merge event (matched pairs cancellation)."""
    timestamp: datetime
    condition_id: str
    shares_merged: float
    usdc_recovered: float


@dataclass
class LatencyMetrics:
    """Latency and slippage measurements for order execution."""
    order_generation_time: datetime
    network_ping_time: datetime
    theoretical_slippage: float


class TradeExporter:
    """Exports trade history, merges, and latency metrics to CSV for analysis."""

    DEFAULT_PATH = "data/paper_trade_logs.csv"
    MERGE_LOG_PATH = "data/paper_merge_logs.csv"
    LATENCY_LOG_PATH = "data/paper_latency_logs.csv"
    MAKER_REBATE_BPS = 0.5  # 0.5 basis points (0.005%)

    def __init__(self, csv_path: str = None, merge_path: str = None, latency_path: str = None):
        """
        Initialize TradeExporter with CSV paths.

        Args:
            csv_path: Path to trade log CSV (default: data/paper_trade_logs.csv)
            merge_path: Path to merge log CSV (default: data/paper_merge_logs.csv)
            latency_path: Path to latency log CSV (default: data/paper_latency_logs.csv)
        """
        self._trade_path = Path(csv_path or self.DEFAULT_PATH)
        self._merge_path = Path(merge_path or self.MERGE_LOG_PATH)
        self._latency_path = Path(latency_path or self.LATENCY_LOG_PATH)

        # Create parent directories
        self._trade_path.parent.mkdir(parents=True, exist_ok=True)
        self._merge_path.parent.mkdir(parents=True, exist_ok=True)
        self._latency_path.parent.mkdir(parents=True, exist_ok=True)

        # Initialize CSV files with headers if they don't exist
        self._init_trade_csv()
        self._init_merge_csv()
        self._init_latency_csv()

        logger.info(f"TradeExporter initialized with paths: {self._trade_path}, {self._merge_path}, {self._latency_path}")

    def _init_trade_csv(self):
        """Initialize trade CSV with headers if it doesn't exist."""
        if not self._trade_path.exists():
            with open(self._trade_path, 'w', newline='') as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        'timestamp',
                        'market_id',
                        'condition_id',
                        'side',
                        'price',
                        'size',
                        'order_type',
                        'is_maker',
                        'fill_status',
                        'latency_ms',
                        'theoretical_rebate'
                    ]
                )
                writer.writeheader()
            logger.debug(f"Initialized trade CSV: {self._trade_path}")

    def _init_merge_csv(self):
        """Initialize merge CSV with headers if it doesn't exist."""
        if not self._merge_path.exists():
            with open(self._merge_path, 'w', newline='') as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        'timestamp',
                        'condition_id',
                        'shares_merged',
                        'usdc_recovered'
                    ]
                )
                writer.writeheader()
            logger.debug(f"Initialized merge CSV: {self._merge_path}")

    def _init_latency_csv(self):
        """Initialize latency CSV with headers if it doesn't exist."""
        if not self._latency_path.exists():
            with open(self._latency_path, 'w', newline='') as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        'order_generation_time',
                        'network_ping_time',
                        'latency_delta_ms',
                        'theoretical_slippage'
                    ]
                )
                writer.writeheader()
            logger.debug(f"Initialized latency CSV: {self._latency_path}")

    def log_trade(self, trade: TradeRecord) -> bool:
        """
        Append a filled trade to the trade log CSV.

        Args:
            trade: TradeRecord object to log

        Returns:
            bool: True if successfully logged, False otherwise
        """
        try:
            # Convert datetime to ISO format string
            trade_dict = asdict(trade)
            trade_dict['timestamp'] = trade.timestamp.isoformat()

            with open(self._trade_path, 'a', newline='') as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        'timestamp',
                        'market_id',
                        'condition_id',
                        'side',
                        'price',
                        'size',
                        'order_type',
                        'is_maker',
                        'fill_status',
                        'latency_ms',
                        'theoretical_rebate'
                    ]
                )
                writer.writerow(trade_dict)

            logger.info(
                f"Logged trade: {trade.condition_id} {trade.side} "
                f"{trade.size}@{trade.price} ({trade.fill_status})"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to log trade: {e}")
            return False

    def log_merge(self, merge: MergeRecord) -> bool:
        """
        Log a merge event (matched pairs cancellation).

        Args:
            merge: MergeRecord object to log

        Returns:
            bool: True if successfully logged, False otherwise
        """
        try:
            merge_dict = asdict(merge)
            merge_dict['timestamp'] = merge.timestamp.isoformat()

            with open(self._merge_path, 'a', newline='') as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        'timestamp',
                        'condition_id',
                        'shares_merged',
                        'usdc_recovered'
                    ]
                )
                writer.writerow(merge_dict)

            logger.info(
                f"Logged merge: {merge.condition_id} "
                f"{merge.shares_merged} shares, {merge.usdc_recovered} USDC recovered"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to log merge: {e}")
            return False

    def calculate_maker_rebate(self, trade: TradeRecord) -> float:
        """
        Calculate theoretical maker rebate in USDC.

        Maker rebate formula: notional_value * MAKER_REBATE_BPS / 10000
        where notional_value = price * size

        Args:
            trade: TradeRecord to calculate rebate for

        Returns:
            float: Theoretical rebate in USDC (0 if taker)
        """
        if not trade.is_maker:
            return 0.0

        notional_value = trade.price * trade.size
        rebate = notional_value * (self.MAKER_REBATE_BPS / 10000)

        logger.debug(
            f"Calculated maker rebate for {trade.condition_id}: "
            f"notional={notional_value}, rebate={rebate}"
        )
        return rebate

    def calculate_slippage(self, metrics: LatencyMetrics) -> float:
        """
        Calculate theoretical slippage based on latency.

        Slippage is estimated as the time delta between order generation
        and network ping response, adjusted for typical market volatility.

        Formula: latency_delta_ms * VOLATILITY_FACTOR

        Args:
            metrics: LatencyMetrics with generation and ping times

        Returns:
            float: Theoretical slippage in basis points (bps)
        """
        # Calculate latency delta in milliseconds
        latency_delta = (metrics.network_ping_time - metrics.order_generation_time).total_seconds() * 1000

        # Theoretical slippage: assume 0.1 bps per millisecond of latency
        # This is a conservative estimate for typical market conditions
        volatility_factor = 0.1
        theoretical_slippage = latency_delta * volatility_factor

        logger.debug(
            f"Calculated slippage: latency_delta={latency_delta}ms, "
            f"slippage={theoretical_slippage}bps"
        )
        return theoretical_slippage

    def log_latency_metrics(self, metrics: LatencyMetrics) -> bool:
        """
        Log latency and slippage metrics to CSV.

        Args:
            metrics: LatencyMetrics object to log

        Returns:
            bool: True if successfully logged, False otherwise
        """
        try:
            latency_delta_ms = (
                (metrics.network_ping_time - metrics.order_generation_time).total_seconds() * 1000
            )

            latency_dict = {
                'order_generation_time': metrics.order_generation_time.isoformat(),
                'network_ping_time': metrics.network_ping_time.isoformat(),
                'latency_delta_ms': latency_delta_ms,
                'theoretical_slippage': metrics.theoretical_slippage
            }

            with open(self._latency_path, 'a', newline='') as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        'order_generation_time',
                        'network_ping_time',
                        'latency_delta_ms',
                        'theoretical_slippage'
                    ]
                )
                writer.writerow(latency_dict)

            logger.info(
                f"Logged latency metrics: delta={latency_delta_ms:.2f}ms, "
                f"slippage={metrics.theoretical_slippage:.4f}bps"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to log latency metrics: {e}")
            return False

    def get_trade_summary(self) -> dict:
        """
        Get summary statistics from trade logs.

        Returns:
            dict: Summary including total trades, volume, rebates earned
        """
        try:
            trades = []
            with open(self._trade_path, 'r') as f:
                reader = csv.DictReader(f)
                trades = list(reader)

            if not trades or len(trades) <= 1:  # Header only
                return {
                    'total_trades': 0,
                    'total_volume': 0.0,
                    'total_rebates': 0.0,
                    'maker_trades': 0,
                    'taker_trades': 0
                }

            total_trades = len(trades)
            total_volume = sum(float(t['size']) for t in trades)
            total_rebates = sum(float(t['theoretical_rebate']) for t in trades)
            maker_trades = sum(1 for t in trades if t['is_maker'] == 'True')
            taker_trades = total_trades - maker_trades

            return {
                'total_trades': total_trades,
                'total_volume': total_volume,
                'total_rebates': total_rebates,
                'maker_trades': maker_trades,
                'taker_trades': taker_trades
            }
        except Exception as e:
            logger.error(f"Failed to generate trade summary: {e}")
            return {
                'total_trades': 0,
                'total_volume': 0.0,
                'total_rebates': 0.0,
                'maker_trades': 0,
                'taker_trades': 0
            }

    def get_merge_summary(self) -> dict:
        """
        Get summary statistics from merge logs.

        Returns:
            dict: Summary including total merges, shares merged, USDC recovered
        """
        try:
            merges = []
            with open(self._merge_path, 'r') as f:
                reader = csv.DictReader(f)
                merges = list(reader)

            if not merges or len(merges) <= 1:  # Header only
                return {
                    'total_merges': 0,
                    'total_shares_merged': 0.0,
                    'total_usdc_recovered': 0.0
                }

            total_merges = len(merges)
            total_shares_merged = sum(float(m['shares_merged']) for m in merges)
            total_usdc_recovered = sum(float(m['usdc_recovered']) for m in merges)

            return {
                'total_merges': total_merges,
                'total_shares_merged': total_shares_merged,
                'total_usdc_recovered': total_usdc_recovered
            }
        except Exception as e:
            logger.error(f"Failed to generate merge summary: {e}")
            return {
                'total_merges': 0,
                'total_shares_merged': 0.0,
                'total_usdc_recovered': 0.0
            }

    def get_latency_summary(self) -> dict:
        """
        Get summary statistics from latency logs.

        Returns:
            dict: Summary including avg/min/max latency and slippage
        """
        try:
            latencies = []
            with open(self._latency_path, 'r') as f:
                reader = csv.DictReader(f)
                latencies = list(reader)

            if not latencies or len(latencies) <= 1:  # Header only
                return {
                    'total_measurements': 0,
                    'avg_latency_ms': 0.0,
                    'min_latency_ms': 0.0,
                    'max_latency_ms': 0.0,
                    'avg_slippage_bps': 0.0
                }

            latency_deltas = [float(l['latency_delta_ms']) for l in latencies]
            slippages = [float(l['theoretical_slippage']) for l in latencies]

            return {
                'total_measurements': len(latencies),
                'avg_latency_ms': sum(latency_deltas) / len(latency_deltas),
                'min_latency_ms': min(latency_deltas),
                'max_latency_ms': max(latency_deltas),
                'avg_slippage_bps': sum(slippages) / len(slippages)
            }
        except Exception as e:
            logger.error(f"Failed to generate latency summary: {e}")
            return {
                'total_measurements': 0,
                'avg_latency_ms': 0.0,
                'min_latency_ms': 0.0,
                'max_latency_ms': 0.0,
                'avg_slippage_bps': 0.0
            }
