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


def simulate_latency_arbitrage(
    entry_price: float = 0.98,
    expiry_price: float = 1.00,
    network_latency_ms: float = 50.0,
    n_simulations: int = 1000,
    market_volatility: float = 0.005,
) -> dict:
    """
    Phase 2 - Task 94: Mathematically model the Gabagool $0.98 -> $1.00
    expiry arbitrage with assumed network latency.
    
    Simulates the probability of successful arbitrage execution given
    network latency and market volatility.
    
    Args:
        entry_price: Entry price for the arbitrage (e.g., 0.98)
        expiry_price: Expected expiry price (e.g., 1.00)
        network_latency_ms: Network round-trip latency in milliseconds
        n_simulations: Number of Monte Carlo simulations
        market_volatility: Per-second price volatility
    
    Returns:
        Dictionary with success rate, expected PnL, and statistics
    """
    import random
    import math
    
    results = {
        'successful_trades': 0,
        'failed_trades': 0,
        'pnl_values': [],
        'slippage_values': [],
        'execution_times': [],
    }
    
    # Convert latency to seconds
    latency_s = network_latency_ms / 1000.0
    
    # Simulate arbitrage attempts
    for _ in range(n_simulations):
        # Simulate price movement during latency window
        # Price can move due to volatility during order execution
        price_drift = random.gauss(0, market_volatility * math.sqrt(latency_s))
        
        # Effective execution price
        execution_price = entry_price * (1 + price_drift)
        
        # Add random execution time jitter (±20% of latency)
        actual_latency = latency_s * (1 + random.uniform(-0.2, 0.2))
        results['execution_times'].append(actual_latency * 1000)
        
        # Determine if trade was successful
        # Trade fails if:
        # 1. Price moved too much (slippage > 1%)
        # 2. Order arrived too late (simulated with probability)
        
        slippage = abs(execution_price - entry_price) / entry_price
        results['slippage_values'].append(slippage * 10000)  # In bps
        
        # Success probability decreases with latency
        # Assuming 1-second window for arbitrage
        time_success_prob = max(0, 1 - (actual_latency / 1.0))
        
        # Price success: slippage under 1%
        price_success = slippage < 0.01
        
        # Combined success
        is_successful = price_success and random.random() < time_success_prob
        
        if is_successful:
            results['successful_trades'] += 1
            # PnL = (expiry_price - execution_price) * shares
            pnl = (expiry_price - execution_price)
            results['pnl_values'].append(pnl)
        else:
            results['failed_trades'] += 1
            # Failed trade: lose the spread or position goes against us
            pnl = -abs(entry_price - execution_price)
            results['pnl_values'].append(pnl)
    
    # Calculate statistics
    success_rate = results['successful_trades'] / n_simulations
    
    pnl_values = results['pnl_values']
    avg_pnl = sum(pnl_values) / len(pnl_values)
    
    # Sort for percentiles
    sorted_pnl = sorted(pnl_values)
    p5_pnl = sorted_pnl[int(n_simulations * 0.05)]
    p95_pnl = sorted_pnl[int(n_simulations * 0.95)]
    
    slippage_values = results['slippage_values']
    avg_slippage = sum(slippage_values) / len(slippage_values)
    
    exec_times = results['execution_times']
    avg_exec_time = sum(exec_times) / len(exec_times)
    
    return {
        'entry_price': entry_price,
        'expiry_price': expiry_price,
        'network_latency_ms': network_latency_ms,
        'n_simulations': n_simulations,
        'success_rate': success_rate * 100,  # Percentage
        'expected_pnl_per_trade': avg_pnl,
        'expected_pnl_per_100_trades': avg_pnl * 100,
        'pnl_5th_percentile': p5_pnl,
        'pnl_95th_percentile': p95_pnl,
        'avg_slippage_bps': avg_slippage,
        'avg_execution_time_ms': avg_exec_time,
        'max_execution_time_ms': max(exec_times),
        'profitable_threshold_latency_ms': 100,  # Estimated
        'recommendation': 'VIABLE' if success_rate > 0.8 and avg_pnl > 0 else 'RISKY',
    }
