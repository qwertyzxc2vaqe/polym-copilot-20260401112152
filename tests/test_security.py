"""
Test suite for Security Module - Validation and Protection.

Tests verify:
1. Sensitive data filtering (redaction of keys, secrets)
2. Input validation (private_key, address, token_id, price, amount)
3. Daily loss limiter functionality
4. Audit logging

Following TDD (Red-Green-Refactor):
- Each test verifies ONE specific security behavior
- Tests ensure no sensitive data leaks
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
import sys
from pathlib import Path
import re

# Add src to path
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))


class TestSensitiveDataFilter:
    """Test sensitive data redaction."""
    
    def test_redacts_private_key(self):
        """Test: Private keys are redacted from strings."""
        from security import SensitiveDataFilter
        
        text = "Private key: 0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
        
        filtered = SensitiveDataFilter.redact_string(text)
        
        assert "1234567890abcdef1234567890abcdef" not in filtered
        assert "REDACTED" in filtered
    
    def test_redacts_api_key(self):
        """Test: API keys are redacted from strings."""
        from security import SensitiveDataFilter
        
        text = "API Key: sk_live_abcdef123456"
        
        filtered = SensitiveDataFilter.redact_string(text)
        
        assert "abcdef123456" not in filtered
    
    def test_preserves_safe_content(self):
        """Test: Non-sensitive content is preserved."""
        from security import SensitiveDataFilter
        
        text = "Balance: $100.00, Trades: 5"
        
        filtered = SensitiveDataFilter.redact_string(text)
        
        assert "$100.00" in filtered
        assert "Trades: 5" in filtered


class TestValidators:
    """Test input validation functions."""
    
    def test_validate_private_key_valid_64_hex(self):
        """Test: Valid 64-character hex private key passes."""
        from security import Validator
        
        key = "a" * 64  # 64 hex chars
        
        result = Validator.validate_private_key(key)
        
        assert result is True
    
    def test_validate_private_key_with_0x_prefix(self):
        """Test: Private key with 0x prefix is valid."""
        from security import Validator
        
        key = "0x" + "a" * 64
        
        result = Validator.validate_private_key(key)
        
        assert result is True
    
    def test_validate_private_key_invalid_length(self):
        """Test: Private key with wrong length fails."""
        from security import Validator
        
        key = "a" * 32  # Too short
        
        result = Validator.validate_private_key(key)
        
        assert result is False
    
    def test_validate_private_key_invalid_chars(self):
        """Test: Private key with invalid chars fails."""
        from security import Validator
        
        key = "g" * 64  # 'g' is not hex
        
        result = Validator.validate_private_key(key)
        
        assert result is False
    
    def test_validate_address_valid_ethereum(self):
        """Test: Valid Ethereum address passes."""
        from security import Validator
        
        address = "0x" + "a" * 40
        
        result = Validator.validate_address(address)
        
        assert result is True
    
    def test_validate_address_invalid(self):
        """Test: Invalid address fails."""
        from security import Validator
        
        address = "invalid"
        
        result = Validator.validate_address(address)
        
        assert result is False
    
    def test_validate_price_valid_range(self):
        """Test: Price in 0-1 range passes."""
        from security import Validator
        
        assert Validator.validate_price(0.0) is True
        assert Validator.validate_price(0.5) is True
        assert Validator.validate_price(0.99) is True
        assert Validator.validate_price(1.0) is True
    
    def test_validate_price_invalid_range(self):
        """Test: Price outside 0-1 range fails."""
        from security import Validator
        
        assert Validator.validate_price(-0.1) is False
        assert Validator.validate_price(1.1) is False
    
    def test_validate_amount_positive(self):
        """Test: Positive amounts pass (with min_amount=0)."""
        from security import Validator
        
        # The actual API uses min_amount parameter, default 0
        assert Validator.validate_amount(1.0) is True
        assert Validator.validate_amount(0.01) is True
        assert Validator.validate_amount(1000.0) is True
    
    def test_validate_amount_zero_allowed_by_default(self):
        """Test: Zero is allowed with default min_amount=0."""
        from security import Validator
        
        # With default min_amount=0, zero is valid
        assert Validator.validate_amount(0) is True
        assert Validator.validate_amount(-1.0) is False
    
    def test_validate_token_id_valid(self):
        """Test: Valid token IDs pass (64 hex chars)."""
        from security import Validator
        
        # Token IDs are 64 hex characters
        token_id = "a" * 64
        
        result = Validator.validate_token_id(token_id)
        
        assert result is True
    
    def test_validate_token_id_empty(self):
        """Test: Empty token ID fails."""
        from security import Validator
        
        result = Validator.validate_token_id("")
        
        assert result is False


class TestDailyLossLimiter:
    """Test daily loss limit enforcement."""
    
    def test_trading_allowed_when_under_limit(self):
        """Test: Trading allowed when total loss < limit."""
        from security import DailyLossLimiter
        
        limiter = DailyLossLimiter(daily_limit=10.0)
        
        limiter.record_loss(5.0)
        
        assert limiter.is_trading_allowed() is True
    
    def test_trading_blocked_when_over_limit(self):
        """Test: Trading blocked when total loss >= limit."""
        from security import DailyLossLimiter
        
        limiter = DailyLossLimiter(daily_limit=10.0)
        
        limiter.record_loss(10.0)
        
        assert limiter.is_trading_allowed() is False
    
    def test_multiple_losses_accumulate(self):
        """Test: Multiple losses are accumulated."""
        from security import DailyLossLimiter
        
        limiter = DailyLossLimiter(daily_limit=10.0)
        
        limiter.record_loss(3.0)
        limiter.record_loss(3.0)
        limiter.record_loss(3.0)
        
        status = limiter.get_status()
        
        assert status.total_loss == 9.0
        assert limiter.is_trading_allowed() is True
        
        # One more loss puts us over
        limiter.record_loss(2.0)
        
        assert limiter.is_trading_allowed() is False
    
    def test_status_shows_remaining_budget(self):
        """Test: Status shows remaining loss budget."""
        from security import DailyLossLimiter
        
        limiter = DailyLossLimiter(daily_limit=10.0)
        
        limiter.record_loss(3.0)
        
        status = limiter.get_status()
        
        assert status.limit == 10.0
        assert status.total_loss == 3.0
        assert status.remaining == 7.0


class TestSecureErrorHandler:
    """Test error handler doesn't leak sensitive info."""
    
    def test_error_handler_wraps_exceptions(self):
        """Test: Error handler wraps regular exceptions."""
        from security import secure_error_handler, SecureException
        
        @secure_error_handler
        def func_that_raises():
            raise ValueError("Internal error")
        
        with pytest.raises(SecureException):
            func_that_raises()
    
    def test_secure_exception_preserves_public_message(self):
        """Test: SecureException shows public message."""
        from security import SecureException
        
        exc = SecureException("Public error", internal_details="secret info")
        
        assert str(exc) == "Public error"
        assert "secret" not in str(exc)


