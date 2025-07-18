#!/usr/bin/env python3
"""
Test script to verify the new get_connector_active_orders method works correctly.
"""

import sys
import logging

# Add the hummingbot directory to the path
sys.path.insert(0, '/home/mailr/hummingbot-private/hummingbot')

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def test_connector_orders_method():
    """Test the new connector orders method."""
    logger.info("🧪 Testing connector orders method...")
    
    try:
        # Import the bot
        from scripts.valr_test_bot import ValrTestBot
        logger.info("✅ Bot imported successfully")
        
        # Check that the new method exists
        if hasattr(ValrTestBot, 'get_connector_active_orders'):
            logger.info("✅ get_connector_active_orders method exists")
        else:
            logger.error("❌ get_connector_active_orders method not found")
            return False
        
        # Check method signature
        import inspect
        method = getattr(ValrTestBot, 'get_connector_active_orders')
        sig = inspect.signature(method)
        params = list(sig.parameters.keys())
        
        if 'connector_name' in params:
            logger.info("✅ Method has correct signature")
        else:
            logger.error("❌ Method signature incorrect")
            return False
        
        logger.info("🎉 All tests passed!")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error testing connector orders method: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_connector_orders_method()
    if success:
        print("\n✅ CONNECTOR ORDERS METHOD TEST PASSED")
    else:
        print("\n❌ CONNECTOR ORDERS METHOD TEST FAILED")
        sys.exit(1)