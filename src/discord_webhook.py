"""
Discord Webhook Module.

Phase 2 - Task 84: Post daily PnL snapshots and alerts to Discord.

Educational purpose only - paper trading simulation.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional
import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class DiscordEmbed:
    """Discord embed structure."""
    title: str
    description: str = ""
    color: int = 0x00ff00  # Green
    fields: List[Dict] = None
    footer: str = ""
    timestamp: str = None
    
    def to_dict(self) -> dict:
        embed = {
            'title': self.title,
            'description': self.description,
            'color': self.color,
        }
        
        if self.fields:
            embed['fields'] = self.fields
        
        if self.footer:
            embed['footer'] = {'text': self.footer}
        
        if self.timestamp:
            embed['timestamp'] = self.timestamp
        
        return embed


class DiscordWebhook:
    """
    Discord webhook client for trading alerts.
    
    Features:
    - Daily PnL snapshots
    - Trade alerts
    - Error notifications
    - Risk warnings
    - Circuit breaker alerts
    """
    
    # Embed colors
    COLOR_SUCCESS = 0x00ff00  # Green
    COLOR_WARNING = 0xffff00  # Yellow
    COLOR_ERROR = 0xff0000    # Red
    COLOR_INFO = 0x0099ff     # Blue
    
    def __init__(
        self,
        webhook_url: str,
        username: str = "Polym Trading Bot",
        avatar_url: str = None,
    ):
        """
        Initialize Discord webhook.
        
        Args:
            webhook_url: Discord webhook URL
            username: Bot username to display
            avatar_url: Bot avatar URL
        """
        self.webhook_url = webhook_url
        self.username = username
        self.avatar_url = avatar_url
        
        self._session: Optional[aiohttp.ClientSession] = None
        self._rate_limit_remaining = 5
        self._rate_limit_reset: float = 0
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session
    
    async def close(self) -> None:
        """Close HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def send_message(
        self,
        content: str = None,
        embeds: List[DiscordEmbed] = None,
    ) -> bool:
        """
        Send message to Discord.
        
        Args:
            content: Plain text content
            embeds: List of embeds
        
        Returns:
            True if successful
        """
        if not self.webhook_url:
            logger.warning("No webhook URL configured")
            return False
        
        payload = {
            'username': self.username,
        }
        
        if self.avatar_url:
            payload['avatar_url'] = self.avatar_url
        
        if content:
            payload['content'] = content
        
        if embeds:
            payload['embeds'] = [e.to_dict() for e in embeds]
        
        try:
            session = await self._get_session()
            
            # Rate limit check
            if self._rate_limit_remaining <= 0:
                wait_time = self._rate_limit_reset - datetime.now().timestamp()
                if wait_time > 0:
                    await asyncio.sleep(wait_time)
            
            async with session.post(
                self.webhook_url,
                json=payload,
            ) as response:
                # Update rate limit info
                self._rate_limit_remaining = int(
                    response.headers.get('X-RateLimit-Remaining', 5)
                )
                self._rate_limit_reset = float(
                    response.headers.get('X-RateLimit-Reset', 0)
                )
                
                if response.status == 204:
                    return True
                elif response.status == 429:
                    logger.warning("Discord rate limited")
                    return False
                else:
                    text = await response.text()
                    logger.error(f"Discord error {response.status}: {text}")
                    return False
        
        except Exception as e:
            logger.error(f"Failed to send Discord message: {e}")
            return False
    
    async def send_daily_pnl(
        self,
        pnl: float,
        trades: int,
        win_rate: float,
        sharpe: float,
        max_drawdown: float,
    ) -> bool:
        """
        Send daily PnL snapshot.
        
        Args:
            pnl: Total PnL for the day
            trades: Number of trades
            win_rate: Win rate percentage
            sharpe: Sharpe ratio
            max_drawdown: Maximum drawdown
        
        Returns:
            True if successful
        """
        color = self.COLOR_SUCCESS if pnl >= 0 else self.COLOR_ERROR
        pnl_emoji = "📈" if pnl >= 0 else "📉"
        
        embed = DiscordEmbed(
            title=f"{pnl_emoji} Daily PnL Report",
            description=f"Paper trading simulation results for {datetime.now().strftime('%Y-%m-%d')}",
            color=color,
            fields=[
                {'name': '💰 PnL', 'value': f'${pnl:,.2f}', 'inline': True},
                {'name': '📊 Trades', 'value': str(trades), 'inline': True},
                {'name': '🎯 Win Rate', 'value': f'{win_rate:.1%}', 'inline': True},
                {'name': '📐 Sharpe', 'value': f'{sharpe:.3f}', 'inline': True},
                {'name': '📉 Max DD', 'value': f'{max_drawdown:.1%}', 'inline': True},
            ],
            footer="Educational purpose only - paper trading",
            timestamp=datetime.utcnow().isoformat(),
        )
        
        return await self.send_message(embeds=[embed])
    
    async def send_trade_alert(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        pnl: float = None,
    ) -> bool:
        """
        Send trade execution alert.
        
        Args:
            symbol: Trading symbol
            side: Buy/Sell
            quantity: Trade quantity
            price: Execution price
            pnl: Trade PnL (for closes)
        
        Returns:
            True if successful
        """
        side_emoji = "🟢" if side.lower() == "buy" else "🔴"
        
        fields = [
            {'name': 'Symbol', 'value': symbol, 'inline': True},
            {'name': 'Side', 'value': side.upper(), 'inline': True},
            {'name': 'Quantity', 'value': f'{quantity:.4f}', 'inline': True},
            {'name': 'Price', 'value': f'${price:.4f}', 'inline': True},
        ]
        
        if pnl is not None:
            fields.append({
                'name': 'PnL',
                'value': f'${pnl:,.2f}',
                'inline': True,
            })
        
        embed = DiscordEmbed(
            title=f"{side_emoji} Trade Executed",
            color=self.COLOR_INFO,
            fields=fields,
            timestamp=datetime.utcnow().isoformat(),
        )
        
        return await self.send_message(embeds=[embed])
    
    async def send_risk_alert(
        self,
        alert_type: str,
        message: str,
        current_value: float = None,
        threshold: float = None,
    ) -> bool:
        """
        Send risk alert.
        
        Args:
            alert_type: Type of alert (drawdown, position_size, etc.)
            message: Alert message
            current_value: Current metric value
            threshold: Threshold that was exceeded
        
        Returns:
            True if successful
        """
        fields = []
        
        if current_value is not None:
            fields.append({
                'name': 'Current',
                'value': f'{current_value:.2f}',
                'inline': True,
            })
        
        if threshold is not None:
            fields.append({
                'name': 'Threshold',
                'value': f'{threshold:.2f}',
                'inline': True,
            })
        
        embed = DiscordEmbed(
            title=f"⚠️ Risk Alert: {alert_type}",
            description=message,
            color=self.COLOR_WARNING,
            fields=fields if fields else None,
            timestamp=datetime.utcnow().isoformat(),
        )
        
        return await self.send_message(embeds=[embed])
    
    async def send_circuit_breaker_alert(
        self,
        reason: str,
        drawdown: float,
        threshold: float,
    ) -> bool:
        """
        Send circuit breaker trigger alert.
        
        Args:
            reason: Reason for trigger
            drawdown: Current drawdown
            threshold: Trigger threshold
        
        Returns:
            True if successful
        """
        embed = DiscordEmbed(
            title="🛑 CIRCUIT BREAKER TRIGGERED",
            description=f"Trading halted: {reason}",
            color=self.COLOR_ERROR,
            fields=[
                {'name': 'Drawdown', 'value': f'{drawdown:.1%}', 'inline': True},
                {'name': 'Threshold', 'value': f'{threshold:.1%}', 'inline': True},
            ],
            footer="Manual intervention required",
            timestamp=datetime.utcnow().isoformat(),
        )
        
        return await self.send_message(embeds=[embed])
    
    async def send_error(
        self,
        error_type: str,
        error_message: str,
        stack_trace: str = None,
    ) -> bool:
        """
        Send error notification.
        
        Args:
            error_type: Type of error
            error_message: Error message
            stack_trace: Optional stack trace
        
        Returns:
            True if successful
        """
        description = error_message
        if stack_trace:
            description += f"\n```\n{stack_trace[:500]}\n```"
        
        embed = DiscordEmbed(
            title=f"❌ Error: {error_type}",
            description=description,
            color=self.COLOR_ERROR,
            timestamp=datetime.utcnow().isoformat(),
        )
        
        return await self.send_message(embeds=[embed])


def create_discord_webhook(
    webhook_url: str,
    username: str = "Polym Trading Bot",
) -> DiscordWebhook:
    """Create and return a DiscordWebhook instance."""
    return DiscordWebhook(
        webhook_url=webhook_url,
        username=username,
    )


class MockDiscordWebhook(DiscordWebhook):
    """Mock webhook for testing (logs instead of sending)."""
    
    def __init__(self):
        super().__init__(webhook_url="", username="Mock")
        self.sent_messages: List[Dict] = []
    
    async def send_message(
        self,
        content: str = None,
        embeds: List[DiscordEmbed] = None,
    ) -> bool:
        """Log message instead of sending."""
        message = {
            'content': content,
            'embeds': [e.to_dict() for e in embeds] if embeds else [],
            'timestamp': datetime.now().isoformat(),
        }
        self.sent_messages.append(message)
        logger.info(f"[Mock Discord] {json.dumps(message, indent=2)}")
        return True