class TestAuditLogger:
    """Test audit logging functionality."""
    
    def test_audit_logger_records_trades(self):
        """Test: Audit logger records trade events."""
        from security import AuditLogger
        
        logger = AuditLogger()
        
        logger.log_trade(
            action="ORDER_PLACED",
            token_id="a" * 64,
            side="BUY",
            price=0.95,
            size=10.0,
            status="SUCCESS"
        )
        
        # Verify entry was recorded
        assert len(logger._entries) == 1
        assert logger._entries[0].action == "ORDER_PLACED"
    
    def test_audit_logger_records_security_events(self):
        """Test: Audit logger records security events."""
        from security import AuditLogger
        
        logger = AuditLogger()
        
        logger.log_security_event(
            event_type="RATE_LIMIT_HIT",
            details={"service": "polymarket"},
            severity="WARNING"
        )
        
        assert len(logger._entries) == 1
        assert "SECURITY" in logger._entries[0].action
    
    def test_audit_logger_bounds_memory(self):
        """Test: Audit logger doesn't grow unbounded."""
        from security import AuditLogger
        
        logger = AuditLogger()
        max_entries = logger.MAX_IN_MEMORY_ENTRIES
        
        # Add more entries than max
        for i in range(max_entries + 100):
            logger.log_security_event(
                event_type=f"EVENT_{i}",
                details={"index": i},
            )
        
        # Should be bounded
        assert len(logger._entries) <= max_entries


class TestTransactionVerifier:
    """Test transaction verification before signing."""
    
    def test_verify_before_signing_valid(self):
        """Test: Valid transaction passes pre-sign check."""
        from security import TransactionVerifier
        
        verifier = TransactionVerifier()
        
        is_safe, warning = verifier.verify_before_signing(
            to_address="0x" + "a" * 40,
            value=0.001,
            gas_price_gwei=50.0,
        )
        
        assert is_safe is True
    
    def test_verify_before_signing_invalid_address(self):
        """Test: Invalid address fails verification."""
        from security import TransactionVerifier
        
        verifier = TransactionVerifier()
        
        is_safe, warning = verifier.verify_before_signing(
            to_address="invalid",
            value=0.001,
            gas_price_gwei=50.0,
        )
        
        assert is_safe is False
        assert "address" in warning.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
