#!/usr/bin/env python3

import asyncio
import logging
import time

from hummingbot.connector.exchange.valr.valr_exchange import ValrExchange
from hummingbot.client.config.client_config_map import ClientConfigMap
from hummingbot.client.config.config_helpers import ClientConfigAdapter

async def comprehensive_valr_test():
    """Comprehensive test of all VALR connector fixes."""
    
    # Set up logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    
    logger.info("🚀 Starting comprehensive VALR connector test...")
    
    try:
        # Create config adapter
        client_config_map = ClientConfigMap()
        client_config_adapter = ClientConfigAdapter(client_config_map)
        
        # Test 1: Basic connector creation
        logger.info("\n📋 Test 1: Creating VALR connector...")
        connector = ValrExchange(
            client_config_map=client_config_adapter,
            valr_api_key="dummy_key",
            valr_api_secret="dummy_secret",
            trading_pairs=["DOGE-USDT"],
            trading_required=False  # Bypass account balance requirement for testing
        )
        logger.info("✅ Connector created successfully")
        
        # Test 2: Check initial status
        logger.info("\n📋 Test 2: Checking initial status...")
        initial_status = connector.status_dict
        logger.info(f"Initial status: {initial_status}")
        
        # Test 3: Start network and monitor initialization
        logger.info("\n📋 Test 3: Starting network and monitoring initialization...")
        start_time = time.time()
        await connector.start_network()
        
        ready_achieved = False
        max_wait_time = 60  # seconds
        
        for i in range(max_wait_time):
            await asyncio.sleep(1)
            current_time = time.time() - start_time
            
            status = connector.status_dict
            is_ready = connector.ready
            
            # Log status every 10 seconds or when ready
            if i % 10 == 9 or is_ready:
                logger.info(f"After {current_time:.1f}s - Ready: {is_ready}, Status: {status}")
                
            if is_ready:
                logger.info(f"🎉 SUCCESS! Connector became ready after {current_time:.1f} seconds!")
                ready_achieved = True
                break
                
        # Test 4: Analyze final status
        logger.info("\n📋 Test 4: Final status analysis...")
        final_status = connector.status_dict
        final_ready = connector.ready
        
        # Count successful components
        true_count = sum(1 for value in final_status.values() if value)
        total_count = len(final_status)
        
        logger.info(f"Final ready state: {final_ready}")
        logger.info(f"Status breakdown: {final_status}")
        logger.info(f"Components working: {true_count}/{total_count}")
        
        # Test 5: Check specific functionality
        logger.info("\n📋 Test 5: Checking specific functionality...")
        
        # Check trading rules
        trading_rules = connector.trading_rules
        logger.info(f"Trading rules loaded: {len(trading_rules)}")
        
        # Check user stream
        try:
            user_stream_ready = connector._is_user_stream_initialized()
            logger.info(f"User stream initialized: {user_stream_ready}")
        except Exception as e:
            logger.warning(f"Could not check user stream: {e}")
            
        # Check order book
        if hasattr(connector, '_order_book_tracker'):
            ob_ready = connector._order_book_tracker.ready if connector._order_book_tracker else False
            logger.info(f"Order book tracker ready: {ob_ready}")
        else:
            logger.warning("No order book tracker found")
            
        # Test 6: Summary and recommendations
        logger.info("\n📋 Test 6: Summary and recommendations...")
        
        if ready_achieved:
            logger.info("🎉 OVERALL RESULT: SUCCESS - Connector is fully functional!")
            logger.info("✅ The VALR connector should work with the test bot")
        else:
            logger.info("⚠️  OVERALL RESULT: PARTIAL SUCCESS - Some components working")
            logger.info(f"✅ Working: {[k for k, v in final_status.items() if v]}")
            logger.info(f"❌ Not working: {[k for k, v in final_status.items() if not v]}")
            
            if true_count >= 4:  # Most components working
                logger.info("💡 RECOMMENDATION: The connector may still be functional for basic operations")
            else:
                logger.info("💡 RECOMMENDATION: Additional fixes needed before production use")
        
        # Clean up
        await connector.stop_network()
        logger.info("\n🔧 Network stopped, test complete")
        
    except Exception as e:
        logger.exception(f"❌ Test failed with error: {e}")

if __name__ == "__main__":
    asyncio.run(comprehensive_valr_test())