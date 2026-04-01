"""
Toxic Flow Classifier - Retail vs Institutional Flow Detection.

Phase 2 - Task 65: Logistic Regression classifier to predict if incoming
trade cluster represents retail or institutional sweeper.

Educational purpose only - paper trading simulation.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple, Any
from collections import deque
from enum import Enum

logger = logging.getLogger(__name__)

# Try to import sklearn
try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    import numpy as np
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False
    logger.warning("scikit-learn not available, toxic flow classifier disabled")


class FlowType(Enum):
    """Classification of trade flow origin."""
    RETAIL = "retail"
    INSTITUTIONAL = "institutional"
    UNKNOWN = "unknown"


@dataclass
class TradeCluster:
    """A cluster of related trades for analysis."""
    cluster_id: str
    symbol: str
    start_time: float
    end_time: float
    trades: List[Dict] = field(default_factory=list)
    
    # Aggregate metrics
    total_volume: float = 0.0
    trade_count: int = 0
    avg_trade_size: float = 0.0
    max_trade_size: float = 0.0
    
    # Timing metrics
    duration_ms: float = 0.0
    avg_inter_arrival_ms: float = 0.0
    
    # Price impact
    price_start: float = 0.0
    price_end: float = 0.0
    price_impact_bps: float = 0.0
    
    # Classification result
    flow_type: FlowType = FlowType.UNKNOWN
    institutional_probability: float = 0.5
    
    def to_dict(self) -> dict:
        return {
            'cluster_id': self.cluster_id,
            'symbol': self.symbol,
            'start_time': self.start_time,
            'end_time': self.end_time,
            'total_volume': self.total_volume,
            'trade_count': self.trade_count,
            'avg_trade_size': self.avg_trade_size,
            'max_trade_size': self.max_trade_size,
            'duration_ms': self.duration_ms,
            'avg_inter_arrival_ms': self.avg_inter_arrival_ms,
            'price_start': self.price_start,
            'price_end': self.price_end,
            'price_impact_bps': self.price_impact_bps,
            'flow_type': self.flow_type.value,
            'institutional_probability': self.institutional_probability,
        }


@dataclass
class ClassificationResult:
    """Result of flow classification."""
    cluster: TradeCluster
    flow_type: FlowType
    institutional_probability: float
    confidence: float
    features_used: Dict[str, float]
    
    def to_dict(self) -> dict:
        return {
            'cluster_id': self.cluster.cluster_id,
            'flow_type': self.flow_type.value,
            'institutional_probability': self.institutional_probability,
            'confidence': self.confidence,
            'features': self.features_used,
        }


class ToxicFlowClassifier:
    """
    Classifies trade flow as retail or institutional.
    
    Institutional flow characteristics:
    - Larger average trade size
    - Higher trade frequency (shorter inter-arrival times)
    - Larger total volume in cluster
    - More significant price impact
    - Often occurs during specific times
    
    Uses Logistic Regression with features:
    - Normalized trade size
    - Trade frequency
    - Volume concentration
    - Price impact
    - Time-based features
    """
    
    # Cluster detection parameters
    CLUSTER_TIMEOUT_MS = 500  # Max gap between trades in cluster
    MIN_CLUSTER_SIZE = 3      # Minimum trades to form cluster
    
    # Institutional thresholds (for training data labeling)
    INSTITUTIONAL_SIZE_MULTIPLIER = 5.0  # Trades > 5x average
    INSTITUTIONAL_IMPACT_BPS = 10.0      # Price impact > 10 bps
    
    def __init__(
        self,
        symbols: List[str] = None,
        auto_train: bool = True,
        min_training_samples: int = 100,
    ):
        """
        Initialize toxic flow classifier.
        
        Args:
            symbols: Symbols to track
            auto_train: Automatically train when enough samples
            min_training_samples: Minimum samples before training
        """
        self.symbols = symbols or ['BTC', 'ETH']
        self.auto_train = auto_train
        self.min_training_samples = min_training_samples
        
        # Models per symbol
        self._models: Dict[str, Any] = {}
        self._scalers: Dict[str, Any] = {}
        self._is_trained: Dict[str, bool] = {}
        
        # Trade buffers
        self._trade_buffer: Dict[str, deque] = {}
        self._current_cluster: Dict[str, Optional[TradeCluster]] = {}
        
        # Training data
        self._training_data: Dict[str, List[Tuple]] = {}
        
        # Historical averages for normalization
        self._avg_trade_size: Dict[str, float] = {}
        self._avg_volume: Dict[str, float] = {}
        
        # Statistics
        self._cluster_history: Dict[str, deque] = {}
        
        # Initialize
        for symbol in self.symbols:
            self._initialize_symbol(symbol)
    
    def _initialize_symbol(self, symbol: str) -> None:
        """Initialize classifier for a symbol."""
        if SKLEARN_AVAILABLE:
            self._models[symbol] = LogisticRegression(
                C=1.0,
                class_weight='balanced',
                max_iter=1000,
            )
            self._scalers[symbol] = StandardScaler()
        
        self._is_trained[symbol] = False
        self._trade_buffer[symbol] = deque(maxlen=10000)
        self._current_cluster[symbol] = None
        self._training_data[symbol] = []
        self._avg_trade_size[symbol] = 1.0
        self._avg_volume[symbol] = 1000.0
        self._cluster_history[symbol] = deque(maxlen=1000)
    
    def add_trade(
        self,
        symbol: str,
        price: float,
        quantity: float,
        timestamp: float,
        is_buyer_maker: bool,
    ) -> Optional[ClassificationResult]:
        """
        Add a trade and check for cluster completion.
        
        Returns classification result if cluster is completed.
        """
        if symbol not in self._trade_buffer:
            self._initialize_symbol(symbol)
        
        trade = {
            'price': price,
            'quantity': quantity,
            'timestamp': timestamp,
            'is_buyer_maker': is_buyer_maker,
            'side': 'sell' if is_buyer_maker else 'buy',
        }
        
        self._trade_buffer[symbol].append(trade)
        
        # Update running averages
        self._update_averages(symbol, quantity)
        
        # Check if trade extends current cluster or starts new one
        current = self._current_cluster[symbol]
        
        if current is None:
            # Start new cluster
            self._start_cluster(symbol, trade)
            return None
        
        # Check if trade is part of current cluster
        gap = timestamp - current.end_time
        
        if gap <= self.CLUSTER_TIMEOUT_MS:
            # Add to current cluster
            self._add_to_cluster(current, trade)
            return None
        else:
            # Cluster complete, classify and start new
            result = self._complete_cluster(symbol, current)
            self._start_cluster(symbol, trade)
            return result
    
    def _start_cluster(self, symbol: str, trade: Dict) -> None:
        """Start a new trade cluster."""
        cluster_id = f"{symbol}_{int(trade['timestamp'])}"
        
        cluster = TradeCluster(
            cluster_id=cluster_id,
            symbol=symbol,
            start_time=trade['timestamp'],
            end_time=trade['timestamp'],
            price_start=trade['price'],
            price_end=trade['price'],
        )
        
        self._add_to_cluster(cluster, trade)
        self._current_cluster[symbol] = cluster
    
    def _add_to_cluster(self, cluster: TradeCluster, trade: Dict) -> None:
        """Add a trade to a cluster."""
        cluster.trades.append(trade)
        cluster.end_time = trade['timestamp']
        cluster.price_end = trade['price']
        cluster.total_volume += trade['quantity']
        cluster.trade_count += 1
        cluster.max_trade_size = max(cluster.max_trade_size, trade['quantity'])
    
    def _complete_cluster(
        self,
        symbol: str,
        cluster: TradeCluster,
    ) -> Optional[ClassificationResult]:
        """Complete and classify a cluster."""
        if cluster.trade_count < self.MIN_CLUSTER_SIZE:
            return None
        
        # Calculate aggregate metrics
        cluster.duration_ms = cluster.end_time - cluster.start_time
        cluster.avg_trade_size = cluster.total_volume / cluster.trade_count
        
        if cluster.trade_count > 1:
            cluster.avg_inter_arrival_ms = cluster.duration_ms / (cluster.trade_count - 1)
        
        if cluster.price_start > 0:
            cluster.price_impact_bps = (
                (cluster.price_end - cluster.price_start) /
                cluster.price_start * 10000
            )
        
        # Classify
        result = self._classify_cluster(cluster)
        
        # Store for history
        self._cluster_history[symbol].append(cluster)
        
        # Add to training data if labeled
        self._add_training_sample(symbol, cluster)
        
        return result
    
    def _classify_cluster(self, cluster: TradeCluster) -> ClassificationResult:
        """Classify a cluster as retail or institutional."""
        symbol = cluster.symbol
        
        # Extract features
        features = self._extract_features(cluster)
        
        if SKLEARN_AVAILABLE and self._is_trained.get(symbol, False):
            # Use trained model
            X = np.array([list(features.values())])
            X_scaled = self._scalers[symbol].transform(X)
            
            prob = self._models[symbol].predict_proba(X_scaled)[0]
            institutional_prob = prob[1] if len(prob) > 1 else prob[0]
            
            flow_type = FlowType.INSTITUTIONAL if institutional_prob > 0.5 else FlowType.RETAIL
            confidence = max(institutional_prob, 1 - institutional_prob)
        else:
            # Use heuristic classification
            institutional_prob, confidence = self._heuristic_classify(cluster)
            flow_type = FlowType.INSTITUTIONAL if institutional_prob > 0.5 else FlowType.RETAIL
        
        cluster.flow_type = flow_type
        cluster.institutional_probability = institutional_prob
        
        return ClassificationResult(
            cluster=cluster,
            flow_type=flow_type,
            institutional_probability=institutional_prob,
            confidence=confidence,
            features_used=features,
        )
    
    def _extract_features(self, cluster: TradeCluster) -> Dict[str, float]:
        """Extract features for classification."""
        symbol = cluster.symbol
        avg_size = self._avg_trade_size.get(symbol, 1.0)
        avg_vol = self._avg_volume.get(symbol, 1000.0)
        
        features = {
            # Size features
            'norm_avg_size': cluster.avg_trade_size / avg_size,
            'norm_max_size': cluster.max_trade_size / avg_size,
            'norm_total_volume': cluster.total_volume / avg_vol,
            
            # Frequency features
            'trade_count': cluster.trade_count,
            'trades_per_second': cluster.trade_count / max(0.001, cluster.duration_ms / 1000),
            'avg_inter_arrival': cluster.avg_inter_arrival_ms / 1000,  # In seconds
            
            # Impact features
            'abs_price_impact': abs(cluster.price_impact_bps),
            'price_impact_sign': 1 if cluster.price_impact_bps > 0 else -1,
            
            # Size distribution
            'size_concentration': cluster.max_trade_size / cluster.total_volume if cluster.total_volume > 0 else 0,
        }
        
        return features
    
    def _heuristic_classify(self, cluster: TradeCluster) -> Tuple[float, float]:
        """
        Heuristic classification when model is not trained.
        
        Returns (institutional_probability, confidence)
        """
        symbol = cluster.symbol
        avg_size = self._avg_trade_size.get(symbol, 1.0)
        
        score = 0.0
        
        # Large average trade size
        if cluster.avg_trade_size > avg_size * self.INSTITUTIONAL_SIZE_MULTIPLIER:
            score += 0.3
        elif cluster.avg_trade_size > avg_size * 2:
            score += 0.15
        
        # High price impact
        if abs(cluster.price_impact_bps) > self.INSTITUTIONAL_IMPACT_BPS:
            score += 0.3
        elif abs(cluster.price_impact_bps) > 5:
            score += 0.15
        
        # High trade frequency
        trades_per_sec = cluster.trade_count / max(0.001, cluster.duration_ms / 1000)
        if trades_per_sec > 10:
            score += 0.2
        elif trades_per_sec > 5:
            score += 0.1
        
        # Large total volume
        avg_vol = self._avg_volume.get(symbol, 1000.0)
        if cluster.total_volume > avg_vol * 10:
            score += 0.2
        elif cluster.total_volume > avg_vol * 3:
            score += 0.1
        
        # Clamp to [0, 1]
        institutional_prob = min(1.0, max(0.0, score))
        
        # Confidence based on how extreme the features are
        confidence = 0.5 + abs(institutional_prob - 0.5)
        
        return institutional_prob, confidence
    
    def _update_averages(self, symbol: str, quantity: float) -> None:
        """Update running averages for normalization."""
        # Exponential moving average
        alpha = 0.01
        
        current_avg = self._avg_trade_size.get(symbol, quantity)
        self._avg_trade_size[symbol] = alpha * quantity + (1 - alpha) * current_avg
        
        # Update volume average (use cluster total if available)
        current = self._current_cluster.get(symbol)
        if current and current.total_volume > 0:
            current_vol = self._avg_volume.get(symbol, current.total_volume)
            self._avg_volume[symbol] = alpha * current.total_volume + (1 - alpha) * current_vol
    
    def _add_training_sample(self, symbol: str, cluster: TradeCluster) -> None:
        """Add cluster to training data with automatic labeling."""
        if not SKLEARN_AVAILABLE:
            return
        
        features = self._extract_features(cluster)
        
        # Auto-label based on heuristics
        inst_prob, _ = self._heuristic_classify(cluster)
        label = 1 if inst_prob > 0.7 else (0 if inst_prob < 0.3 else None)
        
        if label is not None:
            self._training_data[symbol].append((list(features.values()), label))
            
            # Auto-train if enough samples
            if self.auto_train and len(self._training_data[symbol]) >= self.min_training_samples:
                self.train(symbol)
    
    def train(self, symbol: str) -> bool:
        """
        Train the classifier for a symbol.
        
        Returns True if training successful.
        """
        if not SKLEARN_AVAILABLE:
            return False
        
        if symbol not in self._training_data:
            return False
        
        data = self._training_data[symbol]
        if len(data) < self.min_training_samples:
            logger.warning(f"Not enough training data for {symbol}: {len(data)}")
            return False
        
        try:
            X = np.array([d[0] for d in data])
            y = np.array([d[1] for d in data])
            
            # Fit scaler
            self._scalers[symbol].fit(X)
            X_scaled = self._scalers[symbol].transform(X)
            
            # Train model
            self._models[symbol].fit(X_scaled, y)
            self._is_trained[symbol] = True
            
            # Log accuracy on training data
            accuracy = self._models[symbol].score(X_scaled, y)
            logger.info(f"Trained toxic flow classifier for {symbol}: accuracy={accuracy:.2%}")
            
            return True
        except Exception as e:
            logger.error(f"Training failed for {symbol}: {e}")
            return False
    
    def get_recent_clusters(
        self,
        symbol: str,
        count: int = 100,
    ) -> List[TradeCluster]:
        """Get recent classified clusters."""
        history = self._cluster_history.get(symbol, [])
        return list(history)[-count:]
    
    def get_flow_summary(self, symbol: str, window_minutes: int = 5) -> dict:
        """
        Get summary of recent flow classification.
        
        Returns summary of retail vs institutional flow.
        """
        clusters = self.get_recent_clusters(symbol, 100)
        
        now = time.time() * 1000
        window_ms = window_minutes * 60 * 1000
        
        recent = [c for c in clusters if now - c.end_time <= window_ms]
        
        if not recent:
            return {
                'symbol': symbol,
                'total_clusters': 0,
                'retail_pct': 0.0,
                'institutional_pct': 0.0,
                'avg_institutional_prob': 0.5,
                'total_volume': 0.0,
            }
        
        retail = [c for c in recent if c.flow_type == FlowType.RETAIL]
        institutional = [c for c in recent if c.flow_type == FlowType.INSTITUTIONAL]
        
        total = len(recent)
        
        return {
            'symbol': symbol,
            'total_clusters': total,
            'retail_count': len(retail),
            'institutional_count': len(institutional),
            'retail_pct': len(retail) / total * 100,
            'institutional_pct': len(institutional) / total * 100,
            'avg_institutional_prob': sum(c.institutional_probability for c in recent) / total,
            'total_volume': sum(c.total_volume for c in recent),
            'institutional_volume': sum(c.total_volume for c in institutional),
            'retail_volume': sum(c.total_volume for c in retail),
        }
    
    def is_flow_institutional(self, symbol: str, threshold: float = 0.6) -> bool:
        """
        Check if recent flow is predominantly institutional.
        
        Args:
            symbol: Symbol to check
            threshold: Probability threshold (default 0.6)
        
        Returns:
            True if institutional flow detected
        """
        summary = self.get_flow_summary(symbol)
        return summary['avg_institutional_prob'] > threshold
    
    @property
    def is_available(self) -> bool:
        """Check if sklearn is available."""
        return SKLEARN_AVAILABLE


# Factory function
def create_toxic_flow_classifier(
    symbols: List[str] = None,
    auto_train: bool = True,
) -> ToxicFlowClassifier:
    """Create and return a ToxicFlowClassifier instance."""
    return ToxicFlowClassifier(symbols=symbols, auto_train=auto_train)
