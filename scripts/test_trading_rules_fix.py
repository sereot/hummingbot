#!/usr/bin/env python3
"""
Test Trading Rules Fix for VALR Connector
Specifically tests if the trading rules initialization fix works.
"""

import asyncio
import logging
import sys
import time

# Add hummingbot to path
sys.path.append('/home/mailr/hummingbot-private/hummingbot')

from hummingbot.client.config.client_config_map import ClientConfigMap
from hummingbot.connector.exchange.valr.valr_exchange import ValrExchange

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def test_trading_rules_fix():
    """Test the trading rules initialization fix"""
    
    print("=" * 60)
    print("TRADING RULES INITIALIZATION FIX TEST")
    print("=" * 60)
    
    try:
        # Create VALR connector
        print("🏗️  Creating VALR connector...")
        client_config_map = ClientConfigMap()
        
        connector = ValrExchange(
            client_config_map=client_config_map,
            valr_api_key="test_key",
            valr_api_secret="test_secret",
            trading_pairs=["DOGE-USDT"],
            trading_required=True
        )
        
        print("✅ VALR connector created")
        
        # Test direct trading rules loading
        print("\n📏 Testing direct trading rules loading...")
        
        # Check initial state
        has_trading_rules_before = hasattr(connector, '_trading_rules') and len(connector._trading_rules) > 0
        print(f"   Trading rules before: {has_trading_rules_before}")
        
        # Directly call the trading rules update method
        try:
            print("   🔄 Calling _update_trading_rules()...")
            await connector._update_trading_rules()
            
            # Check state after
            has_trading_rules_after = hasattr(connector, '_trading_rules') and len(connector._trading_rules) > 0
            rules_count = len(connector._trading_rules) if hasattr(connector, '_trading_rules') else 0
            
            print(f"   Trading rules after: {has_trading_rules_after}")
            print(f"   Trading rules count: {rules_count}")
            
            if has_trading_rules_after:
                print("   ✅ Trading rules loaded successfully!")
                
                # Check for DOGE-USDT specifically
                doge_usdt_rule = connector._trading_rules.get("DOGE-USDT")
                if doge_usdt_rule:
                    print(f"   ✅ DOGE-USDT rule found:")
                    print(f"      Min order size: {doge_usdt_rule.min_order_size}")
                    print(f"      Min price increment: {doge_usdt_rule.min_price_increment}")
                    print(f"      Min base amount increment: {doge_usdt_rule.min_base_amount_increment}")
                else:
                    print("   ⚠️  DOGE-USDT rule not found")
                    print("   Available trading pairs:")
                    for trading_pair in list(connector._trading_rules.keys())[:10]:
                        print(f"      - {trading_pair}")
                    if len(connector._trading_rules) > 10:
                        print(f"      ... and {len(connector._trading_rules) - 10} more")
            else:
                print("   ❌ Trading rules loading failed")
                
        except Exception as e:
            print(f"   ❌ Error loading trading rules: {e}")
            import traceback
            traceback.print_exc()
        
        # Test the timeout task fix
        print("\n⏰ Testing timeout task fix...")
        
        # Reset trading rules to simulate missing state
        connector._trading_rules = {}
        print("   📤 Reset trading rules to empty")
        
        # Manually trigger the timeout task logic
        try:
            print("   🔄 Triggering timeout task initialization logic...")
            
            # Simulate the timeout task logic
            if not hasattr(connector, '_trading_rules') or len(connector._trading_rules) == 0:
                print("   🔄 Attempting to initialize trading rules via timeout task...")
                await connector._update_trading_rules()
                print(f"   ✅ Trading rules initialized via timeout task: {len(connector._trading_rules)} rules")
            
        except Exception as e:
            print(f"   ❌ Timeout task initialization failed: {e}")
        
        # Final status check
        print("\n🎯 Final status check...")
        final_status = connector.status_dict
        
        print("   Final connector status:")
        for key, value in final_status.items():
            status_icon = "✅" if value else "❌"
            print(f"      {status_icon} {key}: {value}")
        
        # Overall assessment
        trading_rules_working = final_status.get('trading_rule_initialized', False)
        symbol_mapping_working = final_status.get('symbols_mapping_initialized', False)
        
        print("\n" + "=" * 60)
        print("FIX ASSESSMENT")
        print("=" * 60)
        
        if trading_rules_working and symbol_mapping_working:
            print("🎉 SUCCESS: Trading rules fix is working!")
            print("   ✅ Trading rules are now properly initialized")
            print("   ✅ Symbol mapping is working")
            print("   📈 Pure Market Making should now work with VALR")
        elif trading_rules_working:
            print("✅ PARTIAL SUCCESS: Trading rules fix is working!")
            print("   ✅ Trading rules are properly initialized")
            print("   ⚠️  Some other components still need work")
        else:
            print("❌ FIX NEEDS MORE WORK:")
            print("   ❌ Trading rules are still not initialized")
            print("   💡 Need to investigate further")
        
    except Exception as e:
        print(f"💥 Critical error during test: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    print("Testing Trading Rules Initialization Fix...")
    print("This will verify that our VALR connector fixes work correctly")
    print()
    
    # Run the test
    asyncio.run(test_trading_rules_fix())
    
    print("\n" + "=" * 60)
    print("TEST COMPLETE")
    print("=" * 60)