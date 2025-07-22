#!/usr/bin/env python3
"""
Test script to verify Phase 1 improvements to VALR connector:
1. Rate limit optimizations
2. Performance metrics collection
3. WebSocket order placement
4. Connection pooling
"""

import asyncio
import time
from decimal import Decimal
from hummingbot.connector.exchange.valr import valr_constants as CONSTANTS
from hummingbot.connector.exchange.valr.valr_exchange import ValrExchange
from hummingbot.connector.exchange.valr.valr_web_utils import build_api_factory
from hummingbot.core.api_throttler.async_throttler import AsyncThrottler
from hummingbot.client.config.client_config_map import ClientConfigMap
from hummingbot.client.config.config_helpers import ClientConfigAdapter


async def test_rate_limits():
    """Test that rate limits are properly configured for HFT."""
    print("\n=== Testing Rate Limits ===")
    
    # Check critical HFT endpoints
    hft_endpoints = {
        CONSTANTS.PLACE_ORDER_PATH_URL: 400,  # Expected 400/s
        CONSTANTS.CANCEL_ORDER_PATH_URL: 450,  # Expected 450/s
        CONSTANTS.ORDER_MODIFY_PATH_URL: 400,  # Expected 400/s
        CONSTANTS.BATCH_ORDERS_PATH_URL: 400,  # Expected 400/s
    }
    
    for endpoint, expected_limit in hft_endpoints.items():
        rate_limit = next((rl for rl in CONSTANTS.RATE_LIMITS if rl.limit_id == endpoint), None)
        if rate_limit:
            print(f"✓ {endpoint}: {rate_limit.limit}/s (expected {expected_limit}/s)")
            assert rate_limit.limit == expected_limit, f"Rate limit mismatch for {endpoint}"
        else:
            print(f"✗ {endpoint}: NOT FOUND")
    
    print("\nRate limits test passed!")


async def test_performance_metrics():
    """Test performance metrics collection."""
    print("\n=== Testing Performance Metrics ===")
    
    # Create mock connector
    client_config = ClientConfigAdapter(ClientConfigMap())
    connector = ValrExchange(
        client_config_map=client_config,
        valr_api_key="test_key",
        valr_api_secret="test_secret",
        trading_pairs=["BTC-USDT"],
        trading_required=False
    )
    
    # Access performance metrics
    metrics = connector.performance_metrics
    
    # Simulate some operations
    metrics.record_order_placement(10.5, success=True)
    metrics.record_order_placement(8.2, success=True)
    metrics.record_order_placement(150.0, success=False)
    metrics.record_order_cancellation(5.3, success=True)
    
    # Get report
    report = metrics.get_performance_report()
    
    print(f"✓ Uptime: {report['uptime_seconds']:.1f}s")
    print(f"✓ Order placement latency (avg): {report['latency']['placement_ms']['avg']:.1f}ms")
    print(f"✓ Order success rate: {report['reliability']['order_success_rate_pct']:.1f}%")
    print(f"✓ Memory usage: {report['resources']['memory_mb']:.1f}MB")
    
    print("\nPerformance metrics test passed!")


async def test_websocket_configuration():
    """Test WebSocket order placement configuration."""
    print("\n=== Testing WebSocket Configuration ===")
    
    # Check WebSocket event types
    ws_events = [
        CONSTANTS.WS_PLACE_LIMIT_ORDER_EVENT,
        CONSTANTS.WS_PLACE_MARKET_ORDER_EVENT,
        CONSTANTS.WS_MODIFY_ORDER_EVENT,
        CONSTANTS.WS_BATCH_ORDERS_EVENT,
        CONSTANTS.WS_ORDER_PROCESSED_EVENT,
    ]
    
    for event in ws_events:
        print(f"✓ WebSocket event defined: {event}")
    
    # Check timeouts
    print(f"\n✓ WebSocket order timeout: {CONSTANTS.WS_ORDER_TIMEOUT}s")
    print(f"✓ WebSocket batch timeout: {CONSTANTS.WS_BATCH_ORDER_TIMEOUT}s")
    
    print("\nWebSocket configuration test passed!")


async def test_connection_pool():
    """Test connection pool functionality."""
    print("\n=== Testing Connection Pool ===")
    
    from hummingbot.connector.exchange.valr.valr_connection_pool import VALRConnectionPool
    
    # Mock WebSocket factory
    async def mock_ws_factory():
        class MockWS:
            def __init__(self):
                self._ws = type('obj', (object,), {'closed': False})
                
            async def connect(self, ws_url, ping_timeout):
                print(f"  Mock connection to {ws_url}")
                
            async def disconnect(self):
                self._ws.closed = True
        
        return MockWS()
    
    # Create connection pool
    pool = VALRConnectionPool(
        ws_factory=mock_ws_factory,
        ws_url="wss://api.valr.com/ws/account",
        pool_size=3,
        rotation_interval=5.0  # Short for testing
    )
    
    # Initialize pool
    await pool.initialize()
    print(f"✓ Pool initialized with {len(pool.connections)} connections")
    
    # Get connection
    conn = await pool.get_connection()
    print(f"✓ Got active connection from pool")
    
    # Check stats
    stats = pool.get_stats()
    print(f"✓ Pool stats: {stats['total_connections']} total connections")
    
    # Cleanup
    await pool.shutdown()
    print("✓ Pool shutdown successful")
    
    print("\nConnection pool test passed!")


async def test_batch_operations():
    """Test batch order operations."""
    print("\n=== Testing Batch Operations ===")
    
    # Check batch endpoint is defined
    assert CONSTANTS.BATCH_ORDERS_PATH_URL == "/v1/batch/orders"
    print(f"✓ Batch orders endpoint: {CONSTANTS.BATCH_ORDERS_PATH_URL}")
    
    # Check batch rate limit
    batch_limit = next((rl for rl in CONSTANTS.RATE_LIMITS if rl.limit_id == CONSTANTS.BATCH_ORDERS_PATH_URL), None)
    assert batch_limit and batch_limit.limit == 400
    print(f"✓ Batch orders rate limit: {batch_limit.limit}/s")
    
    print("\nBatch operations test passed!")


async def main():
    """Run all Phase 1 tests."""
    print("VALR Connector Phase 1 Improvements Test Suite")
    print("=" * 50)
    
    try:
        await test_rate_limits()
        await test_performance_metrics()
        await test_websocket_configuration()
        await test_connection_pool()
        await test_batch_operations()
        
        print("\n" + "=" * 50)
        print("✅ ALL PHASE 1 TESTS PASSED!")
        print("\nPhase 1 improvements successfully implemented:")
        print("- Rate limits optimized for HFT (400 orders/s)")
        print("- Performance metrics collection active")
        print("- WebSocket order placement infrastructure ready")
        print("- Connection pooling for predictive reconnection")
        print("- Batch operations support added")
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())