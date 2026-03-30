"""
Rate Limiter with Circuit Breaker for Polymarket Arbitrage Bot.

Implements Token Bucket rate limiting with automatic throttling,
cooldown periods, and dashboard status export for high-frequency trading.
"""

import asyncio
import logging
import time
import functools
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Callable, Any, Tuple
from enum import Enum


logger = logging.getLogger(__name__)


class ThrottleLevel(Enum):
    """Throttle severity levels."""
    NORMAL = "normal"      # < 80% utilization
    WARNING = "warning"    # 80-90% utilization
    CRITICAL = "critical"  # > 90% utilization
    COOLDOWN = "cooldown"  # Forced cooldown active


@dataclass
class ThrottleStatus:
    """Status of the rate limiting system."""
    is_throttling: bool
    throttled_services: List[str]
    warning_message: str
    suggested_cooldown_seconds: float
    level: ThrottleLevel = ThrottleLevel.NORMAL
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class BucketState:
    """Exported state of a single bucket for dashboard."""
    name: str
    current_tokens: float
    capacity: int
    refill_rate: float
    utilization: float
    throttle_level: ThrottleLevel
    last_acquire_time: Optional[datetime]
    total_acquires: int
    total_throttles: int
    in_cooldown: bool
    cooldown_remaining_seconds: float


