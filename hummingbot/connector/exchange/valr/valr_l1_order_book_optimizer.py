"""
VALR L1 Order Book Optimizer for High-Frequency Trading
Phase 2 Enhancement: Specialized handler for OB_L1_DIFF feed
"""
import asyncio
import time
from collections import deque
from decimal import Decimal
from typing import Dict, Optional, Tuple, Deque, Any
import logging

from hummingbot.core.data_type.common import OrderType, PriceType
from hummingbot.logger import HummingbotLogger


class VALRL1OrderBookOptimizer:
    """
    High-performance order book optimizer specifically for VALR's OB_L1_DIFF feed.
    Provides ultra-low latency access to best bid/ask prices and implements
    advanced caching strategies for HFT operations.
    """
    
    _logger: Optional[HummingbotLogger] = None
    
    def __init__(self, max_cache_size: int = 1000):
        """
        Initialize the L1 order book optimizer.
        
        Args:
            max_cache_size: Maximum number of L1 updates to cache
        """
        # L1 data storage - optimized for fast access
        self._best_bids: Dict[str, Tuple[Decimal, Decimal, float]] = {}  # price, quantity, timestamp
        self._best_asks: Dict[str, Tuple[Decimal, Decimal, float]] = {}  # price, quantity, timestamp
        
        # Mid-price cache for ultra-fast access
        self._mid_price_cache: Dict[str, Tuple[Decimal, float]] = {}  # mid_price, timestamp
        
        # Spread cache
        self._spread_cache: Dict[str, Tuple[Decimal, float]] = {}  # spread, timestamp
        
        # L1 update history for analysis
        self._l1_history: Dict[str, Deque[Dict[str, Any]]] = {}
        self._max_cache_size = max_cache_size
        
        # Performance metrics
        self._metrics = {
            "l1_updates_processed": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "avg_update_latency_us": 0.0,
            "total_update_time_us": 0.0,
            "fastest_update_us": float('inf'),
            "slowest_update_us": 0.0,
        }
        
        # Update frequency tracking
        self._update_timestamps: Dict[str, Deque[float]] = {}
        self._update_frequency_window = 60.0  # Track updates per minute
        
    @classmethod
    def logger(cls) -> HummingbotLogger:
        if cls._logger is None:
            cls._logger = logging.getLogger(__name__)
        return cls._logger
    
    def update_l1_data(self, trading_pair: str, bid_data: Optional[Dict], ask_data: Optional[Dict], 
                      sequence_number: Optional[int] = None) -> float:
        """
        Update L1 order book data with ultra-low latency.
        
        Args:
            trading_pair: The trading pair
            bid_data: Best bid data {"price": "100", "quantity": "1.5"}
            ask_data: Best ask data {"price": "101", "quantity": "2.0"}
            sequence_number: Optional sequence number for tracking
            
        Returns:
            Update latency in microseconds
        """
        start_time = time.perf_counter()
        
        try:
            # Update best bid
            if bid_data and bid_data.get("price") and bid_data.get("quantity"):
                bid_price = Decimal(bid_data["price"])
                bid_quantity = Decimal(bid_data["quantity"])
                if bid_price > 0 and bid_quantity > 0:
                    self._best_bids[trading_pair] = (bid_price, bid_quantity, time.time())
            
            # Update best ask
            if ask_data and ask_data.get("price") and ask_data.get("quantity"):
                ask_price = Decimal(ask_data["price"])
                ask_quantity = Decimal(ask_data["quantity"])
                if ask_price > 0 and ask_quantity > 0:
                    self._best_asks[trading_pair] = (ask_price, ask_quantity, time.time())
            
            # Invalidate dependent caches
            self._invalidate_caches(trading_pair)
            
            # Update history
            self._update_history(trading_pair, bid_data, ask_data, sequence_number)
            
            # Track update frequency
            self._track_update_frequency(trading_pair)
            
            # Calculate and record metrics
            latency_us = (time.perf_counter() - start_time) * 1_000_000
            self._update_metrics(latency_us)
            
            return latency_us
            
        except Exception as e:
            self.logger().error(f"Error updating L1 data for {trading_pair}: {e}")
            return -1.0
    
    def get_best_bid(self, trading_pair: str) -> Optional[Tuple[Decimal, Decimal]]:
        """
        Get best bid price and quantity with near-zero latency.
        
        Returns:
            Tuple of (price, quantity) or None if not available
        """
        bid_data = self._best_bids.get(trading_pair)
        if bid_data:
            return (bid_data[0], bid_data[1])  # price, quantity
        return None
    
    def get_best_ask(self, trading_pair: str) -> Optional[Tuple[Decimal, Decimal]]:
        """
        Get best ask price and quantity with near-zero latency.
        
        Returns:
            Tuple of (price, quantity) or None if not available
        """
        ask_data = self._best_asks.get(trading_pair)
        if ask_data:
            return (ask_data[0], ask_data[1])  # price, quantity
        return None
    
    def get_mid_price(self, trading_pair: str, force_refresh: bool = False) -> Optional[Decimal]:
        """
        Get mid-price with caching for ultra-low latency.
        
        Args:
            trading_pair: The trading pair
            force_refresh: Force recalculation instead of using cache
            
        Returns:
            Mid-price or None if not available
        """
        # Check cache first unless forced refresh
        if not force_refresh and trading_pair in self._mid_price_cache:
            cached_price, cache_time = self._mid_price_cache[trading_pair]
            # Cache valid for 100ms in HFT mode
            if time.time() - cache_time < 0.1:
                self._metrics["cache_hits"] += 1
                return cached_price
        
        self._metrics["cache_misses"] += 1
        
        # Calculate fresh mid-price
        bid_data = self._best_bids.get(trading_pair)
        ask_data = self._best_asks.get(trading_pair)
        
        if bid_data and ask_data:
            mid_price = (bid_data[0] + ask_data[0]) / 2
            self._mid_price_cache[trading_pair] = (mid_price, time.time())
            return mid_price
        
        return None
    
    def get_spread(self, trading_pair: str, force_refresh: bool = False) -> Optional[Decimal]:
        """
        Get spread with caching for ultra-low latency.
        
        Args:
            trading_pair: The trading pair
            force_refresh: Force recalculation instead of using cache
            
        Returns:
            Spread or None if not available
        """
        # Check cache first unless forced refresh
        if not force_refresh and trading_pair in self._spread_cache:
            cached_spread, cache_time = self._spread_cache[trading_pair]
            # Cache valid for 100ms in HFT mode
            if time.time() - cache_time < 0.1:
                return cached_spread
        
        # Calculate fresh spread
        bid_data = self._best_bids.get(trading_pair)
        ask_data = self._best_asks.get(trading_pair)
        
        if bid_data and ask_data:
            spread = ask_data[0] - bid_data[0]
            self._spread_cache[trading_pair] = (spread, time.time())
            return spread
        
        return None
    
    def get_update_frequency(self, trading_pair: str) -> float:
        """
        Get the update frequency (updates per second) for a trading pair.
        
        Returns:
            Updates per second
        """
        if trading_pair not in self._update_timestamps:
            return 0.0
        
        timestamps = self._update_timestamps[trading_pair]
        if len(timestamps) < 2:
            return 0.0
        
        # Calculate updates per second over the window
        time_range = timestamps[-1] - timestamps[0]
        if time_range > 0:
            return len(timestamps) / time_range
        
        return 0.0
    
    def get_l1_age(self, trading_pair: str) -> Optional[float]:
        """
        Get age of L1 data in milliseconds.
        
        Returns:
            Age in milliseconds or None if no data
        """
        bid_data = self._best_bids.get(trading_pair)
        ask_data = self._best_asks.get(trading_pair)
        
        if bid_data or ask_data:
            # Use the most recent update
            bid_time = bid_data[2] if bid_data else 0
            ask_time = ask_data[2] if ask_data else 0
            latest_time = max(bid_time, ask_time)
            return (time.time() - latest_time) * 1000
        
        return None
    
    def is_l1_stale(self, trading_pair: str, max_age_ms: float = 1000) -> bool:
        """
        Check if L1 data is stale.
        
        Args:
            trading_pair: The trading pair
            max_age_ms: Maximum age in milliseconds before considering stale
            
        Returns:
            True if stale, False otherwise
        """
        age = self.get_l1_age(trading_pair)
        return age is None or age > max_age_ms
    
    def get_performance_metrics(self) -> Dict[str, Any]:
        """Get performance metrics for monitoring."""
        avg_latency = 0.0
        if self._metrics["l1_updates_processed"] > 0:
            avg_latency = self._metrics["total_update_time_us"] / self._metrics["l1_updates_processed"]
        
        cache_hit_rate = 0.0
        total_cache_ops = self._metrics["cache_hits"] + self._metrics["cache_misses"]
        if total_cache_ops > 0:
            cache_hit_rate = self._metrics["cache_hits"] / total_cache_ops
        
        return {
            "l1_updates_processed": self._metrics["l1_updates_processed"],
            "avg_update_latency_us": avg_latency,
            "fastest_update_us": self._metrics["fastest_update_us"] if self._metrics["fastest_update_us"] != float('inf') else 0,
            "slowest_update_us": self._metrics["slowest_update_us"],
            "cache_hit_rate": cache_hit_rate,
            "cache_hits": self._metrics["cache_hits"],
            "cache_misses": self._metrics["cache_misses"],
        }
    
    def _invalidate_caches(self, trading_pair: str):
        """Invalidate dependent caches when L1 data updates."""
        self._mid_price_cache.pop(trading_pair, None)
        self._spread_cache.pop(trading_pair, None)
    
    def _update_history(self, trading_pair: str, bid_data: Optional[Dict], 
                       ask_data: Optional[Dict], sequence_number: Optional[int]):
        """Update L1 history for analysis."""
        if trading_pair not in self._l1_history:
            self._l1_history[trading_pair] = deque(maxlen=self._max_cache_size)
        
        self._l1_history[trading_pair].append({
            "timestamp": time.time(),
            "bid": bid_data,
            "ask": ask_data,
            "sequence": sequence_number
        })
    
    def _track_update_frequency(self, trading_pair: str):
        """Track update frequency for monitoring."""
        if trading_pair not in self._update_timestamps:
            self._update_timestamps[trading_pair] = deque(maxlen=1000)
        
        current_time = time.time()
        timestamps = self._update_timestamps[trading_pair]
        timestamps.append(current_time)
        
        # Remove old timestamps outside the window
        cutoff_time = current_time - self._update_frequency_window
        while timestamps and timestamps[0] < cutoff_time:
            timestamps.popleft()
    
    def _update_metrics(self, latency_us: float):
        """Update performance metrics."""
        self._metrics["l1_updates_processed"] += 1
        self._metrics["total_update_time_us"] += latency_us
        self._metrics["avg_update_latency_us"] = (
            self._metrics["total_update_time_us"] / self._metrics["l1_updates_processed"]
        )
        
        if latency_us < self._metrics["fastest_update_us"]:
            self._metrics["fastest_update_us"] = latency_us
        
        if latency_us > self._metrics["slowest_update_us"]:
            self._metrics["slowest_update_us"] = latency_us
    
    def clear_pair_data(self, trading_pair: str):
        """Clear all data for a specific trading pair."""
        self._best_bids.pop(trading_pair, None)
        self._best_asks.pop(trading_pair, None)
        self._mid_price_cache.pop(trading_pair, None)
        self._spread_cache.pop(trading_pair, None)
        self._l1_history.pop(trading_pair, None)
        self._update_timestamps.pop(trading_pair, None)
    
    def clear_all_data(self):
        """Clear all cached data."""
        self._best_bids.clear()
        self._best_asks.clear()
        self._mid_price_cache.clear()
        self._spread_cache.clear()
        self._l1_history.clear()
        self._update_timestamps.clear()