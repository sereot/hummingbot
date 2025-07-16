#!/usr/bin/env python3
"""
Test API key permissions and info.
"""

import asyncio
import sys
import json

# Add the hummingbot directory to the path
sys.path.insert(0, '/home/mailr/hummingbot-private/hummingbot')

from hummingbot.connector.exchange.valr.valr_auth import ValrAuth
from hummingbot.connector.exchange.valr import valr_constants as CONSTANTS
from hummingbot.connector.exchange.valr import valr_web_utils as web_utils
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory
from hummingbot.core.web_assistant.connections.data_types import RESTMethod
from hummingbot.core.api_throttler.async_throttler import AsyncThrottler


async def test_api_permissions():
    """Test API key permissions."""
    api_key = "7a66934ebab38005a97641271a99ffaf95a01f85d3c70272673cb83713a70308"
    api_secret = "ab356d3d77b0f4b9d7c6ffa7399a073782fd7afbcb0d2c830bf868f6eeb8dde5"
    
    auth = ValrAuth(api_key, api_secret)
    throttler = AsyncThrottler(CONSTANTS.RATE_LIMITS)
    web_assistants_factory = web_utils.build_api_factory(
        throttler=throttler,
        auth=auth
    )
    
    rest_assistant = await web_assistants_factory.get_rest_assistant()
    
    print("Testing API key permissions...")
    
    # Test 1: Get account info
    try:
        url = web_utils.private_rest_url("/v1/account/api-keys/current")
        response = await rest_assistant.execute_request(
            url=url,
            method=RESTMethod.GET,
            is_auth_required=True,
            throttler_limit_id=CONSTANTS.USER_STREAM_PATH_URL
        )
        print(f"✅ API Key Info: {json.dumps(response, indent=2)}")
    except Exception as e:
        print(f"❌ API Key Info failed: {e}")
    
    # Test 2: Get account balances (should work)
    try:
        url = web_utils.private_rest_url(CONSTANTS.ACCOUNTS_PATH_URL)
        response = await rest_assistant.execute_request(
            url=url,
            method=RESTMethod.GET,
            is_auth_required=True,
            throttler_limit_id=CONSTANTS.ACCOUNTS_PATH_URL
        )
        print(f"✅ Account Balances: Got {len(response)} balances")
    except Exception as e:
        print(f"❌ Account Balances failed: {e}")
    
    # Test 3: Try to get order history
    try:
        url = web_utils.private_rest_url(CONSTANTS.ORDER_HISTORY_PATH_URL)
        response = await rest_assistant.execute_request(
            url=url,
            method=RESTMethod.GET,
            is_auth_required=True,
            throttler_limit_id=CONSTANTS.ORDER_HISTORY_PATH_URL
        )
        print(f"✅ Order History: Got {len(response)} orders")
    except Exception as e:
        print(f"❌ Order History failed: {e}")


if __name__ == "__main__":
    asyncio.run(test_api_permissions())