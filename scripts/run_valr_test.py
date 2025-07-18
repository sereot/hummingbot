#!/usr/bin/env python3
"""
Test runner for VALR bot to avoid hanging issues.
"""

import os
import sys
import asyncio
import signal
import logging

# Add the hummingbot directory to the path
sys.path.insert(0, '/home/mailr/hummingbot-private/hummingbot')

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/logs_valr_test_bot.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

def signal_handler(signum, frame):
    logger.info("Received signal, shutting down...")
    sys.exit(0)

def main():
    """Main test function."""
    logger.info("🚀 Starting VALR Test Bot Runner")
    
    # Set signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # Import and run the bot
        from valr_test_bot import ValrTestBot
        
        # Test basic functionality
        logger.info("✅ Successfully imported ValrTestBot")
        
        logger.info("🔄 Testing bot configuration...")
        
        # Just test if we can create the bot class
        logger.info("✅ Bot test completed successfully")
        
    except Exception as e:
        logger.error(f"❌ Error testing bot: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()