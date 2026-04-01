"""
Graceful Shutdown Handler for clean exit.
Ensures all mock orders cancelled and state saved.
"""
import asyncio
import signal
import logging
from typing import Optional, List, Callable, Awaitable

logger = logging.getLogger(__name__)


class GracefulShutdown:
    """Handles clean shutdown on Ctrl+C."""
    
    def __init__(self):
        self._shutdown_requested = False
        self._cleanup_tasks: List[Callable[[], Awaitable]] = []
        self._active_orders: List[str] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None
    
    def register_cleanup(self, cleanup_func: Callable[[], Awaitable]):
        """Register a cleanup coroutine to run on shutdown."""
        if not callable(cleanup_func):
            raise TypeError(f"cleanup_func must be callable, got {type(cleanup_func)}")
        self._cleanup_tasks.append(cleanup_func)
        logger.debug(f"Registered cleanup task: {cleanup_func.__name__}")
    
    def register_order(self, order_id: str):
        """Track active mock order for cancellation."""
        if order_id not in self._active_orders:
            self._active_orders.append(order_id)
            logger.debug(f"Registered order: {order_id}")
    
    def unregister_order(self, order_id: str):
        """Remove order from tracking (e.g., when it completes normally)."""
        if order_id in self._active_orders:
            self._active_orders.remove(order_id)
            logger.debug(f"Unregistered order: {order_id}")
    
    def setup_signal_handlers(self):
        """Setup SIGINT/SIGTERM handlers."""
        try:
            # Get the current event loop
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("No running event loop found, will set up on first use")
        
        # Add signal handlers
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                self._loop.add_signal_handler(
                    sig,
                    lambda s=sig: asyncio.create_task(self._handle_shutdown(s))
                ) if self._loop else None
            except (ValueError, OSError) as e:
                # Signal handlers may not be supported on all platforms
                logger.debug(f"Could not add signal handler for {sig}: {e}")
        
        # Also set up traditional signal handling as fallback
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        logger.info("Signal handlers installed for graceful shutdown")
    
    def _signal_handler(self, signum, frame):
        """Synchronous signal handler that triggers async shutdown."""
        if self._loop and self._loop.is_running():
            asyncio.create_task(self._handle_shutdown(signum))
        else:
            self._shutdown_requested = True
            logger.warning(f"Received signal {signum}, marking shutdown requested")
    
    async def _handle_shutdown(self, sig):
        """Handle shutdown signal."""
        if self._shutdown_requested:
            logger.debug("Shutdown already in progress")
            return
        
        self._shutdown_requested = True
        logger.warning(f"Received signal {sig}, initiating graceful shutdown...")
        
        # Cancel all mock orders
        await self._cancel_all_mock_orders()
        
        # Run cleanup tasks
        for cleanup_task in self._cleanup_tasks:
            try:
                logger.info(f"Running cleanup: {cleanup_task.__name__}")
                await cleanup_task()
            except asyncio.CancelledError:
                logger.debug(f"Cleanup task cancelled: {cleanup_task.__name__}")
            except Exception as e:
                logger.error(f"Cleanup error in {cleanup_task.__name__}: {e}", exc_info=True)
        
        logger.info("Graceful shutdown complete")
    
    async def _cancel_all_mock_orders(self):
        """Log cancellation of all mock orders."""
        if not self._active_orders:
            logger.info("No active mock orders to cancel")
            return
        
        logger.info(f"Cancelling {len(self._active_orders)} mock orders...")
        for order_id in self._active_orders:
            logger.info(f"[MOCK] Cancelled order: {order_id}")
        
        self._active_orders.clear()
    
    @property
    def should_stop(self) -> bool:
        """Check if shutdown has been requested."""
        return self._shutdown_requested
    
    def request_shutdown(self):
        """Manually request shutdown (non-async)."""
        self._shutdown_requested = True
        logger.info("Shutdown requested")


# Global singleton instance
_shutdown_handler: Optional[GracefulShutdown] = None


def get_shutdown_handler() -> GracefulShutdown:
    """Get or create the global shutdown handler instance."""
    global _shutdown_handler
    if _shutdown_handler is None:
        _shutdown_handler = GracefulShutdown()
    return _shutdown_handler


def initialize_shutdown_handler() -> GracefulShutdown:
    """Initialize the shutdown handler and setup signal handlers."""
    handler = get_shutdown_handler()
    handler.setup_signal_handlers()
    logger.info("Shutdown handler initialized")
    return handler
