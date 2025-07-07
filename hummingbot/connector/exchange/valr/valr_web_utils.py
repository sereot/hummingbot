import time
from typing import Any, Dict, Optional, Callable
from urllib.parse import urlencode

import hummingbot.connector.exchange.valr.valr_constants as CONSTANTS
from hummingbot.connector.exchange.valr.valr_auth import ValrAuth
from hummingbot.connector.time_synchronizer import TimeSynchronizer
from hummingbot.connector.utils import TimeSynchronizerRESTPreProcessor
from hummingbot.core.api_throttler.async_throttler import AsyncThrottler
from hummingbot.core.api_throttler.data_types import RateLimit
from hummingbot.core.web_assistant.connections.data_types import RESTMethod, RESTRequest
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory


def public_rest_url(path_url: str, domain: str = CONSTANTS.DEFAULT_DOMAIN) -> str:
    """
    Creates a full URL for a public REST endpoint.
    
    Args:
        path_url: The path of the API endpoint
        domain: The domain to use (default: com)
        
    Returns:
        The full URL for the endpoint
    """
    return f"{CONSTANTS.REST_URL}{path_url}"


def private_rest_url(path_url: str, domain: str = CONSTANTS.DEFAULT_DOMAIN) -> str:
    """
    Creates a full URL for a private REST endpoint.
    
    Args:
        path_url: The path of the API endpoint
        domain: The domain to use (default: com)
        
    Returns:
        The full URL for the endpoint
    """
    return f"{CONSTANTS.REST_URL}{path_url}"


def build_api_factory(
    throttler: Optional[AsyncThrottler] = None,
    time_synchronizer: Optional[TimeSynchronizer] = None,
    domain: str = CONSTANTS.DEFAULT_DOMAIN,
    time_provider: Optional[Callable] = None,
    auth: Optional[ValrAuth] = None,
) -> WebAssistantsFactory:
    """
    Builds a WebAssistantsFactory for making API calls.
    
    Args:
        throttler: The async throttler to use for rate limiting
        time_synchronizer: The time synchronizer for syncing with server time
        domain: The domain to use (default: com)
        time_provider: Optional custom time provider
        auth: The authentication instance
        
    Returns:
        A WebAssistantsFactory instance
    """
    time_synchronizer = time_synchronizer or TimeSynchronizer()
    time_provider = time_provider or (lambda: get_current_server_time(
        throttler=throttler,
        domain=domain,
    ))
    
    api_factory = WebAssistantsFactory(
        throttler=throttler,
        auth=auth,
        rest_pre_processors=[
            TimeSynchronizerRESTPreProcessor(synchronizer=time_synchronizer, time_provider=time_provider),
        ],
    )
    return api_factory


def build_api_factory_without_time_synchronizer_pre_processor(
    throttler: Optional[AsyncThrottler] = None,
    domain: str = CONSTANTS.DEFAULT_DOMAIN,
    auth: Optional[ValrAuth] = None,
) -> WebAssistantsFactory:
    """
    Builds a WebAssistantsFactory without time synchronization.
    
    Args:
        throttler: The async throttler to use for rate limiting
        domain: The domain to use (default: com)
        auth: The authentication instance
        
    Returns:
        A WebAssistantsFactory instance
    """
    api_factory = WebAssistantsFactory(
        throttler=throttler,
        auth=auth,
    )
    return api_factory


async def get_current_server_time(
    throttler: Optional[AsyncThrottler] = None,
    domain: str = CONSTANTS.DEFAULT_DOMAIN,
) -> float:
    """
    Gets the current server time from VALR.
    
    Args:
        throttler: The async throttler to use for rate limiting
        domain: The domain to use (default: com)
        
    Returns:
        Current server time as a float timestamp
    """
    api_factory = build_api_factory_without_time_synchronizer_pre_processor(
        throttler=throttler,
        domain=domain,
    )
    rest_assistant = await api_factory.get_rest_assistant()
    
    response = await rest_assistant.execute_request(
        url=public_rest_url(path_url=CONSTANTS.SERVER_TIME_PATH_URL, domain=domain),
        method=RESTMethod.GET,
        throttler_limit_id=CONSTANTS.SERVER_TIME_PATH_URL,
    )
    
    # VALR returns time in milliseconds
    if not isinstance(response, dict):
        raise ValueError(f"Expected dict response for server time, got {type(response)}: {response}")
    
    server_time_ms = response.get("epochTime")
    if server_time_ms is None:
        raise ValueError(f"Missing epochTime in server response: {response}")
    
    return server_time_ms / 1000.0  # Convert to seconds


