#!/usr/bin/env python3
"""
Test script to verify Hummingbot installation without starting the full CLI
"""

import sys
import traceback

def test_core_imports():
    """Test core Hummingbot components can be imported"""
    print("🧪 Testing core Hummingbot imports...")
    
    try:
        # Test core components
        from hummingbot.core.clock import Clock
        print("✅ Clock import successful")
        
        from hummingbot.core.data_type.order_book import OrderBook
        print("✅ OrderBook import successful")
        
        from hummingbot.strategy.strategy_base import StrategyBase
        print("✅ StrategyBase import successful")
        
        from hummingbot.connector.connector_base import ConnectorBase
        print("✅ ConnectorBase import successful")
        
        # Test paper trade connector
        from hummingbot.connector.exchange.paper_trade.paper_trade_exchange import PaperTradeExchange
        print("✅ PaperTradeExchange import successful")
        
        print("🎉 All core imports successful!")
        return True
        
    except Exception as e:
        print(f"❌ Import failed: {e}")
        traceback.print_exc()
        return False

def test_strategy_imports():
    """Test strategy imports"""
    print("\n🧪 Testing strategy imports...")
    
    try:
        from hummingbot.strategy.pure_market_making import PureMarketMakingStrategy
        print("✅ Pure Market Making strategy import successful")
        
        # Test if we can get strategy list
        from hummingbot.client.settings import STRATEGIES
        print(f"✅ Found {len(STRATEGIES)} available strategies")
        
        return True
        
    except Exception as e:
        print(f"❌ Strategy import failed: {e}")
        traceback.print_exc()
        return False

def test_basic_functionality():
    """Test basic functionality without full CLI"""
    print("\n🧪 Testing basic functionality...")
    
    try:
        # Test creating a paper trade exchange
        from hummingbot.connector.exchange.paper_trade.paper_trade_exchange import PaperTradeExchange
        
        exchange = PaperTradeExchange(
            ["BTC-USDT", "ETH-USDT"], 
            {"BTC": 1.0, "ETH": 10.0, "USDT": 10000.0}
        )
        print("✅ Created paper trade exchange")
        
        # Test basic exchange methods
        trading_pairs = exchange.trading_pairs
        print(f"✅ Trading pairs: {trading_pairs}")
        
        return True
        
    except Exception as e:
        print(f"❌ Basic functionality test failed: {e}")
        traceback.print_exc()
        return False

def main():
    """Run all tests"""
    print("🚀 Hummingbot Installation Test")
    print("=" * 50)
    
    # Test results
    core_ok = test_core_imports()
    strategy_ok = test_strategy_imports()
    basic_ok = test_basic_functionality()
    
    print("\n" + "=" * 50)
    print("📊 Test Results:")
    print(f"   Core imports: {'✅ PASS' if core_ok else '❌ FAIL'}")
    print(f"   Strategy imports: {'✅ PASS' if strategy_ok else '❌ FAIL'}")
    print(f"   Basic functionality: {'✅ PASS' if basic_ok else '❌ FAIL'}")
    
    if all([core_ok, strategy_ok, basic_ok]):
        print("\n🎉 Installation test PASSED! Hummingbot is ready for development.")
        print("\n💡 Next steps:")
        print("   1. Study existing strategies in hummingbot/strategy/")
        print("   2. Create your first custom strategy")
        print("   3. Test with paper trading")
        return 0
    else:
        print("\n❌ Installation test FAILED. Please check the errors above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())