"""
Dynamic 5-Minute Market Scanner for Polymarket.
Asynchronously queries Polymarket Gamma API for active BTC/ETH 5-minute markets.

Implements "Cruising Altitude" rate-limited polling:
- DISCOVERY (T > 5m): Normal polling (5-10 second intervals)
- CRUISING (T-5m to T-1m): Strict throttling (30 second intervals per asset)
- TERMINAL (T < 1m): High-frequency polling (handled separately)
"""

import asyncio
import aiohttp
import json
import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional, Callable, Awaitable, Set, Dict, Any
from datetime import datetime, timezone, timedelta
from enum import Enum

from config import get_config

logger = logging.getLogger(__name__)


class ScanningPhase(Enum):
    """
    Scanning phases based on time to market expiry.
    
    DISCOVERY: T > 5 minutes - Normal polling to find new markets
    CRUISING: T-5m to T-1m - Reduced polling (2 scans/min per currency) 
    TERMINAL: T < 1 minute - High-frequency polling for trade execution
    """
    DISCOVERY = "discovery"
    CRUISING = "cruising"
    TERMINAL = "terminal"


class Asset(Enum):
    """Supported crypto assets."""
    BTC = "BTC"
    ETH = "ETH"
    SOL = "SOL"
    XRP = "XRP"
    DOGE = "DOGE"
    HYPE = "HYPE"
    BNB = "BNB"
    UNKNOWN = "UNKNOWN"


@dataclass
class Market5Min:
    """Represents a 5-minute crypto market on Polymarket."""
    condition_id: str
    question: str
    yes_token_id: str
    no_token_id: str
    end_time: datetime
    asset: str  # BTC, ETH, SOL, XRP, DOGE, HYPE, BNB
    slug: Optional[str] = None
    market_id: Optional[str] = None
    
    @property
    def time_to_expiry(self) -> timedelta:
        """Time remaining until market expires."""
        return self.end_time - datetime.now(timezone.utc)
    
    @property
    def seconds_to_expiry(self) -> float:
        """Seconds remaining until market expires."""
        return self.time_to_expiry.total_seconds()
    
    def is_valid_for_entry(self, min_seconds: int = 60, max_seconds: int = 360) -> bool:
        """Check if market is within valid entry window."""
        seconds = self.seconds_to_expiry
        return min_seconds <= seconds <= max_seconds


@dataclass
class ScannerStats:
    """Statistics for the market scanner."""
    total_scans: int = 0
    markets_found: int = 0
    errors: int = 0
    last_scan_time: Optional[datetime] = None
    last_error: Optional[str] = None
    phase_transitions: int = 0
    cruising_polls: int = 0
    throttled_polls: int = 0


