"""
PDF Report Generator.

Phase 2 - Task 78: Generate post-session PDF report using ReportLab.

Educational purpose only - paper trading simulation.
"""

import io
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter, A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        PageBreak, Image, HRFlowable
    )
    from reportlab.graphics.shapes import Drawing
    from reportlab.graphics.charts.lineplots import LinePlot
    from reportlab.graphics.charts.barcharts import VerticalBarChart
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False
    # Define stub types to avoid NameError on class definition
    Table = None
    Drawing = None
    logger.warning("reportlab not available, PDF generation disabled")


@dataclass
class SessionSummary:
    """Summary data for a trading session."""
    session_id: str
    start_time: datetime
    end_time: datetime
    total_trades: int
    winning_trades: int
    losing_trades: int
    total_pnl: float
    max_drawdown: float
    sharpe_ratio: float
    sortino_ratio: float
    win_rate: float
    avg_trade_duration_seconds: float
    symbols_traded: List[str]


@dataclass
class TradeRecord:
    """Individual trade record."""
    trade_id: str
    symbol: str
    side: str
    quantity: float
    entry_price: float
    exit_price: float
    pnl: float
    duration_seconds: float
    timestamp: datetime


class PDFReportGenerator:
    """
    Generates post-session PDF reports.
    
    Features:
    - Session summary statistics
    - Trade-by-trade breakdown
    - PnL charts (equity curve)
    - Risk metrics visualization
    - Exportable to data/ directory
    """
    
    def __init__(
        self,
        output_dir: str = "data",
        page_size=None,
    ):
        """
        Initialize PDF report generator.
        
        Args:
            output_dir: Directory for output files
            page_size: Page size (default: letter)
        """
        if not REPORTLAB_AVAILABLE:
            raise ImportError("reportlab is required for PDF generation")
        
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.page_size = page_size or letter
        self._styles = getSampleStyleSheet()
        self._setup_custom_styles()
    
    def _setup_custom_styles(self) -> None:
        """Setup custom paragraph styles."""
        self._styles.add(ParagraphStyle(
            name='CustomTitle',
            parent=self._styles['Heading1'],
            fontSize=24,
            spaceAfter=30,
            textColor=colors.HexColor('#1a1a2e'),
        ))
        
        self._styles.add(ParagraphStyle(
            name='SectionHeader',
            parent=self._styles['Heading2'],
            fontSize=14,
            spaceAfter=12,
            spaceBefore=20,
            textColor=colors.HexColor('#16213e'),
        ))
        
        self._styles.add(ParagraphStyle(
            name='MetricLabel',
            parent=self._styles['Normal'],
            fontSize=10,
            textColor=colors.HexColor('#666666'),
        ))
        
        self._styles.add(ParagraphStyle(
            name='MetricValue',
            parent=self._styles['Normal'],
            fontSize=12,
            fontName='Helvetica-Bold',
            textColor=colors.HexColor('#1a1a2e'),
        ))
    
    def generate_report(
        self,
        summary: SessionSummary,
        trades: List[TradeRecord] = None,
        equity_curve: List[float] = None,
        filename: str = None,
    ) -> str:
        """
        Generate a PDF report.
        
        Args:
            summary: Session summary data
            trades: List of trade records
            equity_curve: Equity values over time
            filename: Output filename (auto-generated if None)
        
        Returns:
            Path to generated PDF
        """
        trades = trades or []
        equity_curve = equity_curve or []
        
        if filename is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"session_report_{timestamp}.pdf"
        
        filepath = self.output_dir / filename
        
        doc = SimpleDocTemplate(
            str(filepath),
            pagesize=self.page_size,
            rightMargin=72,
            leftMargin=72,
            topMargin=72,
            bottomMargin=72,
        )
        
        story = []
        
        # Title
        story.append(Paragraph(
            "Trading Session Report",
            self._styles['CustomTitle']
        ))
        
        # Session info
        story.append(Paragraph(
            f"Session ID: {summary.session_id}",
            self._styles['Normal']
        ))
        story.append(Paragraph(
            f"Period: {summary.start_time.strftime('%Y-%m-%d %H:%M')} - "
            f"{summary.end_time.strftime('%Y-%m-%d %H:%M')}",
            self._styles['Normal']
        ))
        story.append(Spacer(1, 20))
        
        # Summary metrics
        story.append(Paragraph("Performance Summary", self._styles['SectionHeader']))
        story.append(self._build_summary_table(summary))
        story.append(Spacer(1, 20))
        
        # Risk metrics
        story.append(Paragraph("Risk Metrics", self._styles['SectionHeader']))
        story.append(self._build_risk_table(summary))
        story.append(Spacer(1, 20))
        
        # Equity curve
        if equity_curve:
            story.append(Paragraph("Equity Curve", self._styles['SectionHeader']))
            story.append(self._build_equity_chart(equity_curve))
            story.append(Spacer(1, 20))
        
        # Trade log
        if trades:
            story.append(PageBreak())
            story.append(Paragraph("Trade Log", self._styles['SectionHeader']))
            story.append(self._build_trade_table(trades[:50]))  # Limit to 50
        
        # Footer
        story.append(Spacer(1, 30))
        story.append(HRFlowable(width="100%", color=colors.gray))
        story.append(Spacer(1, 10))
        story.append(Paragraph(
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
            "Educational purpose only - paper trading simulation",
            self._styles['MetricLabel']
        ))
        
        doc.build(story)
        logger.info(f"PDF report generated: {filepath}")
        
        return str(filepath)
    
    def _build_summary_table(self, summary: SessionSummary) -> Table:
        """Build performance summary table."""
        pnl_color = colors.green if summary.total_pnl >= 0 else colors.red
        
        data = [
            ['Metric', 'Value'],
            ['Total Trades', str(summary.total_trades)],
            ['Winning Trades', str(summary.winning_trades)],
            ['Losing Trades', str(summary.losing_trades)],
            ['Win Rate', f'{summary.win_rate:.1%}'],
            ['Total PnL', f'${summary.total_pnl:,.2f}'],
            ['Avg Trade Duration', f'{summary.avg_trade_duration_seconds:.1f}s'],
            ['Symbols Traded', ', '.join(summary.symbols_traded[:5])],
        ]
        
        table = Table(data, colWidths=[2.5*inch, 2.5*inch])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a1a2e')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#f5f5f5')),
            ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 10),
            ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#dddddd')),
            ('ALIGN', (1, 1), (1, -1), 'RIGHT'),
        ]))
        
        return table
    
    def _build_risk_table(self, summary: SessionSummary) -> Table:
        """Build risk metrics table."""
        data = [
            ['Metric', 'Value', 'Status'],
            ['Sharpe Ratio', f'{summary.sharpe_ratio:.3f}',
             'Good' if summary.sharpe_ratio > 1.0 else 'Fair' if summary.sharpe_ratio > 0.5 else 'Poor'],
            ['Sortino Ratio', f'{summary.sortino_ratio:.3f}',
             'Good' if summary.sortino_ratio > 1.5 else 'Fair' if summary.sortino_ratio > 0.7 else 'Poor'],
            ['Max Drawdown', f'{summary.max_drawdown:.1%}',
             'Good' if summary.max_drawdown < 0.1 else 'Fair' if summary.max_drawdown < 0.2 else 'High Risk'],
        ]
        
        table = Table(data, colWidths=[2*inch, 1.5*inch, 1.5*inch])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#16213e')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#fafafa')),
            ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#dddddd')),
        ]))
        
        return table
    
    def _build_equity_chart(self, equity_curve: List[float]) -> Drawing:
        """Build equity curve chart."""
        drawing = Drawing(400, 200)
        
        lp = LinePlot()
        lp.x = 50
        lp.y = 50
        lp.height = 125
        lp.width = 300
        
        # Prepare data
        data = [[(i, v) for i, v in enumerate(equity_curve)]]
        lp.data = data
        
        lp.lines[0].strokeColor = colors.HexColor('#4CAF50')
        lp.lines[0].strokeWidth = 2
        
        lp.xValueAxis.labelTextFormat = '%d'
        lp.yValueAxis.labelTextFormat = '$%d'
        
        drawing.add(lp)
        
        return drawing
    
    def _build_trade_table(self, trades: List[TradeRecord]) -> Table:
        """Build trade log table."""
        data = [['ID', 'Symbol', 'Side', 'Qty', 'Entry', 'Exit', 'PnL']]
        
        for trade in trades:
            pnl_str = f'${trade.pnl:,.2f}'
            data.append([
                trade.trade_id[:8],
                trade.symbol,
                trade.side,
                f'{trade.quantity:.4f}',
                f'${trade.entry_price:.2f}',
                f'${trade.exit_price:.2f}',
                pnl_str,
            ])
        
        table = Table(data, colWidths=[0.8*inch, 0.8*inch, 0.5*inch, 0.7*inch, 0.9*inch, 0.9*inch, 0.9*inch])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a1a2e')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 9),
            ('FONTSIZE', (0, 1), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f9f9f9')]),
        ]))
        
        return table


