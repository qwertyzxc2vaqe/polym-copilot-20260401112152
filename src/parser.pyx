# cython: language_level=3
# cython: boundscheck=False
# cython: wraparound=False
# cython: cdivision=True
"""
Cython-optimized JSON Parser for Binance WebSocket Data.

Phase 2 - Task 53: High-performance parser to reduce deserialization
latency from milliseconds to microseconds.

Educational purpose only - paper trading simulation.

Compile with: python setup_cython.py build_ext --inplace
"""

import json
from libc.stdlib cimport atof, atoll
from libc.string cimport strlen, strstr, memcpy

cdef class TickerData:
    """
    High-performance ticker data container.
    Uses C-level storage for numeric fields.
    """
    cdef public str symbol
    cdef public double price
    cdef public double bid
    cdef public double ask
    cdef public double volume
    cdef public long long timestamp
    cdef public str source
    
    def __init__(self, str symbol="", double price=0.0, double bid=0.0,
                 double ask=0.0, double volume=0.0, long long timestamp=0,
                 str source="binance"):
        self.symbol = symbol
        self.price = price
        self.bid = bid
        self.ask = ask
        self.volume = volume
        self.timestamp = timestamp
        self.source = source
    
    cpdef dict to_dict(self):
        """Convert to dictionary for serialization."""
        return {
            'symbol': self.symbol,
            'price': self.price,
            'bid': self.bid,
            'ask': self.ask,
            'volume': self.volume,
            'timestamp': self.timestamp,
            'source': self.source
        }
    
    @property
    def mid_price(self) -> float:
        """Calculate mid price from bid/ask."""
        return (self.bid + self.ask) / 2.0
    
    @property
    def spread(self) -> float:
        """Calculate bid/ask spread."""
        return self.ask - self.bid
    
    @property
    def spread_bps(self) -> float:
        """Calculate spread in basis points."""
        cdef double mid = self.mid_price
        if mid == 0.0:
            return 0.0
        return (self.spread / mid) * 10000.0


# Symbol mapping from Binance format
cdef dict SYMBOL_MAP = {
    'BTCUSDT': 'BTC',
    'ETHUSDT': 'ETH',
    'SOLUSDT': 'SOL',
    'XRPUSDT': 'XRP',
    'DOGEUSDT': 'DOGE',
    'BNBUSDT': 'BNB',
}


cpdef TickerData parse_ticker_fast(dict data):
    """
    Fast parser for Binance ticker WebSocket message.
    
    Expected format from Binance combined stream:
    {
        "e": "24hrTicker",
        "s": "BTCUSDT",
        "c": "42000.00",  # Close/last price
        "b": "41999.50",  # Best bid
        "a": "42000.50",  # Best ask
        "v": "50000.00",  # Volume
        "E": 1234567890123  # Event timestamp
    }
    """
    cdef str raw_symbol
    cdef str symbol
    cdef double price
    cdef double bid
    cdef double ask
    cdef double volume
    cdef long long timestamp
    
    # Extract and convert fields
    raw_symbol = data.get('s', '')
    symbol = SYMBOL_MAP.get(raw_symbol, raw_symbol.replace('USDT', ''))
    
    # Parse numeric strings to doubles
    price = float(data.get('c', '0'))
    bid = float(data.get('b', '0'))
    ask = float(data.get('a', '0'))
    volume = float(data.get('v', '0'))
    timestamp = int(data.get('E', 0))
    
    return TickerData(
        symbol=symbol,
        price=price,
        bid=bid,
        ask=ask,
        volume=volume,
        timestamp=timestamp,
        source='binance'
    )