class MarketScanner:
    """
    Scans Polymarket for active 5-minute crypto markets.
    Uses the Gamma API for market discovery.
    Supports: BTC, ETH, SOL, XRP, DOGE, HYPE, BNB
    """
    
    # API endpoints
    GAMMA_SEARCH_ENDPOINT = "/public-search"
    GAMMA_MARKETS_ENDPOINT = "/markets"
    GAMMA_MARKET_ENDPOINT = "/market"
    
    # Supported crypto symbols for 5-minute markets
    SUPPORTED_CRYPTOS = ["btc", "eth", "sol", "xrp", "doge", "hype", "bnb"]
    
    # Slug pattern: {crypto}-updown-5m-{timestamp}
    SLUG_PATTERN = re.compile(r"^(btc|eth|sol|xrp|doge|hype|bnb)-updown-5m-(\d+)$", re.IGNORECASE)
    
    # Patterns for identifying 5-minute markets (fallback)
    MARKET_PATTERNS = [
        r"(Bitcoin|BTC).*?(Up|Down).*?5.?min",
        r"(Ethereum|ETH).*?(Up|Down).*?5.?min",
        r"(Solana|SOL).*?(Up|Down).*?5.?min",
        r"(XRP|Ripple).*?(Up|Down).*?5.?min",
        r"(Dogecoin|DOGE).*?(Up|Down).*?5.?min",
        r"(HYPE|Hyperliquid).*?(Up|Down).*?5.?min",
        r"(BNB|Binance).*?(Up|Down).*?5.?min",
        r"5.?min.*(Bitcoin|BTC|Ethereum|ETH|Solana|SOL|XRP|DOGE|HYPE|BNB)",
        r"(Bitcoin|Ethereum|Solana).*(5-minute|5 minute|five.?minute)",
    ]
    
    # Search terms for finding markets (used as fallback)
    SEARCH_TERMS = [
        "Bitcoin 5 minute",
        "Ethereum 5 minute", 
        "Solana 5 minute",
        "XRP 5 minute",
        "DOGE 5 minute",
        "BTC Up or Down",
        "ETH Up or Down",
        "SOL Up or Down",
    ]
    
    def __init__(
        self,
        gamma_host: Optional[str] = None,
        min_time_seconds: int = 60,
        max_time_seconds: int = 360,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
    ):
        """
        Initialize the market scanner.
        
        Args:
            gamma_host: Gamma API host URL
            min_time_seconds: Minimum seconds to expiry for valid markets
            max_time_seconds: Maximum seconds to expiry for valid markets
            max_retries: Maximum retry attempts on network errors
            retry_base_delay: Base delay for exponential backoff
        """
        config = get_config()
        self.gamma_host = gamma_host or config.polymarket.gamma_host
        self.min_time_seconds = min_time_seconds
        self.max_time_seconds = max_time_seconds
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        
        self._session: Optional[aiohttp.ClientSession] = None
        self._seen_markets: Set[str] = set()
        self._running = False
        self.stats = ScannerStats()
        
        # Cruising Altitude polling state
        self._last_poll_time: Dict[str, datetime] = {}  # Track last poll time per asset
        self._polling_mode: ScanningPhase = ScanningPhase.DISCOVERY
        self._previous_mode: Optional[ScanningPhase] = None  # For detecting transitions
        
        # Polling interval constants (in seconds)
        self.DISCOVERY_INTERVAL = 5.0  # Normal polling: 5-10 seconds
        self.CRUISING_INTERVAL = 30.0  # Cruising: 2 scans/min per currency = 30s intervals
        self.TERMINAL_INTERVAL = 0.5  # Terminal: high frequency (handled separately)
        
        # Phase thresholds (in seconds)
        self.CRUISING_THRESHOLD = 300  # 5 minutes - start cruising
        self.TERMINAL_THRESHOLD = 60   # 1 minute - switch to terminal
        
        # Compile regex patterns
        self._compiled_patterns = [
            re.compile(pattern, re.IGNORECASE)
            for pattern in self.MARKET_PATTERNS
        ]
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "PolymBot/1.0",
                }
            )
        return self._session
    
    async def close(self):
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
    
    def get_phase_for_expiry(self, seconds_to_expiry: float) -> ScanningPhase:
        """
        Determine scanning phase based on time to expiry.
        
        Args:
            seconds_to_expiry: Seconds until market expires
            
        Returns:
            ScanningPhase enum value
        """
        if seconds_to_expiry < self.TERMINAL_THRESHOLD:
            return ScanningPhase.TERMINAL
        elif seconds_to_expiry <= self.CRUISING_THRESHOLD:
            return ScanningPhase.CRUISING
        else:
            return ScanningPhase.DISCOVERY
    
    def get_polling_interval(self, seconds_to_expiry: float) -> float:
        """
        Returns appropriate polling interval based on time to expiry.
        
        Implements "Cruising Altitude" rate limiting:
        - T > 5 minutes (DISCOVERY): 5-10 second intervals
        - T-5m to T-1m (CRUISING): 30 second intervals (2 scans/min per currency)
        - T < 1 minute (TERMINAL): 0.5 second intervals (handled separately)
        
        Args:
            seconds_to_expiry: Seconds until market expires
            
        Returns:
            Polling interval in seconds
        """
        phase = self.get_phase_for_expiry(seconds_to_expiry)
        
        if phase == ScanningPhase.TERMINAL:
            return self.TERMINAL_INTERVAL
        elif phase == ScanningPhase.CRUISING:
            return self.CRUISING_INTERVAL
        else:  # DISCOVERY
            return self.DISCOVERY_INTERVAL
    
    def should_poll(self, asset: str) -> bool:
        """
        Check if enough time has passed since last poll for this asset.
        
        Enforces cruising altitude throttling: exactly 2 scans per minute
        per currency (30 second intervals) when in cruising mode.
        
        Args:
            asset: Asset identifier (e.g., "BTC", "ETH")
            
        Returns:
            True if polling is allowed, False if throttled
        """
        now = datetime.now(timezone.utc)
        last_poll = self._last_poll_time.get(asset)
        
        # First poll for this asset is always allowed
        if last_poll is None:
            return True
        
        # Determine required interval based on current polling mode
        if self._polling_mode == ScanningPhase.CRUISING:
            required_interval = self.CRUISING_INTERVAL
        elif self._polling_mode == ScanningPhase.TERMINAL:
            required_interval = self.TERMINAL_INTERVAL
        else:  # DISCOVERY
            required_interval = self.DISCOVERY_INTERVAL
        
        elapsed = (now - last_poll).total_seconds()
        return elapsed >= required_interval
    
    def record_poll(self, asset: str) -> None:
        """
        Record that a poll was made for the given asset.
        
        Args:
            asset: Asset identifier (e.g., "BTC", "ETH")
        """
        self._last_poll_time[asset] = datetime.now(timezone.utc)
    
    def check_and_log_throttle(self, asset: str) -> bool:
        """
        Check if asset poll would be throttled and log the event.
        
        Used for tracking throttled polls in cruising altitude mode.
        Logs when a poll attempt is rejected due to interval enforcement.
        
        Args:
            asset: Asset identifier (e.g., "BTC", "ETH")
            
        Returns:
            True if poll is allowed, False if throttled
        """
        if self.should_poll(asset):
            return True
        
        # Poll would be throttled - track it
        if self._polling_mode == ScanningPhase.CRUISING:
            self.stats.throttled_polls += 1
            last_poll = self._last_poll_time.get(asset)
            if last_poll:
                elapsed = (datetime.now(timezone.utc) - last_poll).total_seconds()
                logger.debug(
                    f"[PAUSE] THROTTLED: {asset} poll skipped (only {elapsed:.1f}s since last poll, "
                    f"needs {self.CRUISING_INTERVAL}s). Throttled count: {self.stats.throttled_polls}"
                )
        
        return False
    

    def update_polling_mode(self, markets: List['Market5Min']) -> ScanningPhase:
        """
        Update polling mode based on closest market expiry.
        
        The polling mode is determined by the market closest to expiry,
        as it requires the most time-sensitive polling.
        
        Args:
            markets: List of active markets
            
        Returns:
            Current ScanningPhase
        """
        if not markets:
            # No markets - stay in discovery mode
            new_mode = ScanningPhase.DISCOVERY
        else:
            # Find the market closest to expiry
            min_expiry = min(m.seconds_to_expiry for m in markets)
            new_mode = self.get_phase_for_expiry(min_expiry)
        
        # Log phase transitions
        if self._previous_mode is not None and new_mode != self._previous_mode:
            self._log_phase_transition(self._previous_mode, new_mode, markets)
            self.stats.phase_transitions += 1
        
        self._previous_mode = self._polling_mode
        self._polling_mode = new_mode
        
        return new_mode
    
    def _log_phase_transition(
        self, 
        old_phase: ScanningPhase, 
        new_phase: ScanningPhase,
        markets: List['Market5Min']
    ) -> None:
        """Log scanning phase transitions for debugging."""
        closest_market = min(markets, key=lambda m: m.seconds_to_expiry) if markets else None
        closest_info = ""
        if closest_market:
            closest_info = f" (closest: {closest_market.asset} @ {closest_market.seconds_to_expiry:.0f}s)"
        
        logger.info(
            f"[SIGNAL] Phase transition: {old_phase.value.upper()} -> {new_phase.value.upper()}{closest_info}"
        )
        
        if new_phase == ScanningPhase.CRUISING:
            logger.info(
                "[TAKEOFF] CRUISING ALTITUDE: Enforcing 30s polling intervals (2 scans/min per currency)"
            )
        elif new_phase == ScanningPhase.TERMINAL:
            logger.info(
                ">> TERMINAL VELOCITY: High-frequency polling enabled"
            )
        elif new_phase == ScanningPhase.DISCOVERY:
            logger.info(
                "[SEARCH] DISCOVERY MODE: Normal polling intervals"
            )
    
    def get_current_phase(self) -> ScanningPhase:
        """Get the current scanning phase."""
        return self._polling_mode
    
    def get_phase_info(self) -> Dict[str, Any]:
        """
        Get current phase information for dashboard display.
        
        Returns:
            Dict with phase name, interval, and stats
        """
        phase = self._polling_mode
        interval = {
            ScanningPhase.DISCOVERY: self.DISCOVERY_INTERVAL,
            ScanningPhase.CRUISING: self.CRUISING_INTERVAL,
            ScanningPhase.TERMINAL: self.TERMINAL_INTERVAL,
        }.get(phase, self.DISCOVERY_INTERVAL)
        
        return {
            "phase": phase.value,
            "interval_seconds": interval,
            "phase_transitions": self.stats.phase_transitions,
            "cruising_polls": self.stats.cruising_polls,
            "throttled_polls": self.stats.throttled_polls,
            "last_poll_times": {
                asset: ts.isoformat() 
                for asset, ts in self._last_poll_time.items()
            },
        }
    
    async def _request_with_retry(
        self,
        method: str,
        url: str,
        **kwargs,
    ) -> Optional[Dict[str, Any]]:
        """
        Make HTTP request with exponential backoff retry.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            url: Full URL to request
            **kwargs: Additional arguments for aiohttp request
            
        Returns:
            JSON response or None on failure
        """
        session = await self._get_session()
        last_error = None
        
        for attempt in range(self.max_retries):
            try:
                async with session.request(method, url, **kwargs) as response:
                    if response.status == 200:
                        return await response.json()
                    elif response.status == 429:
                        # Rate limited - wait longer
                        delay = self.retry_base_delay * (2 ** attempt) * 2
                        logger.warning(f"Rate limited, waiting {delay}s")
                        await asyncio.sleep(delay)
                    else:
                        logger.warning(
                            f"API request failed: {response.status} - {url}"
                        )
                        return None
                        
            except aiohttp.ClientError as e:
                last_error = str(e)
                delay = self.retry_base_delay * (2 ** attempt)
                logger.warning(
                    f"Network error (attempt {attempt + 1}/{self.max_retries}): {e}"
                )
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(delay)
                    
            except asyncio.TimeoutError:
                last_error = "Request timeout"
                delay = self.retry_base_delay * (2 ** attempt)
                logger.warning(
                    f"Timeout (attempt {attempt + 1}/{self.max_retries})"
                )
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(delay)
        
        self.stats.errors += 1
        self.stats.last_error = last_error
        return None
    
    def _is_5min_crypto_market(self, question: str, tags: List[str] = None, slug: str = None) -> bool:
        """
        Check if market question/title/slug matches 5-minute crypto pattern.
        
        Args:
            question: Market question/title
            tags: Optional list of market tags
            slug: Optional market slug
            
        Returns:
            True if this appears to be a 5-minute crypto market
        """
        # Primary check: match slug pattern {crypto}-updown-5m-{timestamp}
        if slug and self.SLUG_PATTERN.match(slug):
            return True
        
        # Check compiled patterns
        for pattern in self._compiled_patterns:
            if pattern.search(question):
                return True
        
        # Check tags if available
        if tags:
            tags_lower = [t.lower() for t in tags]
            has_crypto = any(t in tags_lower for t in [
                "bitcoin", "btc", "ethereum", "eth", "solana", "sol",
                "xrp", "ripple", "dogecoin", "doge", "hype", "bnb", "crypto"
            ])
            has_5min = any("5" in t and "min" in t.lower() for t in tags_lower) or "5m" in tags_lower
            if has_crypto and has_5min:
                return True
        
        # Fallback: check for both crypto mention and 5-minute mention
        question_lower = question.lower()
        has_crypto = any(term in question_lower for term in [
            "bitcoin", "btc", "ethereum", "eth", "solana", "sol",
            "xrp", "ripple", "dogecoin", "doge", "hype", "bnb"
        ])
        has_5min = any(term in question_lower for term in ["5 min", "5-min", "five min", "5min"])
        
        return has_crypto and has_5min
    
    def _extract_asset(self, question: str, slug: str = None) -> str:
        """Extract asset type from market question or slug."""
        # Primary: extract from slug pattern
        if slug:
            match = self.SLUG_PATTERN.match(slug)
            if match:
                return match.group(1).upper()
        
        question_lower = question.lower()
        
        if "bitcoin" in question_lower or "btc" in question_lower:
            return Asset.BTC.value
        elif "ethereum" in question_lower or "eth" in question_lower:
            return Asset.ETH.value
        elif "solana" in question_lower or "sol" in question_lower:
            return Asset.SOL.value
        elif "xrp" in question_lower or "ripple" in question_lower:
            return Asset.XRP.value
        elif "dogecoin" in question_lower or "doge" in question_lower:
            return Asset.DOGE.value
        elif "hype" in question_lower or "hyperliquid" in question_lower:
            return Asset.HYPE.value
        elif "bnb" in question_lower or "binance" in question_lower:
            return Asset.BNB.value
        else:
            return Asset.UNKNOWN.value
    
    def _parse_end_time(self, time_value: Any) -> Optional[datetime]:
        """
        Parse end time from various formats.
        
        Args:
            time_value: End time as string, int timestamp, or datetime
            
        Returns:
            datetime in UTC or None if parsing fails
        """
        try:
            if isinstance(time_value, datetime):
                if time_value.tzinfo is None:
                    return time_value.replace(tzinfo=timezone.utc)
                return time_value
            
            if isinstance(time_value, (int, float)):
                # Unix timestamp (seconds or milliseconds)
                if time_value > 1e12:  # Milliseconds
                    time_value = time_value / 1000
                return datetime.fromtimestamp(time_value, tz=timezone.utc)
            
            if isinstance(time_value, str):
                # Try ISO format
                try:
                    dt = datetime.fromisoformat(time_value.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt
                except ValueError:
                    pass
                
                # Try Unix timestamp as string
                try:
                    ts = float(time_value)
                    if ts > 1e12:
                        ts = ts / 1000
                    return datetime.fromtimestamp(ts, tz=timezone.utc)
                except ValueError:
                    pass
            
            logger.warning(f"Could not parse end time: {time_value}")
            return None
            
        except Exception as e:
            logger.warning(f"Error parsing end time {time_value}: {e}")
            return None
    
    def _parse_market(self, market_data: Dict[str, Any]) -> Optional[Market5Min]:
        """
        Parse market data into Market5Min object.
        
        Args:
            market_data: Raw market data from API
            
        Returns:
            Market5Min object or None if parsing fails
        """
        try:
            # Extract slug first for pattern matching
            slug = market_data.get("slug", "")
            
            # Extract required fields - try multiple possible field names
            condition_id = (
                market_data.get("condition_id") or
                market_data.get("conditionId") or
                market_data.get("id")
            )
            
            question = (
                market_data.get("question") or
                market_data.get("title") or
                market_data.get("name") or
                ""
            )
            
            # Check if this is a 5-min crypto market (using slug pattern primarily)
            tags = market_data.get("tags", [])
            if not self._is_5min_crypto_market(question, tags, slug):
                return None
            
            # Extract token IDs
            # Try different API response structures
            yes_token_id = None
            no_token_id = None
            
            # Structure 1: tokens array
            tokens = market_data.get("tokens", [])
            for token in tokens:
                outcome = token.get("outcome", "").upper()
                token_id = token.get("token_id") or token.get("tokenId")
                if outcome in ("YES", "UP"):
                    yes_token_id = token_id
                elif outcome in ("NO", "DOWN"):
                    no_token_id = token_id
            
            # Structure 2: clobTokenIds (JSON string or list)
            if not yes_token_id or not no_token_id:
                clob_token_ids = market_data.get("clobTokenIds", [])
                # Handle JSON string format
                if isinstance(clob_token_ids, str):
                    try:
                        clob_token_ids = json.loads(clob_token_ids)
                    except json.JSONDecodeError:
                        clob_token_ids = []
                if len(clob_token_ids) >= 2:
                    yes_token_id = yes_token_id or clob_token_ids[0]
                    no_token_id = no_token_id or clob_token_ids[1]
            
            # Structure 3: Direct fields
            yes_token_id = yes_token_id or market_data.get("yes_token_id") or market_data.get("yesTokenId")
            no_token_id = no_token_id or market_data.get("no_token_id") or market_data.get("noTokenId")
            
            # Parse end time
            end_time_raw = (
                market_data.get("end_date_iso") or
                market_data.get("endDate") or
                market_data.get("end_time") or
                market_data.get("endTime") or
                market_data.get("expirationTime") or
                market_data.get("resolution_time")
            )
            
            end_time = self._parse_end_time(end_time_raw)
            
            # Validate required fields
            if not all([condition_id, question, end_time]):
                logger.debug(
                    f"Missing required fields: condition_id={bool(condition_id)}, "
                    f"question={bool(question)}, end_time={bool(end_time)}"
                )
                return None
            
            # Check if market is active (not resolved)
            resolved = market_data.get("resolved", False) or market_data.get("is_resolved", False)
            if resolved:
                return None
            
            # Check if within time window
            now = datetime.now(timezone.utc)
            time_to_expiry = (end_time - now).total_seconds()
            
            if time_to_expiry < self.min_time_seconds or time_to_expiry > self.max_time_seconds:
                return None
            
            # Extract asset
            asset = self._extract_asset(question, slug)
            
            return Market5Min(
                condition_id=condition_id,
                question=question,
                yes_token_id=yes_token_id or "",
                no_token_id=no_token_id or "",
                end_time=end_time,
                asset=asset,
                slug=slug,
                market_id=market_data.get("market_id") or market_data.get("marketId"),
            )
            
        except Exception as e:
            logger.warning(f"Error parsing market data: {e}")
            return None
    
    async def _search_markets(self, search_term: str) -> List[Dict[str, Any]]:
        """
        Search for markets using the Gamma API.
        
        Args:
            search_term: Search query
            
        Returns:
            List of market data dictionaries
        """
        url = f"{self.gamma_host}{self.GAMMA_SEARCH_ENDPOINT}"
        params = {"q": search_term, "limit": 50}
        
        response = await self._request_with_retry("GET", url, params=params)
        
        if response is None:
            return []
        
        # Handle different response structures
        if isinstance(response, list):
            return response
        elif isinstance(response, dict):
            return response.get("markets", response.get("data", response.get("results", [])))
        
        return []
    
    async def _get_5m_markets_by_slug(self) -> List[Dict[str, Any]]:
        """
        Get 5-minute markets by querying exact slugs.
        
        The Gamma API's general /markets listing excludes 5-minute markets,
        but they can be fetched directly by slug. This method generates
        slugs for upcoming 5-minute intervals and queries them.
        
        Returns:
            List of market data dictionaries for valid 5m markets
        """
        now = datetime.now(timezone.utc)
        current_ts = int(now.timestamp())
        
        # Round to current 5-minute interval
        rounded_ts = (current_ts // 300) * 300
        
        # Generate slugs for current + next several intervals
        # Query up to 4 intervals ahead (20 minutes) to find markets in window
        slugs_to_query = []
        for delta_intervals in range(5):  # 0 to 4 (current + next 4)
            ts = rounded_ts + (delta_intervals * 300)
            for crypto in self.SUPPORTED_CRYPTOS:
                slugs_to_query.append(f"{crypto}-updown-5m-{ts}")
        
        # Query all slugs concurrently
        url = f"{self.gamma_host}{self.GAMMA_MARKETS_ENDPOINT}"
        tasks = []
        for slug in slugs_to_query:
            tasks.append(self._request_with_retry("GET", url, params={"slug": slug}))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        markets = []
        for result in results:
            if isinstance(result, Exception):
                continue
            if result and isinstance(result, list) and len(result) > 0:
                markets.append(result[0])
        
        logger.debug(f"Found {len(markets)} 5m markets via direct slug query")
        return markets

    async def _get_all_markets(self) -> List[Dict[str, Any]]:
        """
        Get all active 5-minute crypto markets from the Gamma API.
        
        Primary strategy: Query markets directly by generated slug patterns,
        since 5-minute markets are not included in the general listing.
        
        Fallback: Query general listing for any matching markets.
        
        Returns:
            List of market data dictionaries sorted by end_date (soonest first)
        """
        # Primary: Direct slug query (5m markets excluded from general listing)
        all_markets = await self._get_5m_markets_by_slug()
        
        # Fallback: Also check general listing in case API behavior changes
        if len(all_markets) == 0:
            url = f"{self.gamma_host}{self.GAMMA_MARKETS_ENDPOINT}"
            base_params = {
                "active": "true",
                "closed": "false",
                "limit": 500,
                "order": "endDate",
                "ascending": "true",
            }
            
            response = await self._request_with_retry("GET", url, params=base_params)
            
            if response:
                if isinstance(response, list):
                    listing_markets = response
                elif isinstance(response, dict):
                    listing_markets = response.get("markets", response.get("data", []))
                else:
                    listing_markets = []
                
                # Filter to only 5m updown markets using slug pattern
                all_markets = [
                    m for m in listing_markets
                    if self.SLUG_PATTERN.match(m.get("slug", ""))
                ]
        
        # Sort by endDate (soonest expiring first)
        all_markets.sort(
            key=lambda m: m.get("endDate", ""),
            reverse=False
        )
        
        logger.debug(f"Found {len(all_markets)} 5m markets total")
        
        return all_markets
    
    async def scan_markets(self) -> List[Market5Min]:
        """
        Find active 5-minute crypto markets ending within the time window.
        Supports: BTC, ETH, SOL, XRP, DOGE, HYPE, BNB
        
        Returns:
            List of Market5Min objects for valid markets, sorted by end_time (soonest first)
        """
        self.stats.total_scans += 1
        self.stats.last_scan_time = datetime.now(timezone.utc)
        
        markets: List[Market5Min] = []
        seen_condition_ids: Set[str] = set()
        
        try:
            # Primary strategy: Get all markets and filter by slug pattern
            # This is more reliable than search for finding updown-5m markets
            all_markets = await self._get_all_markets()
            for market_data in all_markets:
                market = self._parse_market(market_data)
                if market and market.condition_id not in seen_condition_ids:
                    seen_condition_ids.add(market.condition_id)
                    markets.append(market)
            
            # Fallback strategy: Search with specific terms (may catch some missed markets)
            if len(markets) == 0:
                search_tasks = [
                    self._search_markets(term)
                    for term in self.SEARCH_TERMS
                ]
                search_results = await asyncio.gather(*search_tasks, return_exceptions=True)
                
                for result in search_results:
                    if isinstance(result, Exception):
                        logger.warning(f"Search error: {result}")
                        continue
                        
                    for market_data in result:
                        market = self._parse_market(market_data)
                        if market and market.condition_id not in seen_condition_ids:
                            seen_condition_ids.add(market.condition_id)
                            markets.append(market)
            
            # Sort by end_time (soonest first)
            markets.sort(key=lambda m: m.end_time)
            
            # Update polling mode based on closest market expiry
            if markets:
                self.update_polling_mode(markets)
            
            self.stats.markets_found += len(markets)
            
            # Build asset summary
            asset_counts = {}
            for m in markets:
                asset_counts[m.asset] = asset_counts.get(m.asset, 0) + 1
            asset_summary = ", ".join(f"{k}: {v}" for k, v in sorted(asset_counts.items()))
            
            # Log polling mode and throttling info in cruising phase
            if self._polling_mode == ScanningPhase.CRUISING:
                self.stats.cruising_polls += 1
                logger.debug(
                    f"[TAKEOFF] CRUISING: Scan #{self.stats.cruising_polls} - "
                    f"found {len(markets)} markets ({asset_summary})"
                )
            
            logger.info(
                f"Scan complete: found {len(markets)} valid 5-min markets ({asset_summary})"
            )
            
            return markets
            
        except Exception as e:
            self.stats.errors += 1
            self.stats.last_error = str(e)
            logger.error(f"Error during market scan: {e}")
            return []
    
    async def run_continuous(
        self,
        callback: Callable[[List[Market5Min]], Awaitable[None]],
        interval_seconds: float = 5.0,
        report_all: bool = False,
    ):
        """
        Continuously scan for markets and invoke callback with new markets.
        
        Args:
            callback: Async function called with list of new markets
            interval_seconds: Seconds between scans
            report_all: If True, report all markets each scan; if False, only new ones
        """
        self._running = True
        logger.info(
            f"Starting continuous scanner (interval={interval_seconds}s, "
            f"time_window={self.min_time_seconds}-{self.max_time_seconds}s)"
        )
        
        try:
            while self._running:
                try:
                    markets = await self.scan_markets()
                    
                    if report_all:
                        new_markets = markets
                    else:
                        # Filter to only markets we haven't seen
                        new_markets = [
                            m for m in markets
                            if m.condition_id not in self._seen_markets
                        ]
                        
                        # Update seen markets
                        for market in new_markets:
                            self._seen_markets.add(market.condition_id)
                    
                    if new_markets:
                        logger.info(f"Found {len(new_markets)} new markets")
                        await callback(new_markets)
                    
                    # Clean up expired markets from seen set
                    now = datetime.now(timezone.utc)
                    self._seen_markets = {
                        cid for cid in self._seen_markets
                        if cid in {m.condition_id for m in markets}
                    }
                    
                except Exception as e:
                    logger.error(f"Error in scan loop: {e}")
                    self.stats.errors += 1
                    self.stats.last_error = str(e)
                
                await asyncio.sleep(interval_seconds)
                
        finally:
            await self.close()
            logger.info("Continuous scanner stopped")
    
    def stop(self):
        """Stop the continuous scanner."""
        self._running = False
        logger.info("Scanner stop requested")
    
    def get_stats(self) -> ScannerStats:
        """Get scanner statistics."""
        return self.stats
    
    def reset_seen_markets(self):
        """Clear the set of seen market IDs."""
        self._seen_markets.clear()


async def main():
    """Example usage of the market scanner."""
    import sys
    
    # Simple callback that prints markets
    async def print_markets(markets: List[Market5Min]):
        for market in markets:
            print(f"\n{'='*60}")
            print(f"Market: {market.question}")
            print(f"Asset: {market.asset}")
            print(f"Condition ID: {market.condition_id}")
            print(f"YES Token: {market.yes_token_id}")
            print(f"NO Token: {market.no_token_id}")
            print(f"Expires: {market.end_time}")
            print(f"Time remaining: {market.seconds_to_expiry:.0f}s")
    
    scanner = MarketScanner()
    
    try:
        # Single scan
        print("Performing single market scan...")
        markets = await scanner.scan_markets()
        
        if markets:
            await print_markets(markets)
        else:
            print("No active 5-minute markets found in the time window.")
        
        print(f"\nStats: {scanner.get_stats()}")
        
        # Optional: Run continuous scan
        if "--continuous" in sys.argv:
            print("\nStarting continuous scan (Ctrl+C to stop)...")
            await scanner.run_continuous(print_markets, interval_seconds=5)
            
    except KeyboardInterrupt:
        print("\nScan interrupted")
    finally:
        await scanner.close()


if __name__ == "__main__":
    asyncio.run(main())
