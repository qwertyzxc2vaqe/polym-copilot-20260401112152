"""
Expiry Pause Module - suppresses API calls during settlement.
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Set, Optional

logger = logging.getLogger(__name__)


class ExpiryPauseManager:
    """Manages post-expiry pause periods."""
    
    PAUSE_DURATION = 10.0  # 10 seconds
    
    def __init__(self):
        self._paused_markets: Set[str] = set()
        self._pause_end_times: dict = {}
    
    def start_pause(self, condition_id: str):
        """Start 10-second pause for expired market."""
        logger.info(f"[PAUSE] Starting {self.PAUSE_DURATION}s pause for {condition_id[:8]}...")
        self._paused_markets.add(condition_id)
        self._pause_end_times[condition_id] = datetime.now(timezone.utc) + timedelta(seconds=self.PAUSE_DURATION)
    
    def is_paused(self, condition_id: str = None) -> bool:
        """Check if scanning is paused."""
        if condition_id:
            return condition_id in self._paused_markets
        return len(self._paused_markets) > 0
    
    def should_suppress_api_call(self) -> bool:
        """Check if API calls should be suppressed."""
        self._cleanup_expired_pauses()
        return len(self._paused_markets) > 0
    
    def _cleanup_expired_pauses(self):
        """Remove pauses that have completed."""
        now = datetime.now(timezone.utc)
        completed = [cid for cid, end_time in self._pause_end_times.items() if now >= end_time]
        for cid in completed:
            self._paused_markets.discard(cid)
            del self._pause_end_times[cid]
            logger.info(f"[RESUME] Pause ended for {cid[:8]}")
