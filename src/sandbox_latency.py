"""
Sandbox Latency Test Module.
Tests network ping speeds with 1-share mock orders.
"""
import asyncio
import aiohttp
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class LatencyResult:
    endpoint: str
    latency_ms: float
    status_code: int
    timestamp: datetime


class SandboxLatencyTest:
    """Test network latency with mock 1-share orders."""
    
    MAX_ORDER_SIZE = 1.0  # Hardcoded 1 paper-share
    
    ENDPOINTS = [
        "https://gamma-api.polymarket.com/markets",
        "https://clob.polymarket.com/health",
    ]
    
    def __init__(self):
        self._results: List[LatencyResult] = []
        self.timeout = aiohttp.ClientTimeout(total=10)
    
    async def ping_endpoint(self, url: str) -> LatencyResult:
        """Measure roundtrip latency to endpoint."""
        start_time = time.time()
        status_code = None
        
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.get(url) as response:
                    status_code = response.status
                    # Ensure we read the response to complete the roundtrip
                    await response.text()
        except asyncio.TimeoutError:
            status_code = 408  # Request Timeout
            logger.warning(f"Timeout while pinging {url}")
        except aiohttp.ClientError as e:
            status_code = 0  # Connection error
            logger.warning(f"Connection error pinging {url}: {e}")
        except Exception as e:
            status_code = 0
            logger.error(f"Unexpected error pinging {url}: {e}")
        
        end_time = time.time()
        latency_ms = (end_time - start_time) * 1000
        
        result = LatencyResult(
            endpoint=url,
            latency_ms=latency_ms,
            status_code=status_code or 0,
            timestamp=datetime.now(timezone.utc)
        )
        
        self._results.append(result)
        logger.info(f"Latency to {url}: {latency_ms:.2f}ms (status: {status_code})")
        
        return result
    
    async def run_latency_test(self) -> List[LatencyResult]:
        """Run latency test against all endpoints."""
        logger.info(f"Starting latency test against {len(self.ENDPOINTS)} endpoints")
        
        # Run all pings concurrently
        tasks = [self.ping_endpoint(endpoint) for endpoint in self.ENDPOINTS]
        results = await asyncio.gather(*tasks)
        
        logger.info(f"Completed latency test. Results: {len(results)} endpoints pinged")
        return results
    
    def generate_mock_order(self, token_id: str, side: str, price: float) -> dict:
        """Generate mock order with MAX_ORDER_SIZE limit."""
        return {
            "token_id": token_id,
            "side": side,
            "price": price,
            "size": self.MAX_ORDER_SIZE,  # Hardcoded 1 share
            "type": "GTC",
            "post_only": True,
            "_simulation": True
        }
    
    def get_stats(self) -> dict:
        """Get latency statistics."""
        if not self._results:
            return {
                "total_pings": 0,
                "successful_pings": 0,
                "failed_pings": 0,
                "min_latency_ms": None,
                "max_latency_ms": None,
                "avg_latency_ms": None,
                "endpoints": {}
            }
        
        successful_results = [r for r in self._results if r.status_code in (200, 204)]
        failed_results = [r for r in self._results if r.status_code not in (200, 204)]
        
        latencies = [r.latency_ms for r in successful_results]
        
        endpoints_stats = {}
        for endpoint in self.ENDPOINTS:
            endpoint_results = [r for r in self._results if r.endpoint == endpoint]
            endpoint_successful = [r for r in endpoint_results if r.status_code in (200, 204)]
            endpoint_latencies = [r.latency_ms for r in endpoint_successful]
            
            endpoints_stats[endpoint] = {
                "pings": len(endpoint_results),
                "successful": len(endpoint_successful),
                "failed": len(endpoint_results) - len(endpoint_successful),
                "min_latency_ms": min(endpoint_latencies) if endpoint_latencies else None,
                "max_latency_ms": max(endpoint_latencies) if endpoint_latencies else None,
                "avg_latency_ms": sum(endpoint_latencies) / len(endpoint_latencies) if endpoint_latencies else None,
            }
        
        return {
            "total_pings": len(self._results),
            "successful_pings": len(successful_results),
            "failed_pings": len(failed_results),
            "min_latency_ms": min(latencies) if latencies else None,
            "max_latency_ms": max(latencies) if latencies else None,
            "avg_latency_ms": sum(latencies) / len(latencies) if latencies else None,
            "endpoints": endpoints_stats
        }
