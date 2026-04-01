"""
ERC-1155 Token Merger for educational conditional token analysis.
Models mergePositions to recycle YES+NO pairs back to USDC.

EDUCATIONAL PURPOSE ONLY - This module simulates blockchain interactions
without executing real transactions. It demonstrates how conditional tokens
work and how merge operations recover USDC from paired positions.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, List
from decimal import Decimal
import json

logger = logging.getLogger(__name__)

# ERC-1155 ConditionalTokens ABI (minimal for merge operations)
# Educational reference: https://docs.gnosis.io/
CTF_ABI = [
    {
        "name": "mergePositions",
        "type": "function",
        "inputs": [
            {"name": "collateralToken", "type": "address"},
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId", "type": "bytes32"},
            {"name": "partition", "type": "uint256[]"},
            {"name": "amount", "type": "uint256"}
        ],
        "outputs": [],
        "stateMutability": "nonpayable"
    },
    {
        "name": "getPositionId",
        "type": "function",
        "inputs": [
            {"name": "collateralToken", "type": "address"},
            {"name": "collectionId", "type": "bytes32"}
        ],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "pure"
    },
    {
        "name": "balanceOf",
        "type": "function",
        "inputs": [
            {"name": "account", "type": "address"},
            {"name": "id", "type": "uint256"}
        ],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view"
    }
]


@dataclass
class MergeSimulation:
    """Records a simulated merge transaction."""
    condition_id: str
    shares_merged: float
    usdc_recovered: float
    simulated_gas: float  # Simulated gas cost in gwei
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    tx_hash: str = ""  # Simulated transaction hash
    status: str = "simulated"
    relayer_payload: Optional[Dict] = None

    def to_dict(self) -> Dict:
        """Convert to dictionary for logging."""
        return {
            "condition_id": self.condition_id,
            "shares_merged": self.shares_merged,
            "usdc_recovered": self.usdc_recovered,
            "simulated_gas": self.simulated_gas,
            "timestamp": self.timestamp.isoformat(),
            "tx_hash": self.tx_hash,
            "status": self.status,
            "relayer_payload": self.relayer_payload
        }


@dataclass
class InventoryState:
    """Tracks share inventory for a condition."""
    yes_shares: float = 0.0
    no_shares: float = 0.0
    pending_merges: int = 0
    total_merged: float = 0.0
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def can_merge(self) -> bool:
        """Check if YES and NO shares are equal (merge opportunity)."""
        return self.yes_shares > 0 and abs(self.yes_shares - self.no_shares) < 1e-10

    def mergeable_amount(self) -> float:
        """Get the amount that can be merged (minimum of YES/NO)."""
        return min(self.yes_shares, self.no_shares)


class TokenMerger:
    """
    Simulates ERC-1155 conditional token merging.

    This educational module demonstrates:
    - Inventory tracking for YES/NO positions
    - Merge opportunity detection
    - Simulated transaction generation
    - Gasless relayer API payload construction
    - USDC recovery simulation
    """

    # Gnosis Conditional Tokens contract on Polygon
    CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
    # Paper USDC token for simulation
    USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

    def __init__(self, paper_usdc_balance: float = 100.0):
        """
        Initialize TokenMerger with simulated USDC balance.

        Args:
            paper_usdc_balance: Starting simulated USDC (default 100.0)
        """
        self._inventory: Dict[str, InventoryState] = {}
        self._paper_usdc = Decimal(str(paper_usdc_balance))
        self._merge_history: List[MergeSimulation] = []
        self._transaction_nonce = 0
        
        logger.info(
            f"TokenMerger initialized with {paper_usdc_balance} Paper-USDC "
            f"[Educational Simulation Mode]"
        )

    def update_inventory(
        self,
        condition_id: str,
        yes_delta: float,
        no_delta: float
    ) -> InventoryState:
        """
        Update share inventory after a trade or position change.

        Args:
            condition_id: Bytes32 condition identifier
            yes_delta: Change in YES shares (positive/negative)
            no_delta: Change in NO shares (positive/negative)

        Returns:
            Updated InventoryState
        """
        if condition_id not in self._inventory:
            self._inventory[condition_id] = InventoryState()

        state = self._inventory[condition_id]
        old_yes = state.yes_shares
        old_no = state.no_shares

        # Update shares
        state.yes_shares = max(0, state.yes_shares + yes_delta)
        state.no_shares = max(0, state.no_shares + no_delta)
        state.last_updated = datetime.now(timezone.utc)

        logger.debug(
            f"[{condition_id[:8]}] Inventory updated: "
            f"YES {old_yes:.2f}→{state.yes_shares:.2f} | "
            f"NO {old_no:.2f}→{state.no_shares:.2f}"
        )

        return state

    def check_merge_opportunity(self, condition_id: str) -> Optional[MergeSimulation]:
        """
        Check if shares can be merged (YES == NO).

        Educational context: When a user holds equal YES and NO shares for
        a condition, they can merge them back into the collateral token,
        recovering their initial USDC investment.

        Args:
            condition_id: Bytes32 condition identifier

        Returns:
            MergeSimulation if merge is possible, None otherwise
        """
        if condition_id not in self._inventory:
            return None

        state = self._inventory[condition_id]

        if not state.can_merge():
            return None

        mergeable = state.mergeable_amount()
        usdc_recovered = float(mergeable)

        # Simulate merge with minimal gas cost (gasless via relayer)
        simulated_gas = 0.001  # Simulated gas in gwei (near-zero for gasless)

        merge_sim = MergeSimulation(
            condition_id=condition_id,
            shares_merged=mergeable,
            usdc_recovered=usdc_recovered,
            simulated_gas=simulated_gas,
            status="ready"
        )

        logger.info(
            f"[{condition_id[:8]}] ✓ Merge opportunity detected: "
            f"{mergeable:.2f} pairs → {usdc_recovered:.2f} USDC"
        )

        return merge_sim

    async def simulate_merge(
        self,
        condition_id: str,
        verbose: bool = True
    ) -> MergeSimulation:
        """
        Log simulated mergePositions transaction.

        This models the mergePositions call that burns YES+NO pairs and
        recovers the underlying collateral (USDC).

        Args:
            condition_id: Bytes32 condition identifier
            verbose: Print transaction details to terminal

        Returns:
            MergeSimulation with transaction details

        Raises:
            ValueError: If condition not found or merge not possible
        """
        if condition_id not in self._inventory:
            raise ValueError(f"Condition {condition_id} not found in inventory")

        state = self._inventory[condition_id]
        mergeable = state.mergeable_amount()

        if mergeable == 0:
            raise ValueError(
                f"No mergeable shares for {condition_id}. "
                f"YES: {state.yes_shares}, NO: {state.no_shares}"
            )

        # Generate simulated transaction hash
        self._transaction_nonce += 1
        tx_hash = f"0x{'dead' * 16}{self._transaction_nonce:04x}"

        # Calculate USDC recovery
        usdc_recovered = Decimal(str(mergeable))
        simulated_gas = Decimal("0.001")

        # Generate relayer payload
        relayer_payload = self.generate_relayer_payload(condition_id, mergeable)

        # Create merge simulation record
        merge_sim = MergeSimulation(
            condition_id=condition_id,
            shares_merged=float(mergeable),
            usdc_recovered=float(usdc_recovered),
            simulated_gas=float(simulated_gas),
            tx_hash=tx_hash,
            status="executed",
            relayer_payload=relayer_payload
        )

        # Update inventory
        state.yes_shares -= mergeable
        state.no_shares -= mergeable
        state.pending_merges += 1
        state.total_merged += mergeable

        # Update Paper-USDC balance
        self._paper_usdc += usdc_recovered

        # Record in history
        self._merge_history.append(merge_sim)

        # Log transaction to terminal
        if verbose:
            await self._log_transaction(merge_sim)

        logger.info(
            f"[{condition_id[:8]}] Merge executed: "
            f"{mergeable:.2f} YES+NO pairs → {usdc_recovered:.2f} USDC | "
            f"TX: {tx_hash}"
        )

        return merge_sim

    async def _log_transaction(self, merge_sim: MergeSimulation) -> None:
        """
        Log simulated merge transaction to terminal with formatting.

        Args:
            merge_sim: MergeSimulation to log
        """
        timestamp_str = merge_sim.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")

        log_output = f"""
