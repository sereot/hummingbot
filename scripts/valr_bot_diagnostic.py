#!/usr/bin/env python3
"""
Diagnostic script to test ValrTestBot initialization exactly like Hummingbot does.
This will help identify why the bot fails during Hummingbot startup.
"""

import asyncio
import sys
import time
from unittest.mock import MagicMock

# Add the hummingbot directory to the path
sys.path.insert(0, '/home/mailr/hummingbot-private/hummingbot')

def test_bot_initialization():
    """Test bot initialization in the same way Hummingbot does."""
    
    print("=" * 60)
    print("VALR BOT INITIALIZATION DIAGNOSTIC TEST")
    print("=" * 60)
    
    try:
        print("\n1. Testing script import...")
        from scripts.valr_test_bot import ValrTestBot, ValrTestBotConfig
        print("✅ Script imports successfully")
        
        print("\n2. Testing markets attribute...")
        if hasattr(ValrTestBot, 'markets'):
            print(f"✅ Markets attribute exists: {ValrTestBot.markets}")
        else:
            print("❌ Markets attribute missing")
            return False
        
        print("\n3. Testing config creation...")
        config = ValrTestBotConfig()
        print(f"✅ Config created: {config.exchange}/{config.trading_pair}")
        
        print("\n4. Testing init_markets method...")
        ValrTestBot.init_markets(config)
        print(f"✅ Markets after init: {ValrTestBot.markets}")
        
        print("\n5. Testing mock connector creation...")
        # Create mock connector like Hummingbot does
        from hummingbot.connector.exchange.valr.valr_exchange import ValrExchange
        
        class MockClientConfig:
            def __init__(self):
                self.anonymized_metrics_mode = MagicMock()
                self.anonymized_metrics_mode.get_collector.return_value = MagicMock()
                self.kill_switch_enabled = False
                self.kill_switch_rate = 0
                self.telegram_enabled = False
                self.send_error_logs = False
                self.strategy_report_interval = 900
                self.logger = MagicMock()
                self.instance_id = "test-instance"
                from decimal import Decimal
                self.rate_limits_share_pct = Decimal("100")  # Add missing attribute
                self.commands_timeout = 30  # Add missing attribute
                self.create_command_timeout = 60  # Add missing attribute
        
        mock_config = MockClientConfig()
        
        # Try to create VALR connector like the bot would
        try:
            valr_connector = ValrExchange(
                client_config_map=mock_config,
                valr_api_key="test_key",
                valr_api_secret="test_secret",
                trading_pairs=["DOGE-USDT"],
                trading_required=True
            )
            print("✅ VALR connector created successfully")
            
            connectors = {"valr": valr_connector}
            
            print("\n6. Testing bot initialization...")
            # Test bot creation like Hummingbot does
            bot = ValrTestBot(connectors, config)
            print("✅ Bot initialized successfully")
            
            print("\n7. Testing bot attributes...")
            print(f"   Config: {hasattr(bot, 'config')}")
            print(f"   Markets: {hasattr(bot.__class__, 'markets')}")
            print(f"   Connectors: {len(bot.connectors) if hasattr(bot, 'connectors') else 'MISSING'}")
            
            print("\n✅ ALL TESTS PASSED!")
            print("Bot should work correctly in Hummingbot.")
            return True
            
        except Exception as e:
            print(f"❌ Connector creation failed: {e}")
            import traceback
            traceback.print_exc()
            return False
            
    except Exception as e:
        print(f"❌ Script import/setup failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_markets_attribute_persistence():
    """Test if markets attribute persists across different import methods."""
    
    print("\n" + "=" * 60)
    print("MARKETS ATTRIBUTE PERSISTENCE TEST")
    print("=" * 60)
    
    try:
        # Test 1: Direct import
        print("\n1. Direct import test...")
        from scripts.valr_test_bot import ValrTestBot
        print(f"   Markets: {getattr(ValrTestBot, 'markets', 'MISSING')}")
        
        # Test 2: Module import
        print("\n2. Module import test...")
        import importlib
        module = importlib.import_module('scripts.valr_test_bot')
        bot_class = getattr(module, 'ValrTestBot')
        print(f"   Markets: {getattr(bot_class, 'markets', 'MISSING')}")
        
        # Test 3: Getattr test like Hummingbot uses
        print("\n3. Getattr access test...")
        markets = getattr(bot_class, 'markets', None)
        if markets:
            print(f"✅ Markets accessible: {markets}")
            for exchange, pairs in markets.items():
                print(f"   Exchange: {exchange}, Pairs: {pairs}")
        else:
            print("❌ Markets not accessible via getattr")
        
        return True
        
    except Exception as e:
        print(f"❌ Markets persistence test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success1 = test_bot_initialization()
    success2 = test_markets_attribute_persistence()
    
    print("\n" + "=" * 60)
    print("DIAGNOSTIC SUMMARY")
    print("=" * 60)
    print(f"Bot Initialization: {'✅ PASS' if success1 else '❌ FAIL'}")
    print(f"Markets Persistence: {'✅ PASS' if success2 else '❌ FAIL'}")
    
    if success1 and success2:
        print("\n🎉 All tests passed! Bot should work in Hummingbot.")
    else:
        print("\n⚠️ Issues detected. See test results above.")