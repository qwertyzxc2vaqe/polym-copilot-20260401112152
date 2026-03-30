"""
Security Hardening Module for Polymarket Trading Bot
=====================================================
Comprehensive security measures including:
- Sensitive data filtering for logs
- Rate limiting for API calls
- Input validation and sanitization
- Secure key management with memory protection
- Error handling that prevents information leakage
- Transaction verification and audit logging
- Daily loss limit enforcement
"""

import os
import re
import gc
import time
import json
import logging
import hashlib
import secrets
import asyncio
from functools import wraps
from collections import deque
from datetime import datetime, date, timezone
from typing import Optional, Callable, Any, Dict, List, Tuple
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


# ==============================================================================
# SENSITIVE DATA FILTERING
# ==============================================================================

class SensitiveDataFilter(logging.Filter):
    """
    Filter to redact sensitive data from log messages.
    
    Automatically detects and masks:
    - Private keys (hex strings)
    - API keys and secrets
    - Passphrases
    - Wallet addresses in sensitive contexts
    """
    
    PATTERNS: List[Tuple[re.Pattern, str]] = [
        # Private keys (64 hex chars with or without 0x prefix)
        (re.compile(r'(0x[a-fA-F0-9]{64})'), '0x***REDACTED_KEY***'),
        (re.compile(r'(?<![a-fA-F0-9])([a-fA-F0-9]{64})(?![a-fA-F0-9])'), '***REDACTED_KEY***'),
        # API key formats
        (re.compile(r'(sk_[a-zA-Z0-9_-]+)'), 'sk_***REDACTED***'),
        (re.compile(r'(pk_[a-zA-Z0-9_-]+)'), 'pk_***REDACTED***'),
        # Key-value patterns (case insensitive)
        (re.compile(r'(private_key[=:"\s]+)[^\s,}"\']+', re.I), r'\1***REDACTED***'),
        (re.compile(r'(secret[=:"\s]+)[^\s,}"\']+', re.I), r'\1***REDACTED***'),
        (re.compile(r'(passphrase[=:"\s]+)[^\s,}"\']+', re.I), r'\1***REDACTED***'),
        (re.compile(r'(api_key[=:"\s]+)[^\s,}"\']+', re.I), r'\1***REDACTED***'),
        (re.compile(r'(api_secret[=:"\s]+)[^\s,}"\']+', re.I), r'\1***REDACTED***'),
        (re.compile(r'(password[=:"\s]+)[^\s,}"\']+', re.I), r'\1***REDACTED***'),
        (re.compile(r'(bearer\s+)[a-zA-Z0-9_-]+', re.I), r'\1***REDACTED***'),
        (re.compile(r'(authorization[=:"\s]+)[^\s,}"\']+', re.I), r'\1***REDACTED***'),
    ]
    
    def filter(self, record: logging.LogRecord) -> bool:
        """Filter and redact sensitive data from log record."""
        if record.msg:
            msg = str(record.msg)
            for pattern, replacement in self.PATTERNS:
                msg = pattern.sub(replacement, msg)
            record.msg = msg
        
        # Also filter args if present
        if record.args:
            new_args = []
            for arg in record.args:
                if isinstance(arg, str):
                    for pattern, replacement in self.PATTERNS:
                        arg = pattern.sub(replacement, arg)
                new_args.append(arg)
            record.args = tuple(new_args)
        
        return True
    
    @classmethod
    def redact_string(cls, text: str) -> str:
        """Utility to redact sensitive data from any string."""
        for pattern, replacement in cls.PATTERNS:
            text = pattern.sub(replacement, text)
        return text


def setup_secure_logging(
    log_level: str = "INFO",
    log_file: Optional[str] = None,
    add_filter: bool = True
) -> logging.Logger:
    """
    Set up secure logging with sensitive data filtering.
    
    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        log_file: Optional file path for log output
        add_filter: Whether to add the sensitive data filter
    
    Returns:
        Configured logger instance
    """
    root_logger = logging.getLogger()
    level = getattr(logging, log_level.upper(), logging.INFO)
    root_logger.setLevel(level)
    
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)s | %(name)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    if add_filter:
        console_handler.addFilter(SensitiveDataFilter())
    root_logger.addHandler(console_handler)
    
    # File handler with UTF-8 encoding
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        if add_filter:
            file_handler.addFilter(SensitiveDataFilter())
        root_logger.addHandler(file_handler)
    
    return root_logger


