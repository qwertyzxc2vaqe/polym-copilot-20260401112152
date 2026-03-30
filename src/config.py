"""
Secure Configuration Module
Handles environment variables, validation, and secure key management.
"""

import os
import sys
import stat
import logging
from dataclasses import dataclass
from typing import Optional, List
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path)


class ConfigurationError(Exception):
    """Raised when configuration is invalid or missing."""
    pass


@dataclass
class BlockchainConfig:
    """Blockchain-related configuration."""
    chain_id: int
    rpc_url: str
    rpc_fallbacks: List[str]
    private_key: str
    wallet_address: str
    
    # Contract addresses (Polygon Mainnet)
    USDC_ADDRESS: str = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
    USDC_DECIMALS: int = 6
    POLYMARKET_EXCHANGE: str = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
    POLYMARKET_CTF: str = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"


@dataclass
class PolymarketConfig:
    """Polymarket API configuration."""
    api_key: str
    secret: str
    passphrase: str
    clob_host: str
    gamma_host: str


@dataclass
class TradingConfig:
    """Trading parameters."""
    mode: str  # dry_run, live_test, autonomous
    starting_capital: float
    trade_allocation_pct: float
    live_test_size: float
    required_consecutive_wins: int
    max_entry_price: float
    time_threshold_seconds: int
    max_slippage: float
    daily_loss_limit: float
    max_concurrent_positions: int


@dataclass
class OracleConfig:
    """Binance oracle configuration."""
    ws_url: str
    rolling_window: int
    staleness_threshold: int


@dataclass
class LoggingConfig:
    """Logging configuration."""
    level: str
    log_file: str
    log_format: str
    enable_trade_log: bool
    trade_log_file: str


