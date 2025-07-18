#!/usr/bin/env python3
"""
VALR Connector Diagnostic Script
Tests specific VALR connector initialization issues to identify why 
order_books_initialized and trading_rule_initialized are failing.
"""

import asyncio
import logging
import sys
import time
from decimal import Decimal

# Add hummingbot to path
sys.path.append('/home/mailr/hummingbot-private/hummingbot')

from hummingbot.client.config.config_helpers import read_system_configs_from_yml
from hummingbot.client.config.client_config_map import ClientConfigMap
from hummingbot.connector.exchange.valr.valr_exchange import ValrExchange
from hummingbot.connector.exchange.valr import valr_constants as CONSTANTS

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def test_valr_connector_initialization():
    """Test VALR connector initialization step by step"""
    
    print("=" * 70)
    print("VALR CONNECTOR INITIALIZATION DIAGNOSTIC")
    print("=" * 70)
    
    try:
        # 1. Setup basic configuration
        print("🔧 Step 1: Setting up configuration...")
        client_config_map = ClientConfigMap()
        
        # Load VALR credentials (you may need to update these)
        # Note: These should be your actual VALR API credentials
        # For testing, you can use test credentials or paper trading
        api_key = "test_api_key"  # Replace with your VALR API key
        api_secret = "test_api_secret"  # Replace with your VALR API secret
        
        print("✅ Configuration setup complete")
        
        # 2. Create VALR connector instance
        print("\n🏗️  Step 2: Creating VALR connector...")
        trading_pairs = ["DOGE-USDT"]  # Test with DOGE-USDT pair
        
        connector = ValrExchange(
            client_config_map=client_config_map,
            valr_api_key=api_key,
            valr_api_secret=api_secret,
            trading_pairs=trading_pairs,
            trading_required=True
        )
        
        print("✅ VALR connector created")
        
        # 3. Test basic properties
        print("\n📋 Step 3: Testing basic connector properties...")
        print(f"   Connector name: {connector.name}")
        print(f"   Domain: {connector.domain}")
        print(f"   Trading pairs: {connector.trading_pairs}")
        print(f"   Rate limits count: {len(connector.rate_limits_rules)}")
        print("✅ Basic properties working")
        
        # 4. Test symbol mapping readiness
        print("\n🗺️  Step 4: Testing symbol mapping...")
        symbol_map_ready = connector.trading_pair_symbol_map_ready()
        print(f"   Symbol mapping ready: {symbol_map_ready}")
        
        if not symbol_map_ready:
            print("   ⚠️  Symbol mapping not ready - this is expected before API calls")
        
        # 5. Test data source creation
        print("\n📊 Step 5: Testing data source creation...")
        try:
            order_book_data_source = connector._create_order_book_data_source()
            print(f"   ✅ Order book data source created: {type(order_book_data_source).__name__}")
        except Exception as e:
            print(f"   ❌ Order book data source creation failed: {e}")
        
        try:
            user_stream_data_source = connector._create_user_stream_data_source()
            print(f"   ✅ User stream data source created: {type(user_stream_data_source).__name__}")
        except Exception as e:
            print(f"   ❌ User stream data source creation failed: {e}")
        
        # 6. Test API connectivity
        print("\n🌐 Step 6: Testing API connectivity...")
        try:
            # Test basic connectivity
            response = await connector._api_get(path_url=CONSTANTS.PAIRS_PATH_URL)
            if response:
                print(f"   ✅ API connectivity working - got {len(response)} trading pairs")
                
                # Test symbol mapping initialization
                if isinstance(response, list) and len(response) > 0:
                    print("   🗺️  Initializing symbol mapping from API response...")
                    connector._initialize_trading_pair_symbols_from_exchange_info(response)
                    
                    # Check symbol mapping after initialization
                    symbol_map_ready_after = connector.trading_pair_symbol_map_ready()
                    print(f"   Symbol mapping ready after init: {symbol_map_ready_after}")
                    
                    if symbol_map_ready_after:
                        print("   ✅ Symbol mapping successfully initialized")
                    else:
                        print("   ❌ Symbol mapping initialization failed")
            else:
                print("   ❌ API returned empty response")
                
        except Exception as e:
            print(f"   ❌ API connectivity failed: {e}")
            print("   ℹ️  This might be due to invalid credentials or network issues")
        
        # 7. Test trading rules initialization
        print("\n📏 Step 7: Testing trading rules initialization...")
        try:
            # Check if trading rules are set
            has_trading_rules = hasattr(connector, '_trading_rules') and len(connector._trading_rules) > 0
            print(f"   Trading rules initialized: {has_trading_rules}")
            
            if has_trading_rules:
                print(f"   Trading rules count: {len(connector._trading_rules)}")
                
                # Check for DOGE-USDT specifically
                doge_usdt_rule = connector._trading_rules.get("DOGE-USDT")
                if doge_usdt_rule:
                    print(f"   ✅ DOGE-USDT trading rule found")
                    print(f"      Min order size: {doge_usdt_rule.min_order_size}")
                    print(f"      Min price increment: {doge_usdt_rule.min_price_increment}")
                    print(f"      Min base amount increment: {doge_usdt_rule.min_base_amount_increment}")
                else:
                    print("   ⚠️  DOGE-USDT trading rule not found")
            else:
                print("   ❌ Trading rules not initialized")
                
        except Exception as e:
            print(f"   ❌ Trading rules check failed: {e}")
        
        # 8. Test order book tracker
        print("\n📈 Step 8: Testing order book tracker...")
        try:
            has_order_book_tracker = hasattr(connector, '_order_book_tracker') and connector._order_book_tracker is not None
            print(f"   Order book tracker exists: {has_order_book_tracker}")
            
            if has_order_book_tracker:
                print("   ✅ Order book tracker initialized")
            else:
                print("   ❌ Order book tracker not initialized")
                
        except Exception as e:
            print(f"   ❌ Order book tracker check failed: {e}")
        
        # 9. Test final ready state
        print("\n🎯 Step 9: Testing final ready state...")
        try:
            final_ready = connector.ready
            status_dict = connector.status_dict
            
            print(f"   Final ready state: {final_ready}")
            print("   Status breakdown:")
            for key, value in status_dict.items():
                status_icon = "✅" if value else "❌"
                print(f"      {status_icon} {key}: {value}")
            
            # Wait a bit for async initialization
            print("\n   ⏳ Waiting 5 seconds for async initialization...")
            await asyncio.sleep(5)
            
            final_ready_after_wait = connector.ready
            status_dict_after_wait = connector.status_dict
            
            print(f"   Ready state after wait: {final_ready_after_wait}")
            print("   Status after wait:")
            for key, value in status_dict_after_wait.items():
                status_icon = "✅" if value else "❌"
                print(f"      {status_icon} {key}: {value}")
                
        except Exception as e:
            print(f"   ❌ Ready state check failed: {e}")
        
        # 10. Summary and recommendations
        print("\n" + "=" * 70)
        print("DIAGNOSTIC SUMMARY")
        print("=" * 70)
        
        try:
            final_status = connector.status_dict
            
            issues_found = []
            if not final_status.get('symbols_mapping_initialized', False):
                issues_found.append("Symbol mapping not initialized")
            if not final_status.get('order_books_initialized', False):
                issues_found.append("Order book not initialized")
            if not final_status.get('trading_rule_initialized', False):
                issues_found.append("Trading rules not initialized")
            
            if not issues_found:
                print("🎉 SUCCESS: All components initialized successfully!")
                print("   The VALR connector should work properly for Pure Market Making")
            else:
                print(f"⚠️  ISSUES FOUND: {len(issues_found)} problems detected")
                for i, issue in enumerate(issues_found, 1):
                    print(f"   {i}. {issue}")
                
                print("\n💡 RECOMMENDATIONS:")
                if "Symbol mapping not initialized" in issues_found:
                    print("   • Check VALR API connectivity and credentials")
                    print("   • Verify VALR API endpoints are accessible")
                
                if "Order book not initialized" in issues_found:
                    print("   • Check WebSocket connectivity to VALR")
                    print("   • Verify order book data source creation")
                
                if "Trading rules not initialized" in issues_found:
                    print("   • Check trading pairs API response format")
                    print("   • Verify DOGE-USDT pair is available on VALR")
        
        except Exception as e:
            print(f"❌ Error generating summary: {e}")
        
    except Exception as e:
        print(f"💥 Critical error during diagnostic: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    print("Starting VALR Connector Diagnostic...")
    print("This will test the specific issues preventing Pure Market Making from working")
    print()
    
    # Run the diagnostic
    asyncio.run(test_valr_connector_initialization())
    
    print("\n" + "=" * 70)
    print("DIAGNOSTIC COMPLETE")
    print("=" * 70)
    print("Use the recommendations above to fix any identified issues.")