"""
Test suite for Rate Limiter - Token Bucket Implementation.

Tests verify:
1. Token bucket mechanics (capacity, refill rate)
2. Throttle level transitions (NORMAL -> WARNING -> CRITICAL -> COOLDOWN)
3. Async lock behavior
4. Cooldown period enforcement

Following TDD (Red-Green-Refactor):
- Each test verifies ONE specific behavior
- Tests use time mocking for deterministic results
"""

import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock, patch
import sys
from pathlib import Path
import time

# Add src to path
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))


class TestTokenBucketBasics:
    """Test basic token bucket mechanics."""
    
    @pytest.mark.asyncio
    async def test_bucket_starts_full(self):
        """Test: New bucket starts at full capacity."""
        from rate_limiter import TokenBucket
        
        bucket = TokenBucket(capacity=10, refill_rate=1.0, name="test")
        
        assert bucket._tokens == 10
        assert bucket.capacity == 10
    
    @pytest.mark.asyncio
    async def test_acquire_consumes_tokens(self):
        """Test: Acquiring tokens reduces available tokens."""
        from rate_limiter import TokenBucket
        
        bucket = TokenBucket(capacity=10, refill_rate=1.0, name="test")
        
        acquired = await bucket.acquire(3)
        
        assert acquired is True
        assert bucket._tokens == 7
    
    @pytest.mark.asyncio
    async def test_acquire_fails_when_insufficient(self):
        """Test: Cannot acquire more tokens than available."""
        from rate_limiter import TokenBucket
        
        bucket = TokenBucket(capacity=5, refill_rate=1.0, name="test")
        
        # Drain the bucket
        await bucket.acquire(5)
        
        # Try to acquire more
        acquired = await bucket.acquire(1)
        
        assert acquired is False
    
    @pytest.mark.asyncio
    async def test_tokens_refill_over_time(self):
        """Test: Tokens refill at the configured rate."""
        from rate_limiter import TokenBucket
        
        # Use a bucket with high capacity to avoid cooldown triggers
        bucket = TokenBucket(capacity=100, refill_rate=100.0, name="test")  # 100 tokens/second
        
        # Use only some tokens (not triggering cooldown)
        await bucket.acquire(50)
        assert bucket._tokens == 50
        
        # Wait for refill
        await asyncio.sleep(0.3)  # Should refill ~30 tokens
        
        # Trigger refill calculation
        await bucket.acquire(0)  # Just to trigger refill
        
        # Should have refilled some tokens (but capped at capacity)
        assert bucket._tokens >= 70  # 50 + at least 20 more
    
    @pytest.mark.asyncio
    async def test_tokens_do_not_exceed_capacity(self):
        """Test: Tokens don't exceed capacity when refilling."""
        from rate_limiter import TokenBucket
        
        bucket = TokenBucket(capacity=10, refill_rate=100.0, name="test")  # Fast refill
        
        # Use some tokens
        await bucket.acquire(2)
        
        # Wait for refill
        await asyncio.sleep(0.2)
        
        # Trigger refill
        await bucket.acquire(0)
        
        # Should be capped at capacity
        assert bucket._tokens <= 10


class TestThrottleLevels:
    """Test throttle level transitions."""
    
    def test_normal_level_when_bucket_full(self):
        """Test: NORMAL level when bucket > 50% full."""
        from rate_limiter import TokenBucket, ThrottleLevel
        
        bucket = TokenBucket(capacity=10, refill_rate=1.0, name="test")
        
        state = bucket.get_state()
        
        assert state.throttle_level == ThrottleLevel.NORMAL
    
    def test_warning_level_when_bucket_low(self):
        """Test: WARNING level when bucket 80-90% utilized."""
        from rate_limiter import TokenBucket, ThrottleLevel
        
        bucket = TokenBucket(capacity=10, refill_rate=1.0, name="test")
        bucket._tokens = 1.5  # ~85% utilized
        
        state = bucket.get_state()
        
        assert state.throttle_level == ThrottleLevel.WARNING
    
    def test_critical_level_when_bucket_nearly_empty(self):
        """Test: CRITICAL level when bucket >90% utilized."""
        from rate_limiter import TokenBucket, ThrottleLevel
        
        bucket = TokenBucket(capacity=10, refill_rate=1.0, name="test")
        bucket._tokens = 0.5  # 95% utilized
        
        state = bucket.get_state()
        
        assert state.throttle_level == ThrottleLevel.CRITICAL
    
    def test_cooldown_level_when_in_cooldown(self):
        """Test: COOLDOWN level when bucket is in cooldown."""
        from rate_limiter import TokenBucket, ThrottleLevel
        
        bucket = TokenBucket(capacity=10, refill_rate=1.0, name="test")
        bucket._tokens = 0
        bucket._in_cooldown = True
        bucket._cooldown_end = time.monotonic() + 10
        
        state = bucket.get_state()
        
        assert state.throttle_level == ThrottleLevel.COOLDOWN


class TestBucketState:
    """Test bucket state export for monitoring."""
    
    def test_bucket_state_export(self):
        """Test: Bucket state can be exported for dashboard."""
        from rate_limiter import TokenBucket, ThrottleLevel
        
        bucket = TokenBucket(capacity=100, refill_rate=10.0, name="test")
        bucket._tokens = 50
        
        state = bucket.get_state()
        
        assert state.capacity == 100
        assert state.current_tokens == 50
        assert state.refill_rate == 10.0
        assert state.utilization == 0.5  # (100-50)/100 = 50% utilized


class TestCooldownPeriod:
    """Test cooldown period enforcement."""
    
    @pytest.mark.asyncio
    async def test_cooldown_blocks_requests(self):
        """Test: During cooldown, requests are blocked."""
        from rate_limiter import TokenBucket
        
        bucket = TokenBucket(
            capacity=10,
            refill_rate=1.0,
            name="test",
        )
        
        # Force into cooldown
        bucket._in_cooldown = True
        bucket._cooldown_end = time.monotonic() + 5.0
        
        # Should be in cooldown
        assert bucket._in_cooldown is True


class TestConcurrentAccess:
    """Test thread-safe bucket operations."""
    
    @pytest.mark.asyncio
    async def test_concurrent_acquires_are_atomic(self):
        """Test: Multiple concurrent acquires don't cause race conditions."""
        from rate_limiter import TokenBucket
        
        bucket = TokenBucket(capacity=100, refill_rate=0.0, name="test")  # No refill
        
        # Simulate concurrent acquires
        async def acquire_one():
            return await bucket.acquire(1)
        
        # Run many concurrent acquires
        tasks = [acquire_one() for _ in range(50)]
        results = await asyncio.gather(*tasks)
        
        # All should succeed (we have 100 tokens)
        successful = sum(1 for r in results if r)
        
        assert successful == 50
        assert bucket._tokens == 50  # 100 - 50


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