class SecureConfig:
    """
    Centralized secure configuration manager.
    Validates all required settings and provides secure access to credentials.
    """
    
    def __init__(self):
        self._validate_environment()
        self._load_configs()
        self._setup_logging()
    
    def _validate_environment(self):
        """Validate all required environment variables are present."""
        required_vars = [
            "POLYGON_CHAIN_ID",
            "RPC_URL",
            "PRIVATE_KEY",
            "CLOB_API_KEY",
            "CLOB_SECRET",
            "CLOB_PASSPHRASE",
        ]
        
        missing = []
        for var in required_vars:
            if not os.getenv(var):
                missing.append(var)
        
        if missing:
            raise ConfigurationError(
                f"Missing required environment variables: {', '.join(missing)}\n"
                f"Please copy .env.example to .env and fill in all required values."
            )
        
        # Validate private key format
        private_key = os.getenv("PRIVATE_KEY", "")
        if private_key.startswith("0x"):
            private_key = private_key[2:]
        if len(private_key) != 64:
            raise ConfigurationError(
                "PRIVATE_KEY must be a 64-character hex string (without 0x prefix)"
            )
    
    def _validate_url_scheme(self, url: str, name: str):
        """Validate URL uses secure scheme."""
        if url and not url.startswith(('https://', 'wss://')):
            raise ConfigurationError(f"{name} must use HTTPS or WSS scheme")
    
    def _load_configs(self):
        """Load all configuration sections."""
        # Blockchain
        fallbacks = []
        if os.getenv("RPC_FALLBACK_1"):
            fallbacks.append(os.getenv("RPC_FALLBACK_1"))
        if os.getenv("RPC_FALLBACK_2"):
            fallbacks.append(os.getenv("RPC_FALLBACK_2"))
        
        rpc_url = os.getenv("RPC_URL", "https://polygon-bor-rpc.publicnode.com")
        self._validate_url_scheme(rpc_url, "RPC_URL")
        for i, fb in enumerate(fallbacks):
            self._validate_url_scheme(fb, f"RPC_FALLBACK_{i+1}")
        
        self.blockchain = BlockchainConfig(
            chain_id=int(os.getenv("POLYGON_CHAIN_ID", "137")),
            rpc_url=rpc_url,
            rpc_fallbacks=fallbacks,
            private_key=os.getenv("PRIVATE_KEY", ""),
            wallet_address=os.getenv("WALLET_ADDRESS", ""),
        )
        
        # Polymarket
        clob_host = os.getenv("CLOB_HOST", "https://clob.polymarket.com")
        gamma_host = os.getenv("GAMMA_API_HOST", "https://gamma-api.polymarket.com")
        self._validate_url_scheme(clob_host, "CLOB_HOST")
        self._validate_url_scheme(gamma_host, "GAMMA_API_HOST")
        
        self.polymarket = PolymarketConfig(
            api_key=os.getenv("CLOB_API_KEY", ""),
            secret=os.getenv("CLOB_SECRET", ""),
            passphrase=os.getenv("CLOB_PASSPHRASE", ""),
            clob_host=clob_host,
            gamma_host=gamma_host,
        )
        
        # Trading
        mode = os.getenv("TRADING_MODE", "dry_run").lower()
        if mode not in ("dry_run", "live_test", "autonomous"):
            raise ConfigurationError(
                f"Invalid TRADING_MODE: {mode}. Must be: dry_run, live_test, or autonomous"
            )
        
        self.trading = TradingConfig(
            mode=mode,
            starting_capital=float(os.getenv("STARTING_CAPITAL", "100.00")),
            trade_allocation_pct=float(os.getenv("TRADE_ALLOCATION_PCT", "0.05")),
            live_test_size=float(os.getenv("LIVE_TEST_SIZE", "1.00")),
            required_consecutive_wins=int(os.getenv("REQUIRED_CONSECUTIVE_WINS", "3")),
            max_entry_price=float(os.getenv("MAX_ENTRY_PRICE", "0.99")),
            time_threshold_seconds=int(os.getenv("TIME_THRESHOLD_SECONDS", "1")),
            max_slippage=float(os.getenv("MAX_SLIPPAGE", "0.01")),
            daily_loss_limit=float(os.getenv("DAILY_LOSS_LIMIT", "10.00")),
            max_concurrent_positions=int(os.getenv("MAX_CONCURRENT_POSITIONS", "1")),
        )
        
        # Oracle
        ws_url = os.getenv("BINANCE_WS_URL", "wss://stream.binance.com:9443")
        self._validate_url_scheme(ws_url, "BINANCE_WS_URL")
        
        self.oracle = OracleConfig(
            ws_url=ws_url,
            rolling_window=int(os.getenv("ORACLE_ROLLING_WINDOW", "30")),
            staleness_threshold=int(os.getenv("PRICE_STALENESS_THRESHOLD", "5")),
        )
        
        # Logging
        self.logging = LoggingConfig(
            level=os.getenv("LOG_LEVEL", "INFO"),
            log_file=os.getenv("LOG_FILE", "logs/polym_bot.log"),
            log_format=os.getenv("LOG_FORMAT", "json"),
            enable_trade_log=os.getenv("ENABLE_TRADE_LOG", "true").lower() == "true",
            trade_log_file=os.getenv("TRADE_LOG_FILE", "logs/trades.json"),
        )
        
        # Security settings
        self.api_rate_limit = int(os.getenv("API_RATE_LIMIT", "60"))
        self.connection_timeout = int(os.getenv("CONNECTION_TIMEOUT", "30"))
        self.max_retries = int(os.getenv("MAX_RETRIES", "3"))
        self.debug_mode = os.getenv("DEBUG_MODE", "false").lower() == "true"
    
    def _setup_logging(self):
        """Configure logging with sensitive data filtering."""
        import re
        
        class SensitiveDataFilter(logging.Filter):
            """Filter to redact sensitive data from logs."""
            PATTERNS = [
                (re.compile(r'(sk_[a-zA-Z0-9]+)'), 'sk_***REDACTED***'),
                (re.compile(r'(0x[a-fA-F0-9]{64})'), '0x***REDACTED***'),
                (re.compile(r'(PRIVATE_KEY[=:]\s*)[^\s]+'), r'\1***REDACTED***'),
                (re.compile(r'(secret[=:]\s*)[^\s]+', re.I), r'\1***REDACTED***'),
                (re.compile(r'(passphrase[=:]\s*)[^\s]+', re.I), r'\1***REDACTED***'),
            ]
            
            def filter(self, record):
                if record.msg:
                    msg = str(record.msg)
                    for pattern, replacement in self.PATTERNS:
                        msg = pattern.sub(replacement, msg)
                    record.msg = msg
                return True
        
        # Ensure log directory exists
        log_dir = Path(self.logging.log_file).parent
        log_dir.mkdir(parents=True, exist_ok=True)
        
        # Configure root logger
        log_level = getattr(logging, self.logging.level.upper(), logging.INFO)
        
        handlers = [logging.StreamHandler(sys.stdout)]
        if self.logging.log_file:
            log_path = Path(self.logging.log_file)
            # Use UTF-8 encoding to avoid Windows cp1252 encoding issues
            handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
            # Set restrictive permissions (owner read/write only)
            try:
                os.chmod(log_path, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
            except Exception:
                pass  # Best effort on Windows
        
        logging.basicConfig(
            level=log_level,
            format='%(asctime)s | %(levelname)s | %(name)s | %(message)s',
            handlers=handlers,
        )
        
        # Add sensitive data filter to all handlers
        for handler in logging.root.handlers:
            handler.addFilter(SensitiveDataFilter())
    
    def get_rpc_urls(self) -> List[str]:
        """Get list of RPC URLs with fallbacks."""
        urls = [self.blockchain.rpc_url]
        urls.extend(self.blockchain.rpc_fallbacks)
        return urls
    
    def is_dry_run(self) -> bool:
        """Check if running in dry run mode."""
        return self.trading.mode == "dry_run"
    
    def is_live_test(self) -> bool:
        """Check if running in live test mode."""
        return self.trading.mode == "live_test"
    
    def is_autonomous(self) -> bool:
        """Check if running in autonomous mode."""
        return self.trading.mode == "autonomous"


# Global config instance (lazy loaded)
_config: Optional[SecureConfig] = None


def get_config() -> SecureConfig:
    """Get the global configuration instance."""
    global _config
    if _config is None:
        _config = SecureConfig()
    return _config
