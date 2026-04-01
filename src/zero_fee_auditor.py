"""
Zero-Fee Auditor - ensures no real funds are spent.
Validates all transaction payloads before logging.
"""
import logging
from typing import Dict, Any, List, Tuple, Callable, TypeVar, Optional
from functools import wraps

logger = logging.getLogger(__name__)

F = TypeVar('F', bound=Callable[..., Any])


class ZeroFeeViolation(Exception):
    """Raised when a payload would spend real funds."""
    pass


class ZeroFeeAuditor:
    """Audits payloads to ensure zero gas/MATIC spending."""
    
    FORBIDDEN_KEYS = [
        "gas", "gasPrice", "maxFeePerGas", "maxPriorityFeePerGas",
        "value", "nonce", "chainId"
    ]
    
    FORBIDDEN_PATTERNS = [
        "0x",  # Raw transaction hex
        "sendTransaction",
        "signTransaction", 
    ]
    
    def __init__(self, strict_mode: bool = True):
        self._strict = strict_mode
        self._violations: List[str] = []
    
    def audit_payload(self, payload: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Audit a payload for fee-related fields.
        
        Returns:
            (is_valid, message) - True if payload is safe, error message if not
        """
        if not isinstance(payload, dict):
            msg = f"Payload must be dict, got {type(payload).__name__}"
            self._violations.append(msg)
            return False, msg
        
        # Check for forbidden keys (gas, value, etc.)
        found_keys = set(payload.keys()) & set(self.FORBIDDEN_KEYS)
        if found_keys:
            msg = f"Forbidden keys detected in payload: {found_keys}"
            self._violations.append(msg)
            if self._strict:
                return False, msg
        
        # Check for forbidden patterns in string values
        for key, value in payload.items():
            if isinstance(value, str):
                for pattern in self.FORBIDDEN_PATTERNS:
                    if pattern in value:
                        msg = f"Forbidden pattern '{pattern}' found in field '{key}': {value}"
                        self._violations.append(msg)
                        if self._strict:
                            return False, msg
            # Check for nested dicts/lists
            elif isinstance(value, dict):
                is_valid, nested_msg = self.audit_payload(value)
                if not is_valid:
                    return False, nested_msg
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        is_valid, nested_msg = self.audit_payload(item)
                        if not is_valid:
                            return False, nested_msg
        
        return True, "Payload audit passed"
    
    def validate_order(self, order: Dict[str, Any]) -> bool:
        """Validate order dict has no gas/value fields."""
        if not isinstance(order, dict):
            logger.error(f"Order must be dict, got {type(order).__name__}")
            return False
        
        # Check for any gas or value related fields
        forbidden_in_order = {"gas", "gasPrice", "value", "gasLimit"}
        found = set(order.keys()) & forbidden_in_order
        
        if found:
            logger.error(f"Order contains forbidden fields: {found}")
            return False
        
        logger.debug(f"Order validation passed for keys: {list(order.keys())}")
        return True
    
    def validate_transaction(self, tx: Dict[str, Any]) -> bool:
        """Validate transaction has no MATIC spending."""
        if not isinstance(tx, dict):
            logger.error(f"Transaction must be dict, got {type(tx).__name__}")
            return False
        
        # Check for value field (MATIC spending)
        if "value" in tx:
            value = tx.get("value")
            if value is not None and value != 0 and value != "0":
                logger.error(f"Transaction has non-zero value (MATIC spending): {value}")
                return False
        
        # Check for gas limits
        gas_keys = {"gas", "gasLimit", "gasPrice", "maxFeePerGas", "maxPriorityFeePerGas"}
        for key in gas_keys:
            if key in tx:
                gas_value = tx.get(key)
                if gas_value is not None and gas_value != 0 and gas_value != "0":
                    logger.error(f"Transaction has non-zero {key}: {gas_value}")
                    return False
        
        logger.debug(f"Transaction validation passed")
        return True
    
    def audit_and_reject(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Audit payload and reject if it would spend real funds.
        
        Raises:
            ZeroFeeViolation: If payload contains fee fields or real fund transfers
        """
        is_valid, message = self.audit_payload(payload)
        
        if not is_valid:
            logger.error(f"Audit failed: {message}")
            raise ZeroFeeViolation(message)
        
        logger.info("Payload passed zero-fee audit")
        return payload
    
    def get_violations(self) -> List[str]:
        """Get all recorded violations."""
        return self._violations.copy()
    
    def clear_violations(self) -> None:
        """Clear violation history."""
        self._violations.clear()
    
    @staticmethod
    def enforce_paper_trade(func: F) -> F:
        """
        Decorator to audit function return values.
        Ensures returned payloads are zero-fee compliant.
        """
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            result = func(*args, **kwargs)
            
            # Only audit dict results
            if isinstance(result, dict):
                auditor = ZeroFeeAuditor(strict_mode=True)
                is_valid, message = auditor.audit_payload(result)
                
                if not is_valid:
                    logger.error(
                        f"Function {func.__name__} returned non-compliant payload: {message}"
                    )
                    raise ZeroFeeViolation(
                        f"Function {func.__name__} returned payload with real funds: {message}"
                    )
                
                logger.debug(f"Function {func.__name__} result passed zero-fee audit")
            
            return result
        
        return wrapper  # type: ignore
    
    def validate_batch(self, payloads: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
        """
        Validate a batch of payloads.
        
        Returns:
            (all_valid, error_messages)
        """
        errors = []
        
        for idx, payload in enumerate(payloads):
            is_valid, message = self.audit_payload(payload)
            if not is_valid:
                errors.append(f"Payload {idx}: {message}")
        
        all_valid = len(errors) == 0
        return all_valid, errors


# Module-level convenience functions
_default_auditor = ZeroFeeAuditor(strict_mode=True)


def audit_payload(payload: Dict[str, Any]) -> Tuple[bool, str]:
    """Audit a single payload using default auditor."""
    return _default_auditor.audit_payload(payload)


def validate_order(order: Dict[str, Any]) -> bool:
    """Validate a single order using default auditor."""
    return _default_auditor.validate_order(order)


def validate_transaction(tx: Dict[str, Any]) -> bool:
    """Validate a single transaction using default auditor."""
    return _default_auditor.validate_transaction(tx)


def enforce_paper_trade(func: F) -> F:
    """Decorator to enforce paper trading."""
    return ZeroFeeAuditor.enforce_paper_trade(func)
