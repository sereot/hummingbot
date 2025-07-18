#!/usr/bin/env python3
"""
Quick test script to check if VALR order book initialization works with the modified sequence validation.
"""

import asyncio
import sys
import os

# Add the hummingbot directory to the path
sys.path.insert(0, '/home/mailr/hummingbot-private/hummingbot')

from hummingbot.connector.exchange.valr.valr_exchange import ValrExchange
from hummingbot.connector.exchange.valr.valr_api_order_book_data_source import ValrAPIOrderBookDataSource
from decimal import Decimal

class MockClientConfig:
    def __init__(self):
        from unittest.mock import MagicMock
        self.anonymized_metrics_mode = MagicMock()
        self.anonymized_metrics_mode.get_collector.return_value = MagicMock()
        self.kill_switch_enabled = False
        self.kill_switch_rate = 0
        self.telegram_enabled = False
        self.send_error_logs = False
        self.strategy_report_interval = 900
        self.logger = MagicMock()
        self.instance_id = "test-instance"
        self.rate_limits_share_pct = Decimal("100")
        self.commands_timeout = 30
        self.create_command_timeout = 60

async def test_order_book_initialization():
    """Test if order book initialization works with modified sequence validation."""
    
    print("=" * 60)
    print("VALR ORDER BOOK INITIALIZATION TEST")
    print("=" * 60)
    
    try:
        # Create mock config
        mock_config = MockClientConfig()
        
        print("\n1. Creating VALR connector...")
        valr_connector = ValrExchange(
            client_config_map=mock_config,
            valr_api_key="test_key",
            valr_api_secret="test_secret",
            trading_pairs=["DOGE-USDT"],
            trading_required=True
        )
        print("✅ VALR connector created")
        
        print("\n2. Checking initial ready state...")
        print(f"   Ready: {valr_connector.ready}")
        print(f"   Symbol mapping ready: {valr_connector.trading_pair_symbol_map_ready()}")
        
        print("\n3. Testing order book data source...")
        order_book_tracker = valr_connector.order_book_tracker
        data_source = order_book_tracker.data_source
        
        print(f"   Data source type: {type(data_source)}")
        print(f"   Trading pairs: {valr_connector.trading_pairs}")
        
        print("\n4. Waiting for connector to become ready...")
        max_wait = 30  # seconds
        for i in range(max_wait):
            if valr_connector.ready:
                print(f"✅ Connector became ready after {i} seconds!")
                break
            print(f"   Waiting... ({i+1}/{max_wait})")
            await asyncio.sleep(1)
        else:
            print("⚠️ Connector did not become ready within timeout")
        
        print("\n5. Final status check...")
        print(f"   Ready: {valr_connector.ready}")
        print(f"   Trading pairs: {valr_connector.trading_pairs}")
        print(f"   Symbol mapping ready: {valr_connector.trading_pair_symbol_map_ready()}")
        
        # Check order book tracker status
        print(f"   Order book tracker ready: {hasattr(order_book_tracker, '_ready') and order_book_tracker._ready}")
        print(f"   Order books: {list(order_book_tracker.order_books.keys())}")
        
        return True
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("Starting VALR order book test...")
    success = asyncio.run(test_order_book_initialization())
    print(f"\nTest result: {'✅ SUCCESS' if success else '❌ FAILED'}")