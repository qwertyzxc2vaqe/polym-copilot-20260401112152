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
from rich.progress import Progress, BarColumn

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
        
        # Task-33: Inventory tracking (mock data)
        self._inventory: Dict[str, Dict[str, int]] = {
            "BTC": {"yes_shares": 150, "no_shares": 75, "pending_merges": 3},
            "ETH": {"yes_shares": 500, "no_shares": 250, "pending_merges": 2},
        }
        
        # Task-34: Session PnL tracking
        self._session_profit: float = 0.0
        self._merged_shares: int = 0
        self._paper_usdc: float = 100.0
        self._session_start_time = datetime.now(timezone.utc)
    
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
    
    def get_ticker_countdown(self) -> Panel:
        """
        Task-31: Real-time ticker counting down to market end in MILLISECONDS.
        """
        # Find market with earliest expiry across all assets
        closest_market = None
        for market in self._markets.values():
            if closest_market is None or market.seconds_to_expiry < closest_market.seconds_to_expiry:
                closest_market = market
        
        if not closest_market:
            return Panel(
                Text("No active market", style="dim italic"),
                title="⏱ MARKET COUNTDOWN",
                border_style="dim",
                padding=(1, 2),
            )
        
        # Calculate time remaining in milliseconds
        seconds_remaining = closest_market.seconds_to_expiry
        ms_remaining = int(seconds_remaining * 1000)
        
        # Format: MM:SS.mmm
        minutes = ms_remaining // 60000
        seconds = (ms_remaining % 60000) // 1000
        millis = ms_remaining % 1000
        
        countdown_str = f"{minutes:02d}:{seconds:02d}.{millis:03d}"
        
        # Color based on urgency
        if ms_remaining <= 10000:  # 10 seconds
            style = "red bold blink"
        elif ms_remaining <= 30000:  # 30 seconds
            style = "red bold"
        elif ms_remaining <= 60000:  # 1 minute
            style = "yellow bold"
        else:
            style = "green bold"
        
        content = Text()
        content.append("⏳ ", style="bold")
        content.append(countdown_str, style=style)
        content.append("\n", style="dim")
        content.append(closest_market.asset.upper(), style="dim italic")
        
        return Panel(
            Align.center(content),
            title="⏱ MARKET COUNTDOWN",
            border_style=style.split()[0] if style else "white",
            padding=(1, 2),
        )
    
    def get_oracle_panel(self) -> Panel:
        """
        Task-32: Live Oracle panel showing Binance price vs Polymarket implied 
        probability with OFI color-coding (green/red).
        """
        table = Table(
            show_header=True,
            header_style="bold cyan",
            box=None,
            padding=(0, 1),
            expand=True,
        )
        table.add_column("Asset", style="bold", width=10)
        table.add_column("Binance Price", justify="right", width=18)
        table.add_column("Implied Prob", justify="right", width=14)
        table.add_column("OFI Status", justify="center", width=12)
        
        for asset in self.ASSETS:
            # Get Binance price
            price_data = self.oracle.get_price(asset)
            binance_price = price_data.price if price_data else None
            
            # Get Polymarket implied probability (from yes price)
            yes_price = self._yes_prices.get(asset)
            
            if binance_price is None or yes_price is None:
                table.add_row(
                    asset,
                    Text("N/A", style="dim"),
                    Text("N/A", style="dim"),
                    Text("--", style="dim"),
                )
                continue
            
            # Format Binance price
            binance_text = Text(f"${binance_price:,.2f}", style="green bold")
            
            # Format implied probability as percentage
            implied_prob = yes_price * 100
            prob_text = Text(f"{implied_prob:.1f}%", style="yellow")
            
            # OFI logic: compare implied prob vs market sentiment
            # Green if probability is high (bullish), Red if low (bearish)
            ofi_differential = implied_prob - 50  # 50% is neutral
            
            if abs(ofi_differential) < 5:
                ofi_status = Text("NEUTRAL", style="white")
            elif ofi_differential > 0:
                ofi_status = Text("🟢 BULLISH", style="green bold")
            else:
                ofi_status = Text("🔴 BEARISH", style="red bold")
            
            table.add_row(
                asset,
                binance_text,
                prob_text,
                ofi_status,
            )
        
        return Panel(
            table,
            title="[bold cyan]ORACLE FEED - Binance vs Polymarket[/bold cyan]",
            border_style="cyan",
            padding=(1, 2),
        )
    
    def get_inventory_panel(self) -> Panel:
        """
        Task-33: Inventory Tracker showing mock YES shares, NO shares, pending merges.
        """
        table = Table(
            show_header=True,
            header_style="bold magenta",
            box=None,
            padding=(0, 1),
            expand=True,
        )
        table.add_column("Asset", style="bold", width=10)
        table.add_column("YES Shares", justify="right", width=14)
        table.add_column("NO Shares", justify="right", width=14)
        table.add_column("Pending Merges", justify="right", width=16)
        table.add_column("Total", justify="right", width=10)
        
        for asset in self.ASSETS:
            inv = self._inventory.get(asset, {})
            yes_shares = inv.get("yes_shares", 0)
            no_shares = inv.get("no_shares", 0)
            pending = inv.get("pending_merges", 0)
            total = yes_shares + no_shares
            
            yes_text = Text(f"{yes_shares}", style="green bold")
            no_text = Text(f"{no_shares}", style="red bold")
            
            pending_style = "yellow bold" if pending > 0 else "dim"
            pending_text = Text(f"{pending}", style=pending_style)
            
            total_text = Text(f"{total}", style="cyan bold")
            
            table.add_row(
                asset,
                yes_text,
                no_text,
                pending_text,
                total_text,
            )
        
        return Panel(
            table,
            title="[bold magenta]INVENTORY TRACKER[/bold magenta]",
            border_style="magenta",
            padding=(1, 2),
        )
    
    def get_pnl_panel(self) -> Panel:
        """
        Task-34: Dynamic PnL metric showing session profit from merged shares 
        minus 100 Paper-USDC.
        """
        # Calculate net PnL
        net_pnl = self._session_profit - self._paper_usdc
        
        # Determine color based on PnL
        if net_pnl > 0:
            pnl_style = "green bold"
            pnl_prefix = "↑ "
        elif net_pnl < 0:
            pnl_style = "red bold"
            pnl_prefix = "↓ "
        else:
            pnl_style = "yellow bold"
            pnl_prefix = "→ "
        
        # Build content
        content = Text()
        content.append("Session Performance\n", style="bold dim")
        content.append("\n")
        content.append("Profit from Merges:  ", style="bold")
        content.append(f"${self._session_profit:+,.2f}\n", style="green bold")
        
        content.append("Paper-USDC Cost:  ", style="bold")
        content.append(f"$({self._paper_usdc:.2f})\n", style="yellow")
        
        content.append("\n")
        content.append("Net PnL:  ", style="bold")
        content.append(f"{pnl_prefix}${net_pnl:+,.2f}", style=pnl_style)
        
        content.append("\n\n")
        content.append("Shares Merged:  ", style="bold")
        content.append(f"{self._merged_shares}\n", style="cyan bold")
        
        # Session duration
        elapsed = datetime.now(timezone.utc) - self._session_start_time
        elapsed_mins = int(elapsed.total_seconds() / 60)
        content.append("Session Duration:  ", style="bold")
        content.append(f"{elapsed_mins}m\n", style="dim")
        
        return Panel(
            content,
            title="[bold green]SESSION PnL[/bold green]",
            border_style="green",
            padding=(1, 2),
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
        
        # Main structure: header, body sections, footer
        layout.split_column(
            Layout(name="header", size=6),
            Layout(name="top_body", size=12),      # Ticker, Oracle, Inventory, PnL
            Layout(name="middle_body", ratio=1),    # BTC and ETH markets
            Layout(name="footer", size=3),          # Status bar
        )
        
        # Header
        layout["header"].update(self.get_header())
        
        # Top body: 2x2 grid for new features
        layout["top_body"].split_row(
            Layout(name="ticker_pnl", ratio=1),
            Layout(name="oracle_inventory", ratio=1),
        )
        
        # Left side: Ticker + PnL
        layout["ticker_pnl"].split_column(
            Layout(name="ticker", size=6),
            Layout(name="pnl", ratio=1),
        )
        layout["ticker"].update(self.get_ticker_countdown())
        layout["pnl"].update(self.get_pnl_panel())
        
        # Right side: Oracle + Inventory
        layout["oracle_inventory"].split_column(
            Layout(name="oracle", size=8),
            Layout(name="inventory", ratio=1),
        )
        layout["oracle"].update(self.get_oracle_panel())
        layout["inventory"].update(self.get_inventory_panel())
        
        # Middle body: split into two columns for BTC and ETH markets
        layout["middle_body"].split_row(
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
    
    def update_session_pnl(self, profit: float, merged_shares: int) -> None:
        """
        Update session PnL metrics.
        
        Args:
            profit: Total profit from merged shares
            merged_shares: Number of shares successfully merged
        """
        self._session_profit = profit
        self._merged_shares = merged_shares
    
    def update_inventory(self, asset: str, yes_shares: int, no_shares: int, pending_merges: int) -> None:
        """
        Update inventory for an asset.
        
        Args:
            asset: Asset symbol (BTC or ETH)
            yes_shares: Number of YES shares held
            no_shares: Number of NO shares held
            pending_merges: Number of pending merge operations
        """
        asset = asset.upper()
        if asset in self.ASSETS:
            self._inventory[asset] = {
                "yes_shares": yes_shares,
                "no_shares": no_shares,
                "pending_merges": pending_merges,
            }
    
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
