"""
PostgreSQL Database Schema - Timeseries Tick Data Storage.

Phase 2 - Task 58: PostgreSQL schema optimized for tick-data storage,
serving as "cold storage" for paper-trading logs and ML model training.

Educational purpose only - paper trading simulation.
"""

import asyncio
import logging
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any
from enum import Enum

logger = logging.getLogger(__name__)

# Try to import asyncpg
try:
    import asyncpg
    ASYNCPG_AVAILABLE = True
except ImportError:
    ASYNCPG_AVAILABLE = False
    logger.warning("asyncpg not available, database features disabled")


# SQL Schema Definitions
SCHEMA_SQL = """
-- Enable TimescaleDB extension if available (for hypertables)
-- CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Tick data table - core price data storage
CREATE TABLE IF NOT EXISTS ticks (
    id BIGSERIAL,
    symbol VARCHAR(10) NOT NULL,
    price DECIMAL(20, 8) NOT NULL,
    bid DECIMAL(20, 8),
    ask DECIMAL(20, 8),
    volume DECIMAL(20, 8),
    timestamp TIMESTAMPTZ NOT NULL,
    source VARCHAR(20) DEFAULT 'binance',
    PRIMARY KEY (id, timestamp)
);

-- Create index for fast symbol + time queries
CREATE INDEX IF NOT EXISTS idx_ticks_symbol_time ON ticks (symbol, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_ticks_timestamp ON ticks (timestamp DESC);

-- Order book snapshots
CREATE TABLE IF NOT EXISTS order_book_snapshots (
    id BIGSERIAL,
    symbol VARCHAR(10) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    bids JSONB NOT NULL,  -- Array of [price, quantity] pairs
    asks JSONB NOT NULL,
    mid_price DECIMAL(20, 8),
    spread_bps DECIMAL(10, 4),
    ofi_1 DECIMAL(10, 6),
    ofi_5 DECIMAL(10, 6),
    ofi_10 DECIMAL(10, 6),
    ofi_20 DECIMAL(10, 6),
    PRIMARY KEY (id, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_ob_symbol_time ON order_book_snapshots (symbol, timestamp DESC);

-- Trades table - public tape data
CREATE TABLE IF NOT EXISTS trades (
    id BIGSERIAL,
    symbol VARCHAR(10) NOT NULL,
    trade_id BIGINT,
    price DECIMAL(20, 8) NOT NULL,
    quantity DECIMAL(20, 8) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    is_buyer_maker BOOLEAN,
    PRIMARY KEY (id, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_trades_symbol_time ON trades (symbol, timestamp DESC);

-- Paper trading orders
CREATE TABLE IF NOT EXISTS paper_orders (
    id BIGSERIAL PRIMARY KEY,
    order_id VARCHAR(64) UNIQUE NOT NULL,
    symbol VARCHAR(10) NOT NULL,
    side VARCHAR(10) NOT NULL,  -- 'buy' or 'sell'
    order_type VARCHAR(20) NOT NULL,  -- 'limit', 'market'
    price DECIMAL(20, 8),
    quantity DECIMAL(20, 8) NOT NULL,
    status VARCHAR(20) NOT NULL,  -- 'pending', 'filled', 'cancelled', 'expired'
    created_at TIMESTAMPTZ DEFAULT NOW(),
    filled_at TIMESTAMPTZ,
    filled_price DECIMAL(20, 8),
    filled_quantity DECIMAL(20, 8),
    slippage_bps DECIMAL(10, 4),
    queue_position INTEGER,
    market_id VARCHAR(128),
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_orders_symbol ON paper_orders (symbol);
CREATE INDEX IF NOT EXISTS idx_orders_status ON paper_orders (status);
CREATE INDEX IF NOT EXISTS idx_orders_created ON paper_orders (created_at DESC);

-- Paper trading fills
CREATE TABLE IF NOT EXISTS paper_fills (
    id BIGSERIAL PRIMARY KEY,
    fill_id VARCHAR(64) UNIQUE NOT NULL,
    order_id VARCHAR(64) REFERENCES paper_orders(order_id),
    symbol VARCHAR(10) NOT NULL,
    side VARCHAR(10) NOT NULL,
    price DECIMAL(20, 8) NOT NULL,
    quantity DECIMAL(20, 8) NOT NULL,
    fee DECIMAL(20, 8) DEFAULT 0,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    pnl DECIMAL(20, 8),
    pnl_1min DECIMAL(20, 8),  -- PnL 1 minute after fill (adverse selection)
    oracle_price_at_fill DECIMAL(20, 8),
    oracle_price_1min DECIMAL(20, 8)
);

CREATE INDEX IF NOT EXISTS idx_fills_order ON paper_fills (order_id);
CREATE INDEX IF NOT EXISTS idx_fills_timestamp ON paper_fills (timestamp DESC);

-- Portfolio snapshots
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    total_equity DECIMAL(20, 8) NOT NULL,
    cash_balance DECIMAL(20, 8) NOT NULL,
    positions JSONB,  -- {symbol: {quantity, avg_price, unrealized_pnl}}
    daily_pnl DECIMAL(20, 8),
    total_pnl DECIMAL(20, 8),
    sharpe_ratio DECIMAL(10, 4),
    sortino_ratio DECIMAL(10, 4),
    max_drawdown DECIMAL(10, 4),
    win_rate DECIMAL(10, 4),
    trade_count INTEGER
);

CREATE INDEX IF NOT EXISTS idx_portfolio_time ON portfolio_snapshots (timestamp DESC);

-- Funding rates
CREATE TABLE IF NOT EXISTS funding_rates (
    id BIGSERIAL,
    symbol VARCHAR(10) NOT NULL,
    funding_rate DECIMAL(20, 10) NOT NULL,
    funding_rate_bps DECIMAL(10, 4),
    next_funding_time TIMESTAMPTZ,
    mark_price DECIMAL(20, 8),
    index_price DECIMAL(20, 8),
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (id, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_funding_symbol_time ON funding_rates (symbol, timestamp DESC);

-- ML model predictions
CREATE TABLE IF NOT EXISTS ml_predictions (
    id BIGSERIAL,
    symbol VARCHAR(10) NOT NULL,
    model_name VARCHAR(50) NOT NULL,
    prediction DECIMAL(20, 8),  -- Predicted price
    confidence DECIMAL(10, 6),  -- Model confidence 0-1
    direction VARCHAR(10),  -- 'up', 'down', 'neutral'
    actual_price DECIMAL(20, 8),  -- Price at prediction time
    actual_price_1s DECIMAL(20, 8),  -- Actual price 1 second later
    prediction_error DECIMAL(10, 6),  -- (predicted - actual) / actual
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (id, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_predictions_symbol_time ON ml_predictions (symbol, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_predictions_model ON ml_predictions (model_name);

-- Risk metrics log
CREATE TABLE IF NOT EXISTS risk_metrics (
    id BIGSERIAL,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    var_99 DECIMAL(20, 8),  -- 99% VaR
    var_95 DECIMAL(20, 8),  -- 95% VaR
    expected_shortfall DECIMAL(20, 8),
    portfolio_volatility DECIMAL(10, 6),
    beta_btc DECIMAL(10, 6),
    beta_eth DECIMAL(10, 6),
    correlation_btc_eth DECIMAL(10, 6),
    max_position_size DECIMAL(20, 8),
    current_exposure DECIMAL(20, 8),
    PRIMARY KEY (id, timestamp)
);

-- Session logs
CREATE TABLE IF NOT EXISTS session_logs (
    id BIGSERIAL PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    event_data JSONB,
    timestamp TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_session_id ON session_logs (session_id);
CREATE INDEX IF NOT EXISTS idx_session_time ON session_logs (timestamp DESC);

-- Convert to hypertables if TimescaleDB is available
-- SELECT create_hypertable('ticks', 'timestamp', if_not_exists => TRUE);
-- SELECT create_hypertable('order_book_snapshots', 'timestamp', if_not_exists => TRUE);
-- SELECT create_hypertable('trades', 'timestamp', if_not_exists => TRUE);
-- SELECT create_hypertable('funding_rates', 'timestamp', if_not_exists => TRUE);
-- SELECT create_hypertable('ml_predictions', 'timestamp', if_not_exists => TRUE);
-- SELECT create_hypertable('risk_metrics', 'timestamp', if_not_exists => TRUE);
"""


