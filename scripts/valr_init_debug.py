#!/usr/bin/env python3

import asyncio
import logging
from decimal import Decimal
import os

from hummingbot.connector.exchange.valr.valr_exchange import ValrExchange
from hummingbot.core.utils.async_utils import safe_ensure_future
from hummingbot.client.config.client_config_map import ClientConfigMap
from hummingbot.client.config.config_helpers import ClientConfigAdapter

async def test_valr_initialization():
    """Debug script to test VALR connector initialization sequence."""
    
    # Set up logging
    logging.basicConfig(level=logging.DEBUG)
    logger = logging.getLogger(__name__)
    
    try:
        # Create config adapter
        client_config_map = ClientConfigMap()
        client_config_adapter = ClientConfigAdapter(client_config_map)
        
        # Try to get real credentials from environment or config
        api_key = os.getenv("VALR_API_KEY", "dummy_key")
        api_secret = os.getenv("VALR_API_SECRET", "dummy_secret")
        
        logger.info(f"Using API key: {api_key[:8]}...")
        
        # Initialize connector
        connector = ValrExchange(
            client_config_map=client_config_adapter,
            valr_api_key=api_key,
            valr_api_secret=api_secret,
            trading_pairs=["DOGE-USDT"],
            trading_required=True  # Enable trading mode
        )
        
        logger.info("VALR connector created")
        
        # Check if order book data source was created
        if hasattr(connector, '_order_book_data_source'):
            logger.info("Order book data source exists")
        else:
            logger.warning("No order book data source found")
            
        # Manually start network to trigger initialization
        logger.info("Starting network...")
        await connector.start_network()
        
        # Wait for initialization
        for i in range(30):  # Wait up to 30 seconds
            await asyncio.sleep(1)
            
            status = connector.status_dict
            logger.info(f"Status after {i+1}s: {status}")
            
            if connector.ready:
                logger.info("Connector is ready!")
                break
        else:
            logger.warning("Connector did not become ready within 30 seconds")
            
        # Check final status
        logger.info(f"Final ready status: {connector.ready}")
        
        # Check trading rules after network start
        trading_rules = connector.trading_rules
        logger.info(f"Trading rules count after network start: {len(trading_rules)}")
        
        # Check order book tracker
        if hasattr(connector, '_order_book_tracker'):
            logger.info(f"Order book tracker exists: {connector._order_book_tracker is not None}")
            if connector._order_book_tracker:
                logger.info(f"Order book tracker ready: {connector._order_book_tracker.ready}")
        else:
            logger.info("No order book tracker attribute found")
            
        # Stop network
        await connector.stop_network()
        logger.info("Network stopped")
        
    except Exception as e:
        logger.exception(f"Error during initialization test: {e}")

if __name__ == "__main__":
    asyncio.run(test_valr_initialization())