def create_pdf_report_generator(
    output_dir: str = "data",
) -> Optional[PDFReportGenerator]:
    """Create and return a PDFReportGenerator instance."""
    if not REPORTLAB_AVAILABLE:
        logger.warning("PDF generation unavailable - install reportlab")
        return None
    
    return PDFReportGenerator(output_dir=output_dir)


def generate_sample_report(output_dir: str = "data") -> Optional[str]:
    """Generate a sample report for testing."""
    generator = create_pdf_report_generator(output_dir)
    
    if generator is None:
        return None
    
    summary = SessionSummary(
        session_id="sample_001",
        start_time=datetime(2024, 1, 15, 9, 0, 0),
        end_time=datetime(2024, 1, 15, 16, 0, 0),
        total_trades=47,
        winning_trades=28,
        losing_trades=19,
        total_pnl=1234.56,
        max_drawdown=0.08,
        sharpe_ratio=1.45,
        sortino_ratio=2.12,
        win_rate=0.596,
        avg_trade_duration_seconds=312.5,
        symbols_traded=['BTC', 'ETH', 'SOL'],
    )
    
    equity_curve = [10000 + i * 50 + (i % 5) * 20 - (i % 7) * 15 for i in range(50)]
    
    return generator.generate_report(
        summary=summary,
        equity_curve=equity_curve,
        filename="sample_report.pdf",
    )
