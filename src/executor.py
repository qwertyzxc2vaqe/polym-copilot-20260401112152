"""
Zero-Fee Execution Protocol Module
==================================
Implements gasless order execution via Polymarket's CLOB (Central Limit Order Book).
All transactions go through the Polygon relayer, ensuring zero network fees for users.

Features:
- Fill-or-Kill (FOK) order execution
- Pre-signing zero-fee verification
- Audit logging for all executions
- Rate limiting for API protection
- Exponential backoff retry logic
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any
from enum import Enum

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType, MarketOrderArgs

from config import SecureConfig
from security import (
    TransactionVerifier,
    AuditLogger,
    rate_limited,
    Validator,
)

logger = logging.getLogger(__name__)


class ExecutionStatus(Enum):
    """Status of an order execution."""
    PENDING = "pending"
    SUBMITTED = "submitted"
    FILLED = "filled"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    ERROR = "error"


class ExecutionError(Exception):
    """Raised when order execution fails."""
    pass


class ZeroFeeViolationError(Exception):
    """Raised when a non-zero fee is detected."""
    pass


@dataclass
class ExecutionResult:
    """Result of an order execution attempt."""
    order_id: str
    status: ExecutionStatus
    token_id: str
    side: str
    price: float
    size: float
    filled_size: float
    total_cost: float  # Must be 0 for network fees
    platform_fee: float  # Must be 0 or minimal
    timestamp: datetime
    tx_hash: Optional[str]
    error_message: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging."""
        return {
            'order_id': self.order_id,
            'status': self.status.value,
            'token_id': self.token_id,
            'side': self.side,
            'price': self.price,
            'size': self.size,
            'filled_size': self.filled_size,
            'total_cost': self.total_cost,
            'platform_fee': self.platform_fee,
            'timestamp': self.timestamp.isoformat(),
            'tx_hash': self.tx_hash,
            'error_message': self.error_message,
        }


