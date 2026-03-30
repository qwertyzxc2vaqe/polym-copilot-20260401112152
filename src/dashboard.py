"""
Rich TUI Dashboard for Polymarket Arbitrage Bot.

Live monitoring dashboard displaying 5-minute BTC/ETH markets,
oracle prices, rate limiting status, and bot state.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.align import Align

from oracle import BinanceOracle, PriceData
from scanner import MarketScanner, Market5Min, ScanningPhase
from config import get_config, SecureConfig

# Optional rate limiter import
try:
    from rate_limiter import RateLimitOverwatch, ThrottleLevel
    HAS_RATE_LIMITER = True
except ImportError:
    HAS_RATE_LIMITER = False
    RateLimitOverwatch = None
    ThrottleLevel = None

logger = logging.getLogger(__name__)


class Dashboard:
    """
    Live TUI dashboard for monitoring the Polymarket arbitrage bot.
    
    Displays:
    - Split-screen layout for BTC and ETH markets
    - Real-time Binance oracle prices
    - Polymarket 'Yes' prices
    - Time remaining to market expiry
    - Bot state (Dry Run vs. Live)
    - Rate limit status and scanning phase
    """
    
    ASSETS = ["BTC", "ETH"]
    REFRESH_RATE = 1.0  # seconds
    
    def __init__(
        self,
        oracle: BinanceOracle,
        scanner: MarketScanner,
        config: Optional[SecureConfig] = None,
        rate_limiter: Optional["RateLimitOverwatch"] = None,
    ):
        """
        Initialize the dashboard.
        
        Args:
            oracle: BinanceOracle instance for price data
            scanner: MarketScanner instance for market data
            config: SecureConfig instance for bot mode
            rate_limiter: Optional RateLimitOverwatch for throttle status
        """
        self.oracle = oracle
        self.scanner = scanner
        self.config = config or get_config()
        self.rate_limiter = rate_limiter
        self.console = Console()
        self._running = False
        self._markets: Dict[str, Market5Min] = {}  # asset -> closest market
        self._yes_prices: Dict[str, float] = {}  # asset -> yes price
    
    def get_bot_mode(self) -> str:
        """Get the current bot mode as a display string."""
        if self.config.is_dry_run():
            return "DRY RUN"
        elif self.config.is_live_test():
            return "LIVE TEST"
        elif self.config.is_autonomous():
            return "AUTONOMOUS"
        return "UNKNOWN"
    
    def get_bot_mode_style(self) -> str:
        """Get the Rich style for the current bot mode."""
        if self.config.is_dry_run():
            return "cyan"
        elif self.config.is_live_test():
            return "yellow"
        elif self.config.is_autonomous():
            return "red bold"
        return "white"
    
    def format_time_remaining(self, seconds: float) -> Text:
        """Format time remaining with color coding."""
        if seconds <= 0:
            return Text("EXPIRED", style="red bold")
        
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        time_str = f"{minutes:02d}:{secs:02d}"
        
        # Color code based on urgency
        if seconds <= 60:
            style = "red bold"  # Terminal phase
        elif seconds <= 300:
            style = "yellow"  # Cruising phase
        else:
            style = "green"  # Discovery phase
        
        return Text(time_str, style=style)
    
    def format_price(self, price: Optional[float], decimals: int = 2) -> Text:
        """Format price for display."""
        if price is None:
            return Text("N/A", style="dim")
        return Text(f"${price:,.{decimals}f}", style="green bold")
    
    def format_yes_price(self, price: Optional[float]) -> Text:
        """Format Polymarket Yes price (0-1 range as percentage)."""
        if price is None:
            return Text("N/A", style="dim")
        pct = price * 100
        
        # Color based on implied probability
        if pct >= 90:
            style = "green bold"
        elif pct >= 50:
            style = "yellow"
        else:
            style = "red"
        
        return Text(f"{pct:.1f}¢", style=style)
    
    def get_market_panel(self, asset: str) -> Panel:
        """
        Build a panel for a specific asset's market data.
        
        Args:
            asset: Asset symbol (BTC or ETH)
            
        Returns:
            Rich Panel with market information
        """
        table = Table(
            show_header=False,
            box=None,
            padding=(0, 1),
            expand=True,
        )
        table.add_column("Label", style="bold", width=18)
        table.add_column("Value", justify="right")
        
        # Oracle price
        price_data = self.oracle.get_price(asset)
        oracle_price = price_data.price if price_data else None
        
        table.add_row(
            "Oracle Price:",
            self.format_price(oracle_price, decimals=2),
        )
        
        # Rolling average
        rolling_avg = self.oracle.get_rolling_average(asset)
        table.add_row(
            "30s Avg:",
            self.format_price(rolling_avg, decimals=2),
        )
        
        # Momentum
        momentum = self.oracle.get_price_momentum(asset)
        if momentum is not None:
            momentum_style = "green" if momentum >= 0 else "red"
            momentum_text = Text(f"{momentum:+.3f}%", style=momentum_style)
        else:
            momentum_text = Text("N/A", style="dim")
        table.add_row("Momentum:", momentum_text)
        
        # Market data
        market = self._markets.get(asset)
        
        if market:
            # Yes price (placeholder - would need CLOB client for real data)
            yes_price = self._yes_prices.get(asset)
            table.add_row(
                "Yes Price:",
                self.format_yes_price(yes_price),
            )
            
            # Time remaining
            seconds_remaining = market.seconds_to_expiry
            table.add_row(
                "Time Remaining:",
                self.format_time_remaining(seconds_remaining),
            )
            
            # Market question (truncated)
            question = market.question[:40] + "..." if len(market.question) > 40 else market.question
            table.add_row(
                "Market:",
                Text(question, style="dim italic"),
            )
        else:
            table.add_row("Yes Price:", Text("No market", style="dim"))
            table.add_row("Time Remaining:", Text("--:--", style="dim"))
            table.add_row("Market:", Text("Searching...", style="dim italic"))
        
        # Connection status
        connected = "●" if self.oracle.is_connected else "○"
        conn_style = "green" if self.oracle.is_connected else "red"
        stale = self.oracle.is_stale(asset)
        stale_indicator = " (STALE)" if stale else ""
        
        table.add_row(
            "Oracle Status:",
            Text(f"{connected} Connected{stale_indicator}", style=conn_style),
        )
        
        # Panel title with asset name
        title_style = "bold cyan" if asset == "BTC" else "bold magenta"
        asset_name = "Bitcoin" if asset == "BTC" else "Ethereum"
        
        return Panel(
            table,
            title=f"[{title_style}]{asset} - {asset_name}[/{title_style}]",
            border_style=title_style.split()[1],
            padding=(1, 2),
        )
    
    def get_status_bar(self) -> Panel:
        """Build the status bar with rate limit warnings and phase info."""
        status_parts: List[Text] = []
        
        # Bot Mode
        mode = self.get_bot_mode()
        mode_style = self.get_bot_mode_style()
        status_parts.append(Text(f"Mode: ", style="bold"))
        status_parts.append(Text(f"[{mode}]", style=mode_style))
        status_parts.append(Text("  │  ", style="dim"))
        
        # Scanning Phase
        phase = self.scanner.get_current_phase()
        phase_styles = {
            ScanningPhase.DISCOVERY: ("[SEARCH] DISCOVERY", "green"),
            ScanningPhase.CRUISING: ("[TAKEOFF] CRUISING", "yellow"),
            ScanningPhase.TERMINAL: (">> TERMINAL", "red bold"),
        }
        phase_text, phase_style = phase_styles.get(phase, ("UNKNOWN", "white"))
        status_parts.append(Text("Phase: ", style="bold"))
        status_parts.append(Text(phase_text, style=phase_style))
        status_parts.append(Text("  │  ", style="dim"))
        
        # Rate Limit Status
        if self.rate_limiter and HAS_RATE_LIMITER:
            throttle_status = self.rate_limiter.get_throttle_status()
            
            if throttle_status.level == ThrottleLevel.COOLDOWN:
                status_parts.append(
                    Text("[PAUSE] COOLDOWN ACTIVE", style="red bold blink")
                )
            elif throttle_status.level == ThrottleLevel.CRITICAL:
                status_parts.append(
                    Text("[!!] RATE LIMIT CRITICAL", style="red bold")
                )
            elif throttle_status.level == ThrottleLevel.WARNING:
                status_parts.append(
                    Text("[WARN] RATE LIMIT WARNING: THROTTLING", style="yellow bold")
                )
            else:
                status_parts.append(Text("✓ Rate OK", style="green"))
        else:
            status_parts.append(Text("Rate Limiter: N/A", style="dim"))
        
        status_parts.append(Text("  │  ", style="dim"))
        
        # Timestamp
        now = datetime.now(timezone.utc)
        status_parts.append(
            Text(f"UTC: {now.strftime('%H:%M:%S')}", style="dim")
        )
        
        # Combine all parts
        combined = Text()
        for part in status_parts:
            combined.append_text(part)
        
        return Panel(
            Align.center(combined),
            style="dim",
            height=3,
        )
    
    def get_header(self) -> Panel:
        """Build the header panel."""
        title = Text()
        title.append("╔═══════════════════════════════════════════════╗\n", style="bold cyan")
        title.append("║  ", style="bold cyan")
        title.append("POLYMARKET ARBITRAGE BOT", style="bold white")
        title.append("  ║\n", style="bold cyan")
        title.append("║  ", style="bold cyan")
        title.append("5-Minute Market Monitor", style="dim")
        title.append("          ║\n", style="bold cyan")
        title.append("╚═══════════════════════════════════════════════╝", style="bold cyan")
        
        return Panel(
            Align.center(title),
            style="bold",
            padding=(0, 0),
        )
    
    def build_layout(self) -> Layout:
        """Build the complete dashboard layout."""
        layout = Layout()
        
        # Main structure: header, body, footer
        layout.split_column(
            Layout(name="header", size=6),
            Layout(name="body", ratio=1),
            Layout(name="footer", size=3),
        )
        
        # Header
        layout["header"].update(self.get_header())
        
        # Body: split into two columns for BTC and ETH
        layout["body"].split_row(
            Layout(name="btc", ratio=1),
            Layout(name="eth", ratio=1),
        )
        
        layout["btc"].update(self.get_market_panel("BTC"))
        layout["eth"].update(self.get_market_panel("ETH"))
        
        # Footer: status bar
        layout["footer"].update(self.get_status_bar())
        
        return layout
    
    def update_markets(self, markets: List[Market5Min]) -> None:
        """
        Update internal market cache with latest scan results.
        
        Args:
            markets: List of active 5-minute markets
        """
        # Clear old markets
        self._markets.clear()
        
        # Find the closest market for each asset
        for market in markets:
            asset = market.asset.upper()
            if asset in self.ASSETS:
                existing = self._markets.get(asset)
                if existing is None or market.seconds_to_expiry < existing.seconds_to_expiry:
                    self._markets[asset] = market
    
    def update_yes_price(self, asset: str, price: float) -> None:
        """
        Update the Yes price for an asset.
        
        Args:
            asset: Asset symbol (BTC or ETH)
            price: Yes price (0-1 range)
        """
        self._yes_prices[asset.upper()] = price
    
    async def run(self) -> None:
        """
        Main dashboard loop with live updates.
        
        Refreshes display every second using Rich's Live context.
        """
        self._running = True
        logger.info("Starting dashboard...")
        
        with Live(
            self.build_layout(),
            console=self.console,
            refresh_per_second=1,
            screen=True,
        ) as live:
            while self._running:
                try:
                    # Rebuild and update layout
                    live.update(self.build_layout())
                    await asyncio.sleep(self.REFRESH_RATE)
                except asyncio.CancelledError:
                    logger.info("Dashboard cancelled")
                    break
                except Exception as e:
                    logger.error(f"Dashboard error: {e}")
                    await asyncio.sleep(1.0)
        
        logger.info("Dashboard stopped")
    
    def stop(self) -> None:
        """Stop the dashboard loop."""
        self._running = False


async def run_standalone_dashboard() -> None:
    """
    Run the dashboard as a standalone process for testing.
    
    Creates mock oracle and scanner connections and displays the TUI.
    """
    from config import get_config
    
    console = Console()
    console.print("[bold cyan]Starting Polymarket Dashboard...[/bold cyan]")
    
    # Initialize components
    config = get_config()
    oracle = BinanceOracle.from_config()
    scanner = MarketScanner()
    
    # Initialize rate limiter if available
    rate_limiter = None
    if HAS_RATE_LIMITER:
        rate_limiter = await RateLimitOverwatch.get_instance()
    
    # Create dashboard
    dashboard = Dashboard(
        oracle=oracle,
        scanner=scanner,
        config=config,
        rate_limiter=rate_limiter,
    )
    
    async def market_scanner_task():
        """Background task to scan for markets."""
        while dashboard._running:
            try:
                markets = await scanner.scan_markets()
                dashboard.update_markets(markets)
                await asyncio.sleep(5.0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Scanner error: {e}")
                await asyncio.sleep(5.0)
    
    try:
        # Start oracle connection
        oracle_task = asyncio.create_task(oracle.connect())
        
        # Wait briefly for initial price data
        await asyncio.sleep(2.0)
        
        # Start market scanner
        scanner_task = asyncio.create_task(market_scanner_task())
        
        # Run dashboard
        await dashboard.run()
        
    except KeyboardInterrupt:
        console.print("\n[yellow]Dashboard interrupted[/yellow]")
    finally:
        dashboard.stop()
        await oracle.disconnect()
        await scanner.close()


if __name__ == "__main__":
    import sys
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[logging.FileHandler("logs/dashboard.log")],
    )
    
    print("Polymarket TUI Dashboard")
    print("Press Ctrl+C to exit\n")
    
    try:
        asyncio.run(run_standalone_dashboard())
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
        sys.exit(0)