class TokenBucket:
    """
    Token Bucket rate limiter implementation.
    
    Thread-safe async implementation using asyncio.Lock.
    Supports gradual refill and burst capacity.
    """
    
    # Throttle thresholds
    WARNING_THRESHOLD = 0.80   # 80% utilization
    CRITICAL_THRESHOLD = 0.90  # 90% utilization
    
    # Cooldown settings
    COOLDOWN_DURATION = 5.0    # Seconds to cooldown when critical
    
    def __init__(self, capacity: int, refill_rate: float, name: str):
        """
        Initialize token bucket.
        
        Args:
            capacity: Maximum tokens the bucket can hold
            refill_rate: Tokens added per second
            name: Identifier for this bucket (e.g., 'polymarket_rest')
        """
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.name = name
        
        self._tokens = float(capacity)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()
        
        # Statistics
        self._total_acquires = 0
        self._total_throttles = 0
        self._last_acquire_time: Optional[datetime] = None
        
        # Cooldown state
        self._in_cooldown = False
        self._cooldown_end: Optional[float] = None
        
        logger.info(
            f"TokenBucket '{name}' initialized: capacity={capacity}, "
            f"refill_rate={refill_rate}/sec"
        )
    
    def _refill(self) -> None:
        """Refill tokens based on elapsed time. Must be called with lock held."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        refill_amount = elapsed * self.refill_rate
        
        self._tokens = min(self.capacity, self._tokens + refill_amount)
        self._last_refill = now
    
    def _check_cooldown_expired(self) -> None:
        """Check and clear expired cooldown. Must be called with lock held."""
        if self._in_cooldown and self._cooldown_end:
            if time.monotonic() >= self._cooldown_end:
                self._in_cooldown = False
                self._cooldown_end = None
                logger.info(f"TokenBucket '{self.name}': Cooldown period ended")
    
    async def acquire(self, tokens: int = 1) -> bool:
        """
        Try to acquire tokens from the bucket.
        
        Args:
            tokens: Number of tokens to acquire
            
        Returns:
            True if tokens were acquired, False otherwise
        """
        async with self._lock:
            self._check_cooldown_expired()
            
            # Block during cooldown
            if self._in_cooldown:
                self._total_throttles += 1
                logger.debug(
                    f"TokenBucket '{self.name}': Blocked during cooldown "
                    f"({self.cooldown_remaining:.1f}s remaining)"
                )
                return False
            
            self._refill()
            
            if self._tokens >= tokens:
                self._tokens -= tokens
                self._total_acquires += 1
                self._last_acquire_time = datetime.now(timezone.utc)
                
                # Check if we need to enter cooldown
                utilization = self.utilization
                if utilization >= self.CRITICAL_THRESHOLD:
                    self._enter_cooldown()
                
                return True
            else:
                self._total_throttles += 1
                logger.warning(
                    f"TokenBucket '{self.name}': Rate limit hit "
                    f"(requested={tokens}, available={self._tokens:.1f})"
                )
                return False
    
    def _enter_cooldown(self) -> None:
        """Enter cooldown period. Must be called with lock held."""
        self._in_cooldown = True
        self._cooldown_end = time.monotonic() + self.COOLDOWN_DURATION
        logger.warning(
            f"TokenBucket '{self.name}': Entering cooldown for "
            f"{self.COOLDOWN_DURATION}s (utilization >= {self.CRITICAL_THRESHOLD*100:.0f}%)"
        )
    
    async def acquire_or_wait(self, tokens: int = 1, max_wait: float = 30.0) -> bool:
        """
        Acquire tokens, waiting if necessary.
        
        Args:
            tokens: Number of tokens to acquire
            max_wait: Maximum seconds to wait for tokens
            
        Returns:
            True if tokens were acquired within max_wait, False otherwise
        """
        start = time.monotonic()
        
        while (time.monotonic() - start) < max_wait:
            if await self.acquire(tokens):
                return True
            
            wait_time = await self.wait_for_tokens(tokens)
            wait_time = min(wait_time, max_wait - (time.monotonic() - start))
            
            if wait_time <= 0:
                return False
            
            await asyncio.sleep(min(wait_time, 0.1))  # Check frequently
        
        return False
    
    async def wait_for_tokens(self, tokens: int = 1) -> float:
        """
        Calculate time to wait for tokens to become available.
        
        Args:
            tokens: Number of tokens needed
            
        Returns:
            Seconds to wait (0 if tokens available now)
        """
        async with self._lock:
            self._check_cooldown_expired()
            
            # If in cooldown, return remaining cooldown time
            if self._in_cooldown and self._cooldown_end:
                return max(0, self._cooldown_end - time.monotonic())
            
            self._refill()
            
            if self._tokens >= tokens:
                return 0.0
            
            tokens_needed = tokens - self._tokens
            wait_time = tokens_needed / self.refill_rate
            return wait_time
    
    @property
    def utilization(self) -> float:
        """Get current bucket utilization (0.0 = empty, 1.0 = full capacity used)."""
        # Higher utilization means fewer tokens available
        return 1.0 - (self._tokens / self.capacity)
    
    @property
    def current_tokens(self) -> float:
        """Get current available tokens (without refill calculation)."""
        return self._tokens
    
    @property
    def throttle_level(self) -> ThrottleLevel:
        """Get current throttle level based on utilization."""
        if self._in_cooldown:
            return ThrottleLevel.COOLDOWN
        
        util = self.utilization
        if util >= self.CRITICAL_THRESHOLD:
            return ThrottleLevel.CRITICAL
        elif util >= self.WARNING_THRESHOLD:
            return ThrottleLevel.WARNING
        return ThrottleLevel.NORMAL
    
    @property
    def cooldown_remaining(self) -> float:
        """Seconds remaining in cooldown (0 if not in cooldown)."""
        if self._in_cooldown and self._cooldown_end:
            return max(0, self._cooldown_end - time.monotonic())
        return 0.0
    
    def get_state(self) -> BucketState:
        """Get current bucket state for dashboard export."""
        return BucketState(
            name=self.name,
            current_tokens=self._tokens,
            capacity=self.capacity,
            refill_rate=self.refill_rate,
            utilization=self.utilization,
            throttle_level=self.throttle_level,
            last_acquire_time=self._last_acquire_time,
            total_acquires=self._total_acquires,
            total_throttles=self._total_throttles,
            in_cooldown=self._in_cooldown,
            cooldown_remaining_seconds=self.cooldown_remaining,
        )
    
    async def reset(self) -> None:
        """Reset bucket to full capacity."""
        async with self._lock:
            self._tokens = float(self.capacity)
            self._last_refill = time.monotonic()
            self._in_cooldown = False
            self._cooldown_end = None
            logger.info(f"TokenBucket '{self.name}': Reset to full capacity")


class RateLimitOverwatch:
    """
    Singleton rate limit manager for all API endpoints.
    
    Tracks separate buckets for different services and provides
    circuit breaker functionality to prevent API bans.
    """
    
    _instance: Optional['RateLimitOverwatch'] = None
    _lock = asyncio.Lock()
    
    # Default bucket configurations
    # Format: (capacity, refill_rate per second)
    DEFAULT_BUCKETS = {
        # Polymarket REST: ~60 requests/minute = 1 req/sec sustained, burst to 10
        'polymarket_rest': (10, 1.0),
        
        # Polymarket WebSocket: Higher rate for streaming (100 msgs/sec burst)
        'polymarket_ws': (100, 50.0),
        
        # Binance WebSocket: Very permissive but tracked
        'binance_ws': (200, 100.0),
        
        # Polygon RPC calls
        'polygon_rpc': (20, 5.0),
    }
    
    # Priority order for WebSocket connections (lower = higher priority)
    WS_PRIORITY = {
        'polymarket_ws': 1,  # Highest priority - for trading
        'binance_ws': 2,     # Oracle data
    }
    
    def __new__(cls):
        """Singleton pattern - return existing instance or create new."""
        # Note: Proper async singleton needs async factory method
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        """Initialize rate limit overwatch (only runs once due to singleton)."""
        if self._initialized:
            return
        
        self._buckets: Dict[str, TokenBucket] = {}
        self._callbacks: List[Callable[[ThrottleStatus], Any]] = []
        self._sync_lock = asyncio.Lock()
        
        # Initialize default buckets
        for name, (capacity, refill_rate) in self.DEFAULT_BUCKETS.items():
            self._buckets[name] = TokenBucket(capacity, refill_rate, name)
        
        self._initialized = True
        logger.info("RateLimitOverwatch initialized with default buckets")
    
    @classmethod
    async def get_instance(cls) -> 'RateLimitOverwatch':
        """Get singleton instance (thread-safe async factory)."""
        async with cls._lock:
            return cls()
    
    def get_bucket(self, name: str) -> Optional[TokenBucket]:
        """Get a specific bucket by name."""
        return self._buckets.get(name)
    
    def add_bucket(self, name: str, capacity: int, refill_rate: float) -> TokenBucket:
        """Add a new bucket or replace existing."""
        bucket = TokenBucket(capacity, refill_rate, name)
        self._buckets[name] = bucket
        return bucket
    
    async def acquire(self, bucket_name: str, tokens: int = 1) -> bool:
        """
        Acquire tokens from a specific bucket.
        
        Args:
            bucket_name: Name of the bucket to acquire from
            tokens: Number of tokens to acquire
            
        Returns:
            True if tokens acquired, False otherwise
        """
        bucket = self._buckets.get(bucket_name)
        if bucket is None:
            logger.error(f"RateLimitOverwatch: Unknown bucket '{bucket_name}'")
            return False
        
        result = await bucket.acquire(tokens)
        
        # Check for throttle status after acquire
        status = self.get_throttle_status()
        if status.is_throttling:
            await self._notify_callbacks(status)
        
        return result
    
    async def acquire_or_wait(
        self, 
        bucket_name: str, 
        tokens: int = 1, 
        max_wait: float = 30.0
    ) -> bool:
        """Acquire tokens, waiting if necessary."""
        bucket = self._buckets.get(bucket_name)
        if bucket is None:
            logger.error(f"RateLimitOverwatch: Unknown bucket '{bucket_name}'")
            return False
        
        return await bucket.acquire_or_wait(tokens, max_wait)
    
    def get_utilization(self, bucket_name: str) -> float:
        """
        Get utilization for a specific bucket.
        
        Args:
            bucket_name: Name of the bucket
            
        Returns:
            Utilization from 0.0 (empty) to 1.0 (full capacity used)
        """
        bucket = self._buckets.get(bucket_name)
        if bucket is None:
            logger.warning(f"RateLimitOverwatch: Unknown bucket '{bucket_name}'")
            return 0.0
        
        return bucket.utilization
    
    def is_throttling(self) -> bool:
        """
        Check if ANY bucket is above warning threshold.
        
        Returns:
            True if throttling should be applied
        """
        for bucket in self._buckets.values():
            if bucket.utilization >= TokenBucket.WARNING_THRESHOLD:
                return True
            if bucket._in_cooldown:
                return True
        return False
    
    def get_throttle_status(self) -> ThrottleStatus:
        """
        Get comprehensive throttle status for all buckets.
        
        Returns:
            ThrottleStatus dataclass with detailed throttle information
        """
        throttled_services: List[str] = []
        max_utilization = 0.0
        worst_level = ThrottleLevel.NORMAL
        cooldown_times: List[float] = []
        
        for name, bucket in self._buckets.items():
            util = bucket.utilization
            level = bucket.throttle_level
            
            if util > max_utilization:
                max_utilization = util
            
            if level == ThrottleLevel.COOLDOWN:
                worst_level = ThrottleLevel.COOLDOWN
                throttled_services.append(name)
                cooldown_times.append(bucket.cooldown_remaining)
            elif level == ThrottleLevel.CRITICAL:
                if worst_level != ThrottleLevel.COOLDOWN:
                    worst_level = ThrottleLevel.CRITICAL
                throttled_services.append(name)
            elif level == ThrottleLevel.WARNING:
                if worst_level == ThrottleLevel.NORMAL:
                    worst_level = ThrottleLevel.WARNING
                throttled_services.append(name)
        
        # Calculate suggested cooldown
        if cooldown_times:
            suggested_cooldown = max(cooldown_times)
        elif max_utilization >= TokenBucket.CRITICAL_THRESHOLD:
            suggested_cooldown = TokenBucket.COOLDOWN_DURATION
        elif max_utilization >= TokenBucket.WARNING_THRESHOLD:
            suggested_cooldown = 1.0  # Brief pause
        else:
            suggested_cooldown = 0.0
        
        # Generate warning message
        if worst_level == ThrottleLevel.COOLDOWN:
            msg = f"COOLDOWN ACTIVE: {', '.join(throttled_services)} - waiting {suggested_cooldown:.1f}s"
        elif worst_level == ThrottleLevel.CRITICAL:
            msg = f"CRITICAL: {', '.join(throttled_services)} at {max_utilization*100:.0f}% utilization"
        elif worst_level == ThrottleLevel.WARNING:
            msg = f"WARNING: {', '.join(throttled_services)} approaching limit ({max_utilization*100:.0f}%)"
        else:
            msg = ""
        
        return ThrottleStatus(
            is_throttling=worst_level != ThrottleLevel.NORMAL,
            throttled_services=throttled_services,
            warning_message=msg,
            suggested_cooldown_seconds=suggested_cooldown,
            level=worst_level,
        )
    
    def register_callback(self, callback: Callable[[ThrottleStatus], Any]) -> None:
        """Register callback for throttle events."""
        self._callbacks.append(callback)
    
    def unregister_callback(self, callback: Callable[[ThrottleStatus], Any]) -> None:
        """Unregister a previously registered callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)
    
    async def _notify_callbacks(self, status: ThrottleStatus) -> None:
        """Notify all registered callbacks of throttle status."""
        for callback in self._callbacks:
            try:
                result = callback(status)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"RateLimitOverwatch: Callback error: {e}")
    
    def get_priority_ws_bucket(self) -> Optional[str]:
        """
        Get the highest priority WebSocket bucket that's NOT throttled.
        Useful for ensuring trading connections get priority.
        
        Returns:
            Bucket name or None if all are throttled
        """
        ws_buckets = sorted(
            [(name, self._buckets[name]) for name in self.WS_PRIORITY.keys() 
             if name in self._buckets],
            key=lambda x: self.WS_PRIORITY.get(x[0], 999)
        )
        
        for name, bucket in ws_buckets:
            if bucket.utilization < TokenBucket.WARNING_THRESHOLD:
                return name
        
        return None
    
    def get_priority_ws_bucket_with_market(
        self, 
        markets: Optional[List[Tuple[str, float]]] = None
    ) -> Optional[Tuple[str, Optional[str]]]:
        """
        Get highest priority WebSocket bucket with optional market prioritization.
        
        When throttling occurs, prioritize the bucket serving the closest-expiring market
        to ensure trading happens before market expiry.
        
        Args:
            markets: List of tuples (market_id, seconds_to_expiry) for prioritization.
                     If provided, prioritizes WebSocket serving market expiring soonest.
        
        Returns:
            Tuple of (bucket_name, prioritized_market_id) or (None, None) if all throttled
        """
        ws_buckets = sorted(
            [(name, self._buckets[name]) for name in self.WS_PRIORITY.keys() 
             if name in self._buckets],
            key=lambda x: self.WS_PRIORITY.get(x[0], 999)
        )
        
        prioritized_market = None
        if markets:
            # Find market expiring soonest
            closest_market = min(markets, key=lambda x: x[1]) if markets else None
            prioritized_market = closest_market[0] if closest_market else None
        
        # Return highest priority non-throttled bucket
        for name, bucket in ws_buckets:
            if bucket.utilization < TokenBucket.WARNING_THRESHOLD:
                return (name, prioritized_market)
        
        # If all throttled, return highest priority and closest expiring market
        if ws_buckets:
            return (ws_buckets[0][0], prioritized_market)
        
        return (None, None)
    
    def get_dashboard_status(self) -> Dict[str, Any]:
        """
        Get comprehensive status for dashboard display.
        
        Returns dict suitable for TUI display with all bucket states.
        """
        throttle_status = self.get_throttle_status()
        
        bucket_states = {}
        for name, bucket in self._buckets.items():
            state = bucket.get_state()
            bucket_states[name] = {
                'current_tokens': round(state.current_tokens, 1),
                'capacity': state.capacity,
                'refill_rate': state.refill_rate,
                'utilization_pct': round(state.utilization * 100, 1),
                'throttle_level': state.throttle_level.value,
                'in_cooldown': state.in_cooldown,
                'cooldown_remaining': round(state.cooldown_remaining_seconds, 1),
                'total_acquires': state.total_acquires,
                'total_throttles': state.total_throttles,
            }
        
        return {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'is_throttling': throttle_status.is_throttling,
            'throttle_level': throttle_status.level.value,
            'throttled_services': throttle_status.throttled_services,
            'warning_message': throttle_status.warning_message,
            'suggested_cooldown_seconds': throttle_status.suggested_cooldown_seconds,
            'buckets': bucket_states,
            'badge_color': self._get_badge_color(throttle_status.level),
            'badge_text': self._get_badge_text(throttle_status),
        }
    
    def get_all_bucket_states(self) -> Dict[str, BucketState]:
        """
        Get the raw state objects for all buckets.
        
        Useful for detailed programmatic access to bucket states,
        especially for dashboard rendering or analysis.
        
        Returns:
            Dictionary mapping bucket names to BucketState objects
        """
        return {name: bucket.get_state() for name, bucket in self._buckets.items()}
    
    def _get_badge_color(self, level: ThrottleLevel) -> str:
        """Get TUI badge color for throttle level."""
        colors = {
            ThrottleLevel.NORMAL: 'green',
            ThrottleLevel.WARNING: 'yellow',
            ThrottleLevel.CRITICAL: 'red',
            ThrottleLevel.COOLDOWN: 'magenta',
        }
        return colors.get(level, 'white')
    
    def _get_badge_text(self, status: ThrottleStatus) -> str:
        """Get TUI badge text for throttle status."""
        if status.level == ThrottleLevel.COOLDOWN:
            return "RATE LIMIT: COOLDOWN"
        elif status.level == ThrottleLevel.CRITICAL:
            return "RATE LIMIT: CRITICAL"
        elif status.level == ThrottleLevel.WARNING:
            return "RATE LIMIT WARNING: THROTTLING"
        return "RATE LIMIT: OK"
    
    async def reset_bucket(self, bucket_name: str) -> bool:
        """Reset a specific bucket to full capacity."""
        bucket = self._buckets.get(bucket_name)
        if bucket:
            await bucket.reset()
            return True
        return False
    
    async def reset_all(self) -> None:
        """Reset all buckets to full capacity."""
        for bucket in self._buckets.values():
            await bucket.reset()
        logger.info("RateLimitOverwatch: All buckets reset")


