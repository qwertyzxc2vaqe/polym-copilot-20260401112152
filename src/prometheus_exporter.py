"""
Prometheus Metrics Exporter - Simulation Metrics Export.

Phase 2 - Task 82: Build exporter endpoint serving simulated metrics
(mock latency, mock orders placed, mock PnL) to localhost:8000/metrics.

Educational purpose only - paper trading simulation.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any
from collections import defaultdict

logger = logging.getLogger(__name__)

# Try to import prometheus_client
try:
    from prometheus_client import (
        Counter, Gauge, Histogram, Summary,
        CollectorRegistry, generate_latest, CONTENT_TYPE_LATEST,
        start_http_server, REGISTRY
    )
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    logger.warning("prometheus_client not available, metrics export disabled")


class PrometheusExporter:
    """
    Exports simulation metrics to Prometheus.
    
    Metrics exposed:
    - Orders: placed, filled, cancelled
    - PnL: realized, unrealized, total
    - Latency: API calls, WebSocket messages
    - System: memory usage, CPU, connections
    """
    
    def __init__(
        self,
        port: int = 8000,
        prefix: str = "polym_",
    ):
        """
        Initialize Prometheus exporter.
        
        Args:
            port: HTTP port for metrics endpoint
            prefix: Metric name prefix
        """
        self.port = port
        self.prefix = prefix
        self._running = False
        
        if not PROMETHEUS_AVAILABLE:
            logger.warning("Prometheus client not available")
            return
        
        # Create custom registry
        self.registry = REGISTRY
        
        # Define metrics
        self._define_metrics()
    
    def _define_metrics(self) -> None:
        """Define all Prometheus metrics."""
        p = self.prefix
        
        # ============================================================
        # Order Metrics
        # ============================================================
        
        self.orders_placed = Counter(
            f'{p}orders_placed_total',
            'Total number of mock orders placed',
            ['symbol', 'side', 'order_type']
        )
        
        self.orders_filled = Counter(
            f'{p}orders_filled_total',
            'Total number of mock orders filled',
            ['symbol', 'side']
        )
        
        self.orders_cancelled = Counter(
            f'{p}orders_cancelled_total',
            'Total number of mock orders cancelled',
            ['symbol', 'reason']
        )
        
        self.orders_open = Gauge(
            f'{p}orders_open',
            'Current number of open mock orders',
            ['symbol']
        )
        
        self.order_fill_rate = Gauge(
            f'{p}order_fill_rate',
            'Order fill rate percentage',
            ['symbol']
        )
        
        # ============================================================
        # PnL Metrics
        # ============================================================
        
        self.realized_pnl = Gauge(
            f'{p}realized_pnl_usdc',
            'Realized PnL in USDC',
            ['symbol']
        )
        
        self.unrealized_pnl = Gauge(
            f'{p}unrealized_pnl_usdc',
            'Unrealized PnL in USDC',
            ['symbol']
        )
        
        self.total_pnl = Gauge(
            f'{p}total_pnl_usdc',
            'Total PnL (realized + unrealized) in USDC'
        )
        
        self.portfolio_value = Gauge(
            f'{p}portfolio_value_usdc',
            'Total portfolio value in USDC'
        )
        
        self.daily_pnl = Gauge(
            f'{p}daily_pnl_usdc',
            'Daily PnL in USDC'
        )
        
        # ============================================================
        # Latency Metrics
        # ============================================================
        
        self.api_latency = Histogram(
            f'{p}api_latency_seconds',
            'API call latency in seconds',
            ['endpoint', 'method'],
            buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)
        )
        
        self.ws_message_latency = Histogram(
            f'{p}ws_message_latency_seconds',
            'WebSocket message processing latency',
            ['stream'],
            buckets=(0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1)
        )
        
        self.order_execution_latency = Histogram(
            f'{p}order_execution_latency_seconds',
            'Order execution latency (submit to fill)',
            ['symbol'],
            buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)
        )
        
        # ============================================================
        # Trading Metrics
        # ============================================================
        
        self.position_size = Gauge(
            f'{p}position_size',
            'Current position size',
            ['symbol', 'side']
        )
        
        self.spread_bps = Gauge(
            f'{p}spread_bps',
            'Current spread in basis points',
            ['symbol']
        )
        
        self.ofi = Gauge(
            f'{p}order_flow_imbalance',
            'Order Flow Imbalance',
            ['symbol', 'depth']
        )
        
        self.win_rate = Gauge(
            f'{p}win_rate_percent',
            'Win rate percentage'
        )
        
        self.profit_factor = Gauge(
            f'{p}profit_factor',
            'Profit factor (gross profit / gross loss)'
        )
        
        # ============================================================
        # Risk Metrics
        # ============================================================
        
        self.sharpe_ratio = Gauge(
            f'{p}sharpe_ratio',
            'Sharpe ratio'
        )
        
        self.sortino_ratio = Gauge(
            f'{p}sortino_ratio',
            'Sortino ratio'
        )
        
        self.max_drawdown = Gauge(
            f'{p}max_drawdown_percent',
            'Maximum drawdown percentage'
        )
        
        self.var_99 = Gauge(
            f'{p}var_99_usdc',
            '99% Value at Risk in USDC'
        )
        
        self.current_drawdown = Gauge(
            f'{p}current_drawdown_percent',
            'Current drawdown percentage'
        )
        
        # ============================================================
        # ML Metrics
        # ============================================================
        
        self.ml_prediction_confidence = Gauge(
            f'{p}ml_prediction_confidence',
            'ML model prediction confidence',
            ['symbol', 'model']
        )
        
        self.ml_prediction_accuracy = Gauge(
            f'{p}ml_prediction_accuracy',
            'ML model prediction accuracy',
            ['symbol', 'model']
        )
        
        self.ml_training_loss = Gauge(
            f'{p}ml_training_loss',
            'ML model training loss',
            ['symbol', 'model']
        )
        
        # ============================================================
        # System Metrics
        # ============================================================
        
        self.ws_connections = Gauge(
            f'{p}websocket_connections',
            'Number of active WebSocket connections'
        )
        
        self.tick_buffer_size = Gauge(
            f'{p}tick_buffer_size',
            'Size of tick buffer',
            ['symbol']
        )
        
        self.circuit_breaker_triggered = Gauge(
            f'{p}circuit_breaker_triggered',
            'Circuit breaker status (1=triggered, 0=normal)'
        )
        
        # ============================================================
        # Counters for events
        # ============================================================
        
        self.toxic_flow_detected = Counter(
            f'{p}toxic_flow_detected_total',
            'Number of toxic flow events detected',
            ['symbol']
        )
        
        self.flash_crash_detected = Counter(
            f'{p}flash_crash_detected_total',
            'Number of flash crash events detected',
            ['symbol']
        )
    
    def start(self) -> bool:
        """Start the metrics HTTP server."""
        if not PROMETHEUS_AVAILABLE:
            return False
        
        try:
            start_http_server(self.port)
            self._running = True
            logger.info(f"Prometheus metrics server started on port {self.port}")
            return True
        except Exception as e:
            logger.error(f"Failed to start Prometheus server: {e}")
            return False
    
    # ================================================================
    # Order metric updates
    # ================================================================
    
    def record_order_placed(
        self,
        symbol: str,
        side: str,
        order_type: str = "limit",
    ) -> None:
        """Record an order being placed."""
        if PROMETHEUS_AVAILABLE:
            self.orders_placed.labels(
                symbol=symbol,
                side=side,
                order_type=order_type,
            ).inc()
    
    def record_order_filled(self, symbol: str, side: str) -> None:
        """Record an order being filled."""
        if PROMETHEUS_AVAILABLE:
            self.orders_filled.labels(symbol=symbol, side=side).inc()
    
    def record_order_cancelled(self, symbol: str, reason: str = "user") -> None:
        """Record an order being cancelled."""
        if PROMETHEUS_AVAILABLE:
            self.orders_cancelled.labels(symbol=symbol, reason=reason).inc()
    
    def set_open_orders(self, symbol: str, count: int) -> None:
        """Set current open order count."""
        if PROMETHEUS_AVAILABLE:
            self.orders_open.labels(symbol=symbol).set(count)
    
    # ================================================================
    # PnL metric updates
    # ================================================================
    
    def set_pnl(
        self,
        realized: float = None,
        unrealized: float = None,
        total: float = None,
        symbol: str = "ALL",
    ) -> None:
        """Update PnL metrics."""
        if not PROMETHEUS_AVAILABLE:
            return
        
        if realized is not None:
            self.realized_pnl.labels(symbol=symbol).set(realized)
        if unrealized is not None:
            self.unrealized_pnl.labels(symbol=symbol).set(unrealized)
        if total is not None:
            self.total_pnl.set(total)
    
    def set_portfolio_value(self, value: float) -> None:
        """Set portfolio value."""
        if PROMETHEUS_AVAILABLE:
            self.portfolio_value.set(value)
    
    # ================================================================
    # Latency metric updates
    # ================================================================
    
    def record_api_latency(
        self,
        endpoint: str,
        method: str,
        latency_seconds: float,
    ) -> None:
        """Record API call latency."""
        if PROMETHEUS_AVAILABLE:
            self.api_latency.labels(endpoint=endpoint, method=method).observe(latency_seconds)
    
    def record_ws_latency(self, stream: str, latency_seconds: float) -> None:
        """Record WebSocket message latency."""
        if PROMETHEUS_AVAILABLE:
            self.ws_message_latency.labels(stream=stream).observe(latency_seconds)
    
    def record_execution_latency(self, symbol: str, latency_seconds: float) -> None:
        """Record order execution latency."""
        if PROMETHEUS_AVAILABLE:
            self.order_execution_latency.labels(symbol=symbol).observe(latency_seconds)
    
    # ================================================================
    # Risk metric updates
    # ================================================================
    
    def set_risk_metrics(
        self,
        sharpe: float = None,
        sortino: float = None,
        max_dd: float = None,
        var_99: float = None,
        current_dd: float = None,
    ) -> None:
        """Update risk metrics."""
        if not PROMETHEUS_AVAILABLE:
            return
        
        if sharpe is not None:
            self.sharpe_ratio.set(sharpe)
        if sortino is not None:
            self.sortino_ratio.set(sortino)
        if max_dd is not None:
            self.max_drawdown.set(max_dd)
        if var_99 is not None:
            self.var_99.set(var_99)
        if current_dd is not None:
            self.current_drawdown.set(current_dd)
    
    # ================================================================
    # ML metric updates
    # ================================================================
    
    def set_ml_metrics(
        self,
        symbol: str,
        model: str,
        confidence: float = None,
        accuracy: float = None,
        loss: float = None,
    ) -> None:
        """Update ML model metrics."""
        if not PROMETHEUS_AVAILABLE:
            return
        
        if confidence is not None:
            self.ml_prediction_confidence.labels(symbol=symbol, model=model).set(confidence)
        if accuracy is not None:
            self.ml_prediction_accuracy.labels(symbol=symbol, model=model).set(accuracy)
        if loss is not None:
            self.ml_training_loss.labels(symbol=symbol, model=model).set(loss)
    
    # ================================================================
    # Trading metric updates
    # ================================================================
    
    def set_ofi(self, symbol: str, depth: int, value: float) -> None:
        """Set OFI value."""
        if PROMETHEUS_AVAILABLE:
            self.ofi.labels(symbol=symbol, depth=str(depth)).set(value)
    
    def set_spread(self, symbol: str, bps: float) -> None:
        """Set spread in basis points."""
        if PROMETHEUS_AVAILABLE:
            self.spread_bps.labels(symbol=symbol).set(bps)
    
    def set_position(self, symbol: str, side: str, size: float) -> None:
        """Set position size."""
        if PROMETHEUS_AVAILABLE:
            self.position_size.labels(symbol=symbol, side=side).set(size)
    
    # ================================================================
    # Event counters
    # ================================================================
    
    def record_toxic_flow(self, symbol: str) -> None:
        """Record toxic flow detection."""
        if PROMETHEUS_AVAILABLE:
            self.toxic_flow_detected.labels(symbol=symbol).inc()
    
    def record_flash_crash(self, symbol: str) -> None:
        """Record flash crash detection."""
        if PROMETHEUS_AVAILABLE:
            self.flash_crash_detected.labels(symbol=symbol).inc()
    
    def set_circuit_breaker(self, triggered: bool) -> None:
        """Set circuit breaker status."""
        if PROMETHEUS_AVAILABLE:
            self.circuit_breaker_triggered.set(1 if triggered else 0)
    
    @property
    def is_available(self) -> bool:
        return PROMETHEUS_AVAILABLE
    
    @property
    def is_running(self) -> bool:
        return self._running


# Singleton instance
_exporter: Optional[PrometheusExporter] = None


def get_prometheus_exporter(port: int = 8000) -> PrometheusExporter:
    """Get or create the global Prometheus exporter."""
    global _exporter
    if _exporter is None:
        _exporter = PrometheusExporter(port=port)
    return _exporter
