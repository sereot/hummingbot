"""
Performance metrics collection for VALR connector HFT optimization.
"""
import asyncio
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional, Deque
import statistics
import psutil
import os


@dataclass
class OrderLatencyMetrics:
    """Track order operation latencies"""
    placement_latencies: Deque[float] = field(default_factory=lambda: deque(maxlen=1000))
    cancellation_latencies: Deque[float] = field(default_factory=lambda: deque(maxlen=1000))
    modification_latencies: Deque[float] = field(default_factory=lambda: deque(maxlen=1000))
    
    def record_placement(self, latency_ms: float):
        self.placement_latencies.append(latency_ms)
    
    def record_cancellation(self, latency_ms: float):
        self.cancellation_latencies.append(latency_ms)
    
    def record_modification(self, latency_ms: float):
        self.modification_latencies.append(latency_ms)
    
    def get_stats(self, latencies: Deque[float]) -> Dict[str, float]:
        """Calculate statistics for a latency collection"""
        if not latencies:
            return {"avg": 0, "p50": 0, "p95": 0, "p99": 0, "min": 0, "max": 0}
        
        sorted_latencies = sorted(latencies)
        n = len(sorted_latencies)
        
        return {
            "avg": statistics.mean(sorted_latencies),
            "p50": sorted_latencies[int(n * 0.5)],
            "p95": sorted_latencies[int(n * 0.95)] if n > 20 else sorted_latencies[-1],
            "p99": sorted_latencies[int(n * 0.99)] if n > 100 else sorted_latencies[-1],
            "min": sorted_latencies[0],
            "max": sorted_latencies[-1]
        }


@dataclass
class MessageRateMetrics:
    """Track message processing rates"""
    message_counts: Dict[str, Deque[int]] = field(default_factory=lambda: defaultdict(lambda: deque(maxlen=60)))
    last_minute_timestamp: float = field(default_factory=time.time)
    current_counts: Counter = field(default_factory=Counter)
    
    def record_message(self, message_type: str):
        """Record a processed message"""
        self.current_counts[message_type] += 1
        
        # Roll over to new minute if needed
        current_time = time.time()
        if current_time - self.last_minute_timestamp >= 1.0:
            for msg_type, count in self.current_counts.items():
                self.message_counts[msg_type].append(count)
            self.current_counts.clear()
            self.last_minute_timestamp = current_time
    
    def get_rate(self, message_type: str) -> float:
        """Get messages per second for a type"""
        if message_type not in self.message_counts:
            return 0.0
        
        counts = list(self.message_counts[message_type])
        if self.current_counts[message_type] > 0:
            counts.append(self.current_counts[message_type])
        
        if not counts:
            return 0.0
        
        return sum(counts) / len(counts)


@dataclass
class ConnectionHealthMetrics:
    """Track WebSocket connection health"""
    total_connections: int = 0
    successful_connections: int = 0
    failed_connections: int = 0
    normal_disconnects: int = 0
    abnormal_disconnects: int = 0
    last_connection_duration: float = 0
    connection_durations: Deque[float] = field(default_factory=lambda: deque(maxlen=100))
    
    def record_connection_attempt(self):
        self.total_connections += 1
    
    def record_connection_success(self):
        self.successful_connections += 1
    
    def record_connection_failure(self, duration: float, is_normal: bool = False):
        self.failed_connections += 1
        self.last_connection_duration = duration
        self.connection_durations.append(duration)
        
        if is_normal:
            self.normal_disconnects += 1
        else:
            self.abnormal_disconnects += 1
    
    def get_uptime_percentage(self) -> float:
        """Calculate uptime percentage"""
        if self.total_connections == 0:
            return 100.0
        
        return (self.successful_connections / self.total_connections) * 100
    
    def get_average_connection_duration(self) -> float:
        """Get average connection duration in seconds"""
        if not self.connection_durations:
            return 0.0
        
        return statistics.mean(self.connection_durations)