# ==============================================================================
# RATE LIMITING
# ==============================================================================

class RateLimiter:
    """
    Token bucket rate limiter for API calls.
    
    Provides both synchronous and asynchronous rate limiting
    with configurable calls per time window.
    """
    
    def __init__(self, max_calls: int, time_window: float):
        """
        Initialize rate limiter.
        
        Args:
            max_calls: Maximum number of calls allowed in the time window
            time_window: Time window in seconds
        """
        self.max_calls = max_calls
        self.time_window = time_window
        self._timestamps: deque = deque()
        self._lock = asyncio.Lock()  # Always create lock for async usage
    
    def _clean_old_timestamps(self):
        """Remove timestamps outside the current window."""
        current_time = time.monotonic()
        cutoff = current_time - self.time_window
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()
    
    def is_allowed(self) -> bool:
        """
        Check if a call is allowed without consuming a slot.
        
        Returns:
            True if call would be allowed
        """
        self._clean_old_timestamps()
        return len(self._timestamps) < self.max_calls
    
    def try_acquire(self) -> bool:
        """
        Try to acquire a rate limit slot.
        
        Returns:
            True if slot acquired, False if rate limited
        """
        self._clean_old_timestamps()
        if len(self._timestamps) < self.max_calls:
            self._timestamps.append(time.monotonic())
            return True
        return False
    
    def wait_time(self) -> float:
        """
        Get the time to wait until a slot is available.
        
        Returns:
            Seconds to wait (0 if slot available now)
        """
        self._clean_old_timestamps()
        if len(self._timestamps) < self.max_calls:
            return 0.0
        
        oldest = self._timestamps[0]
        wait = (oldest + self.time_window) - time.monotonic()
        return max(0.0, wait)
    
    async def wait_if_needed(self):
        """Async wait until rate limit allows."""
        async with self._lock:
            wait = self.wait_time()
            if wait > 0:
                logger.debug(f"Rate limited, waiting {wait:.2f}s")
                await asyncio.sleep(wait)
            self.try_acquire()
    
    def wait_if_needed_sync(self):
        """Synchronous wait until rate limit allows."""
        wait = self.wait_time()
        if wait > 0:
            logger.debug(f"Rate limited, waiting {wait:.2f}s")
            time.sleep(wait)
        self.try_acquire()


# Global rate limiters for different services
_rate_limiters: Dict[str, RateLimiter] = {}


def get_rate_limiter(
    name: str,
    max_calls: int = 60,
    time_window: float = 60.0
) -> RateLimiter:
    """
    Get or create a named rate limiter.
    
    Args:
        name: Unique name for the rate limiter
        max_calls: Maximum calls per window
        time_window: Window size in seconds
    
    Returns:
        RateLimiter instance
    """
    if name not in _rate_limiters:
        _rate_limiters[name] = RateLimiter(max_calls, time_window)
    return _rate_limiters[name]


def rate_limited(max_calls: int = 60, time_window: float = 60.0, name: Optional[str] = None):
    """
    Decorator for rate limiting async functions.
    
    Args:
        max_calls: Maximum calls per time window
        time_window: Time window in seconds
        name: Optional name for shared rate limiter
    """
    def decorator(func: Callable) -> Callable:
        limiter_name = name or f"{func.__module__}.{func.__name__}"
        limiter = get_rate_limiter(limiter_name, max_calls, time_window)
        
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            await limiter.wait_if_needed()
            return await func(*args, **kwargs)
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            limiter.wait_if_needed_sync()
            return func(*args, **kwargs)
        
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    
    return decorator


# ==============================================================================
# INPUT VALIDATION
# ==============================================================================

class ValidationError(Exception):
    """Raised when input validation fails."""
    pass