╔══════════════════════════════════════════════════════════════╗
║          SIMULATED MERGE TRANSACTION (ERC-1155)              ║
╠══════════════════════════════════════════════════════════════╣
║ Condition ID:        {merge_sim.condition_id[:24]}...
║ Shares Merged:       {merge_sim.shares_merged:.4f} YES + {merge_sim.shares_merged:.4f} NO
║ USDC Recovered:      {merge_sim.usdc_recovered:.4f} Paper-USDC
║ Simulated Gas:       {merge_sim.simulated_gas:.6f} gwei (gasless relayer)
║ Transaction Hash:    {merge_sim.tx_hash}
║ Status:              {merge_sim.status.upper()}
║ Timestamp:           {timestamp_str}
║ CTF Contract:        {self.CTF_ADDRESS}
╠══════════════════════════════════════════════════════════════╣
║ Function: mergePositions(                                    ║
║   collateralToken: {self.USDC_ADDRESS}
║   parentCollectionId: 0x00...00,
║   conditionId: {merge_sim.condition_id[:16]}...,
║   partition: [1, 2],
║   amount: {int(merge_sim.shares_merged * 1e18)}
║ )
╚══════════════════════════════════════════════════════════════╝
"""

        print(log_output)

        # Also log relayer payload if available
        if merge_sim.relayer_payload:
            payload_str = json.dumps(merge_sim.relayer_payload, indent=2)
            print("\n📡 Gasless Relayer Payload:")
            print(payload_str)

    def generate_relayer_payload(
        self,
        condition_id: str,
        shares: float
    ) -> Dict:
        """
        Generate mock gasless relayer API payload for zero-cost recycling.

        Educational context: Gasless relayers abstract gas costs by
        accepting meta-transactions. The payload includes encoded function
        calls, signatures, and metadata for the relayer to execute.

        Args:
            condition_id: Bytes32 condition identifier
            shares: Amount of shares to merge

        Returns:
            Dictionary representing API payload structure
        """
        shares_wei = int(shares * 1e18)  # Convert to wei

        payload = {
            "version": "2.0",
            "chainId": 137,  # Polygon
            "domain": {
                "name": "ConditionalTokens",
                "version": "1.0",
                "verifyingContract": self.CTF_ADDRESS,
                "salt": "0x" + "0" * 64
            },
            "primaryType": "ForwardRequest",
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"},
                    {"name": "salt", "type": "bytes32"}
                ],
                "ForwardRequest": [
                    {"name": "from", "type": "address"},
                    {"name": "to", "type": "address"},
                    {"name": "value", "type": "uint256"},
                    {"name": "gas", "type": "uint256"},
                    {"name": "nonce", "type": "uint256"},
                    {"name": "data", "type": "bytes"},
                    {"name": "validUntil", "type": "uint48"}
                ]
            },
            "message": {
                "from": "0x0000000000000000000000000000000000000000",  # User wallet
                "to": self.CTF_ADDRESS,
                "value": "0",
                "gas": "150000",  # Estimated merge gas
                "nonce": self._transaction_nonce,
                "data": f"0x{self._encode_merge_call(condition_id, shares_wei)}",
                "validUntil": 2524608000  # Year 2050
            },
            "meta": {
                "type": "MERGE_POSITIONS",
                "collateralToken": self.USDC_ADDRESS,
                "conditionId": condition_id,
                "amount": str(shares_wei),
                "gaslessFee": "0",  # No fee in educational simulation
                "relayerAddress": "0x1111111111111111111111111111111111111111"
            }
        }

        return payload

    def _encode_merge_call(self, condition_id: str, shares_wei: int) -> str:
        """
        Encode mergePositions function call (simplified).

        In production, this would use proper ABI encoding. For educational
        purposes, we create a mock encoding.

        Args:
            condition_id: Bytes32 condition identifier
            shares_wei: Amount in wei

        Returns:
            Hex string of encoded function call
        """
        # Function selector for mergePositions (first 4 bytes of keccak256)
        # In reality: keccak256("mergePositions(address,bytes32,bytes32,uint256[],uint256)")
        func_selector = "3b2b4ac5"  # Mock selector

        # Simplified encoding (real implementation would use proper ABI encoding)
        params = (
            func_selector +
            self.USDC_ADDRESS[2:].zfill(64) +  # collateralToken
            condition_id[2:].zfill(64) +  # conditionId
            f"{shares_wei:064x}"  # amount
        )

        return params

    def get_inventory(self, condition_id: str) -> Optional[InventoryState]:
        """Get current inventory state for a condition."""
        return self._inventory.get(condition_id)

    def get_paper_usdc_balance(self) -> float:
        """Get simulated Paper-USDC balance."""
        return float(self._paper_usdc)

    def get_merge_history(self) -> List[MergeSimulation]:
        """Get list of all simulated merges."""
        return self._merge_history.copy()

    def print_summary(self) -> None:
        """Print summary of current state to terminal."""
        print("\n" + "=" * 70)
        print("📊 TOKEN MERGER SUMMARY (Educational Simulation)")
        print("=" * 70)
        print(f"Paper-USDC Balance:      {self._paper_usdc:.4f}")
        print(f"Tracked Conditions:      {len(self._inventory)}")
        print(f"Total Merges Executed:   {len(self._merge_history)}")
        print(f"CTF Contract Address:    {self.CTF_ADDRESS}")
        print(f"USDC Token Address:      {self.USDC_ADDRESS}")
        print()

        if self._inventory:
            print("Condition Inventory:")
            print("-" * 70)
            for cond_id, state in self._inventory.items():
                print(
                    f"  {cond_id[:16]}... | "
                    f"YES: {state.yes_shares:8.2f} | "
                    f"NO: {state.no_shares:8.2f} | "
                    f"Merged: {state.total_merged:8.2f}"
                )
            print()

        if self._merge_history:
            print("Recent Merges:")
            print("-" * 70)
            for i, merge in enumerate(self._merge_history[-5:], 1):
                print(
                    f"  {i}. Condition {merge.condition_id[:8]}... | "
                    f"{merge.shares_merged:.2f} → {merge.usdc_recovered:.2f} USDC | "
                    f"TX: {merge.tx_hash}"
                )

        print("=" * 70 + "\n")


# Example usage and educational demonstration
async def demonstrate_token_merger():
    """Demonstrate TokenMerger functionality with example scenario."""
    print("\n🎓 ERC-1155 Conditional Token Merger - Educational Demonstration\n")

    # Initialize merger
    merger = TokenMerger(paper_usdc_balance=100.0)

    # Simulate condition IDs (normally from Gnosis prediction markets)
    btc_condition = "0xbeefc0dedeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    eth_condition = "0xcafef00dd0c0ffeebabedeadbeefc0ded0c0ffeec0ffeec0ffeec0ffeec0ffee"

    print("Step 1: User buys conditional tokens")
    print("-" * 50)
    merger.update_inventory(btc_condition, yes_delta=5.0, no_delta=0.0)
    print(f"  • Bought 5 YES shares for BTC condition")

    merger.update_inventory(btc_condition, yes_delta=0.0, no_delta=5.0)
    print(f"  • Bought 5 NO shares for BTC condition\n")

    # Check for merge opportunity
    print("Step 2: Check merge opportunity")
    print("-" * 50)
    merge_opp = merger.check_merge_opportunity(btc_condition)
    if merge_opp:
        print(f"  ✓ Merge opportunity found!\n")
    else:
        print(f"  ✗ No merge opportunity\n")

    # Execute simulated merge
    print("Step 3: Execute simulated merge")
    print("-" * 50)
    merge_result = await merger.simulate_merge(btc_condition, verbose=True)
    print(f"  Paper-USDC Balance: {merger.get_paper_usdc_balance():.4f}\n")

    # Print summary
    merger.print_summary()


# Entry point
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Run demonstration
    asyncio.run(demonstrate_token_merger())
