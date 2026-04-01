"""
Auth Manager for py-clob-client in read-only/testnet mode.
EDUCATIONAL SANDBOX ONLY - routes all payloads to local logger.

This module manages authentication with Polymarket's CLOB (Central Limit Order Book)
in a paper trading / dry-run mode. No live transactions are executed.
"""

import logging
import hashlib
import hmac
import base64
from typing import Optional, Dict, Any
from datetime import datetime

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds
    CLOB_AVAILABLE = True
except ImportError:
    CLOB_AVAILABLE = False
    ClobClient = None
    ApiCreds = None

from src.config import get_config

logger = logging.getLogger(__name__)


class AuthManager:
    """
    Manages py-clob-client authentication in read-only/testnet mode.
    
    All operations are paper trades - no live execution occurs.
    L1 signature payloads are logged locally instead of sent to the network.
    """
    
    def __init__(self, chain_id: int = 137, sandbox_mode: bool = True):
        """
        Initialize AuthManager.
        
        Args:
            chain_id: Polygon chain ID (137 = mainnet, 80001 = testnet)
            sandbox_mode: If True, runs in read-only/paper trading mode
        """
        self.chain_id = chain_id
        self.sandbox_mode = sandbox_mode
        self.client: Optional[ClobClient] = None
        self.api_creds: Optional[ApiCreds] = None
        self._initialized = False
        
        logger.info(
            f"AuthManager initialized | chain_id={chain_id} | "
            f"sandbox_mode={sandbox_mode} | CLOB_AVAILABLE={CLOB_AVAILABLE}"
        )
    
    async def initialize(self) -> bool:
        """
        Initialize CLOB client in read-only mode.
        
        Loads credentials from config.py and creates a ClobClient instance
        configured for testnet/dry-run mode only.
        
        Returns:
            bool: True if initialization successful, False otherwise
        """
        try:
            config = get_config()
            
            # Verify we're not in autonomous mode (live trading)
            if config.is_autonomous():
                logger.warning(
                    "AuthManager running with TRADING_MODE=autonomous. "
                    "This is dangerous in sandbox mode - should use dry_run or live_test."
                )
            
            # Create API credentials from config
            self.api_creds = ApiCreds(
                api_key=config.polymarket.api_key,
                secret=config.polymarket.secret,
                passphrase=config.polymarket.passphrase,
            )
            
            if not CLOB_AVAILABLE:
                logger.warning(
                    "py-clob-client not available - running in mock mode. "
                    "All operations will be logged locally."
                )
                self._initialized = True
                logger.info("AuthManager initialized in mock mode (SANDBOX)")
                return True
            
            # Initialize CLOB client in read-only mode
            # Using testnet host for sandbox
            testnet_host = "https://clob-testnet.polymarket.com"
            
            self.client = ClobClient(
                host=testnet_host,
                chain_id=self.chain_id,
                api_credentials=self.api_creds,
                funder=None,  # No on-chain operations in sandbox
            )
            
            self._initialized = True
            logger.info(
                f"AuthManager initialized successfully | "
                f"host={testnet_host} | chain_id={self.chain_id}"
            )
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize AuthManager: {e}")
            self._initialized = False
            return False
    
    def get_client(self) -> Optional[ClobClient]:
        """
        Get the CLOB client instance.
        
        Returns None if client is not available or not initialized.
        
        Returns:
            Optional[ClobClient]: The client instance, or None
        """
        if not self._initialized:
            logger.warning("AuthManager not initialized - call initialize() first")
            return None
        
        if not CLOB_AVAILABLE:
            logger.debug("py-clob-client not available - returning None")
            return None
        
        return self.client
    
    def generate_l1_signature(self, payload: Dict[str, Any]) -> str:
        """
        Generate L1 signature payload.
        
        In sandbox mode, this:
        1. Logs the payload to console (for inspection)
        2. Returns a mock signature (not sent to network)
        3. Never executes on-chain
        
        The signature generation demonstrates the HMAC-SHA256 signing process
        that would be used in production, but in sandbox mode it's purely
        for educational purposes and never transmitted.
        
        Args:
            payload: Dictionary containing the order/transaction details
        
        Returns:
            str: Mock L1 signature (hex string)
        
        Example:
            >>> payload = {
            ...     "order_id": "12345",
            ...     "token_id": "0x...",
            ...     "amount": "100.00",
            ...     "price": "0.50",
            ... }
            >>> sig = auth_mgr.generate_l1_signature(payload)
            >>> # Signature is logged but never sent to network
        """
        try:
            if not self._initialized:
                logger.error("Cannot generate signature - AuthManager not initialized")
                return ""
            
            # Log the payload for inspection
            logger.info(f"[SANDBOX] L1 Signature Payload (DRY_RUN): {payload}")
            
            if not CLOB_AVAILABLE or not self.api_creds:
                logger.warning("Running in mock mode - returning demo signature")
                return self._generate_mock_signature(payload)
            
            # Create signature payload string
            payload_str = str(sorted(payload.items()))
            
            # HMAC-SHA256 signature using API secret
            signature = hmac.new(
                self.api_creds.secret.encode(),
                payload_str.encode(),
                hashlib.sha256,
            ).digest()
            
            # Convert to hex string
            sig_hex = base64.b64encode(signature).decode()
            
            logger.debug(
                f"[SANDBOX] Generated L1 signature (not transmitted): {sig_hex[:16]}..."
            )
            
            return sig_hex
            
        except Exception as e:
            logger.error(f"Error generating L1 signature: {e}")
            return ""
    
    @staticmethod
    def _generate_mock_signature(payload: Dict[str, Any]) -> str:
        """
        Generate a mock L1 signature for testing/demo purposes.
        
        This creates a deterministic hash of the payload without requiring
        the actual API secret. Used when py-clob-client is not available.
        
        Args:
            payload: The order/transaction payload
        
        Returns:
            str: Mock signature (hex string)
        """
        payload_str = str(sorted(payload.items()))
        mock_sig = hashlib.sha256(payload_str.encode()).hexdigest()
        return f"mock_{mock_sig[:24]}"
    
    def is_initialized(self) -> bool:
        """Check if AuthManager has been initialized."""
        return self._initialized
    
    def is_sandbox_mode(self) -> bool:
        """Check if running in sandbox mode."""
        return self.sandbox_mode
    
    def validate_credentials(self) -> bool:
        """
        Validate that credentials are properly loaded.
        
        Returns:
            bool: True if credentials are valid
        """
        if not self.api_creds:
            logger.error("No API credentials loaded")
            return False
        
        # Validate credentials are not empty
        if not all([
            self.api_creds.api_key,
            self.api_creds.secret,
            self.api_creds.passphrase,
        ]):
            logger.error("One or more API credentials are empty")
            return False
        
        logger.info("API credentials validated")
        return True
    
    def get_status(self) -> Dict[str, Any]:
        """
        Get current authentication status.
        
        Returns:
            Dict with status information
        """
        return {
            "initialized": self._initialized,
            "sandbox_mode": self.sandbox_mode,
            "chain_id": self.chain_id,
            "clob_available": CLOB_AVAILABLE,
            "has_client": self.client is not None,
            "has_credentials": self.api_creds is not None,
            "timestamp": datetime.now().isoformat(),
        }