class Validator:
    """Input validation utilities for trading bot."""
    
    # Patterns for validation
    HEX_64_PATTERN = re.compile(r'^[a-fA-F0-9]{64}$')
    ETH_ADDRESS_PATTERN = re.compile(r'^0x[a-fA-F0-9]{40}$')
    TOKEN_ID_PATTERN = re.compile(r'^[a-fA-F0-9]{64}$')
    TX_HASH_PATTERN = re.compile(r'^0x[a-fA-F0-9]{64}$')
    
    @staticmethod
    def validate_private_key(key: str) -> bool:
        """
        Validate private key format (64 hex chars).
        
        Args:
            key: Private key string (with or without 0x prefix)
        
        Returns:
            True if valid format
        """
        if not key:
            return False
        # Remove 0x prefix if present
        if key.startswith('0x') or key.startswith('0X'):
            key = key[2:]
        return bool(Validator.HEX_64_PATTERN.match(key))
    
    @staticmethod
    def validate_address(address: str) -> bool:
        """
        Validate Ethereum address format.
        
        Args:
            address: Ethereum address (must have 0x prefix)
        
        Returns:
            True if valid format
        """
        if not address:
            return False
        return bool(Validator.ETH_ADDRESS_PATTERN.match(address))
    
    @staticmethod
    def validate_token_id(token_id: str) -> bool:
        """
        Validate Polymarket token ID.
        
        Args:
            token_id: Token ID (64 hex chars)
        
        Returns:
            True if valid format
        """
        if not token_id:
            return False
        return bool(Validator.TOKEN_ID_PATTERN.match(token_id))
    
    @staticmethod
    def validate_price(price: float) -> bool:
        """
        Validate price is in valid range (0-1).
        
        Args:
            price: Price value
        
        Returns:
            True if valid range
        """
        return isinstance(price, (int, float)) and 0.0 <= price <= 1.0
    
    @staticmethod
    def validate_amount(amount: float, min_amount: float = 0.0, max_amount: float = float('inf')) -> bool:
        """
        Validate amount is positive and within bounds.
        
        Args:
            amount: Amount value
            min_amount: Minimum allowed amount
            max_amount: Maximum allowed amount
        
        Returns:
            True if valid
        """
        return isinstance(amount, (int, float)) and min_amount <= amount <= max_amount
    
    @staticmethod
    def validate_tx_hash(tx_hash: str) -> bool:
        """
        Validate transaction hash format.
        
        Args:
            tx_hash: Transaction hash string
        
        Returns:
            True if valid format
        """
        if not tx_hash:
            return False
        return bool(Validator.TX_HASH_PATTERN.match(tx_hash))
    
    @staticmethod
    def sanitize_string(value: str, max_length: int = 256) -> str:
        """
        Sanitize string input by removing dangerous characters.
        
        Args:
            value: Input string
            max_length: Maximum allowed length
        
        Returns:
            Sanitized string
        """
        if not isinstance(value, str):
            return ""
        # Remove control characters and limit length
        sanitized = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', value)
        return sanitized[:max_length]
    
    @classmethod
    def validate_order_params(
        cls,
        token_id: str,
        price: float,
        size: float,
        side: str
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate all order parameters.
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        if not cls.validate_token_id(token_id):
            return False, "Invalid token ID format"
        if not cls.validate_price(price):
            return False, f"Invalid price: must be between 0 and 1, got {price}"
        if not cls.validate_amount(size, min_amount=0.01):
            return False, f"Invalid size: must be >= 0.01, got {size}"
        if side.upper() not in ("BUY", "SELL"):
            return False, f"Invalid side: must be BUY or SELL, got {side}"
        return True, None


# ==============================================================================
# SECURE KEY MANAGEMENT
# ==============================================================================

class SecureKeyManager:
    """
    Secure access to private keys with memory protection.
    
    Features:
    - Hash verification to detect key tampering
    - Secure memory wiping (best effort)
    - Access counting and logging
    """
    
    def __init__(self):
        self._key_hash: Optional[str] = None
        self._access_count: int = 0
        self._loaded_at: Optional[datetime] = None
    
    def load_key(self) -> str:
        """
        Load private key from environment.
        
        Returns:
            Private key string
        
        Raises:
            SecureException: If key is not configured
        """
        key = os.getenv("PRIVATE_KEY", "")
        if not key:
            raise SecureException("PRIVATE_KEY not configured")
        
        # Normalize key (remove 0x if present)
        if key.startswith("0x") or key.startswith("0X"):
            key = key[2:]
        
        if not Validator.validate_private_key(key):
            raise SecureException("Invalid private key format")
        
        # Store hash for verification
        self._key_hash = hashlib.sha256(key.encode()).hexdigest()
        self._loaded_at = datetime.now()
        self._access_count += 1
        
        logger.info(f"Private key loaded (access #{self._access_count})")
        return key
    
    def verify_key_unchanged(self) -> bool:
        """
        Verify key hasn't been tampered with since load.
        
        Returns:
            True if key matches stored hash
        """
        if not self._key_hash:
            return False
        
        key = os.getenv("PRIVATE_KEY", "")
        if key.startswith("0x") or key.startswith("0X"):
            key = key[2:]
        
        current_hash = hashlib.sha256(key.encode()).hexdigest()
        return secrets.compare_digest(self._key_hash, current_hash)
    
    def wipe_from_memory(self):
        """
        Attempt to clear sensitive data from memory.
        
        Note: Python doesn't guarantee memory wiping, but we do our best.
        """
        self._key_hash = None
        self._loaded_at = None
        self._access_count = 0
        gc.collect()  # Force garbage collection
        logger.info("Key manager memory wiped")
    
    def get_access_count(self) -> int:
        """Get the number of times the key has been accessed."""
        return self._access_count
    
    def get_loaded_at(self) -> Optional[datetime]:
        """Get timestamp when key was last loaded."""
        return self._loaded_at


# Global key manager
_key_manager: Optional[SecureKeyManager] = None


def get_key_manager() -> SecureKeyManager:
    """Get or create the global key manager."""
    global _key_manager
    if _key_manager is None:
        _key_manager = SecureKeyManager()
    return _key_manager


# ==============================================================================
# ERROR HANDLING
# ==============================================================================

class SecureException(Exception):
    """
    Exception that doesn't expose sensitive data in traceback.
    
    Use this for errors that might contain sensitive information.
    The actual error details are logged securely but not exposed
    to callers.
    """
    
    def __init__(self, public_message: str, internal_details: Optional[str] = None):
        self.public_message = public_message
        self._internal_details = internal_details
        super().__init__(public_message)
    
    def __str__(self):
        return self.public_message
    
    def log_internal(self, log: logging.Logger = logger):
        """Log internal details securely (will be filtered)."""
        if self._internal_details:
            log.debug(f"Internal error details: {self._internal_details}")


def secure_error_handler(func: Callable) -> Callable:
    """
    Decorator to catch and sanitize error messages.
    
    Prevents leaking sensitive information in exception messages
    and stack traces.
    """
    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except SecureException:
            raise
        except Exception as e:
            error_msg = str(e)
            # Redact sensitive data from error message
            safe_msg = SensitiveDataFilter.redact_string(error_msg)
            logger.error(f"Error in {func.__name__}: {safe_msg}")
            raise SecureException(
                f"Operation failed: {func.__name__}",
                internal_details=error_msg
            )
    
    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except SecureException:
            raise
        except Exception as e:
            error_msg = str(e)
            safe_msg = SensitiveDataFilter.redact_string(error_msg)
            logger.error(f"Error in {func.__name__}: {safe_msg}")
            raise SecureException(
                f"Operation failed: {func.__name__}",
                internal_details=error_msg
            )
    
    if asyncio.iscoroutinefunction(func):
        return async_wrapper
    return sync_wrapper


# ==============================================================================
# CONNECTION SECURITY
# ==============================================================================

class SecureConnection:
    """Secure HTTP/WebSocket connection wrapper."""
    
    ALLOWED_SCHEMES = {'https', 'wss'}
    
    @staticmethod
    def validate_url(url: str) -> bool:
        """
        Validate URL is using HTTPS/WSS.
        
        Args:
            url: URL to validate
        
        Returns:
            True if URL uses secure scheme
        """
        if not url:
            return False
        
        # Extract scheme
        scheme_match = re.match(r'^([a-zA-Z]+)://', url)
        if not scheme_match:
            return False
        
        scheme = scheme_match.group(1).lower()
        return scheme in SecureConnection.ALLOWED_SCHEMES
    
    @staticmethod
    def get_secure_headers() -> dict:
        """
        Get headers that don't expose sensitive info.
        
        Returns:
            Dict of safe HTTP headers
        """
        return {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'User-Agent': 'PolymBot/1.0',
        }
    
    @staticmethod
    def validate_ssl_context() -> bool:
        """Check if SSL context is properly configured."""
        import ssl
        try:
            context = ssl.create_default_context()
            return context.verify_mode == ssl.CERT_REQUIRED
        except Exception:
            return False


# ==============================================================================
# TRANSACTION VERIFICATION
# ==============================================================================

@dataclass
class TransactionVerification:
    """Result of transaction verification."""
    is_valid: bool
    tx_hash: Optional[str] = None
    block_number: Optional[int] = None
    status: str = "unknown"
    error: Optional[str] = None


class TransactionVerifier:
    """
    Verify transactions before and after signing.
    
    Provides pre-flight checks and post-execution verification.
    """
    
    def __init__(self, max_gas_price_gwei: float = 500.0):
        self.max_gas_price_gwei = max_gas_price_gwei
    
    def verify_before_signing(
        self,
        to_address: str,
        value: float,
        gas_price_gwei: float,
        data: Optional[str] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Verify transaction parameters before signing.
        
        Returns:
            Tuple of (is_safe, warning_message)
        """
        # Validate recipient address
        if not Validator.validate_address(to_address):
            return False, "Invalid recipient address"
        
        # Check gas price
        if gas_price_gwei > self.max_gas_price_gwei:
            return False, f"Gas price {gas_price_gwei} exceeds maximum {self.max_gas_price_gwei}"
        
        # Check value is reasonable
        if value < 0:
            return False, "Negative transaction value"
        
        return True, None
    
    def verify_tx_hash(self, tx_hash: str) -> bool:
        """
        Verify transaction hash format.
        
        Args:
            tx_hash: Transaction hash to verify
        
        Returns:
            True if valid format
        """
        return Validator.validate_tx_hash(tx_hash)


# ==============================================================================
# DAILY LOSS LIMIT ENFORCEMENT
# ==============================================================================

@dataclass
class DailyLimitStatus:
    """Status of daily loss limits."""
    date: date
    total_loss: float
    limit: float
    is_exceeded: bool
    remaining: float


class DailyLossLimiter:
    """
    Enforce daily loss limits for trading.
    
    Tracks cumulative losses per day and blocks trading
    when limits are exceeded.
    """
    
    def __init__(self, daily_limit: float):
        """
        Initialize loss limiter.
        
        Args:
            daily_limit: Maximum daily loss in USDC
        """
        self.daily_limit = daily_limit
        self._losses: Dict[str, float] = {}
    
    def _get_today_key(self) -> str:
        """Get key for today's date."""
        return date.today().isoformat()
    
    def record_loss(self, amount: float):
        """
        Record a loss.
        
        Args:
            amount: Loss amount (positive number)
        """
        if amount <= 0:
            return
        
        key = self._get_today_key()
        self._losses[key] = self._losses.get(key, 0.0) + amount
        logger.info(f"Recorded loss: ${amount:.2f} (daily total: ${self._losses[key]:.2f})")
    
    def record_profit(self, amount: float):
        """
        Record a profit (reduces daily loss total).
        
        Args:
            amount: Profit amount (positive number)
        """
        if amount <= 0:
            return
        
        key = self._get_today_key()
        self._losses[key] = max(0.0, self._losses.get(key, 0.0) - amount)
    
    def is_trading_allowed(self) -> bool:
        """
        Check if trading is allowed under loss limit.
        
        Returns:
            True if daily loss limit not exceeded
        """
        if self.daily_limit <= 0:
            return True  # No limit configured
        
        key = self._get_today_key()
        current_loss = self._losses.get(key, 0.0)
        return current_loss < self.daily_limit
    
    def get_status(self) -> DailyLimitStatus:
        """
        Get current daily limit status.
        
        Returns:
            DailyLimitStatus with current state
        """
        today = date.today()
        key = today.isoformat()
        total_loss = self._losses.get(key, 0.0)
        
        return DailyLimitStatus(
            date=today,
            total_loss=total_loss,
            limit=self.daily_limit,
            is_exceeded=total_loss >= self.daily_limit,
            remaining=max(0.0, self.daily_limit - total_loss)
        )
    
    def get_remaining_budget(self) -> float:
        """
        Get remaining loss budget for today.
        
        Returns:
            Remaining budget in USDC
        """
        if self.daily_limit <= 0:
            return float('inf')
        
        key = self._get_today_key()
        current_loss = self._losses.get(key, 0.0)
        return max(0.0, self.daily_limit - current_loss)
    
    def cleanup_old_records(self, days_to_keep: int = 30):
        """Remove loss records older than specified days."""
        cutoff = date.today().toordinal() - days_to_keep
        keys_to_remove = [
            key for key in self._losses.keys()
            if date.fromisoformat(key).toordinal() < cutoff
        ]
        for key in keys_to_remove:
            del self._losses[key]


# ==============================================================================
# AUDIT LOGGING
# ==============================================================================

@dataclass
class AuditEntry:
    """Audit log entry for a trade or action."""
    timestamp: datetime
    action: str
    details: Dict[str, Any]
    status: str
    tx_hash: Optional[str] = None
    error: Optional[str] = None


class AuditLogger:
    """
    Audit logging for all trades and security events.
    
    Maintains a tamper-evident log of all trading activity
    for compliance and debugging.
    """
    
    # Maximum entries to keep in memory to prevent unbounded growth
    MAX_IN_MEMORY_ENTRIES = 10000
    
    def __init__(self, log_file: Optional[str] = None):
        """
        Initialize audit logger.
        
        Args:
            log_file: Optional path for audit log file
        """
        self.log_file = log_file
        self._entries: deque = deque(maxlen=self.MAX_IN_MEMORY_ENTRIES)
        self._entry_hashes: deque = deque(maxlen=self.MAX_IN_MEMORY_ENTRIES)
    
    def _compute_entry_hash(self, entry: AuditEntry, prev_hash: str) -> str:
        """Compute hash of entry chained with previous hash."""
        data = json.dumps({
            'timestamp': entry.timestamp.isoformat(),
            'action': entry.action,
            'details': entry.details,
            'status': entry.status,
            'tx_hash': entry.tx_hash,
            'prev_hash': prev_hash,
        }, sort_keys=True)
        return hashlib.sha256(data.encode()).hexdigest()
    
    def _persist_hash(self, entry_hash: str):
        """Persist hash to external file for tamper detection."""
        try:
            hash_file = Path("data/.audit_hashes.log")
            hash_file.parent.mkdir(parents=True, exist_ok=True)
            with open(hash_file, 'a') as f:
                f.write(f"{datetime.now(timezone.utc).isoformat()},{entry_hash}\n")
        except Exception as e:
            logger.warning(f"Could not persist audit hash: {e}")
    
    def log_trade(
        self,
        action: str,
        token_id: str,
        side: str,
        price: float,
        size: float,
        status: str,
        tx_hash: Optional[str] = None,
        error: Optional[str] = None
    ):
        """
        Log a trade action.
        
        Args:
            action: Action type (ORDER_PLACED, ORDER_FILLED, etc.)
            token_id: Market token ID
            side: BUY or SELL
            price: Order price
            size: Order size
            status: Status (SUCCESS, FAILED, PENDING)
            tx_hash: Optional transaction hash
            error: Optional error message
        """
        # Sanitize error message
        if error:
            error = SensitiveDataFilter.redact_string(error)
        
        entry = AuditEntry(
            timestamp=datetime.now(),
            action=action,
            details={
                'token_id': token_id,
                'side': side,
                'price': price,
                'size': size,
            },
            status=status,
            tx_hash=tx_hash,
            error=error,
        )
        
        # Compute chained hash
        prev_hash = self._entry_hashes[-1] if self._entry_hashes else "genesis"
        entry_hash = self._compute_entry_hash(entry, prev_hash)
        
        self._entries.append(entry)
        self._entry_hashes.append(entry_hash)
        
        # Persist hash to external file for tamper detection
        self._persist_hash(entry_hash)
        
        # Log to file if configured
        if self.log_file:
            self._write_to_file(entry, entry_hash)
        
        logger.info(f"AUDIT: {action} {side} {size}@{price} - {status}")
    
    def log_security_event(
        self,
        event_type: str,
        details: Dict[str, Any],
        severity: str = "INFO"
    ):
        """
        Log a security event.
        
        Args:
            event_type: Type of security event
            details: Event details
            severity: INFO, WARNING, or CRITICAL
        """
        # Redact sensitive data from details
        safe_details = {}
        for key, value in details.items():
            if isinstance(value, str):
                safe_details[key] = SensitiveDataFilter.redact_string(value)
            else:
                safe_details[key] = value
        
        entry = AuditEntry(
            timestamp=datetime.now(),
            action=f"SECURITY_{event_type}",
            details=safe_details,
            status=severity,
        )
        
        prev_hash = self._entry_hashes[-1] if self._entry_hashes else "genesis"
        entry_hash = self._compute_entry_hash(entry, prev_hash)
        
        self._entries.append(entry)
        self._entry_hashes.append(entry_hash)
        
        # Persist hash to external file for tamper detection
        self._persist_hash(entry_hash)
        
        if self.log_file:
            self._write_to_file(entry, entry_hash)
        
        log_method = getattr(logger, severity.lower(), logger.info)
        log_method(f"SECURITY: {event_type} - {safe_details}")
    
    def _write_to_file(self, entry: AuditEntry, entry_hash: str):
        """Write entry to audit log file."""
        try:
            log_path = Path(self.log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(log_path, 'a') as f:
                record = {
                    'timestamp': entry.timestamp.isoformat(),
                    'action': entry.action,
                    'details': entry.details,
                    'status': entry.status,
                    'tx_hash': entry.tx_hash,
                    'error': entry.error,
                    'hash': entry_hash,
                }
                f.write(json.dumps(record) + '\n')
        except Exception as e:
            logger.error(f"Failed to write audit log: {e}")
    
    def verify_integrity(self) -> bool:
        """
        Verify audit log integrity.
        
        Returns:
            True if log chain is valid
        """
        if not self._entries:
            return True
        
        prev_hash = "genesis"
        for entry, stored_hash in zip(self._entries, self._entry_hashes):
            computed_hash = self._compute_entry_hash(entry, prev_hash)
            if computed_hash != stored_hash:
                logger.error("Audit log integrity check failed!")
                return False
            prev_hash = stored_hash
        
        return True
    
    def get_entries(
        self,
        action_filter: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 100
    ) -> List[AuditEntry]:
        """
        Get audit log entries with optional filtering.
        
        Args:
            action_filter: Optional action type filter
            since: Optional start timestamp
            limit: Maximum entries to return
        
        Returns:
            List of matching audit entries
        """
        entries = self._entries
        
        if action_filter:
            entries = [e for e in entries if action_filter in e.action]
        
        if since:
            entries = [e for e in entries if e.timestamp >= since]
        
        return entries[-limit:]


# ==============================================================================
# IP BINDING (Optional)
# ==============================================================================

class IPBindingManager:
    """
    Optional IP binding for additional security.
    
    Restricts API access to specific IP addresses.
    """
    
    def __init__(self, allowed_ips: Optional[List[str]] = None):
        """
        Initialize IP binding.
        
        Args:
            allowed_ips: List of allowed IP addresses
        """
        self.allowed_ips = set(allowed_ips or [])
        self.enabled = bool(allowed_ips)
    
    def add_ip(self, ip: str):
        """Add an IP to the allowed list."""
        self.allowed_ips.add(ip)
        logger.info(f"Added allowed IP: {ip}")
    
    def remove_ip(self, ip: str):
        """Remove an IP from the allowed list."""
        self.allowed_ips.discard(ip)
        logger.info(f"Removed allowed IP: {ip}")
    
    def is_allowed(self, ip: str) -> bool:
        """
        Check if IP is allowed.
        
        Args:
            ip: IP address to check
        
        Returns:
            True if IP is allowed (or binding disabled)
        """
        if not self.enabled:
            return True
        return ip in self.allowed_ips
    
    @staticmethod
    def get_current_ip() -> Optional[str]:
        """
        Get current public IP address.
        
        Returns:
            Public IP or None if unable to determine
        """
        import socket
        try:
            # This doesn't actually connect but gives us our IP
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return None


# ==============================================================================
# SECURITY CONTEXT MANAGER
# ==============================================================================

class SecurityContext:
    """
    Unified security context for trading operations.
    
    Combines all security components into a single manager.
    """
    
    def __init__(
        self,
        daily_loss_limit: float = 0.0,
        api_rate_limit: int = 60,
        audit_log_file: Optional[str] = None,
        allowed_ips: Optional[List[str]] = None,
    ):
        self.key_manager = SecureKeyManager()
        self.loss_limiter = DailyLossLimiter(daily_loss_limit)
        self.rate_limiter = RateLimiter(api_rate_limit, 60.0)
        self.audit_logger = AuditLogger(audit_log_file)
        self.ip_binding = IPBindingManager(allowed_ips)
        self.tx_verifier = TransactionVerifier()
        self.validator = Validator()
        
        logger.info("Security context initialized")
    
    def pre_trade_checks(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str
    ) -> Tuple[bool, Optional[str]]:
        """
        Run all pre-trade security checks.
        
        Returns:
            Tuple of (is_allowed, error_message)
        """
        # Validate parameters
        is_valid, error = Validator.validate_order_params(token_id, price, size, side)
        if not is_valid:
            return False, error
        
        # Check daily loss limit
        if not self.loss_limiter.is_trading_allowed():
            return False, "Daily loss limit exceeded"
        
        # Check rate limit
        if not self.rate_limiter.is_allowed():
            return False, f"Rate limited, wait {self.rate_limiter.wait_time():.1f}s"
        
        # Verify key is still valid
        if not self.key_manager.verify_key_unchanged():
            return False, "Key verification failed"
        
        return True, None
    
    def record_trade_result(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
        pnl: float,
        tx_hash: Optional[str] = None,
        status: str = "SUCCESS"
    ):
        """Record trade result for loss tracking and audit."""
        # Track P&L
        if pnl < 0:
            self.loss_limiter.record_loss(abs(pnl))
        else:
            self.loss_limiter.record_profit(pnl)
        
        # Audit log
        self.audit_logger.log_trade(
            action="TRADE_EXECUTED",
            token_id=token_id,
            side=side,
            price=price,
            size=size,
            status=status,
            tx_hash=tx_hash,
        )


# ==============================================================================
# UTILITY FUNCTIONS
# ==============================================================================

def generate_secure_nonce() -> str:
    """Generate a cryptographically secure nonce."""
    return secrets.token_hex(16)


def constant_time_compare(a: str, b: str) -> bool:
    """Compare two strings in constant time to prevent timing attacks."""
    return secrets.compare_digest(a, b)


def secure_hash(data: str, salt: Optional[str] = None) -> str:
    """
    Create a secure hash of data with optional salt.
    
    Args:
        data: Data to hash
        salt: Optional salt (generated if not provided)
    
    Returns:
        Hex-encoded hash
    """
    if salt is None:
        salt = secrets.token_hex(16)
    
    salted = f"{salt}:{data}"
    return hashlib.sha256(salted.encode()).hexdigest()


# ==============================================================================
# MODULE INITIALIZATION
# ==============================================================================

def init_security(
    daily_loss_limit: float = 0.0,
    api_rate_limit: int = 60,
    audit_log_file: Optional[str] = None,
    log_level: str = "INFO",
) -> SecurityContext:
    """
    Initialize security module with all components.
    
    Args:
        daily_loss_limit: Max daily loss in USDC
        api_rate_limit: Max API calls per minute
        audit_log_file: Path for audit log
        log_level: Logging level
    
    Returns:
        Configured SecurityContext
    """
    # Set up secure logging
    setup_secure_logging(log_level)
    
    # Create security context
    context = SecurityContext(
        daily_loss_limit=daily_loss_limit,
        api_rate_limit=api_rate_limit,
        audit_log_file=audit_log_file,
    )
    
    logger.info("Security module initialized")
    return context
