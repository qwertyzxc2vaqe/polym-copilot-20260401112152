"""
LSTM Model Module - Price Prediction Neural Network.

Phase 2 - Task 62: PyTorch LSTM network for predicting Binance spot
price 1 second into the future.

Educational purpose only - paper trading simulation.
"""

import asyncio
import logging
import time
import os
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple, Any
from collections import deque
import json

logger = logging.getLogger(__name__)

# Try to import PyTorch
try:
    import torch
    import torch.nn as nn
    import torch.optim as optim
    from torch.utils.data import Dataset, DataLoader
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False
    logger.warning("PyTorch not available, ML features disabled")

# Try to import numpy
try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False


@dataclass
class PredictionResult:
    """Result of a price prediction."""
    symbol: str
    timestamp: float
    current_price: float
    predicted_price: float
    predicted_change: float  # Percentage change
    confidence: float  # 0.0 to 1.0
    direction: str  # 'up', 'down', 'neutral'
    
    def to_dict(self) -> dict:
        return {
            'symbol': self.symbol,
            'timestamp': self.timestamp,
            'current_price': self.current_price,
            'predicted_price': self.predicted_price,
            'predicted_change': self.predicted_change,
            'confidence': self.confidence,
            'direction': self.direction,
        }


@dataclass
class ModelMetrics:
    """Training and prediction metrics."""
    total_predictions: int = 0
    correct_direction: int = 0
    mean_absolute_error: float = 0.0
    mean_squared_error: float = 0.0
    last_loss: float = 0.0
    training_epochs: int = 0
    
    @property
    def direction_accuracy(self) -> float:
        if self.total_predictions == 0:
            return 0.0
        return self.correct_direction / self.total_predictions * 100


if TORCH_AVAILABLE:
    
    class PriceLSTM(nn.Module):
        """
        LSTM neural network for price prediction.
        
        Architecture:
        - Input: sequence of price features
        - LSTM layers with dropout
        - Fully connected output layer
        """
        
        def __init__(
            self,
            input_size: int = 8,
            hidden_size: int = 64,
            num_layers: int = 2,
            dropout: float = 0.2,
            output_size: int = 1,
        ):
            super(PriceLSTM, self).__init__()
            
            self.hidden_size = hidden_size
            self.num_layers = num_layers
            
            # LSTM layers
            self.lstm = nn.LSTM(
                input_size=input_size,
                hidden_size=hidden_size,
                num_layers=num_layers,
                batch_first=True,
                dropout=dropout if num_layers > 1 else 0,
            )
            
            # Fully connected layers
            self.fc = nn.Sequential(
                nn.Linear(hidden_size, 32),
                nn.ReLU(),
                nn.Dropout(dropout),
                nn.Linear(32, output_size),
            )
        
        def forward(self, x: torch.Tensor) -> torch.Tensor:
            """
            Forward pass.
            
            Args:
                x: Input tensor of shape (batch, seq_len, input_size)
            
            Returns:
                Output tensor of shape (batch, output_size)
            """
            # LSTM forward
            lstm_out, (h_n, c_n) = self.lstm(x)
            
            # Take the last hidden state
            last_hidden = lstm_out[:, -1, :]
            
            # Fully connected layers
            output = self.fc(last_hidden)
            
            return output
    
    
    class PriceDataset(Dataset):
        """Dataset for price prediction training."""
        
        def __init__(
            self,
            sequences: List[np.ndarray],
            targets: List[float],
        ):
            self.sequences = sequences
            self.targets = targets
        
        def __len__(self) -> int:
            return len(self.sequences)
        
        def __getitem__(self, idx: int) -> Tuple[torch.Tensor, torch.Tensor]:
            return (
                torch.FloatTensor(self.sequences[idx]),
                torch.FloatTensor([self.targets[idx]]),
            )


