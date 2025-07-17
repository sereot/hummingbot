#!/usr/bin/env python3

import asyncio
import logging
from decimal import Decimal

from hummingbot.connector.exchange.valr.valr_exchange import ValrExchange
from hummingbot.core.utils.async_utils import safe_ensure_future
from hummingbot.client.config.client_config_map import ClientConfigMap
from hummingbot.client.config.config_helpers import ClientConfigAdapter

async def check_valr_status():
    """Debug script to check VALR connector status and identify ready state issues."""
    
    # Set up logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    
    try:
        # Create config adapter
        client_config_map = ClientConfigMap()
        client_config_adapter = ClientConfigAdapter(client_config_map)
        
        # Initialize connector with dummy credentials for testing
        connector = ValrExchange(
            client_config_map=client_config_adapter,
            valr_api_key="dummy_key_for_testing",
            valr_api_secret="dummy_secret_for_testing", 
            trading_pairs=["DOGE-USDT"],
            trading_required=False  # Set to False for status checking
        )
        
        logger.info("VALR connector created, checking status...")
        
        # Check initial status
        logger.info(f"Initial ready status: {connector.ready}")
        
        # Try to get status dict if available
        try:
            status = connector.status_dict
            logger.info(f"Status dict: {status}")
        except Exception as e:
            logger.warning(f"Could not get status dict: {e}")
        
        # Wait a moment for initialization
        await asyncio.sleep(2)
        
        # Check status again
        logger.info(f"Ready status after wait: {connector.ready}")
        
        try:
            status = connector.status_dict
            logger.info(f"Updated status dict: {status}")
        except Exception as e:
            logger.warning(f"Could not get updated status dict: {e}")
            
        # Check specific components
        logger.info("Checking individual components:")
        
        # Check if trading rules are available
        trading_rules = connector.trading_rules
        logger.info(f"Trading rules count: {len(trading_rules)}")
        
        # Check if order book tracker is ready
        if hasattr(connector, '_order_book_tracker'):
            logger.info(f"Order book tracker exists: {connector._order_book_tracker is not None}")
            if connector._order_book_tracker:
                logger.info(f"Order book tracker ready: {connector._order_book_tracker.ready}")
        else:
            logger.info("No order book tracker attribute found")
            
        # Check balance information
        try:
            balances = connector.get_all_balances()
            logger.info(f"Balances available: {len(balances)}")
        except Exception as e:
            logger.warning(f"Could not get balances: {e}")
            
        logger.info("Status check complete")
        
    except Exception as e:
        logger.exception(f"Error during status check: {e}")

if __name__ == "__main__":
    asyncio.run(check_valr_status())