cpdef TickerData parse_depth_update(dict data):
    """
    Fast parser for Binance depth/order book update.
    
    Expected format:
    {
        "e": "depthUpdate",
        "s": "BTCUSDT",
        "b": [["41999.50", "1.5"], ...],  # Bids
        "a": [["42000.50", "2.0"], ...],  # Asks
        "E": 1234567890123
    }
    """
    cdef str raw_symbol
    cdef str symbol
    cdef double best_bid = 0.0
    cdef double best_ask = 0.0
    cdef double bid_volume = 0.0
    cdef double ask_volume = 0.0
    cdef long long timestamp
    cdef list bids
    cdef list asks
    
    raw_symbol = data.get('s', '')
    symbol = SYMBOL_MAP.get(raw_symbol, raw_symbol.replace('USDT', ''))
    timestamp = int(data.get('E', 0))
    
    bids = data.get('b', [])
    asks = data.get('a', [])
    
    # Get best bid/ask (first in list)
    if bids and len(bids) > 0:
        best_bid = float(bids[0][0])
        bid_volume = float(bids[0][1])
    
    if asks and len(asks) > 0:
        best_ask = float(asks[0][0])
        ask_volume = float(asks[0][1])
    
    cdef double mid_price = (best_bid + best_ask) / 2.0 if best_bid > 0 and best_ask > 0 else 0.0
    cdef double total_volume = bid_volume + ask_volume
    
    return TickerData(
        symbol=symbol,
        price=mid_price,
        bid=best_bid,
        ask=best_ask,
        volume=total_volume,
        timestamp=timestamp,
        source='binance'
    )


cpdef list parse_order_book_levels(dict data, int levels=20):
    """
    Parse full order book with multiple levels.
    
    Returns list of tuples: [(price, quantity, side), ...]
    """
    cdef list result = []
    cdef list bids = data.get('bids', data.get('b', []))
    cdef list asks = data.get('asks', data.get('a', []))
    cdef int i
    cdef double price
    cdef double qty
    
    # Parse bid levels
    for i in range(min(levels, len(bids))):
        price = float(bids[i][0])
        qty = float(bids[i][1])
        result.append((price, qty, 'bid'))
    
    # Parse ask levels
    for i in range(min(levels, len(asks))):
        price = float(asks[i][0])
        qty = float(asks[i][1])
        result.append((price, qty, 'ask'))
    
    return result


cpdef dict calculate_order_flow_imbalance(list bids, list asks, int levels=5):
    """
    Calculate Order Flow Imbalance (OFI) from order book.
    
    OFI = (bid_volume - ask_volume) / (bid_volume + ask_volume)
    
    Returns dict with OFI metrics for different depth levels.
    """
    cdef dict result = {}
    cdef double bid_vol
    cdef double ask_vol
    cdef double ofi
    cdef int i
    cdef int depth
    
    for depth in [1, 3, 5, 10, levels]:
        if depth > levels:
            break
            
        bid_vol = 0.0
        ask_vol = 0.0
        
        for i in range(min(depth, len(bids))):
            bid_vol += float(bids[i][1])
        
        for i in range(min(depth, len(asks))):
            ask_vol += float(asks[i][1])
        
        total_vol = bid_vol + ask_vol
        if total_vol > 0:
            ofi = (bid_vol - ask_vol) / total_vol
        else:
            ofi = 0.0
        
        result[f'ofi_{depth}'] = ofi
        result[f'bid_vol_{depth}'] = bid_vol
        result[f'ask_vol_{depth}'] = ask_vol
    
    return result


cpdef TickerData parse_combined_stream(str raw_message):
    """
    Parse raw JSON message from Binance combined stream.
    
    Format: {"stream":"btcusdt@ticker","data":{...}}
    """
    cdef dict message = json.loads(raw_message)
    cdef str stream_name = message.get('stream', '')
    cdef dict data = message.get('data', {})
    
    if '@ticker' in stream_name:
        return parse_ticker_fast(data)
    elif '@depth' in stream_name:
        return parse_depth_update(data)
    else:
        # Unknown stream type, try ticker parse
        return parse_ticker_fast(data)


# Pure Python fallback functions for when Cython is not compiled
def parse_ticker_python(data: dict) -> dict:
    """
    Pure Python fallback parser.
    Use when Cython module is not compiled.
    """
    raw_symbol = data.get('s', '')
    symbol = SYMBOL_MAP.get(raw_symbol, raw_symbol.replace('USDT', ''))
    
    return {
        'symbol': symbol,
        'price': float(data.get('c', '0')),
        'bid': float(data.get('b', '0')),
        'ask': float(data.get('a', '0')),
        'volume': float(data.get('v', '0')),
        'timestamp': int(data.get('E', 0)),
        'source': 'binance'
    }