# Decorator for rate-limited functions
def rate_limited(bucket_name: str, tokens: int = 1, wait: bool = True, max_wait: float = 30.0):
    """
    Decorator to apply rate limiting to async functions.
    
    Args:
        bucket_name: Name of the rate limit bucket to use
        tokens: Number of tokens to acquire per call
        wait: If True, wait for tokens; if False, raise exception
        max_wait: Maximum seconds to wait for tokens
        
    Usage:
        @rate_limited("polymarket_rest")
        async def fetch_markets():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            overwatch = await RateLimitOverwatch.get_instance()
            
            if wait:
                acquired = await overwatch.acquire_or_wait(bucket_name, tokens, max_wait)
            else:
                acquired = await overwatch.acquire(bucket_name, tokens)
            
            if not acquired:
                raise RateLimitExceeded(
                    f"Rate limit exceeded for '{bucket_name}' - "
                    f"could not acquire {tokens} token(s)"
                )
            
            return await func(*args, **kwargs)
        
        return wrapper
    return decorator


class RateLimitExceeded(Exception):
    """Raised when rate limit is exceeded and waiting is disabled."""
    pass


class RateLimitContext:
    """
    Async context manager for rate limiting.
    
    Usage:
        async with rate_limit_context("polymarket_rest"):
            response = await client.get(url)
    """
    
    def __init__(
        self, 
        bucket_name: str, 
        tokens: int = 1, 
        wait: bool = True, 
        max_wait: float = 30.0
    ):
        self.bucket_name = bucket_name
        self.tokens = tokens
        self.wait = wait
        self.max_wait = max_wait
        self._overwatch: Optional[RateLimitOverwatch] = None
    
    async def __aenter__(self) -> 'RateLimitContext':
        self._overwatch = await RateLimitOverwatch.get_instance()
        
        if self.wait:
            acquired = await self._overwatch.acquire_or_wait(
                self.bucket_name, 
                self.tokens, 
                self.max_wait
            )
        else:
            acquired = await self._overwatch.acquire(self.bucket_name, self.tokens)
        
        if not acquired:
            raise RateLimitExceeded(
                f"Rate limit exceeded for '{self.bucket_name}' - "
                f"could not acquire {self.tokens} token(s)"
            )
        
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        # Nothing to do on exit; tokens are consumed
        pass
    
    def get_status(self) -> Optional[ThrottleStatus]:
        """Get current throttle status."""
        if self._overwatch:
            return self._overwatch.get_throttle_status()
        return None


def rate_limit_context(
    bucket_name: str, 
    tokens: int = 1, 
    wait: bool = True, 
    max_wait: float = 30.0
) -> RateLimitContext:
    """
    Create rate limit context manager.
    
    Args:
        bucket_name: Name of the rate limit bucket to use
        tokens: Number of tokens to acquire
        wait: If True, wait for tokens; if False, raise exception
        max_wait: Maximum seconds to wait
        
    Usage:
        async with rate_limit_context("polymarket_rest"):
            response = await client.get(url)
    """
    return RateLimitContext(bucket_name, tokens, wait, max_wait)


# Convenience function to get global overwatch instance
async def get_overwatch() -> RateLimitOverwatch:
    """Get the global RateLimitOverwatch singleton instance."""
    return await RateLimitOverwatch.get_instance()


# Module-level functions for easy access
async def check_throttle() -> ThrottleStatus:
    """Check current throttle status across all buckets."""
    overwatch = await get_overwatch()
    return overwatch.get_throttle_status()


async def get_dashboard_data() -> Dict[str, Any]:
    """Get dashboard status data for TUI display."""
    overwatch = await get_overwatch()
    return overwatch.get_dashboard_status()


async def get_all_bucket_states() -> Dict[str, BucketState]:
    """Get raw bucket state objects for all buckets."""
    overwatch = await get_overwatch()
    return overwatch.get_all_bucket_states()


async def get_priority_websocket(
    markets: Optional[List[Tuple[str, float]]] = None
) -> Optional[Tuple[str, Optional[str]]]:
    """
    Get priority WebSocket bucket, optionally prioritizing by market expiry.
    
    Args:
        markets: List of tuples (market_id, seconds_to_expiry) for prioritization
        
    Returns:
        Tuple of (bucket_name, prioritized_market_id) or (None, None)
    """
    overwatch = await get_overwatch()
    return overwatch.get_priority_ws_bucket_with_market(markets)


async def is_service_available(bucket_name: str) -> bool:
    """Check if a service bucket has tokens available."""
    overwatch = await get_overwatch()
    bucket = overwatch.get_bucket(bucket_name)
    if bucket:
        return bucket.utilization < TokenBucket.WARNING_THRESHOLD
    return False


# Example usage and self-test
if __name__ == "__main__":
    async def test_rate_limiter():
        """Test the rate limiter functionality."""
        print("Testing RateLimitOverwatch...")
        
        overwatch = await RateLimitOverwatch.get_instance()
        
        # Test basic acquire
        print("\n1. Testing basic acquire:")
        for i in range(5):
            result = await overwatch.acquire("polymarket_rest")
            print(f"   Acquire {i+1}: {result}")
        
        # Test utilization
        print("\n2. Testing utilization:")
        util = overwatch.get_utilization("polymarket_rest")
        print(f"   Utilization: {util*100:.1f}%")
        
        # Test throttle status
        print("\n3. Testing throttle status:")
        status = overwatch.get_throttle_status()
        print(f"   Is throttling: {status.is_throttling}")
        print(f"   Level: {status.level.value}")
        print(f"   Message: {status.warning_message or 'None'}")
        
        # Test dashboard status
        print("\n4. Testing dashboard status:")
        dashboard = overwatch.get_dashboard_status()
        print(f"   Badge: {dashboard['badge_text']} ({dashboard['badge_color']})")
        print(f"   Buckets: {list(dashboard['buckets'].keys())}")
        
        # Test decorator
        print("\n5. Testing @rate_limited decorator:")
        
        @rate_limited("polymarket_rest")
        async def test_api_call():
            return "API call successful"
        
        result = await test_api_call()
        print(f"   Result: {result}")
        
        # Test context manager
        print("\n6. Testing context manager:")
        async with rate_limit_context("polymarket_rest"):
            print("   Inside rate-limited context")
        
        # Test heavy load
        print("\n7. Testing heavy load (rapid acquires):")
        successes = 0
        failures = 0
        for i in range(20):
            if await overwatch.acquire("polymarket_rest"):
                successes += 1
            else:
                failures += 1
        print(f"   Successes: {successes}, Failures: {failures}")
        
        # Final status
        print("\n8. Final dashboard status:")
        dashboard = overwatch.get_dashboard_status()
        for name, bucket_data in dashboard['buckets'].items():
            print(f"   {name}: {bucket_data['utilization_pct']:.1f}% utilized, "
                  f"{bucket_data['total_acquires']} acquires, "
                  f"{bucket_data['total_throttles']} throttles")
        
        print("\n[OK] All tests completed!")
    
    asyncio.run(test_rate_limiter())
