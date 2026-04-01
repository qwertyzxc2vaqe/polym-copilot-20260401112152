"""
Chainlink Oracle for on-chain BTC/USD and ETH/USD price feeds.
Educational latency comparison with Binance WebSocket.

EDUCATIONAL USE ONLY - No real funds or mainnet interaction.
Polygon Amoy Testnet (80002) for learning purposes.
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, Tuple
from web3 import Web3
from web3.contract import Contract
from eth_typing import Address

logger = logging.getLogger(__name__)

# Chainlink AggregatorV3Interface ABI (minimal for price feed reading)
AGGREGATOR_ABI = [
    {
        "inputs": [],
        "name": "latestRoundData",
        "outputs": [
            {"name": "roundId", "type": "uint80"},
            {"name": "answer", "type": "int256"},
            {"name": "startedAt", "type": "uint256"},
            {"name": "updatedAt", "type": "uint256"},
            {"name": "answeredInRound", "type": "uint80"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "description",
        "outputs": [{"name": "", "type": "string"}],
        "stateMutability": "view",
        "type": "function",
    },
]


@dataclass
class ChainlinkPrice:
    """Represents a Chainlink price feed reading with latency metrics."""

    symbol: str
    price: float
    round_id: int
    timestamp: datetime  # When price was updated on-chain
    latency_ms: float  # Time to fetch from RPC
    decimals: int = 8

    def __repr__(self) -> str:
        return (
            f"ChainlinkPrice({self.symbol}={self.price:.2f}, "
            f"latency={self.latency_ms:.1f}ms, "
            f"updated={self.timestamp.isoformat()})"
        )


class ChainlinkOracle:
    """
    Fetches on-chain Chainlink price feeds for latency analysis.
    Supports Polygon Amoy Testnet for educational purposes.
    """

    # Polygon Amoy Testnet (80002) Chainlink Price Feed Addresses
    # These addresses are for testnet/mock purposes
    TESTNET_FEEDS: Dict[str, str] = {
        # Using known testnet mock addresses
        # BTC/USD feed (if available on Amoy)
        "BTC/USD": "0x1b44F3514812d835EB1BFaf04D5326c7C0827B4e",  # Mock/test address
        # ETH/USD feed (if available on Amoy)
        "ETH/USD": "0x48756303e01e8314ffCC8c126D22e901b8E49b23",  # Mock/test address
    }

    # Public RPC endpoints for Polygon Amoy Testnet
    TESTNET_RPCS = [
        "https://rpc-amoy.polygon.technology",
        "https://polygon-amoy.g.alchemy.com/v2/demo",  # Public demo key
    ]

    def __init__(
        self,
        rpc_url: Optional[str] = None,
        chain_id: int = 80002,
        feed_addresses: Optional[Dict[str, str]] = None,
    ):
        """
        Initialize Chainlink Oracle client.

        Args:
            rpc_url: Custom RPC endpoint (defaults to Polygon Amoy public RPC)
            chain_id: Chain ID (80002 for Polygon Amoy)
            feed_addresses: Custom feed addresses (defaults to testnet feeds)
        """
        self.chain_id = chain_id
        self.rpc_url = rpc_url or self.TESTNET_RPCS[0]
        self.feed_addresses = feed_addresses or self.TESTNET_FEEDS

        try:
            self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))
            if not self.w3.is_connected():
                logger.warning(f"Failed to connect to RPC: {self.rpc_url}")
            else:
                logger.info(f"Connected to {self.rpc_url} (Chain: {chain_id})")
        except Exception as e:
            logger.error(f"Error initializing Web3: {e}")
            self.w3 = None

        # Cache for contract instances
        self._contracts: Dict[str, Contract] = {}

    def _get_contract(self, symbol: str) -> Optional[Contract]:
        """Get Web3 contract instance for price feed."""
        if symbol in self._contracts:
            return self._contracts[symbol]

        if not self.w3 or not self.w3.is_connected():
            logger.error("Web3 not connected")
            return None

        if symbol not in self.feed_addresses:
            logger.error(f"Unknown symbol: {symbol}")
            return None

        try:
            address = Web3.to_checksum_address(self.feed_addresses[symbol])
            contract = self.w3.eth.contract(address=address, abi=AGGREGATOR_ABI)
            self._contracts[symbol] = contract
            return contract
        except Exception as e:
            logger.error(f"Error creating contract for {symbol}: {e}")
            return None

    async def get_price(self, symbol: str) -> Optional[ChainlinkPrice]:
        """
        Fetch latest price from Chainlink oracle.

        Args:
            symbol: Price feed symbol (e.g., "BTC/USD", "ETH/USD")

        Returns:
            ChainlinkPrice object with price and latency metrics
        """
        if not self.w3 or not self.w3.is_connected():
            logger.error("Web3 not connected")
            return None

        contract = self._get_contract(symbol)
        if not contract:
            return None

        try:
            # Measure RPC fetch latency
            start_time = time.time()

            # Fetch latest round data
            round_data = await asyncio.to_thread(contract.functions.latestRoundData().call)
            round_id, answer, started_at, updated_at, answered_in_round = round_data

            # Fetch decimals
            decimals = await asyncio.to_thread(
                contract.functions.decimals().call
            )

            latency_ms = (time.time() - start_time) * 1000

            # Convert raw answer to price
            price = float(answer) / (10 ** decimals)

            # Create timestamp from on-chain update time
            timestamp = datetime.fromtimestamp(updated_at, tz=timezone.utc)

            logger.info(
                f"{symbol}: {price:.2f} (latency: {latency_ms:.1f}ms, "
                f"round: {round_id}, updated: {updated_at})"
            )

            return ChainlinkPrice(
                symbol=symbol,
                price=price,
                round_id=round_id,
                timestamp=timestamp,
                latency_ms=latency_ms,
                decimals=decimals,
            )

        except Exception as e:
            logger.error(f"Error fetching {symbol}: {e}")
            return None

    async def get_prices(self, symbols: list[str]) -> Dict[str, Optional[ChainlinkPrice]]:
        """
        Fetch multiple prices concurrently.

        Args:
            symbols: List of symbols (e.g., ["BTC/USD", "ETH/USD"])

        Returns:
            Dictionary mapping symbol to ChainlinkPrice
        """
        tasks = [self.get_price(symbol) for symbol in symbols]
        results = await asyncio.gather(*tasks)
        return {symbol: price for symbol, price in zip(symbols, results)}

    async def compare_latency(
        self, binance_timestamp: datetime
    ) -> Dict[str, float]:
        """
        Compare Chainlink fetch latency vs Binance WebSocket timestamp.

        Args:
            binance_timestamp: Timestamp from Binance WebSocket price update

        Returns:
            Dictionary with latency comparison metrics
        """
        # Fetch Chainlink prices
        prices = await self.get_prices(["BTC/USD", "ETH/USD"])

        # Calculate latency differences
        latency_diff = {}
        for symbol, price in prices.items():
            if price is None:
                continue

            # Time difference between Binance and Chainlink price updates (ms)
            time_diff_ms = (price.timestamp - binance_timestamp).total_seconds() * 1000

            latency_diff[symbol] = {
                "chainlink_latency_ms": price.latency_ms,
                "timestamp_diff_ms": time_diff_ms,
                "chainlink_price": price.price,
            }

        return latency_diff

    async def monitor_feed(
        self,
        symbol: str,
        interval_seconds: float = 10.0,
        max_iterations: Optional[int] = None,
    ) -> list[ChainlinkPrice]:
        """
        Monitor price feed for changes over time.

        Args:
            symbol: Price feed symbol
            interval_seconds: Check interval
            max_iterations: Max checks (None for infinite)

        Returns:
            List of ChainlinkPrice readings
        """
        readings: list[ChainlinkPrice] = []
        iteration = 0

        try:
            while max_iterations is None or iteration < max_iterations:
                price = await self.get_price(symbol)
                if price:
                    readings.append(price)

                # Detect price changes
                if len(readings) > 1:
                    prev_price = readings[-2].price
                    curr_price = readings[-1].price
                    if prev_price != curr_price:
                        change_pct = ((curr_price - prev_price) / prev_price) * 100
                        logger.info(
                            f"{symbol} changed: {prev_price:.2f} → {curr_price:.2f} "
                            f"({change_pct:+.2f}%)"
                        )

                iteration += 1
                if max_iterations is None or iteration < max_iterations:
                    await asyncio.sleep(interval_seconds)

        except KeyboardInterrupt:
            logger.info("Monitoring stopped by user")

        return readings

    def get_health_status(self) -> Dict[str, any]:
        """Get oracle health and connectivity status."""
        status = {
            "connected": self.w3 is not None and self.w3.is_connected(),
            "chain_id": self.chain_id,
            "rpc_url": self.rpc_url,
            "feeds_configured": len(self.feed_addresses),
        }

        if status["connected"]:
            try:
                block_number = self.w3.eth.block_number
                status["latest_block"] = block_number
                logger.info(f"Oracle health: block {block_number}")
            except Exception as e:
                logger.error(f"Error fetching block number: {e}")
                status["error"] = str(e)

        return status


# Example usage and educational demonstration
async def demo_comparison():
    """
    Demo: Compare Chainlink latency vs mock Binance timestamp.
    Educational example only.
    """
    logger.basicConfig(level=logging.INFO)

    oracle = ChainlinkOracle()

    # Check health
    health = oracle.get_health_status()
    print(f"\n=== Oracle Health ===")
    print(f"Connected: {health.get('connected')}")
    print(f"Chain ID: {health.get('chain_id')}")
    print(f"Latest Block: {health.get('latest_block')}")

    print(f"\n=== Fetching Chainlink Prices ===")
    # Fetch prices
    prices = await oracle.get_prices(["BTC/USD", "ETH/USD"])

    for symbol, price in prices.items():
        if price:
            print(f"{symbol}: ${price.price:.2f} (latency: {price.latency_ms:.1f}ms)")

    # Simulate latency comparison
    print(f"\n=== Latency Comparison (Educational) ===")
    binance_time = datetime.now(timezone.utc)
    latency_comparison = await oracle.compare_latency(binance_time)

    for symbol, metrics in latency_comparison.items():
        print(
            f"{symbol}:"
            f"\n  Chainlink latency: {metrics['chainlink_latency_ms']:.1f}ms"
            f"\n  Timestamp diff: {metrics['timestamp_diff_ms']:.0f}ms"
        )


if __name__ == "__main__":
    print("=" * 60)
    print("Chainlink Oracle - Educational Latency Analysis")
    print("=" * 60)
    asyncio.run(demo_comparison())
