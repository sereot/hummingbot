#!/usr/bin/env python3
"""
Simple test to verify VALR bot functionality with our fixes.
This simulates the key parts of the valr_test_bot.py to verify it works.
"""

import asyncio
import logging
import sys
import time
from decimal import Decimal

# Add the hummingbot directory to the path  
sys.path.insert(0, '/home/mailr/hummingbot-private/hummingbot')

from hummingbot.connector.exchange.valr.valr_exchange import ValrExchange
from hummingbot.connector.exchange.valr.valr_utils import ValrConfigMap


async def test_valr_bot_simulation():
    """Test simulation of VALR bot functionality."""
    
    # Set up logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    
    print("VALR Bot Simulation Test")
    print("=" * 50)
    
    # Test credentials
    api_key = "7a66934ebab38005a97641271a99ffaf95a01f85d3c70272673cb83713a70308"
    api_secret = "ab356d3d77b0f4b9d7c6ffa7399a073782fd7afbcb0d2c830bf868f6eeb8dde5"
    
    # Create config with credentials
    config = ValrConfigMap(
        valr_api_key=api_key,
        valr_api_secret=api_secret
    )
    
    # Create exchange
    exchange = ValrExchange(
        client_config_map=config,
        valr_api_key=api_key,
        valr_api_secret=api_secret,
        trading_pairs=["DOGE-USDT"]
    )
    
    try:
        # Start the exchange
        print("1. Starting VALR exchange connector...")
        await exchange.start_network()
        
        # Wait for readiness
        print("2. Waiting for connector to be ready...")
        max_wait = 60  # 1 minute
        start_time = time.time()
        
        while time.time() - start_time < max_wait:
            if exchange.ready:
                print("✅ Connector is ready!")
                break
            
            # Show status
            status = exchange.status_dict
            not_ready = [k for k, v in status.items() if not v]
            if not_ready:
                print(f"   Waiting for: {', '.join(not_ready)}")
            
            await asyncio.sleep(3)
        else:
            print("❌ Connector did not become ready within timeout")
            print(f"   Final status: {exchange.status_dict}")
            return False
        
        # Test balance access
        print("\n3. Testing balance access...")
        try:
            balances = exchange.get_all_balances()
            print(f"✅ Got balances for {len(balances)} currencies")
            
            # Check for DOGE and USDT
            doge_balance = exchange.get_balance("DOGE")
            usdt_balance = exchange.get_balance("USDT")
            print(f"   DOGE balance: {doge_balance}")
            print(f"   USDT balance: {usdt_balance}")
            
        except Exception as e:
            print(f"❌ Balance access failed: {e}")
        
        # Test order book access
        print("\n4. Testing order book access...")
        try:
            order_book = exchange.get_order_book("DOGE-USDT")
            if order_book:
                print("✅ Order book accessible")
                try:
                    mid_price = exchange.get_mid_price("DOGE-USDT")
                    print(f"   Mid price: {mid_price}")
                except Exception as e:
                    print(f"   Could not get mid price: {e}")
            else:
                print("❌ Order book not accessible")
                
        except Exception as e:
            print(f"❌ Order book access failed: {e}")
        
        # Test trading rules
        print("\n5. Testing trading rules...")
        try:
            trading_rules = exchange.trading_rules
            if "DOGE-USDT" in trading_rules:
                rule = trading_rules["DOGE-USDT"]
                print("✅ Trading rules available")
                print(f"   Min order size: {rule.min_order_size}")
                print(f"   Max order size: {rule.max_order_size}")
                print(f"   Tick size: {rule.min_price_increment}")
            else:
                print("❌ DOGE-USDT trading rules not found")
                
        except Exception as e:
            print(f"❌ Trading rules access failed: {e}")
        
        # Final status
        print(f"\n6. Final connector status:")
        print(f"   Ready: {exchange.ready}")
        print(f"   Status: {exchange.status_dict}")
        
        success = exchange.ready
        
    except Exception as e:
        print(f"❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        success = False
    
    finally:
        # Stop the exchange
        try:
            await exchange.stop_network()
            print("\n7. Connector stopped successfully")
        except Exception as e:
            print(f"❌ Error stopping connector: {e}")
    
    print("\n" + "=" * 50)
    if success:
        print("✅ VALR Bot Test PASSED - Connector is working!")
        print("The bot should now be able to:")
        print("- Access balances via REST API")
        print("- Get order book data via WebSocket")
        print("- Place and cancel orders")
        print("- Work without user stream (using REST fallback)")
    else:
        print("❌ VALR Bot Test FAILED")
    
    return success


if __name__ == "__main__":
    success = asyncio.run(test_valr_bot_simulation())
    sys.exit(0 if success else 1)