@dataclass
class DatabaseConfig:
    """PostgreSQL connection configuration."""
    host: str = "localhost"
    port: int = 5432
    database: str = "polym_sandbox"
    user: str = "polym"
    password: str = "polym_dev"
    min_connections: int = 2
    max_connections: int = 10


class DatabaseManager:
    """
    Async PostgreSQL database manager for tick data storage.
    
    Provides methods for storing and querying market data.
    """
    
    def __init__(self, config: DatabaseConfig = None):
        """Initialize database manager."""
        self.config = config or DatabaseConfig()
        self._pool: Optional[asyncpg.Pool] = None
        self._connected = False
    
    async def connect(self) -> bool:
        """Connect to PostgreSQL and initialize schema."""
        if not ASYNCPG_AVAILABLE:
            logger.error("asyncpg not installed, cannot connect to database")
            return False
        
        try:
            self._pool = await asyncpg.create_pool(
                host=self.config.host,
                port=self.config.port,
                database=self.config.database,
                user=self.config.user,
                password=self.config.password,
                min_size=self.config.min_connections,
                max_size=self.config.max_connections,
            )
            
            # Initialize schema
            async with self._pool.acquire() as conn:
                await conn.execute(SCHEMA_SQL)
            
            self._connected = True
            logger.info(f"Connected to PostgreSQL at {self.config.host}:{self.config.port}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to connect to PostgreSQL: {e}")
            self._connected = False
            return False
    
    async def disconnect(self) -> None:
        """Close database connections."""
        if self._pool:
            await self._pool.close()
            self._pool = None
        self._connected = False
        logger.info("Disconnected from PostgreSQL")
    
    @property
    def is_connected(self) -> bool:
        return self._connected and self._pool is not None
    
    async def insert_tick(
        self,
        symbol: str,
        price: float,
        bid: float,
        ask: float,
        volume: float,
        timestamp: datetime,
        source: str = "binance",
    ) -> bool:
        """Insert a tick into the database."""
        if not self.is_connected:
            return False
        
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO ticks (symbol, price, bid, ask, volume, timestamp, source)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                    symbol, price, bid, ask, volume, timestamp, source
                )
            return True
        except Exception as e:
            logger.error(f"Failed to insert tick: {e}")
            return False
    
    async def insert_ticks_batch(self, ticks: List[Dict]) -> int:
        """Insert multiple ticks in a batch."""
        if not self.is_connected or not ticks:
            return 0
        
        try:
            async with self._pool.acquire() as conn:
                result = await conn.executemany(
                    """
                    INSERT INTO ticks (symbol, price, bid, ask, volume, timestamp, source)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                    [
                        (t['symbol'], t['price'], t['bid'], t['ask'],
                         t['volume'], t['timestamp'], t.get('source', 'binance'))
                        for t in ticks
                    ]
                )
            return len(ticks)
        except Exception as e:
            logger.error(f"Failed to insert tick batch: {e}")
            return 0
    
    async def insert_order_book_snapshot(
        self,
        symbol: str,
        timestamp: datetime,
        bids: List[List[float]],
        asks: List[List[float]],
        ofi_values: Dict[str, float],
    ) -> bool:
        """Insert order book snapshot."""
        if not self.is_connected:
            return False
        
        try:
            mid_price = (bids[0][0] + asks[0][0]) / 2 if bids and asks else 0
            spread = asks[0][0] - bids[0][0] if bids and asks else 0
            spread_bps = (spread / mid_price * 10000) if mid_price > 0 else 0
            
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO order_book_snapshots 
                    (symbol, timestamp, bids, asks, mid_price, spread_bps, 
                     ofi_1, ofi_5, ofi_10, ofi_20)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    """,
                    symbol, timestamp, json.dumps(bids), json.dumps(asks),
                    mid_price, spread_bps,
                    ofi_values.get('ofi_1', 0), ofi_values.get('ofi_5', 0),
                    ofi_values.get('ofi_10', 0), ofi_values.get('ofi_20', 0)
                )
            return True
        except Exception as e:
            logger.error(f"Failed to insert order book snapshot: {e}")
            return False
    
    async def insert_paper_order(self, order: Dict) -> bool:
        """Insert paper trading order."""
        if not self.is_connected:
            return False
        
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO paper_orders 
                    (order_id, symbol, side, order_type, price, quantity, status, market_id, notes)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    """,
                    order['order_id'], order['symbol'], order['side'],
                    order['order_type'], order.get('price'), order['quantity'],
                    order.get('status', 'pending'), order.get('market_id'),
                    order.get('notes')
                )
            return True
        except Exception as e:
            logger.error(f"Failed to insert paper order: {e}")
            return False
    
    async def insert_paper_fill(self, fill: Dict) -> bool:
        """Insert paper trading fill."""
        if not self.is_connected:
            return False
        
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO paper_fills 
                    (fill_id, order_id, symbol, side, price, quantity, fee,
                     oracle_price_at_fill)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    """,
                    fill['fill_id'], fill.get('order_id'), fill['symbol'],
                    fill['side'], fill['price'], fill['quantity'],
                    fill.get('fee', 0), fill.get('oracle_price')
                )
            return True
        except Exception as e:
            logger.error(f"Failed to insert paper fill: {e}")
            return False
    
    async def get_ticks(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime = None,
        limit: int = 10000,
    ) -> List[Dict]:
        """Query ticks for a symbol within time range."""
        if not self.is_connected:
            return []
        
        try:
            async with self._pool.acquire() as conn:
                if end_time:
                    rows = await conn.fetch(
                        """
                        SELECT symbol, price, bid, ask, volume, timestamp, source
                        FROM ticks
                        WHERE symbol = $1 AND timestamp >= $2 AND timestamp <= $3
                        ORDER BY timestamp ASC
                        LIMIT $4
                        """,
                        symbol, start_time, end_time, limit
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT symbol, price, bid, ask, volume, timestamp, source
                        FROM ticks
                        WHERE symbol = $1 AND timestamp >= $2
                        ORDER BY timestamp ASC
                        LIMIT $3
                        """,
                        symbol, start_time, limit
                    )
                
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to query ticks: {e}")
            return []
    
    async def get_ohlcv(
        self,
        symbol: str,
        interval_seconds: int,
        start_time: datetime,
        end_time: datetime = None,
    ) -> List[Dict]:
        """Get OHLCV data aggregated at specified interval."""
        if not self.is_connected:
            return []
        
        try:
            async with self._pool.acquire() as conn:
                # Use time_bucket if TimescaleDB, otherwise manual aggregation
                query = f"""
                    SELECT 
                        date_trunc('second', timestamp) - 
                            (EXTRACT(EPOCH FROM timestamp)::integer % {interval_seconds}) * interval '1 second' as bucket,
                        (array_agg(price ORDER BY timestamp ASC))[1] as open,
                        MAX(price) as high,
                        MIN(price) as low,
                        (array_agg(price ORDER BY timestamp DESC))[1] as close,
                        SUM(volume) as volume,
                        COUNT(*) as tick_count
                    FROM ticks
                    WHERE symbol = $1 AND timestamp >= $2
                    {"AND timestamp <= $3" if end_time else ""}
                    GROUP BY bucket
                    ORDER BY bucket ASC
                """
                
                params = [symbol, start_time]
                if end_time:
                    params.append(end_time)
                
                rows = await conn.fetch(query, *params)
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get OHLCV: {e}")
            return []
    
    async def get_portfolio_history(self, limit: int = 100) -> List[Dict]:
        """Get portfolio snapshot history."""
        if not self.is_connected:
            return []
        
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM portfolio_snapshots
                    ORDER BY timestamp DESC
                    LIMIT $1
                    """,
                    limit
                )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get portfolio history: {e}")
            return []
    
    async def insert_portfolio_snapshot(self, snapshot: Dict) -> bool:
        """Insert portfolio snapshot."""
        if not self.is_connected:
            return False
        
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO portfolio_snapshots 
                    (total_equity, cash_balance, positions, daily_pnl, total_pnl,
                     sharpe_ratio, sortino_ratio, max_drawdown, win_rate, trade_count)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    """,
                    snapshot['total_equity'], snapshot['cash_balance'],
                    json.dumps(snapshot.get('positions', {})),
                    snapshot.get('daily_pnl', 0), snapshot.get('total_pnl', 0),
                    snapshot.get('sharpe_ratio'), snapshot.get('sortino_ratio'),
                    snapshot.get('max_drawdown'), snapshot.get('win_rate'),
                    snapshot.get('trade_count', 0)
                )
            return True
        except Exception as e:
            logger.error(f"Failed to insert portfolio snapshot: {e}")
            return False
    
    async def log_session_event(
        self,
        session_id: str,
        event_type: str,
        event_data: Dict = None,
    ) -> bool:
        """Log session event."""
        if not self.is_connected:
            return False
        
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO session_logs (session_id, event_type, event_data)
                    VALUES ($1, $2, $3)
                    """,
                    session_id, event_type, json.dumps(event_data) if event_data else None
                )
            return True
        except Exception as e:
            logger.error(f"Failed to log session event: {e}")
            return False


# Singleton instance
_db_manager: Optional[DatabaseManager] = None


async def get_database_manager(config: DatabaseConfig = None) -> DatabaseManager:
    """Get or create the database manager singleton."""
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager(config)
        await _db_manager.connect()
    return _db_manager
