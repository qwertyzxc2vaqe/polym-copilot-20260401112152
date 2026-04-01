-- PostgreSQL Database Initialization Script
-- Phase 2 - Educational ASI Sandbox
--
-- This script creates all necessary tables and indexes for:
-- - Tick data storage (cold storage)
-- - Order book snapshots
-- - Trade history
-- - Paper trading logs
-- - ML model predictions
-- - Metrics and monitoring
--
-- Educational purpose only - paper trading simulation.

-- Enable extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- TICK DATA STORAGE
-- ============================================================

-- Core tick data table
CREATE TABLE IF NOT EXISTS ticks (
    id BIGSERIAL,
    symbol VARCHAR(10) NOT NULL,
    price DECIMAL(20, 8) NOT NULL,
    bid DECIMAL(20, 8),
    ask DECIMAL(20, 8),
    volume DECIMAL(20, 8),
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source VARCHAR(20) DEFAULT 'binance',
    PRIMARY KEY (id)
);

-- Indexes for efficient queries
CREATE INDEX IF NOT EXISTS idx_ticks_symbol_time ON ticks (symbol, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_ticks_timestamp ON ticks (timestamp DESC);

-- Order book snapshots with OFI metrics
CREATE TABLE IF NOT EXISTS order_book_snapshots (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    bids JSONB NOT NULL DEFAULT '[]',
    asks JSONB NOT NULL DEFAULT '[]',
    mid_price DECIMAL(20, 8),
    spread_bps DECIMAL(10, 4),
    ofi_1 DECIMAL(10, 6),
    ofi_5 DECIMAL(10, 6),
    ofi_10 DECIMAL(10, 6),
    ofi_20 DECIMAL(10, 6)
);

CREATE INDEX IF NOT EXISTS idx_ob_symbol_time ON order_book_snapshots (symbol, timestamp DESC);

-- Public trades tape
CREATE TABLE IF NOT EXISTS trades (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL,
    trade_id BIGINT,
    price DECIMAL(20, 8) NOT NULL,
    quantity DECIMAL(20, 8) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_buyer_maker BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_trades_symbol_time ON trades (symbol, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_trades_trade_id ON trades (trade_id);

-- ============================================================
-- PAPER TRADING LOGS
-- ============================================================

-- Paper trading orders
CREATE TABLE IF NOT EXISTS paper_orders (
    id BIGSERIAL PRIMARY KEY,
    order_id UUID DEFAULT uuid_generate_v4(),
    symbol VARCHAR(10) NOT NULL,
    side VARCHAR(4) NOT NULL CHECK (side IN ('buy', 'sell')),
    order_type VARCHAR(10) NOT NULL DEFAULT 'limit',
    price DECIMAL(20, 8) NOT NULL,
    quantity DECIMAL(20, 8) NOT NULL,
    status VARCHAR(20) DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    filled_at TIMESTAMPTZ,
    cancelled_at TIMESTAMPTZ,
    fill_price DECIMAL(20, 8),
    fill_quantity DECIMAL(20, 8),
    market_id VARCHAR(100),
    strategy VARCHAR(50) DEFAULT 'gabagool'
);

CREATE INDEX IF NOT EXISTS idx_paper_orders_symbol ON paper_orders (symbol, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_paper_orders_status ON paper_orders (status);

-- Paper trade executions (fills)
CREATE TABLE IF NOT EXISTS paper_trades (
    id BIGSERIAL PRIMARY KEY,
    order_id UUID,
    symbol VARCHAR(10) NOT NULL,
    side VARCHAR(4) NOT NULL,
    price DECIMAL(20, 8) NOT NULL,
    quantity DECIMAL(20, 8) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status VARCHAR(20) DEFAULT 'filled',
    pnl DECIMAL(20, 8) DEFAULT 0,
    fees DECIMAL(20, 8) DEFAULT 0,
    slippage_bps DECIMAL(10, 4) DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_paper_trades_timestamp ON paper_trades (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_paper_trades_symbol ON paper_trades (symbol);

-- Portfolio snapshots
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    balance DECIMAL(20, 8) NOT NULL,
    equity DECIMAL(20, 8) NOT NULL,
    unrealized_pnl DECIMAL(20, 8) DEFAULT 0,
    realized_pnl DECIMAL(20, 8) DEFAULT 0,
    positions JSONB DEFAULT '{}',
    daily_pnl DECIMAL(20, 8) DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_portfolio_timestamp ON portfolio_snapshots (timestamp DESC);

-- ============================================================
-- ML MODEL TRACKING
-- ============================================================

-- ML model predictions
CREATE TABLE IF NOT EXISTS ml_predictions (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    current_price DECIMAL(20, 8),
    predicted_price DECIMAL(20, 8),
    predicted_change DECIMAL(10, 6),
    confidence DECIMAL(5, 4),
    direction VARCHAR(10),
    actual_price DECIMAL(20, 8),
    actual_change DECIMAL(10, 6),
    is_correct BOOLEAN,
    model_version VARCHAR(50) DEFAULT 'v1'
);

CREATE INDEX IF NOT EXISTS idx_ml_pred_symbol_time ON ml_predictions (symbol, timestamp DESC);

-- Model training metrics
CREATE TABLE IF NOT EXISTS ml_training_logs (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    epoch INT,
    loss DECIMAL(20, 10),
    val_loss DECIMAL(20, 10),
    samples_count INT,
    training_time_ms INT,
    model_version VARCHAR(50)
);

-- ============================================================
-- METRICS AND MONITORING
-- ============================================================

-- Risk metrics
CREATE TABLE IF NOT EXISTS risk_metrics (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sharpe_ratio DECIMAL(10, 6),
    sortino_ratio DECIMAL(10, 6),
    max_drawdown DECIMAL(20, 8),
    var_95 DECIMAL(20, 8),
    var_99 DECIMAL(20, 8),
    volatility DECIMAL(10, 6),
    win_rate DECIMAL(5, 4),
    avg_trade_pnl DECIMAL(20, 8)
);

CREATE INDEX IF NOT EXISTS idx_risk_timestamp ON risk_metrics (timestamp DESC);

-- Funding rates
CREATE TABLE IF NOT EXISTS funding_rates (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    funding_rate DECIMAL(20, 10),
    mark_price DECIMAL(20, 8),
    next_funding_time TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_funding_symbol_time ON funding_rates (symbol, timestamp DESC);

-- System events log
CREATE TABLE IF NOT EXISTS system_events (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    event_type VARCHAR(50) NOT NULL,
    severity VARCHAR(20) DEFAULT 'info',
    message TEXT,
    details JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_events_timestamp ON system_events (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_events_type ON system_events (event_type);

-- ============================================================
-- HELPER FUNCTIONS
-- ============================================================

-- Function to calculate VWAP over a time window
CREATE OR REPLACE FUNCTION calculate_vwap(
    p_symbol VARCHAR(10),
    p_window_seconds INT DEFAULT 60
) RETURNS DECIMAL(20, 8) AS $$
DECLARE
    v_vwap DECIMAL(20, 8);
BEGIN
    SELECT 
        SUM(price * volume) / NULLIF(SUM(volume), 0)
    INTO v_vwap
    FROM ticks
    WHERE symbol = p_symbol
      AND timestamp > NOW() - (p_window_seconds || ' seconds')::INTERVAL;
    
    RETURN COALESCE(v_vwap, 0);
END;
$$ LANGUAGE plpgsql;

-- Function to get latest price
CREATE OR REPLACE FUNCTION get_latest_price(
    p_symbol VARCHAR(10)
) RETURNS DECIMAL(20, 8) AS $$
DECLARE
    v_price DECIMAL(20, 8);
BEGIN
    SELECT price INTO v_price
    FROM ticks
    WHERE symbol = p_symbol
    ORDER BY timestamp DESC
    LIMIT 1;
    
    RETURN COALESCE(v_price, 0);
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- DATA RETENTION (cleanup old data)
-- ============================================================

-- Function to archive old tick data
CREATE OR REPLACE FUNCTION archive_old_ticks(
    p_days_to_keep INT DEFAULT 7
) RETURNS INT AS $$
DECLARE
    v_deleted INT;
BEGIN
    DELETE FROM ticks
    WHERE timestamp < NOW() - (p_days_to_keep || ' days')::INTERVAL;
    
    GET DIAGNOSTICS v_deleted = ROW_COUNT;
    
    INSERT INTO system_events (event_type, message, details)
    VALUES ('data_archive', 'Archived old tick data', 
            jsonb_build_object('deleted_rows', v_deleted, 'days_kept', p_days_to_keep));
    
    RETURN v_deleted;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- INITIAL DATA
-- ============================================================

-- Insert system startup event
INSERT INTO system_events (event_type, severity, message, details)
VALUES (
    'database_init', 
    'info', 
    'Database initialized for Polym Educational Sandbox',
    jsonb_build_object(
        'version', '2.0',
        'phase', 'Phase 2',
        'purpose', 'educational_simulation'
    )
);

-- Log successful initialization
DO $$
BEGIN
    RAISE NOTICE 'Polym Educational Sandbox database initialized successfully.';
    RAISE NOTICE 'Tables created: ticks, trades, order_book_snapshots, paper_orders, paper_trades, portfolio_snapshots, ml_predictions, risk_metrics, funding_rates, system_events';
END $$;