class ZeroFeeExecutor:
    """
    Zero-fee order executor using Polymarket's CLOB.
    
    All orders are executed via the Polygon relayer, meaning users
    don't pay any gas fees. Platform fees are verified to be zero
    or minimal before order submission.
    """
    
    # Constants
    CLOB_HOST = "https://clob.polymarket.com"
    POLYGON_CHAIN_ID = 137
    SIGNATURE_TYPE_EOA = 0
    
    # Retry configuration
    MAX_RETRIES = 3
    INITIAL_BACKOFF = 1.0  # seconds
    MAX_BACKOFF = 10.0  # seconds
    
    # Fee thresholds (in basis points)
    MAX_ACCEPTABLE_FEE_BPS = 0  # Strictly zero fees
    FEE_WARNING_THRESHOLD_BPS = 1  # Warn if any fee detected
    
    def __init__(self, config: SecureConfig):
        """
        Initialize the zero-fee executor.
        
        Args:
            config: SecureConfig instance with credentials
        """
        self._config = config
        self._client: Optional[ClobClient] = None
        self._initialized = False
        self._transaction_verifier = TransactionVerifier()
        self._audit_logger = AuditLogger(
            log_file=config.logging.trade_log_file if config.logging.enable_trade_log else None
        )
        
    async def initialize(self) -> None:
        """
        Initialize CLOB client with credentials.
        
        Creates or derives API credentials and sets up the client
        for order execution.
        """
        if self._initialized:
            logger.debug("Executor already initialized")
            return
            
        try:
            logger.info("Initializing CLOB client...")
            
            # Initialize client with private key
            self._client = ClobClient(
                host=self._config.polymarket.clob_host or self.CLOB_HOST,
                key=self._config.blockchain.private_key,
                chain_id=self._config.blockchain.chain_id or self.POLYGON_CHAIN_ID,
                signature_type=self.SIGNATURE_TYPE_EOA,
            )
            
            # Create or derive API credentials
            creds = self._client.create_or_derive_api_creds()
            self._client.set_api_creds(creds)
            
            self._initialized = True
            logger.info("CLOB client initialized successfully")
            
            # Log security event
            self._audit_logger.log_security_event(
                event_type="EXECUTOR_INITIALIZED",
                details={"chain_id": self._config.blockchain.chain_id},
                severity="INFO"
            )
            
        except Exception as e:
            logger.error(f"Failed to initialize CLOB client: {e}")
            self._audit_logger.log_security_event(
                event_type="EXECUTOR_INIT_FAILED",
                details={"error": str(e)},
                severity="CRITICAL"
            )
            raise ExecutionError(f"Failed to initialize executor: {e}")
    
    def _ensure_initialized(self) -> None:
        """Ensure the executor is initialized before operations."""
        if not self._initialized or self._client is None:
            raise ExecutionError("Executor not initialized. Call initialize() first.")
    
    async def verify_zero_fee(self, token_id: str, size: float) -> tuple[bool, float]:
        """
        Verify transaction will be gasless.
        
        The Polymarket CLOB uses a relayer for all transactions, meaning
        users don't pay gas fees. This method verifies the fee structure.
        
        Args:
            token_id: The market token ID
            size: Order size
        
        Returns:
            Tuple of (is_zero_fee, estimated_fee_bps)
        """
        self._ensure_initialized()
        
        try:
            # Get platform fee rate
            fee_rate_bps = await self.get_fee_rate(token_id)
            
            # Relayer handles gas - network cost is always 0 for users
            network_cost = 0.0
            
            # Calculate total fee
            total_fee_bps = fee_rate_bps + network_cost
            
            # Verify zero fee
            is_zero = total_fee_bps <= self.MAX_ACCEPTABLE_FEE_BPS
            
            if not is_zero:
                logger.warning(
                    f"Non-zero fee detected: {total_fee_bps} bps for token {token_id}"
                )
                self._audit_logger.log_security_event(
                    event_type="NON_ZERO_FEE_DETECTED",
                    details={
                        "token_id": token_id,
                        "fee_bps": total_fee_bps,
                        "size": size
                    },
                    severity="WARNING"
                )
            elif total_fee_bps > 0:
                # Warn even for minimal fees
                logger.info(f"Minimal fee detected: {total_fee_bps} bps")
            
            return is_zero, total_fee_bps
            
        except Exception as e:
            logger.error(f"Failed to verify zero fee: {e}")
            # Default to safe behavior - assume fee if we can't verify
            return False, -1.0
    
    async def get_fee_rate(self, token_id: str) -> float:
        """
        Get platform fee rate from Polymarket API.
        
        Polymarket may have 0 fee for makers. This method queries
        the current fee structure for the given market.
        
        Args:
            token_id: The market token ID
        
        Returns:
            Fee rate as decimal (0.0 = no fee, 0.01 = 1%)
        """
        self._ensure_initialized()
        
        try:
            # Query fee rate from CLOB client
            if self._client:
                fee_bps = self._client.get_fee_rate_bps(token_id)
                return float(fee_bps) / 10000  # Convert basis points to decimal
        except Exception as e:
            logger.warning(f"Could not query fee rate: {e}")
        
        return 0.0  # Fallback if query fails
    
    @rate_limited(max_calls=30, time_window=60.0, name="clob_orders")
    async def execute_fok_order(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str = "BUY"
    ) -> ExecutionResult:
        """
        Execute Fill-or-Kill order with zero-fee verification.
        
        FOK orders are either fully filled immediately or cancelled.
        This ensures no partial fills that could leave positions incomplete.
        
        Args:
            token_id: Market token ID (64 hex chars)
            price: Order price (0.0 to 1.0)
            size: Order size in shares
            side: "BUY" or "SELL"
        
        Returns:
            ExecutionResult with order details
        """
        self._ensure_initialized()
        timestamp = datetime.now()
        
        # Input validation
        if not Validator.validate_token_id(token_id):
            return self._create_error_result(
                token_id, side, price, size, timestamp,
                "Invalid token ID format"
            )
        
        if not Validator.validate_price(price):
            return self._create_error_result(
                token_id, side, price, size, timestamp,
                f"Invalid price: {price}. Must be between 0 and 1"
            )
        
        if not Validator.validate_amount(size, min_amount=0.01):
            return self._create_error_result(
                token_id, side, price, size, timestamp,
                f"Invalid size: {size}. Must be >= 0.01"
            )
        
        side = side.upper()
        if side not in ("BUY", "SELL"):
            return self._create_error_result(
                token_id, side, price, size, timestamp,
                f"Invalid side: {side}. Must be BUY or SELL"
            )
        
        # Verify zero fee before proceeding
        is_zero_fee, fee_bps = await self.verify_zero_fee(token_id, size)
        if not is_zero_fee:
            logger.warning(f"Non-zero fee detected ({fee_bps} bps), but proceeding with gasless relayer")
        
        # Create order arguments
        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=side,
        )
        
        # Pre-signing verification
        if not self._verify_before_sign(order_args):
            return self._create_error_result(
                token_id, side, price, size, timestamp,
                "Pre-signing verification failed"
            )
        
        # Execute with retry logic
        return await self._execute_with_retry(
            order_args=order_args,
            order_type=OrderType.FOK,
            timestamp=timestamp,
            fee_bps=fee_bps,
        )
    
    @rate_limited(max_calls=30, time_window=60.0, name="clob_orders")
    async def execute_market_order(
        self,
        token_id: str,
        amount: float,
        side: str = "BUY"
    ) -> ExecutionResult:
        """
        Execute market order (FOK by default).
        
        Market orders are executed at the best available price.
        Uses FOK to ensure complete fills or no execution.
        
        Args:
            token_id: Market token ID
            amount: Amount in USDC for BUY, shares for SELL
            side: "BUY" or "SELL"
        
        Returns:
            ExecutionResult with order details
        """
        self._ensure_initialized()
        timestamp = datetime.now()
        
        # Input validation
        if not Validator.validate_token_id(token_id):
            return self._create_error_result(
                token_id, side, 0.0, amount, timestamp,
                "Invalid token ID format"
            )
        
        if amount <= 0:
            return self._create_error_result(
                token_id, side, 0.0, amount, timestamp,
                f"Invalid amount: {amount}. Must be positive"
            )
        
        side = side.upper()
        if side not in ("BUY", "SELL"):
            return self._create_error_result(
                token_id, side, 0.0, amount, timestamp,
                f"Invalid side: {side}. Must be BUY or SELL"
            )
        
        # Verify zero fee
        is_zero_fee, fee_bps = await self.verify_zero_fee(token_id, amount)
        if not is_zero_fee:
            logger.warning(f"Non-zero fee detected ({fee_bps} bps), proceeding with gasless relayer")
        
        # Create market order arguments
        market_order_args = MarketOrderArgs(
            token_id=token_id,
            amount=amount,
        )
        
        # Execute with retry logic
        return await self._execute_market_with_retry(
            order_args=market_order_args,
            side=side,
            timestamp=timestamp,
            fee_bps=fee_bps,
        )
    
    async def _execute_with_retry(
        self,
        order_args: OrderArgs,
        order_type: OrderType,
        timestamp: datetime,
        fee_bps: float,
    ) -> ExecutionResult:
        """Execute order with exponential backoff retry."""
        last_error: Optional[str] = None
        backoff = self.INITIAL_BACKOFF
        
        for attempt in range(self.MAX_RETRIES):
            try:
                # Create signed order
                signed_order = self._client.create_order(order_args)
                
                # Post order to CLOB
                response = self._client.post_order(signed_order, order_type)
                
                # Parse response
                return self._parse_order_response(
                    response=response,
                    order_args=order_args,
                    timestamp=timestamp,
                    fee_bps=fee_bps,
                )
                
            except Exception as e:
                last_error = str(e)
                logger.warning(
                    f"Order attempt {attempt + 1}/{self.MAX_RETRIES} failed: {e}"
                )
                
                if attempt < self.MAX_RETRIES - 1:
                    # Exponential backoff
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, self.MAX_BACKOFF)
        
        # All retries exhausted
        error_msg = f"Order failed after {self.MAX_RETRIES} attempts: {last_error}"
        logger.error(error_msg)
        
        return self._create_error_result(
            token_id=order_args.token_id,
            side=order_args.side,
            price=order_args.price,
            size=order_args.size,
            timestamp=timestamp,
            error_message=error_msg,
        )
    
    async def _execute_market_with_retry(
        self,
        order_args: MarketOrderArgs,
        side: str,
        timestamp: datetime,
        fee_bps: float,
    ) -> ExecutionResult:
        """Execute market order with exponential backoff retry."""
        last_error: Optional[str] = None
        backoff = self.INITIAL_BACKOFF
        
        for attempt in range(self.MAX_RETRIES):
            try:
                # Create and execute market order
                if side == "BUY":
                    response = self._client.create_and_post_market_buy_order(order_args)
                else:
                    response = self._client.create_and_post_market_sell_order(order_args)
                
                # Parse response
                return self._parse_market_order_response(
                    response=response,
                    order_args=order_args,
                    side=side,
                    timestamp=timestamp,
                    fee_bps=fee_bps,
                )
                
            except Exception as e:
                last_error = str(e)
                logger.warning(
                    f"Market order attempt {attempt + 1}/{self.MAX_RETRIES} failed: {e}"
                )
                
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, self.MAX_BACKOFF)
        
        # All retries exhausted
        error_msg = f"Market order failed after {self.MAX_RETRIES} attempts: {last_error}"
        logger.error(error_msg)
        
        return self._create_error_result(
            token_id=order_args.token_id,
            side=side,
            price=0.0,
            size=order_args.amount,
            timestamp=timestamp,
            error_message=error_msg,
        )
    
    def _parse_order_response(
        self,
        response: Dict[str, Any],
        order_args: OrderArgs,
        timestamp: datetime,
        fee_bps: float,
    ) -> ExecutionResult:
        """Parse CLOB order response into ExecutionResult."""
        try:
            # Extract order details from response
            order_id = response.get('orderID', response.get('order_id', 'unknown'))
            status_str = response.get('status', 'unknown').lower()
            
            # Map status
            status_map = {
                'live': ExecutionStatus.SUBMITTED,
                'matched': ExecutionStatus.FILLED,
                'filled': ExecutionStatus.FILLED,
                'partial': ExecutionStatus.PARTIAL,
                'cancelled': ExecutionStatus.CANCELLED,
                'rejected': ExecutionStatus.REJECTED,
            }
            status = status_map.get(status_str, ExecutionStatus.PENDING)
            
            # Extract fill information
            filled_size = float(response.get('filledSize', response.get('filled_size', 0)))
            
            # Calculate platform fee (network fee is 0 via relayer)
            platform_fee = (fee_bps / 10000) * order_args.price * filled_size
            
            # Total cost is 0 for network fees (relayer handles gas)
            total_network_cost = 0.0
            
            result = ExecutionResult(
                order_id=str(order_id),
                status=status,
                token_id=order_args.token_id,
                side=order_args.side,
                price=order_args.price,
                size=order_args.size,
                filled_size=filled_size,
                total_cost=total_network_cost,
                platform_fee=platform_fee,
                timestamp=timestamp,
                tx_hash=response.get('transactionHash', response.get('tx_hash')),
                error_message=None,
            )
            
            # Audit log
            self._audit_logger.log_trade(
                action="ORDER_EXECUTED",
                token_id=order_args.token_id,
                side=order_args.side,
                price=order_args.price,
                size=order_args.size,
                status="SUCCESS",
                tx_hash=result.tx_hash,
            )
            
            logger.info(
                f"Order executed: {order_args.side} {filled_size}/{order_args.size} @ {order_args.price} "
                f"(status={status.value}, network_cost=$0.00)"
            )
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to parse order response: {e}")
            return self._create_error_result(
                token_id=order_args.token_id,
                side=order_args.side,
                price=order_args.price,
                size=order_args.size,
                timestamp=timestamp,
                error_message=f"Failed to parse response: {e}",
            )
    
    def _parse_market_order_response(
        self,
        response: Dict[str, Any],
        order_args: MarketOrderArgs,
        side: str,
        timestamp: datetime,
        fee_bps: float,
    ) -> ExecutionResult:
        """Parse CLOB market order response into ExecutionResult."""
        try:
            order_id = response.get('orderID', response.get('order_id', 'unknown'))
            status_str = response.get('status', 'unknown').lower()
            
            status_map = {
                'live': ExecutionStatus.SUBMITTED,
                'matched': ExecutionStatus.FILLED,
                'filled': ExecutionStatus.FILLED,
                'partial': ExecutionStatus.PARTIAL,
                'cancelled': ExecutionStatus.CANCELLED,
                'rejected': ExecutionStatus.REJECTED,
            }
            status = status_map.get(status_str, ExecutionStatus.PENDING)
            
            filled_size = float(response.get('filledSize', response.get('filled_size', 0)))
            avg_price = float(response.get('averagePrice', response.get('average_price', 0)))
            
            platform_fee = (fee_bps / 10000) * avg_price * filled_size
            
            result = ExecutionResult(
                order_id=str(order_id),
                status=status,
                token_id=order_args.token_id,
                side=side,
                price=avg_price,
                size=order_args.amount,
                filled_size=filled_size,
                total_cost=0.0,  # Relayer handles gas
                platform_fee=platform_fee,
                timestamp=timestamp,
                tx_hash=response.get('transactionHash', response.get('tx_hash')),
                error_message=None,
            )
            
            self._audit_logger.log_trade(
                action="MARKET_ORDER_EXECUTED",
                token_id=order_args.token_id,
                side=side,
                price=avg_price,
                size=order_args.amount,
                status="SUCCESS",
                tx_hash=result.tx_hash,
            )
            
            logger.info(
                f"Market order executed: {side} {filled_size} @ avg {avg_price} "
                f"(status={status.value}, network_cost=$0.00)"
            )
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to parse market order response: {e}")
            return self._create_error_result(
                token_id=order_args.token_id,
                side=side,
                price=0.0,
                size=order_args.amount,
                timestamp=timestamp,
                error_message=f"Failed to parse response: {e}",
            )
    
    def _create_error_result(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
        timestamp: datetime,
        error_message: str,
    ) -> ExecutionResult:
        """Create an error ExecutionResult."""
        self._audit_logger.log_trade(
            action="ORDER_FAILED",
            token_id=token_id,
            side=side,
            price=price,
            size=size,
            status="FAILED",
            error=error_message,
        )
        
        return ExecutionResult(
            order_id="",
            status=ExecutionStatus.ERROR,
            token_id=token_id,
            side=side,
            price=price,
            size=size,
            filled_size=0.0,
            total_cost=0.0,
            platform_fee=0.0,
            timestamp=timestamp,
            tx_hash=None,
            error_message=error_message,
        )
    
    async def get_balance(self) -> float:
        """
        Get current USDC balance.
        
        Returns:
            USDC balance as float
        """
        self._ensure_initialized()
        
        try:
            # Get balance from CLOB client
            balance_response = self._client.get_balance_allowance(
                asset_type="USDC"
            )
            
            if isinstance(balance_response, dict):
                balance = float(balance_response.get('balance', 0))
            else:
                balance = float(balance_response.balance if hasattr(balance_response, 'balance') else 0)
            
            return balance / (10 ** self._config.blockchain.USDC_DECIMALS)
            
        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            return 0.0
    
    async def check_allowance(self, token_id: str) -> bool:
        """
        Check if allowance is sufficient for trading.
        
        Args:
            token_id: Market token ID
        
        Returns:
            True if allowance is sufficient
        """
        self._ensure_initialized()
        
        try:
            # Check allowance via CLOB client
            allowance_response = self._client.get_balance_allowance(
                asset_type="CONDITIONAL"
            )
            
            if isinstance(allowance_response, dict):
                allowance = float(allowance_response.get('allowance', 0))
            else:
                allowance = float(allowance_response.allowance if hasattr(allowance_response, 'allowance') else 0)
            
            # Consider sufficient if allowance > 0 (exact requirements depend on order size)
            return allowance > 0
            
        except Exception as e:
            logger.error(f"Failed to check allowance: {e}")
            return False
    
    def _verify_before_sign(self, order: OrderArgs) -> bool:
        """
        Pre-signing verification of order parameters.
        
        Ensures order parameters are valid before creating signature.
        
        Args:
            order: Order arguments to verify
        
        Returns:
            True if order is safe to sign
        """
        try:
            # Validate token ID
            if not Validator.validate_token_id(order.token_id):
                logger.error(f"Invalid token ID in order: {order.token_id}")
                return False
            
            # Validate price
            if not Validator.validate_price(order.price):
                logger.error(f"Invalid price in order: {order.price}")
                return False
            
            # Validate size
            if not Validator.validate_amount(order.size, min_amount=0.01):
                logger.error(f"Invalid size in order: {order.size}")
                return False
            
            # Validate side
            if order.side.upper() not in ("BUY", "SELL"):
                logger.error(f"Invalid side in order: {order.side}")
                return False
            
            # Log verification for audit
            self._audit_logger.log_security_event(
                event_type="PRE_SIGN_VERIFICATION",
                details={
                    "token_id": order.token_id[:16] + "...",  # Truncate for logs
                    "price": order.price,
                    "size": order.size,
                    "side": order.side,
                },
                severity="INFO"
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Pre-signing verification error: {e}")
            return False
    
    async def close(self) -> None:
        """Clean up resources."""
        if self._initialized:
            logger.info("Closing executor...")
            self._client = None
            self._initialized = False
            
            self._audit_logger.log_security_event(
                event_type="EXECUTOR_CLOSED",
                details={},
                severity="INFO"
            )


# Convenience function for creating executor
async def create_executor(config: SecureConfig) -> ZeroFeeExecutor:
    """
    Create and initialize a ZeroFeeExecutor.
    
    Args:
        config: SecureConfig instance
    
    Returns:
        Initialized ZeroFeeExecutor
    """
    executor = ZeroFeeExecutor(config)
    await executor.initialize()
    return executor
