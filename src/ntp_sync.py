"""
NTP Synchronizer for timestamp delta analysis.
Studies clock drift for educational latency research.

This module implements NTP (Network Time Protocol) client functionality
to measure local system clock offset against authoritative time servers.
Used for understanding latency and timestamp synchronization in distributed systems.
"""
import asyncio
import socket
import struct
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional, List
import time

logger = logging.getLogger(__name__)

# NTP epoch: 1900-01-01, Unix epoch: 1970-01-01
NTP_DELTA = 2208988800


@dataclass
class TimeSyncResult:
    """Result from NTP synchronization query."""
    ntp_server: str
    offset_ms: float  # Local clock offset from NTP (positive = ahead)
    roundtrip_ms: float  # RTT to NTP server
    timestamp: datetime


@dataclass
class OffsetStatistics:
    """Statistics on clock offset history."""
    mean_offset_ms: float
    std_dev_ms: float
    min_offset_ms: float
    max_offset_ms: float
    sample_count: int


class NTPSynchronizer:
    """
    NTP time synchronization for latency analysis.
    
    Implements simplified NTP client (RFC 1305) to query
    authoritative time servers and measure local clock offset.
    """
    
    NTP_SERVERS = [
        "pool.ntp.org",
        "time.google.com",
        "time.cloudflare.com",
        "time.apple.com",
    ]
    
    NTP_PORT = 123
    NTP_QUERY_TIMEOUT = 5.0
    MAX_HISTORY = 1000
    
    def __init__(self, servers: Optional[List[str]] = None):
        """
        Initialize NTP synchronizer.
        
        Args:
            servers: List of NTP servers to use (defaults to pool of public servers)
        """
        self.servers = servers or self.NTP_SERVERS
        self._offset_history: List[TimeSyncResult] = []
        self._last_sync: Optional[TimeSyncResult] = None
        self._sync_lock = asyncio.Lock()
    
    def _build_ntp_request(self) -> bytes:
        """Build NTP request packet (RFC 1305 format)."""
        # NTP packet structure (simplified)
        # Byte 0: LI (2 bits) | VN (3 bits) | Mode (3 bits)
        # Bytes 1-3: Stratum, Poll, Precision
        # Bytes 4-7: Root Delay
        # Bytes 8-11: Root Dispersion
        # Bytes 12-15: Reference ID
        # Bytes 16-23: Reference Timestamp
        # Bytes 24-31: Originate Timestamp
        # Bytes 32-39: Receive Timestamp
        # Bytes 40-47: Transmit Timestamp
        
        data = bytearray(48)
        
        # Set LI=0, VN=3, Mode=3 (client)
        data[0] = 0x23
        
        # Set current time in transmit timestamp (NTP format)
        current_time = time.time()
        ntp_time = current_time + NTP_DELTA
        
        # Split into seconds and fraction
        seconds = int(ntp_time)
        fraction = int((ntp_time - seconds) * (2 ** 32))
        
        # Place in transmit timestamp (bytes 40-47)
        struct.pack_into(">I", data, 40, seconds)
        struct.pack_into(">I", data, 44, fraction)
        
        return bytes(data)
    
    def _parse_ntp_response(self, data: bytes) -> tuple:
        """
        Parse NTP response packet.
        
        Returns:
            (receive_timestamp_ntp, transmit_timestamp_ntp) as Unix floats
        """
        if len(data) < 48:
            raise ValueError(f"Invalid NTP response: {len(data)} bytes")
        
        # Extract transmit timestamp (bytes 40-47) - server's time when sending
        tx_seconds = struct.unpack(">I", data[40:44])[0]
        tx_fraction = struct.unpack(">I", data[44:48])[0]
        
        # Extract receive timestamp (bytes 32-39) - server's time when receiving our request
        rx_seconds = struct.unpack(">I", data[32:36])[0]
        rx_fraction = struct.unpack(">I", data[36:40])[0]
        
        # Convert from NTP to Unix epoch
        tx_time = (tx_seconds - NTP_DELTA) + (tx_fraction / (2 ** 32))
        rx_time = (rx_seconds - NTP_DELTA) + (rx_fraction / (2 ** 32))
        
        return rx_time, tx_time
    
    async def _query_single_server(self, server: str) -> Optional[TimeSyncResult]:
        """Query a single NTP server and measure offset."""
        try:
            # Create UDP socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(self.NTP_QUERY_TIMEOUT)
            
            # Resolve server hostname
            try:
                server_ip = await asyncio.get_event_loop().getaddrinfo(
                    server, self.NTP_PORT, socket.AF_INET, socket.SOCK_DGRAM
                )
                if not server_ip:
                    logger.warning(f"Could not resolve NTP server: {server}")
                    return None
                server_ip = server_ip[0][4][0]
            except socket.gaierror as e:
                logger.warning(f"DNS resolution failed for {server}: {e}")
                return None
            
            # Build and send request
            request = self._build_ntp_request()
            client_send_time = time.time()
            
            try:
                # Send request
                sock.sendto(request, (server_ip, self.NTP_PORT))
                
                # Receive response with timeout
                response, _ = sock.recvfrom(48)
                client_recv_time = time.time()
            except socket.timeout:
                logger.warning(f"NTP query timeout for {server}")
                return None
            finally:
                sock.close()
            
            # Parse response
            rx_time, tx_time = self._parse_ntp_response(response)
            
            # Calculate offset using NTP algorithm
            # offset = ((rx_time - client_send_time) + (tx_time - client_recv_time)) / 2
            roundtrip = client_recv_time - client_send_time
            offset = ((rx_time - client_send_time) + (tx_time - client_recv_time)) / 2
            
            # Convert to milliseconds
            offset_ms = offset * 1000
            roundtrip_ms = roundtrip * 1000
            
            result = TimeSyncResult(
                ntp_server=server,
                offset_ms=offset_ms,
                roundtrip_ms=roundtrip_ms,
                timestamp=datetime.now(timezone.utc)
            )
            
            logger.info(
                f"NTP sync {server}: offset={offset_ms:.2f}ms, "
                f"roundtrip={roundtrip_ms:.2f}ms"
            )
            
            return result
            
        except Exception as e:
            logger.error(f"NTP query failed for {server}: {e}")
            return None
    
    async def sync(self) -> Optional[TimeSyncResult]:
        """
        Query NTP servers and calculate offset.
        
        Attempts to sync with multiple servers and returns result
        from most responsive server.
        
        Returns:
            TimeSyncResult with best measurement, or None if all queries fail
        """
        async with self._sync_lock:
            tasks = [
                self._query_single_server(server)
                for server in self.servers
            ]
            
            results = await asyncio.gather(*tasks, return_exceptions=False)
            
            # Filter successful results
            valid_results = [r for r in results if r is not None]
            
            if not valid_results:
                logger.warning("All NTP queries failed")
                return None
            
            # Select result with minimum roundtrip time (lowest latency)
            best_result = min(valid_results, key=lambda r: r.roundtrip_ms)
            
            # Track in history
            self._offset_history.append(best_result)
            if len(self._offset_history) > self.MAX_HISTORY:
                self._offset_history.pop(0)
            
            self._last_sync = best_result
            return best_result
    
    def get_adjusted_time(self) -> datetime:
        """
        Get NTP-adjusted UTC time.
        
        Applies latest measured offset to current system time
        to get estimate of true UTC time.
        
        Returns:
            UTC datetime adjusted by measured NTP offset
        """
        now = datetime.now(timezone.utc)
        
        if self._last_sync is None:
            logger.warning("No NTP sync available, returning system time")
            return now
        
        # Apply offset to current time
        adjustment = timedelta(milliseconds=self._last_sync.offset_ms)
        adjusted = now + adjustment
        
        return adjusted
    
    def compare_with_server_timestamp(self, server_ts: datetime) -> float:
        """
        Compare external server timestamp against NTP-adjusted local time.
        
        Measures the delta between a server's timestamp and what our
        NTP-adjusted local clock says, indicating potential drift
        or latency between systems.
        
        Args:
            server_ts: Timestamp received from external server
            
        Returns:
            Delta in milliseconds (positive = server ahead)
        """
        adjusted_local = self.get_adjusted_time()
        
        # Both should be timezone-aware
        if server_ts.tzinfo is None:
            server_ts = server_ts.replace(tzinfo=timezone.utc)
        
        delta = server_ts - adjusted_local
        delta_ms = delta.total_seconds() * 1000
        
        return delta_ms
    
    def get_offset_statistics(self) -> Optional[OffsetStatistics]:
        """
        Calculate statistics on recorded offset history.
        
        Returns:
            OffsetStatistics with mean, std dev, min, max offsets
        """
        if not self._offset_history:
            return None
        
        offsets = [r.offset_ms for r in self._offset_history]
        
        mean = sum(offsets) / len(offsets)
        
        # Standard deviation
        variance = sum((x - mean) ** 2 for x in offsets) / len(offsets)
        std_dev = variance ** 0.5
        
        return OffsetStatistics(
            mean_offset_ms=mean,
            std_dev_ms=std_dev,
            min_offset_ms=min(offsets),
            max_offset_ms=max(offsets),
            sample_count=len(offsets)
        )
    
    def get_history(self) -> List[TimeSyncResult]:
        """Get full history of NTP sync measurements."""
        return self._offset_history.copy()
    
    def clear_history(self):
        """Clear recorded offset history."""
        self._offset_history.clear()
        self._last_sync = None


# Example usage for educational latency analysis
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    async def main():
        """Demonstrate NTP synchronization."""
        sync = NTPSynchronizer()
        
        print("Synchronizing with NTP servers...")
        result = await sync.sync()
        
        if result:
            print(f"\nNTP Sync Result:")
            print(f"  Server: {result.ntp_server}")
            print(f"  Offset: {result.offset_ms:.2f} ms")
            print(f"  Roundtrip: {result.roundtrip_ms:.2f} ms")
            print(f"  Timestamp: {result.timestamp}")
            
            adjusted = sync.get_adjusted_time()
            print(f"\nAdjusted UTC time: {adjusted}")
            
            # Simulate server timestamp (5ms in future)
            server_ts = datetime.now(timezone.utc) + timedelta(milliseconds=5)
            delta = sync.compare_with_server_timestamp(server_ts)
            print(f"\nServer timestamp delta: {delta:.2f} ms")
        else:
            print("NTP synchronization failed")
    
    asyncio.run(main())
