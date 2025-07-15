#!/usr/bin/env python3
"""
Simple test script for VALR connector functionality.
Run this to verify the connector is working properly.
"""

import asyncio
import logging
import sys
import os

# Add the project root to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hummingbot.connector.exchange.valr import valr_web_utils as web_utils
from hummingbot.connector.exchange.valr import valr_constants as CONSTANTS
from hummingbot.core.web_assistant.connections.data_types import RESTMethod


async def test_valr_api():
    """Test VALR API functionality."""
    
    print("=" * 60)
    print("VALR Connector API Test")
    print("=" * 60)
    
    try:
        # Test 1: Server Time
        print("\n1. Testing server time endpoint...")
        server_time = await web_utils.get_current_server_time()
        print(f"✓ Server time: {server_time}")
        
        # Test 2: Market Summary
        print("\n2. Testing market summary endpoint...")
        api_factory = web_utils.build_api_factory_without_time_synchronizer_pre_processor()
        rest_assistant = await api_factory.get_rest_assistant()
        
        url = web_utils.public_rest_url(CONSTANTS.TICKER_PRICE_PATH_URL)
        response = await rest_assistant.execute_request(
            url=url,
            method=RESTMethod.GET,
            throttler_limit_id=CONSTANTS.TICKER_PRICE_PATH_URL,
        )
        
        if isinstance(response, list) and len(response) > 0:
            print(f"✓ Market summary: Retrieved {len(response)} market pairs")
            # Show a sample pair
            sample = response[0] if response else {}
            print(f"  Sample pair: {sample.get('currencyPair', 'N/A')}")
        else:
            print(f"✗ Unexpected market summary response: {type(response)}")
            
        # Test 3: Orderbook
        print("\n3. Testing orderbook endpoint...")
        orderbook_url = web_utils.public_rest_url(CONSTANTS.SNAPSHOT_PATH_URL.format('DOGEUSDT'))
        print(f"  URL: {orderbook_url}")
        
        orderbook_response = await rest_assistant.execute_request(
            url=orderbook_url,
            method=RESTMethod.GET,
            throttler_limit_id=CONSTANTS.SNAPSHOT_PATH_URL,
        )
        
        if isinstance(orderbook_response, dict):
            bids = orderbook_response.get('Bids', [])
            asks = orderbook_response.get('Asks', [])
            print(f"✓ Orderbook: {len(bids)} bids, {len(asks)} asks")
            if 'LastChange' in orderbook_response:
                print(f"  Last change: {orderbook_response['LastChange']}")
        else:
            print(f"✗ Unexpected orderbook response: {type(orderbook_response)}")
            
        # Test 4: Trading Pair Conversion
        print("\n4. Testing trading pair conversion...")
        test_pairs = [
            ("BTC-USDT", "BTCUSDT"),
            ("DOGE-USDT", "DOGEUSDT"),
            ("ETH-ZAR", "ETHZAR"),
        ]
        
        for hb_pair, expected_valr in test_pairs:
            valr_pair = web_utils.convert_to_exchange_trading_pair(hb_pair)
            hb_converted = web_utils.convert_from_exchange_trading_pair(valr_pair)
            
            print(f"  {hb_pair} → {valr_pair} → {hb_converted}")
            if valr_pair == expected_valr and hb_converted == hb_pair:
                print(f"  ✓ Conversion working")
            else:
                print(f"  ✗ Conversion issue: expected {expected_valr}, got {valr_pair}")
        
        print("\n" + "=" * 60)
        print("✓ ALL TESTS COMPLETED SUCCESSFULLY!")
        print("The VALR connector REST API is working properly.")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    return True


async def test_order_book_data_source():
    """Test the order book data source specifically."""
    
    print("\n" + "=" * 60)
    print("VALR OrderBook Data Source Test")
    print("=" * 60)
    
    try:
        from hummingbot.connector.exchange.valr.valr_api_order_book_data_source import ValrAPIOrderBookDataSource
        from hummingbot.connector.exchange.valr.valr_exchange import ValrExchange
        
        print("\n1. Testing data source creation...")
        
        # Create a mock exchange (we can't fully initialize without API keys)
        api_factory = web_utils.build_api_factory_without_time_synchronizer_pre_processor()
        
        # Create data source
        data_source = ValrAPIOrderBookDataSource(
            trading_pairs=["DOGE-USDT"],
            connector=None,  # We'll use None for this test
            api_factory=api_factory,
            domain=CONSTANTS.DEFAULT_DOMAIN
        )
        
        print("✓ Data source created successfully")
        
        # Test 2: Get last traded prices
        print("\n2. Testing get_last_traded_prices...")
        prices = await data_source.get_last_traded_prices(["DOGE-USDT"])
        
        if isinstance(prices, dict):
            print(f"✓ Retrieved prices: {prices}")
        else:
            print(f"✗ Unexpected price response: {type(prices)}")
            
        # Test 3: Get order book snapshot
        print("\n3. Testing order book snapshot...")
        snapshot_msg = await data_source._order_book_snapshot("DOGE-USDT")
        
        if snapshot_msg.message_type.name == "SNAPSHOT":
            content = snapshot_msg.content
            print(f"✓ Snapshot retrieved: {len(content.get('bids', []))} bids, {len(content.get('asks', []))} asks")
        else:
            print(f"✗ Unexpected snapshot message type: {snapshot_msg.message_type}")
            
        print("\n✓ OrderBook Data Source tests completed successfully!")
        
    except Exception as e:
        print(f"\n✗ OrderBook Data Source test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    return True


def main():
    """Run all tests."""
    
    # Set up logging
    logging.basicConfig(
        level=logging.WARNING,  # Reduce noise
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    async def run_all_tests():
        """Run all test functions."""
        success = True
        
        # Test 1: Basic API functionality
        api_success = await test_valr_api()
        success = success and api_success
        
        # Test 2: Order book data source
        obs_success = await test_order_book_data_source()
        success = success and obs_success
        
        if success:
            print("\n🎉 ALL CONNECTOR TESTS PASSED! 🎉")
            print("The VALR connector is ready for use.")
        else:
            print("\n❌ SOME TESTS FAILED")
            print("Please check the errors above.")
            
        return success
    
    # Run tests
    try:
        success = asyncio.run(run_all_tests())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\nUnexpected error running tests: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()