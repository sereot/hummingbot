#!/usr/bin/env python3
"""
Test script to verify the synchronous bot logic works.
"""

import sys
import logging

# Add the hummingbot directory to the path
sys.path.insert(0, '/home/mailr/hummingbot-private/hummingbot')

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def test_bot_methods():
    """Test the bot's key methods work synchronously."""
    logger.info("🚀 Testing VALR Bot synchronous execution...")
    
    try:
        # Import the bot
        from scripts.valr_test_bot import ValrTestBot
        logger.info("✅ Bot imported successfully")
        
        # Test that the methods are synchronous
        import inspect
        
        # Check on_tick method
        on_tick_method = getattr(ValrTestBot, 'on_tick', None)
        if on_tick_method:
            if inspect.iscoroutinefunction(on_tick_method):
                logger.error("❌ on_tick method is still async!")
                return False
            else:
                logger.info("✅ on_tick method is synchronous")
        
        # Check did_process_tick method
        did_process_tick_method = getattr(ValrTestBot, 'did_process_tick', None)
        if did_process_tick_method:
            if inspect.iscoroutinefunction(did_process_tick_method):
                logger.error("❌ did_process_tick method is async!")
                return False
            else:
                logger.info("✅ did_process_tick method is synchronous")
        
        # Check create_proposal method
        create_proposal_method = getattr(ValrTestBot, 'create_proposal', None)
        if create_proposal_method:
            if inspect.iscoroutinefunction(create_proposal_method):
                logger.error("❌ create_proposal method is async!")
                return False
            else:
                logger.info("✅ create_proposal method is synchronous")
        
        logger.info("🎉 All methods are properly synchronous!")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error testing bot: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_bot_methods()
    if success:
        print("\n✅ SYNC TEST PASSED - Bot should work properly now!")
    else:
        print("\n❌ SYNC TEST FAILED - Bot needs more fixes")
        sys.exit(1)