"""
Capital Velocity Tracker for inventory management and merge execution.

Tasks:
- Task-22: Capital Velocity Tracker - monitor mock YES/NO inventory for active condition_id
- Task-23: Autonomous Merge Execution - log simulated mergePositions when YES=NO shares
- Task-24: Mock Polygon Relayer - model gasless relayer API payload
- Task-25: Post-Merge Balance Recalculator - update Paper-USDC after merge
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional, Callable, Awaitable
from enum import Enum

logger = logging.getLogger(__name__)


class TradeType(Enum):
    """Trade types for inventory tracking."""
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class Position:
    """Represents a condition position with YES/NO shares and pricing."""
    condition_id: str
    yes_shares: float = 0.0
    no_shares: float = 0.0
    avg_yes_price: float = 0.0
    avg_no_price: float = 0.0
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    @property
    def mergeable_shares(self) -> float:
        """Number of shares that can be merged (min of YES and NO)."""
        return min(self.yes_shares, self.no_shares)
    
    @property
    def net_exposure(self) -> float:
        """Net directional exposure (positive = long YES, negative = long NO)."""
        return self.yes_shares - self.no_shares
    
    @property
    def total_value(self) -> float:
        """Total notional value of position."""
        return (self.yes_shares * self.avg_yes_price) + (self.no_shares * self.avg_no_price)
    
    def update_timestamp(self):
        """Update last modified timestamp."""
        self.last_updated = datetime.now(timezone.utc)


@dataclass
class MergeEvent:
    """Records a merge execution event."""
    condition_id: str
    merged_shares: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    relayer_payload: Optional[dict] = None
    balance_before: float = 0.0
    balance_after: float = 0.0
    status: str = "executed"  # executed, pending, failed


class CapitalVelocityTracker:
    """
    Tracks inventory (YES/NO shares) and manages autonomous merge execution.
    
    Features:
    - Task-22: Monitor positions by condition_id
    - Task-23: Auto-execute merges when YES == NO shares
    - Task-24: Generate gasless relayer payloads
    - Task-25: Recalculate balances after merges
    """
    
    def __init__(self, starting_usdc: float = 100.0):
        """
        Initialize tracker with starting capital.
        
        Args:
            starting_usdc: Starting Paper-USDC balance
        """
        self._paper_usdc = starting_usdc
        self._positions: Dict[str, Position] = {}
        self._merge_history: list[MergeEvent] = []
        self._on_merge: Optional[Callable[[MergeEvent], Awaitable[None]]] = None
        self._merge_lock = asyncio.Lock()
        logger.info(f"CapitalVelocityTracker initialized with ${starting_usdc:.2f} Paper-USDC")
    
    def record_trade(self, condition_id: str, side: str, shares: float, price: float) -> Position:
        """
        Record a trade and update inventory for the condition.
        
        Task-22 implementation: Updates position state for active condition_id
        
        Args:
            condition_id: Condition ID for the position
            side: "YES" or "NO" indicating which outcome
            shares: Number of shares traded
            price: Price per share
            
        Returns:
            Updated Position object
        """
        if shares <= 0:
            raise ValueError(f"Shares must be positive, got {shares}")
        if price < 0 or price > 1.0:
            raise ValueError(f"Price must be between 0 and 1, got {price}")
        
        if condition_id not in self._positions:
            self._positions[condition_id] = Position(condition_id=condition_id)
        
        position = self._positions[condition_id]
        side = side.upper()
        
        if side == "YES":
            # Update YES shares using FIFO-like average price
            total_yes_value = (position.yes_shares * position.avg_yes_price) + (shares * price)
            position.yes_shares += shares
            position.avg_yes_price = total_yes_value / position.yes_shares if position.yes_shares > 0 else 0
        elif side == "NO":
            # Update NO shares using FIFO-like average price
            total_no_value = (position.no_shares * position.avg_no_price) + (shares * price)
            position.no_shares += shares
            position.avg_no_price = total_no_value / position.no_shares if position.no_shares > 0 else 0
        else:
            raise ValueError(f"Side must be 'YES' or 'NO', got {side}")
        
        position.update_timestamp()
        logger.debug(
            f"Recorded {side} trade for {condition_id}: "
            f"{shares} shares @ ${price:.4f} | "
            f"Position: YES={position.yes_shares:.2f}, NO={position.no_shares:.2f}"
        )
        
        return position
    
    async def check_and_execute_merge(self, condition_id: str) -> Optional[MergeEvent]:
        """
        Check if position is mergeable (YES == NO) and execute merge if so.
        
        Task-23 implementation: Autonomous merge execution when YES == NO
        
        Args:
            condition_id: Condition to check for merge eligibility
            
        Returns:
            MergeEvent if merge executed, None otherwise
        """
        async with self._merge_lock:
            if condition_id not in self._positions:
                logger.warning(f"No position found for {condition_id}")
                return None
            
            position = self._positions[condition_id]
            
            # Check if position is mergeable (YES == NO within tolerance)
            tolerance = 0.01  # Allow 0.01 share tolerance for floating point
            if abs(position.yes_shares - position.no_shares) > tolerance:
                logger.debug(
                    f"Position {condition_id} not mergeable: "
                    f"YES={position.yes_shares:.4f}, NO={position.no_shares:.4f}"
                )
                return None
            
            if position.yes_shares < 0.01:  # Minimum mergeable amount
                logger.debug(f"Position {condition_id} too small to merge")
                return None
            
            mergeable_shares = position.mergeable_shares
            balance_before = self._paper_usdc
            
            # Generate relayer payload (Task-24)
            relayer_payload = self._generate_relayer_payload(condition_id, mergeable_shares)
            
            # Simulate merge execution
            await asyncio.sleep(0.01)  # Simulate network latency
            
            # Recalculate balance after merge (Task-25)
            self.recalculate_balance(mergeable_shares)
            balance_after = self._paper_usdc
            
            # Create merge event record
            merge_event = MergeEvent(
                condition_id=condition_id,
                merged_shares=mergeable_shares,
                relayer_payload=relayer_payload,
                balance_before=balance_before,
                balance_after=balance_after,
                status="executed"
            )
            
            # Update position state
            position.yes_shares -= mergeable_shares
            position.no_shares -= mergeable_shares
            position.update_timestamp()
            
            # Record in history
            self._merge_history.append(merge_event)
            
            logger.info(
                f"Merge executed for {condition_id}: "
                f"{mergeable_shares:.2f} shares | "
                f"Balance: ${balance_before:.2f} → ${balance_after:.2f}"
            )
            
            # Trigger callback if registered
            if self._on_merge:
                try:
                    await self._on_merge(merge_event)
                except Exception as e:
                    logger.error(f"Error in merge callback: {e}")
            
            return merge_event
    
    def _generate_relayer_payload(self, condition_id: str, shares: float) -> dict:
        """
        Generate mock gasless relayer API payload.
        
        Task-24 implementation: Model gasless relayer for Polygon
        
        Args:
            condition_id: Condition being merged
            shares: Number of shares to merge
            
        Returns:
            Relayer payload dictionary
        """
        # Convert shares to USDC decimals (6 decimals)
        amount_usdc = int(shares * 1e6)
        
        payload = {
            "method": "mergePositions",
            "version": "1.0",
            "chainId": 137,  # Polygon Mainnet
            "params": {
                "conditionId": condition_id,
                "amount": str(amount_usdc),  # USDC amount in smallest units
                "amountFormatted": f"{shares:.6f}",
                "gasless": True,
                "relayer": "mock-polygon-relayer",
                "network": "polygon"
            },
            "signature": "MOCK_SIGNATURE_FOR_SIMULATION",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "nonce": hash((condition_id, shares)) & 0xFFFFFFFF
        }
        
        logger.debug(f"Generated relayer payload: {payload}")
        return payload
    
    def recalculate_balance(self, merged_shares: float):
        """
        Update Paper-USDC after successful merge.
        
        Task-25 implementation: Balance recalculation post-merge
        
        When YES and NO shares are merged:
        - Each merged pair converts to 1 USDC (each outcome represents 1 USDC value)
        - Balance increases by merged_shares (1 USDC per merged pair)
        
        Args:
            merged_shares: Number of shares that were merged
        """
        # Each merged pair converts to 1 USDC value
        balance_increase = float(merged_shares)
        self._paper_usdc += balance_increase
        
        logger.info(
            f"Balance recalculated: +${balance_increase:.2f} from merge | "
            f"New Paper-USDC: ${self._paper_usdc:.2f}"
        )
    
    def get_position(self, condition_id: str) -> Optional[Position]:
        """
        Get position for a specific condition.
        
        Args:
            condition_id: Condition to retrieve
            
        Returns:
            Position object or None if not found
        """
        return self._positions.get(condition_id)
    
    def get_all_positions(self) -> Dict[str, Position]:
        """Get all active positions."""
        return dict(self._positions)
    
    def get_mergeable_conditions(self) -> Dict[str, float]:
        """
        Get all conditions that are currently mergeable (YES == NO shares).
        
        Returns:
            Dictionary of {condition_id: mergeable_shares}
        """
        mergeable = {}
        for condition_id, position in self._positions.items():
            if abs(position.yes_shares - position.no_shares) <= 0.01 and position.yes_shares >= 0.01:
                mergeable[condition_id] = position.mergeable_shares
        return mergeable
    
    def get_portfolio_summary(self) -> dict:
        """
        Get comprehensive portfolio summary.
        
        Returns:
            Dictionary with portfolio metrics
        """
        total_yes_value = sum(
            pos.yes_shares * pos.avg_yes_price 
            for pos in self._positions.values()
        )
        total_no_value = sum(
            pos.no_shares * pos.avg_no_price 
            for pos in self._positions.values()
        )
        total_gross_exposure = sum(
            (pos.yes_shares + pos.no_shares) 
            for pos in self._positions.values()
        )
        net_exposure = sum(
            pos.net_exposure 
            for pos in self._positions.values()
        )
        
        summary = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "paper_usdc": self._paper_usdc,
            "positions": {
                "active_count": len(self._positions),
                "mergeable_count": len(self.get_mergeable_conditions()),
            },
            "exposure": {
                "yes_value": total_yes_value,
                "no_value": total_no_value,
                "gross_exposure": total_gross_exposure,
                "net_exposure": net_exposure,
            },
            "merge_history": {
                "total_merges": len(self._merge_history),
                "total_shares_merged": sum(
                    event.merged_shares 
                    for event in self._merge_history
                ),
            },
            "position_details": {
                condition_id: {
                    "yes_shares": pos.yes_shares,
                    "no_shares": pos.no_shares,
                    "avg_yes_price": pos.avg_yes_price,
                    "avg_no_price": pos.avg_no_price,
                    "net_exposure": pos.net_exposure,
                    "mergeable_shares": pos.mergeable_shares,
                    "total_value": pos.total_value,
                }
                for condition_id, pos in self._positions.items()
            }
        }
        
        return summary
    
    def get_merge_history(self) -> list[MergeEvent]:
        """Get all recorded merge events."""
        return list(self._merge_history)
    
    def set_merge_callback(self, callback: Callable[[MergeEvent], Awaitable[None]]):
        """
        Register callback to be called when merge executes.
        
        Args:
            callback: Async function that receives MergeEvent
        """
        self._on_merge = callback
        logger.debug("Merge callback registered")
    
    @property
    def paper_usdc(self) -> float:
        """Get current Paper-USDC balance."""
        return self._paper_usdc
    
    def __repr__(self) -> str:
        """String representation of tracker state."""
        mergeable = len(self.get_mergeable_conditions())
        return (
            f"CapitalVelocityTracker("
            f"usdc=${self._paper_usdc:.2f}, "
            f"positions={len(self._positions)}, "
            f"mergeable={mergeable}, "
            f"merges={len(self._merge_history)}"
            f")"
        )


if __name__ == "__main__":
    # Setup logging for demo
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    async def demo():
        """Demo the CapitalVelocityTracker."""
        tracker = CapitalVelocityTracker(starting_usdc=100.0)
        
        # Task-22: Record trades (monitoring inventory)
        print("\n=== Task-22: Record Trades ===")
        tracker.record_trade("COND001", "YES", 10.0, 0.5)
        tracker.record_trade("COND001", "NO", 10.0, 0.5)
        print(f"Position: {tracker.get_position('COND001')}")
        
        # Task-23: Execute merge
        print("\n=== Task-23: Execute Merge ===")
        merge_event = await tracker.check_and_execute_merge("COND001")
        if merge_event:
            print(f"Merge Event: {merge_event}")
        
        # Task-25: Check balance
        print("\n=== Task-25: Balance After Merge ===")
        print(f"Paper-USDC: ${tracker.paper_usdc:.2f}")
        
        # Portfolio summary
        print("\n=== Portfolio Summary ===")
        import json
        summary = tracker.get_portfolio_summary()
        print(json.dumps(summary, indent=2, default=str))
    
    asyncio.run(demo())