def get_new_client_order_id(
    is_buy: bool,
    trading_pair: str,
    hbot_order_id_prefix: str = CONSTANTS.HBOT_ORDER_ID_PREFIX,
    max_id_len: int = CONSTANTS.MAX_ORDER_ID_LEN,
) -> str:
    """
    Generates a new client order ID for VALR.
    
    Args:
        is_buy: Whether this is a buy order
        trading_pair: The trading pair
        hbot_order_id_prefix: The prefix for the order ID
        max_id_len: Maximum length of the order ID
        
    Returns:
        A new client order ID
    """
    from hummingbot.connector.utils import get_new_client_order_id as get_base_order_id
    
    # Get base order ID
    base_id = get_base_order_id(
        is_buy=is_buy,
        trading_pair=trading_pair,
        hbot_order_id_prefix=hbot_order_id_prefix,
        max_id_len=max_id_len,
    )
    
    # VALR accepts alphanumeric order IDs up to 36 characters
    # The base function already handles this, so we can return it as-is
    return base_id


def convert_to_exchange_trading_pair(hb_trading_pair: str) -> str:
    """
    Converts a Hummingbot trading pair to VALR format.
    
    Args:
        hb_trading_pair: Trading pair in Hummingbot format (e.g., "BTC-USDT")
        
    Returns:
        Trading pair in VALR format (e.g., "BTCUSDT")
    """
    # VALR uses format like "BTCZAR" without separator
    return hb_trading_pair.replace("-", "")


def convert_from_exchange_trading_pair(exchange_trading_pair: str) -> str:
    """
    Converts a VALR trading pair to Hummingbot format.
    This is more complex as we need to identify the base/quote split.
    
    Args:
        exchange_trading_pair: Trading pair in VALR format (e.g., "BTCUSDT")
        
    Returns:
        Trading pair in Hummingbot format (e.g., "BTC-USDT")
    """
    # Common quote currencies on VALR
    quote_currencies = [
        "ZAR", "USDT", "USDC", "BTC", "ETH", "EUR", "GBP", "USD", "BUSD", "DAI", "TUSD"
    ]
    
    # Try to find the quote currency from the end of the pair
    for quote in sorted(quote_currencies, key=len, reverse=True):
        if exchange_trading_pair.endswith(quote):
            base = exchange_trading_pair[:-len(quote)]
            return f"{base}-{quote}"
    
    # If no known quote currency found, try common 3-4 letter splits
    # This is a fallback and might not always be accurate
    if len(exchange_trading_pair) >= 6:
        # Try assuming last 3-4 characters are quote
        for split_pos in [4, 3]:
            if len(exchange_trading_pair) > split_pos:
                base = exchange_trading_pair[:-split_pos]
                quote = exchange_trading_pair[-split_pos:]
                if len(base) >= 2 and len(quote) >= 3:
                    return f"{base}-{quote}"
    
    # Last resort - assume it's already in the right format
    return exchange_trading_pair


def get_rest_url_for_endpoint(
    endpoint: str,
    domain: str = CONSTANTS.DEFAULT_DOMAIN,
    params: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Builds the full REST URL for an endpoint with optional parameters.
    
    Args:
        endpoint: The API endpoint path
        domain: The domain to use
        params: Optional query parameters
        
    Returns:
        The full URL with query parameters if provided
    """
    url = public_rest_url(endpoint, domain)
    if params:
        url = f"{url}?{urlencode(params)}"
    return url