class PerformanceMetrics:
    """Comprehensive performance metrics for VALR connector"""
    
    def __init__(self):
        self.order_latencies = OrderLatencyMetrics()
        self.message_rates = MessageRateMetrics()
        self.connection_health = ConnectionHealthMetrics()
        self.error_counts = Counter()
        self.start_time = time.time()
        
        # Resource tracking
        self.process = psutil.Process(os.getpid())
        self.initial_memory = self.process.memory_info().rss / 1024 / 1024  # MB
        
        # Trading metrics
        self.orders_placed = 0
        self.orders_cancelled = 0
        self.orders_filled = 0
        self.orders_failed = 0
        
        # Performance tracking
        self._last_report_time = time.time()
        self._report_interval = 60.0  # Report every minute
    
    def record_order_placement(self, latency_ms: float, success: bool = True):
        """Record order placement metrics"""
        self.order_latencies.record_placement(latency_ms)
        if success:
            self.orders_placed += 1
        else:
            self.orders_failed += 1
    
    def record_order_cancellation(self, latency_ms: float, success: bool = True):
        """Record order cancellation metrics"""
        self.order_latencies.record_cancellation(latency_ms)
        if success:
            self.orders_cancelled += 1
    
    def record_order_fill(self):
        """Record order fill"""
        self.orders_filled += 1
    
    def record_message(self, message_type: str):
        """Record processed message"""
        self.message_rates.record_message(message_type)
    
    def record_error(self, error_type: str):
        """Record error occurrence"""
        self.error_counts[error_type] += 1
    
    def record_connection_attempt(self):
        """Record WebSocket connection attempt"""
        self.connection_health.record_connection_attempt()
    
    def record_connection_success(self):
        """Record WebSocket connection success"""
        self.connection_health.record_connection_success()
    
    def record_connection_failure(self, duration: float, is_normal: bool = False):
        """Record WebSocket connection failure"""
        self.connection_health.record_connection_failure(duration, is_normal)
    
    def get_memory_usage(self) -> float:
        """Get current memory usage in MB"""
        return self.process.memory_info().rss / 1024 / 1024
    
    def get_cpu_usage(self) -> float:
        """Get CPU usage percentage"""
        return self.process.cpu_percent()
    
    def get_performance_report(self) -> Dict:
        """Generate comprehensive performance report"""
        uptime = time.time() - self.start_time
        memory_current = self.get_memory_usage()
        memory_growth = memory_current - self.initial_memory
        
        # Order latency stats
        placement_stats = self.order_latencies.get_stats(self.order_latencies.placement_latencies)
        cancellation_stats = self.order_latencies.get_stats(self.order_latencies.cancellation_latencies)
        
        # Message rates
        message_rates = {
            msg_type: self.message_rates.get_rate(msg_type)
            for msg_type in ["trade", "orderbook", "balance", "order_update"]
        }
        
        # Trading performance
        total_orders = self.orders_placed + self.orders_failed
        success_rate = (self.orders_placed / total_orders * 100) if total_orders > 0 else 0
        
        report = {
            "uptime_seconds": uptime,
            "latency": {
                "placement_ms": placement_stats,
                "cancellation_ms": cancellation_stats,
            },
            "throughput": {
                "messages_per_second": sum(message_rates.values()),
                "message_breakdown": message_rates,
                "orders_per_second": self.orders_placed / uptime if uptime > 0 else 0,
            },
            "reliability": {
                "connection_uptime_pct": self.connection_health.get_uptime_percentage(),
                "order_success_rate_pct": success_rate,
                "total_errors": sum(self.error_counts.values()),
                "error_breakdown": dict(self.error_counts),
                "avg_connection_duration_sec": self.connection_health.get_average_connection_duration(),
            },
            "trading": {
                "orders_placed": self.orders_placed,
                "orders_cancelled": self.orders_cancelled,
                "orders_filled": self.orders_filled,
                "orders_failed": self.orders_failed,
                "fill_rate_pct": (self.orders_filled / self.orders_placed * 100) if self.orders_placed > 0 else 0,
            },
            "resources": {
                "memory_mb": memory_current,
                "memory_growth_mb": memory_growth,
                "cpu_percent": self.get_cpu_usage(),
            },
            "websocket": {
                "total_connections": self.connection_health.total_connections,
                "successful_connections": self.connection_health.successful_connections,
                "normal_disconnects": self.connection_health.normal_disconnects,
                "abnormal_disconnects": self.connection_health.abnormal_disconnects,
            }
        }
        
        return report
    
    def should_report(self) -> bool:
        """Check if it's time to generate a report"""
        return time.time() - self._last_report_time >= self._report_interval
    
    def mark_reported(self):
        """Mark that a report was generated"""
        self._last_report_time = time.time()
    
    def log_performance_summary(self, logger):
        """Log a performance summary"""
        if not self.should_report():
            return
        
        report = self.get_performance_report()
        
        logger.info(
            f"Performance Report - "
            f"Uptime: {report['uptime_seconds']:.0f}s | "
            f"Order Latency: {report['latency']['placement_ms']['p95']:.1f}ms (p95) | "
            f"Throughput: {report['throughput']['orders_per_second']:.1f} orders/s | "
            f"Success Rate: {report['reliability']['order_success_rate_pct']:.1f}% | "
            f"Memory: {report['resources']['memory_mb']:.1f}MB | "
            f"WS Uptime: {report['reliability']['connection_uptime_pct']:.1f}%"
        )
        
        self.mark_reported()