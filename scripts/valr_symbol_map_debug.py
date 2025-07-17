#!/usr/bin/env python3

import asyncio
import logging

from hummingbot.connector.exchange.valr.valr_exchange import ValrExchange
from hummingbot.client.config.client_config_map import ClientConfigMap
from hummingbot.client.config.config_helpers import ClientConfigAdapter

async def test_symbol_map_initialization():
    """Debug symbol mapping initialization in VALR connector."""
    
    # Set up debug logging
    logging.basicConfig(level=logging.DEBUG)
    logger = logging.getLogger(__name__)
    
    try:
        # Create config adapter
        client_config_map = ClientConfigMap()
        client_config_adapter = ClientConfigAdapter(client_config_map)
        
        # Initialize connector
        connector = ValrExchange(
            client_config_map=client_config_adapter,
            valr_api_key="dummy_key",
            valr_api_secret="dummy_secret",
            trading_pairs=["DOGE-USDT"],
            trading_required=False
        )
        
        logger.info("VALR connector created")
        
        # Check initial symbol map status
        try:
            map_ready = connector.trading_pair_symbol_map_ready()
            logger.info(f"Initial symbol map ready: {map_ready}")
        except Exception as e:
            logger.error(f"Error checking symbol map ready: {e}")
            
        # Check if symbol map exists
        if hasattr(connector, '_trading_pair_symbol_map'):
            logger.info(f"Symbol map exists: {len(connector._trading_pair_symbol_map)} pairs")
        else:
            logger.warning("No symbol map attribute found")
            
        # Start network to trigger initialization
        logger.info("Starting network...")
        await connector.start_network()
        
        # Wait and check status periodically
        for i in range(30):
            await asyncio.sleep(1)
            
            try:
                map_ready = connector.trading_pair_symbol_map_ready()
                if hasattr(connector, '_trading_pair_symbol_map'):
                    map_size = len(connector._trading_pair_symbol_map)
                else:
                    map_size = "N/A"
                    
                logger.info(f"After {i+1}s - Symbol map ready: {map_ready}, Size: {map_size}")
                
                if map_ready:
                    logger.info("✅ Symbol map is ready!")
                    break
                    
            except Exception as e:
                logger.error(f"Error checking symbol map: {e}")
                
        # Check final status
        status = connector.status_dict
        logger.info(f"Final status: {status}")
        
        # Clean up
        await connector.stop_network()
        
    except Exception as e:
        logger.exception(f"Error during test: {e}")

if __name__ == "__main__":
    asyncio.run(test_symbol_map_initialization())