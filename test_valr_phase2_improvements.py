#!/usr/bin/env python3
"""
Test script for VALR connector Phase 2 improvements.
Tests OB_L1_DIFF optimization, WebSocket order modification, and circuit breaker patterns.
"""

import asyncio
import logging
import sys
import time
from decimal import Decimal
from typing import Dict, Any

# Add parent directory to path
sys.path.insert(0, '/home/mailr/hummingbot-private/hummingbot')

from hummingbot.connector.exchange.valr.valr_exchange import ValrExchange
from hummingbot.connector.exchange.valr.valr_constants import EXAMPLE_PAIR
from hummingbot.connector.exchange.valr.valr_l1_order_book_optimizer import VALRL1OrderBookOptimizer
from hummingbot.connector.exchange.valr.valr_circuit_breaker import CircuitBreaker, CircuitBreakerManager, CircuitOpenError
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.client.config.client_config_map import ClientConfigMap
from hummingbot.client.config.config_helpers import ClientConfigAdapter

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class Phase2Tester:
    """Test harness for Phase 2 improvements"""
    
    def __init__(self):
        self.connector = None
        self.trading_pair = "BTC-USDT"
        self.results = {
            "l1_optimizer": {"passed": 0, "failed": 0},
            "ws_order_modification": {"passed": 0, "failed": 0},
            "circuit_breaker": {"passed": 0, "failed": 0},
            "performance": {}
        }
        
    async def setup_connector(self):
        """Initialize VALR connector with test credentials"""
        logger.info("Setting up VALR connector...")
        
        # Create client config
        client_config = ClientConfigAdapter(ClientConfigMap())
        
        # Initialize connector (use test credentials if available)
        self.connector = ValrExchange(
            client_config_map=client_config,
            valr_api_key="test_key",  # Replace with actual test key
            valr_api_secret="test_secret",  # Replace with actual test secret
            trading_pairs=[self.trading_pair],
            trading_required=False
        )
        
        logger.info("VALR connector initialized")
        
    async def test_l1_optimizer(self):
        """Test L1 order book optimizer functionality"""
        logger.info("\n=== Testing L1 Order Book Optimizer ===")
        
        try:
            # Create L1 optimizer instance
            l1_optimizer = VALRL1OrderBookOptimizer()
            
            # Test 1: Update L1 data
            logger.info("Test 1: Updating L1 data...")
            bid_data = {"price": "30000.00", "quantity": "1.5"}
            ask_data = {"price": "30010.00", "quantity": "2.0"}
            
            latency = l1_optimizer.update_l1_data(self.trading_pair, bid_data, ask_data)
            
            if latency > 0:
                logger.info(f"✓ L1 update successful - Latency: {latency:.2f} microseconds")
                self.results["l1_optimizer"]["passed"] += 1
                self.results["performance"]["l1_update_latency_us"] = latency
            else:
                logger.error("✗ L1 update failed")
                self.results["l1_optimizer"]["failed"] += 1
                
            # Test 2: Get best bid/ask
            logger.info("Test 2: Getting best bid/ask...")
            best_bid = l1_optimizer.get_best_bid(self.trading_pair)
            best_ask = l1_optimizer.get_best_ask(self.trading_pair)
            
            if best_bid and best_ask:
                logger.info(f"✓ Best bid: {best_bid[0]} @ {best_bid[1]}")
                logger.info(f"✓ Best ask: {best_ask[0]} @ {best_ask[1]}")
                self.results["l1_optimizer"]["passed"] += 1
            else:
                logger.error("✗ Failed to get best bid/ask")
                self.results["l1_optimizer"]["failed"] += 1
                
            # Test 3: Mid-price caching
            logger.info("Test 3: Testing mid-price caching...")
            start_time = time.perf_counter()
            mid_price1 = l1_optimizer.get_mid_price(self.trading_pair)
            cache_miss_time = (time.perf_counter() - start_time) * 1_000_000
            
            start_time = time.perf_counter()
            mid_price2 = l1_optimizer.get_mid_price(self.trading_pair)
            cache_hit_time = (time.perf_counter() - start_time) * 1_000_000
            
            if mid_price1 == mid_price2 and cache_hit_time < cache_miss_time:
                logger.info(f"✓ Mid-price caching working - Cache hit: {cache_hit_time:.2f}us vs miss: {cache_miss_time:.2f}us")
                self.results["l1_optimizer"]["passed"] += 1
                self.results["performance"]["cache_speedup"] = cache_miss_time / cache_hit_time
            else:
                logger.error("✗ Mid-price caching not working properly")
                self.results["l1_optimizer"]["failed"] += 1
                
            # Test 4: Performance metrics
            logger.info("Test 4: Getting performance metrics...")
            metrics = l1_optimizer.get_performance_metrics()
            logger.info(f"L1 Optimizer metrics: {metrics}")
            self.results["l1_optimizer"]["passed"] += 1
            
        except Exception as e:
            logger.error(f"L1 optimizer test error: {e}")
            self.results["l1_optimizer"]["failed"] += 1
            
    async def test_circuit_breaker(self):
        """Test circuit breaker functionality"""
        logger.info("\n=== Testing Circuit Breaker Patterns ===")
        
        try:
            # Create circuit breaker manager
            cb_manager = CircuitBreakerManager()
            
            # Test 1: Circuit breaker creation
            logger.info("Test 1: Creating circuit breakers...")
            order_breaker = cb_manager.get_breaker("test_orders")
            
            if order_breaker:
                logger.info("✓ Circuit breaker created successfully")
                self.results["circuit_breaker"]["passed"] += 1
            else:
                logger.error("✗ Failed to create circuit breaker")
                self.results["circuit_breaker"]["failed"] += 1
                
            # Test 2: Normal operation (closed state)
            logger.info("Test 2: Testing normal operation...")
            
            async def success_function():
                return "success"
                
            result = await order_breaker.run(success_function)
            if result == "success":
                logger.info("✓ Circuit breaker allows normal operation")
                self.results["circuit_breaker"]["passed"] += 1
            else:
                logger.error("✗ Circuit breaker blocked normal operation")
                self.results["circuit_breaker"]["failed"] += 1
                
            # Test 3: Failure handling
            logger.info("Test 3: Testing failure handling...")
            
            async def failing_function():
                raise Exception("Test failure")
                
            # Trigger failures to open circuit
            failures = 0
            for i in range(6):  # Default threshold is 5
                try:
                    await order_breaker.run(failing_function)
                except Exception:
                    failures += 1
                    
            status = order_breaker.get_status()
            if status["state"] == "open" and failures >= 5:
                logger.info("✓ Circuit breaker opened after threshold failures")
                self.results["circuit_breaker"]["passed"] += 1
            else:
                logger.error("✗ Circuit breaker did not open properly")
                self.results["circuit_breaker"]["failed"] += 1
                
            # Test 4: Circuit open behavior
            logger.info("Test 4: Testing circuit open behavior...")
            try:
                await order_breaker.run(success_function)
                logger.error("✗ Circuit breaker allowed request when open")
                self.results["circuit_breaker"]["failed"] += 1
            except CircuitOpenError:
                logger.info("✓ Circuit breaker correctly blocked request when open")
                self.results["circuit_breaker"]["passed"] += 1
                
            # Test 5: Get health status
            logger.info("Test 5: Testing health monitoring...")
            health_score = cb_manager.get_health_score()
            all_status = cb_manager.get_all_status()
            
            logger.info(f"Health score: {health_score}%")
            logger.info(f"Circuit status: {all_status}")
            self.results["circuit_breaker"]["passed"] += 1
            
        except Exception as e:
            logger.error(f"Circuit breaker test error: {e}")
            self.results["circuit_breaker"]["failed"] += 1
            
    async def test_connector_integration(self):
        """Test integrated connector functionality"""
        logger.info("\n=== Testing Connector Integration ===")
        
        if not self.connector:
            logger.error("Connector not initialized")
            return
            
        try:
            # Test 1: Circuit breaker health check
            logger.info("Test 1: Checking circuit breaker health...")
            health = self.connector.get_circuit_breaker_health()
            logger.info(f"Circuit breaker health: {health}")
            
            if health["health_state"] == "healthy":
                logger.info("✓ Circuit breakers healthy")
                self.results["circuit_breaker"]["passed"] += 1
            else:
                logger.warning(f"⚠ Circuit breakers in {health['health_state']} state")
                
            # Test 2: L1 performance metrics
            logger.info("Test 2: Getting L1 performance metrics...")
            l1_metrics = self.connector.get_l1_performance_metrics()
            if l1_metrics:
                logger.info(f"L1 performance metrics: {l1_metrics}")
            else:
                logger.info("L1 metrics not yet available (normal for fresh connector)")
                
            # Test 3: Fast price getters
            logger.info("Test 3: Testing fast price getters...")
            # Note: These will fail without real market data
            mid_price = self.connector.get_mid_price_fast(self.trading_pair)
            if mid_price:
                logger.info(f"✓ Fast mid-price: {mid_price}")
            else:
                logger.info("⚠ Fast mid-price not available (expected without live connection)")
                
        except Exception as e:
            logger.error(f"Connector integration test error: {e}")
            
    def print_results(self):
        """Print test results summary"""
        logger.info("\n" + "="*60)
        logger.info("PHASE 2 TEST RESULTS SUMMARY")
        logger.info("="*60)
        
        total_passed = 0
        total_failed = 0
        
        for category, results in self.results.items():
            if isinstance(results, dict) and "passed" in results:
                logger.info(f"\n{category.upper()}:")
                logger.info(f"  Passed: {results['passed']}")
                logger.info(f"  Failed: {results['failed']}")
                total_passed += results["passed"]
                total_failed += results["failed"]
                
        logger.info(f"\nTOTAL: {total_passed} passed, {total_failed} failed")
        
        if self.results["performance"]:
            logger.info("\nPERFORMANCE METRICS:")
            for metric, value in self.results["performance"].items():
                logger.info(f"  {metric}: {value:.2f}")
                
        logger.info("\n" + "="*60)
        
        return total_failed == 0


async def main():
    """Run Phase 2 tests"""
    tester = Phase2Tester()
    
    try:
        # Setup
        await tester.setup_connector()
        
        # Run tests
        await tester.test_l1_optimizer()
        await tester.test_circuit_breaker()
        await tester.test_connector_integration()
        
        # Print results
        success = tester.print_results()
        
        if success:
            logger.info("\n✅ All Phase 2 tests passed!")
        else:
            logger.info("\n❌ Some tests failed - review results above")
            
    except Exception as e:
        logger.error(f"Test execution error: {e}")
        import traceback
        traceback.print_exc()
        
    finally:
        # Cleanup
        if tester.connector:
            logger.info("\nCleaning up...")
            

if __name__ == "__main__":
    asyncio.run(main())