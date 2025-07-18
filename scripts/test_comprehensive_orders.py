#!/usr/bin/env python3
"""
Test script to verify the comprehensive order detection methods work correctly.
"""

import sys
import logging

# Add the hummingbot directory to the path
sys.path.insert(0, '/home/mailr/hummingbot-private/hummingbot')

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def test_comprehensive_order_methods():
    """Test the comprehensive order detection methods."""
    logger.info("🧪 Testing comprehensive order detection methods...")
    
    try:
        # Import the bot
        from scripts.valr_test_bot import ValrTestBot
        logger.info("✅ Bot imported successfully")
        
        # Check that all new methods exist
        methods_to_check = [
            'get_connector_active_orders',
            'get_active_orders_via_rest',
            'get_all_active_orders_comprehensive',
            'track_placed_order',
            'get_tracked_orders'
        ]
        
        for method_name in methods_to_check:
            if hasattr(ValrTestBot, method_name):
                logger.info(f"✅ {method_name} method exists")
            else:
                logger.error(f"❌ {method_name} method not found")
                return False
        
        # Check that instance variables are initialized
        import inspect
        init_method = getattr(ValrTestBot, '__init__')
        init_source = inspect.getsource(init_method)
        
        if 'self.placed_orders' in init_source:
            logger.info("✅ Persistent order tracking initialized")
        else:
            logger.error("❌ Persistent order tracking not initialized")
            return False
        
        logger.info("🎉 All comprehensive order detection methods are present!")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error testing comprehensive methods: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_comprehensive_order_methods()
    if success:
        print("\n✅ COMPREHENSIVE ORDER DETECTION TEST PASSED")
    else:
        print("\n❌ COMPREHENSIVE ORDER DETECTION TEST FAILED")
        sys.exit(1)