class LSTMPricePredictor:
    """
    LSTM-based price prediction engine.
    
    Predicts price movement 1 second into the future using:
    - Recent price history
    - Volume data
    - Order flow imbalance
    - Technical indicators
    """
    
    # Model parameters
    SEQUENCE_LENGTH = 60  # Number of ticks to use
    INPUT_FEATURES = 8    # Number of input features per tick
    HIDDEN_SIZE = 64
    NUM_LAYERS = 2
    
    # Training parameters
    BATCH_SIZE = 32
    LEARNING_RATE = 0.001
    MIN_TRAINING_SAMPLES = 1000
    
    def __init__(
        self,
        symbols: List[str] = None,
        model_dir: str = "models",
        device: str = None,
    ):
        """
        Initialize LSTM predictor.
        
        Args:
            symbols: Symbols to track
            model_dir: Directory to save/load models
            device: PyTorch device ('cpu', 'cuda', or None for auto)
        """
        self.symbols = symbols or ['BTC', 'ETH']
        self.model_dir = model_dir
        
        # Determine device
        if device:
            self.device = torch.device(device)
        elif TORCH_AVAILABLE and torch.cuda.is_available():
            self.device = torch.device('cuda')
        else:
            self.device = torch.device('cpu')
        
        # Models per symbol
        self._models: Dict[str, nn.Module] = {}
        self._optimizers: Dict[str, optim.Optimizer] = {}
        self._metrics: Dict[str, ModelMetrics] = {}
        
        # Data buffers
        self._feature_buffer: Dict[str, deque] = {}
        self._training_data: Dict[str, List[Tuple]] = {}
        
        # Initialize
        for symbol in self.symbols:
            self._initialize_symbol(symbol)
        
        logger.info(f"LSTM predictor initialized on {self.device}")
    
    def _initialize_symbol(self, symbol: str) -> None:
        """Initialize model and buffers for a symbol."""
        if not TORCH_AVAILABLE:
            return
        
        # Create model
        model = PriceLSTM(
            input_size=self.INPUT_FEATURES,
            hidden_size=self.HIDDEN_SIZE,
            num_layers=self.NUM_LAYERS,
        ).to(self.device)
        
        self._models[symbol] = model
        self._optimizers[symbol] = optim.Adam(model.parameters(), lr=self.LEARNING_RATE)
        self._metrics[symbol] = ModelMetrics()
        self._feature_buffer[symbol] = deque(maxlen=self.SEQUENCE_LENGTH * 2)
        self._training_data[symbol] = []
    
    def add_tick(
        self,
        symbol: str,
        price: float,
        bid: float,
        ask: float,
        volume: float,
        ofi: float = 0.0,
        vwap: float = 0.0,
        volatility: float = 0.0,
        timestamp: float = None,
    ) -> None:
        """
        Add a tick to the feature buffer.
        
        This method should be called for each incoming tick.
        """
        if symbol not in self._feature_buffer:
            self._initialize_symbol(symbol)
        
        timestamp = timestamp or time.time() * 1000
        
        # Normalize features
        mid_price = (bid + ask) / 2 if bid > 0 and ask > 0 else price
        spread = (ask - bid) / mid_price * 10000 if mid_price > 0 else 0
        
        # Create feature vector
        features = np.array([
            price / mid_price - 1 if mid_price > 0 else 0,  # Price deviation from mid
            spread / 100,  # Spread in bps / 100
            volume / 1000,  # Normalized volume
            ofi,  # Order flow imbalance (-1 to 1)
            (price - vwap) / vwap if vwap > 0 else 0,  # VWAP deviation
            volatility / 100,  # Volatility percentage / 100
            np.sin(timestamp / 86400000 * 2 * np.pi),  # Time of day (sin)
            np.cos(timestamp / 86400000 * 2 * np.pi),  # Time of day (cos)
        ], dtype=np.float32)
        
        self._feature_buffer[symbol].append({
            'features': features,
            'price': price,
            'timestamp': timestamp,
        })
    
    def predict(self, symbol: str) -> Optional[PredictionResult]:
        """
        Predict price 1 second into the future.
        
        Args:
            symbol: Symbol to predict
        
        Returns:
            PredictionResult or None if insufficient data
        """
        if not TORCH_AVAILABLE:
            return None
        
        if symbol not in self._models:
            return None
        
        buffer = self._feature_buffer.get(symbol, [])
        if len(buffer) < self.SEQUENCE_LENGTH:
            return None
        
        # Get recent sequence
        recent = list(buffer)[-self.SEQUENCE_LENGTH:]
        features = np.array([t['features'] for t in recent])
        current_price = recent[-1]['price']
        timestamp = recent[-1]['timestamp']
        
        # Prepare input tensor
        x = torch.FloatTensor(features).unsqueeze(0).to(self.device)
        
        # Predict
        model = self._models[symbol]
        model.eval()
        
        with torch.no_grad():
            prediction = model(x)
            pred_change = prediction.item()
        
        # Calculate predicted price
        predicted_price = current_price * (1 + pred_change / 100)
        
        # Determine direction and confidence
        abs_change = abs(pred_change)
        if abs_change < 0.01:  # < 0.01% change
            direction = 'neutral'
            confidence = 0.5
        else:
            direction = 'up' if pred_change > 0 else 'down'
            confidence = min(0.95, 0.5 + abs_change * 10)
        
        result = PredictionResult(
            symbol=symbol,
            timestamp=timestamp,
            current_price=current_price,
            predicted_price=predicted_price,
            predicted_change=pred_change,
            confidence=confidence,
            direction=direction,
        )
        
        self._metrics[symbol].total_predictions += 1
        
        return result
    
    def add_training_sample(
        self,
        symbol: str,
        actual_price_1s: float,
    ) -> None:
        """
        Add a training sample with the actual price 1 second later.
        
        Called after the prediction window has elapsed.
        """
        if symbol not in self._feature_buffer:
            return
        
        buffer = list(self._feature_buffer[symbol])
        if len(buffer) < self.SEQUENCE_LENGTH + 1:
            return
        
        # Get the sequence that was used for prediction
        sequence = buffer[-(self.SEQUENCE_LENGTH + 1):-1]
        prediction_price = sequence[-1]['price']
        
        # Calculate actual return
        if prediction_price > 0:
            actual_return = (actual_price_1s - prediction_price) / prediction_price * 100
        else:
            actual_return = 0.0
        
        # Store training sample
        features = np.array([t['features'] for t in sequence])
        self._training_data[symbol].append((features, actual_return))
        
        # Limit training data size
        if len(self._training_data[symbol]) > 100000:
            self._training_data[symbol] = self._training_data[symbol][-50000:]
    
    def train_step(self, symbol: str, epochs: int = 1) -> float:
        """
        Perform a training step.
        
        Args:
            symbol: Symbol to train
            epochs: Number of training epochs
        
        Returns:
            Training loss
        """
        if not TORCH_AVAILABLE:
            return 0.0
        
        if symbol not in self._models:
            return 0.0
        
        training_data = self._training_data.get(symbol, [])
        if len(training_data) < self.MIN_TRAINING_SAMPLES:
            return 0.0
        
        model = self._models[symbol]
        optimizer = self._optimizers[symbol]
        criterion = nn.MSELoss()
        
        # Prepare dataset
        sequences = [t[0] for t in training_data]
        targets = [t[1] for t in training_data]
        
        dataset = PriceDataset(sequences, targets)
        dataloader = DataLoader(dataset, batch_size=self.BATCH_SIZE, shuffle=True)
        
        model.train()
        total_loss = 0.0
        n_batches = 0
        
        for epoch in range(epochs):
            for batch_x, batch_y in dataloader:
                batch_x = batch_x.to(self.device)
                batch_y = batch_y.to(self.device)
                
                optimizer.zero_grad()
                outputs = model(batch_x)
                loss = criterion(outputs, batch_y)
                loss.backward()
                optimizer.step()
                
                total_loss += loss.item()
                n_batches += 1
        
        avg_loss = total_loss / max(1, n_batches)
        self._metrics[symbol].last_loss = avg_loss
        self._metrics[symbol].training_epochs += epochs
        
        logger.debug(f"Training step for {symbol}: loss={avg_loss:.6f}")
        return avg_loss
    
    async def continuous_training_loop(
        self,
        symbol: str,
        interval_seconds: int = 60,
        epochs_per_step: int = 5,
    ) -> None:
        """
        Run continuous background training.
        
        Args:
            symbol: Symbol to train
            interval_seconds: Seconds between training steps
            epochs_per_step: Epochs per training step
        """
        logger.info(f"Starting continuous training for {symbol}")
        
        while True:
            try:
                loss = self.train_step(symbol, epochs=epochs_per_step)
                if loss > 0:
                    logger.info(f"Training {symbol}: loss={loss:.6f}, samples={len(self._training_data.get(symbol, []))}")
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Training error: {e}")
                await asyncio.sleep(interval_seconds)
    
    def save_model(self, symbol: str, path: str = None) -> bool:
        """Save model to disk."""
        if not TORCH_AVAILABLE or symbol not in self._models:
            return False
        
        if path is None:
            os.makedirs(self.model_dir, exist_ok=True)
            path = os.path.join(self.model_dir, f"lstm_{symbol}.pt")
        
        try:
            torch.save({
                'model_state_dict': self._models[symbol].state_dict(),
                'optimizer_state_dict': self._optimizers[symbol].state_dict(),
                'metrics': self._metrics[symbol].__dict__,
            }, path)
            logger.info(f"Saved model for {symbol} to {path}")
            return True
        except Exception as e:
            logger.error(f"Failed to save model: {e}")
            return False
    
    def load_model(self, symbol: str, path: str = None) -> bool:
        """Load model from disk."""
        if not TORCH_AVAILABLE:
            return False
        
        if path is None:
            path = os.path.join(self.model_dir, f"lstm_{symbol}.pt")
        
        if not os.path.exists(path):
            return False
        
        try:
            if symbol not in self._models:
                self._initialize_symbol(symbol)
            
            checkpoint = torch.load(path, map_location=self.device)
            self._models[symbol].load_state_dict(checkpoint['model_state_dict'])
            self._optimizers[symbol].load_state_dict(checkpoint['optimizer_state_dict'])
            
            if 'metrics' in checkpoint:
                for key, value in checkpoint['metrics'].items():
                    setattr(self._metrics[symbol], key, value)
            
            logger.info(f"Loaded model for {symbol} from {path}")
            return True
        except Exception as e:
            logger.error(f"Failed to load model: {e}")
            return False
    
    def export_to_onnx(self, symbol: str, path: str = None) -> bool:
        """
        Export model to ONNX format for fast inference.
        
        Args:
            symbol: Symbol model to export
            path: Output path (optional)
        
        Returns:
            True if successful
        """
        if not TORCH_AVAILABLE or symbol not in self._models:
            return False
        
        if path is None:
            os.makedirs(self.model_dir, exist_ok=True)
            path = os.path.join(self.model_dir, f"lstm_{symbol}.onnx")
        
        try:
            model = self._models[symbol]
            model.eval()
            
            # Create dummy input
            dummy_input = torch.randn(1, self.SEQUENCE_LENGTH, self.INPUT_FEATURES).to(self.device)
            
            torch.onnx.export(
                model,
                dummy_input,
                path,
                input_names=['input'],
                output_names=['output'],
                dynamic_axes={
                    'input': {0: 'batch_size'},
                    'output': {0: 'batch_size'},
                },
                opset_version=11,
            )
            
            logger.info(f"Exported ONNX model for {symbol} to {path}")
            return True
        except Exception as e:
            logger.error(f"Failed to export ONNX: {e}")
            return False
    
    def get_metrics(self, symbol: str) -> Optional[ModelMetrics]:
        """Get metrics for a symbol."""
        return self._metrics.get(symbol)
    
    def get_all_metrics(self) -> Dict[str, dict]:
        """Get metrics for all symbols."""
        return {
            symbol: metrics.__dict__
            for symbol, metrics in self._metrics.items()
        }
    
    @property
    def is_available(self) -> bool:
        """Check if PyTorch is available."""
        return TORCH_AVAILABLE


# Factory function
def create_lstm_predictor(
    symbols: List[str] = None,
    model_dir: str = "models",
) -> LSTMPricePredictor:
    """Create and return an LSTMPricePredictor instance."""
    return LSTMPricePredictor(symbols=symbols, model_dir=model_dir)
