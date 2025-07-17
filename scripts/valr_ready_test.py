#!/usr/bin/env python3

import asyncio
import logging

from hummingbot.connector.exchange.valr.valr_exchange import ValrExchange
from hummingbot.client.config.client_config_map import ClientConfigMap
from hummingbot.client.config.config_helpers import ClientConfigAdapter

async def test_valr_ready_state():
    """Test if VALR connector can become ready with trading_required=False."""
    
    # Set up logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    
    try:
        # Create config adapter
        client_config_map = ClientConfigMap()
        client_config_adapter = ClientConfigAdapter(client_config_map)
        
        # Initialize connector with trading_required=False to bypass account balance requirement
        connector = ValrExchange(
            client_config_map=client_config_adapter,
            valr_api_key="dummy_key",
            valr_api_secret="dummy_secret",
            trading_pairs=["DOGE-USDT"],
            trading_required=False  # This should bypass account balance requirement
        )
        
        logger.info("VALR connector created with trading_required=False")
        
        # Start network
        await connector.start_network()
        
        # Wait for ready state
        for i in range(60):  # Wait up to 60 seconds
            await asyncio.sleep(1)
            
            if connector.ready:
                logger.info(f"🎉 SUCCESS! Connector became ready after {i+1} seconds!")
                status = connector.status_dict
                logger.info(f"Final status: {status}")
                break
                
            if i % 10 == 9:  # Log status every 10 seconds
                status = connector.status_dict
                logger.info(f"Status after {i+1}s: {status}")
        else:
            logger.error("❌ FAILED: Connector did not become ready within 60 seconds")
            status = connector.status_dict
            logger.error(f"Final status: {status}")
            
        # Clean up
        await connector.stop_network()
        
    except Exception as e:
        logger.exception(f"Error during test: {e}")

if __name__ == "__main__":
    asyncio.run(test_valr_ready_state())