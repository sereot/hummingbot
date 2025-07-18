from hummingbot.core.api_throttler.data_types import LinkedLimitWeightPair, RateLimit
from hummingbot.core.data_type.in_flight_order import OrderState

DEFAULT_DOMAIN = "com"
HBOT_ORDER_ID_PREFIX = "HBOT"
MAX_ORDER_ID_LEN = 36

# Base URLs
REST_URL = "https://api.valr.com"
WSS_ACCOUNT_URL = "wss://api.valr.com/ws/account"
WSS_TRADE_URL = "wss://api.valr.com/ws/trade"

# API versions
PUBLIC_API_VERSION = "v1"
PRIVATE_API_VERSION = "v1"

# Public REST endpoints
TICKER_PRICE_PATH_URL = "/v1/public/marketsummary"
TICKER_PRICE_CHANGE_PATH_URL = "/v1/public/marketsummary"
EXCHANGE_INFO_PATH_URL = "/v1/public/currencies"
PING_PATH_URL = "/v1/public/time"
SNAPSHOT_PATH_URL = "/v1/public/{}/orderbook"
SERVER_TIME_PATH_URL = "/v1/public/time"
PAIRS_PATH_URL = "/v1/public/pairs"

# Private REST endpoints
ACCOUNTS_PATH_URL = "/v1/account/balances"
MY_TRADES_PATH_URL = "/v1/account/transactionhistory"
ORDER_PATH_URL = "/v1/orders/{}"
PLACE_ORDER_PATH_URL = "/v1/orders/limit"
CANCEL_ORDER_PATH_URL = "/v1/orders/order"
CANCEL_ALL_ORDERS_PATH_URL = "/v1/orders/limit/cancel"
ORDER_STATUS_PATH_URL = "/v1/orders/{}/detailed"
USER_STREAM_PATH_URL = "/v1/account/api-keys/current"
SET_LEVERAGE_PATH_URL = "/v1/account/leverage"
GET_INCOME_HISTORY_PATH_URL = "/v1/account/transactionhistory"
POSITION_INFORMATION_PATH_URL = "/v1/account/balances"
ACCOUNT_INFO_PATH_URL = "/v1/account/info"
ORDER_MODIFY_PATH_URL = "/v1/orders/modify"
ORDER_HISTORY_PATH_URL = "/v1/orders/history"

# WebSocket event types
WS_CONNECTION_EVENT = "WS_CONNECTION"
WS_SUBSCRIBE_EVENT = "SUBSCRIBE"
WS_MARKET_AGGREGATED_ORDERBOOK_UPDATE_EVENT = "AGGREGATED_ORDERBOOK_UPDATE"
WS_MARKET_FULL_ORDERBOOK_SNAPSHOT_EVENT = "FULL_ORDERBOOK_SNAPSHOT" 
WS_MARKET_TRADE_EVENT = "NEW_TRADE"
WS_MARKET_STATISTICS_EVENT = "MARKET_SUMMARY_UPDATE"
WS_USER_BALANCE_UPDATE_EVENT = "BALANCE_UPDATE"
WS_USER_NEW_ORDER_EVENT = "NEW_ORDER"
WS_USER_ORDER_UPDATE_EVENT = "ORDER_UPDATE"
WS_USER_ORDER_DELETE_EVENT = "ORDER_DELETE"
WS_USER_TRADE_EVENT = "NEW_TRADE"
WS_USER_FAILED_CANCEL_EVENT = "FAILED_CANCEL_ORDER"
WS_USER_ORDER_CANCEL_EVENT = "ORDER_CANCEL"
WS_USER_INSTANT_ORDER_COMPLETED_EVENT = "INSTANT_ORDER_COMPLETED"
WS_CANCEL_ON_DISCONNECT_EVENT = "CANCEL_ON_DISCONNECT"

# WebSocket order placement event types
WS_PLACE_LIMIT_ORDER_EVENT = "PLACE_LIMIT_ORDER"
WS_PLACE_MARKET_ORDER_EVENT = "PLACE_MARKET_ORDER"
WS_PLACE_CANCEL_ORDER_EVENT = "CANCEL_ORDER"
WS_ORDER_RESPONSE_EVENT = "ORDER_PLACED"
WS_ORDER_FAILED_EVENT = "ORDER_FAILED"

# Timeouts
MESSAGE_TIMEOUT = 30.0
PING_TIMEOUT = 10.0
API_CALL_TIMEOUT = 10.0
API_MAX_RETRIES = 4
READY_STATE_TIMEOUT = 60.0  # Maximum time to wait for connector to reach ready state
SYMBOL_MAPPING_TIMEOUT = 30.0  # Maximum time to wait for symbol mapping initialization

# Connection Health Monitoring
CONNECTION_HEALTH_CHECK_INTERVAL = 60.0  # Check connection health every 60 seconds
CONNECTION_SUCCESS_THRESHOLD = 3  # Number of successful connections to consider healthy
CONNECTION_FAILURE_THRESHOLD = 5  # Number of consecutive failures before circuit breaker
CONNECTION_RECOVERY_TIME = 300.0  # Time to wait before attempting recovery from circuit breaker
ADAPTIVE_DELAY_MAX = 60.0  # Maximum adaptive reconnection delay
ADAPTIVE_DELAY_MIN = 1.0  # Minimum adaptive reconnection delay

# Intervals
ONE_HOUR = 60 * 60
DIFF_STREAM_INTERVAL = 1.0  
TRADE_STREAM_INTERVAL = 1.0
UPDATE_ORDER_STATUS_INTERVAL = 10.0
USER_STREAM_PING_INTERVAL = 60.0

# Order States Mapping
ORDER_STATE = {
    "Placed": OrderState.OPEN,
    "Open": OrderState.OPEN,
    "Partially Filled": OrderState.PARTIALLY_FILLED,
    "Filled": OrderState.FILLED,
    "Cancelled": OrderState.CANCELED,
    "Failed": OrderState.FAILED,
    "Expired": OrderState.FAILED,
    "Rejected": OrderState.FAILED,
}

# Rate Limits - Conservative values to prevent HTTP 429 errors
# Based on observed 429 rate limit errors, using very conservative limits
NO_LIMIT = 1000000  # Used for endpoints without explicit limits
RATE_LIMITS = [
    # Public endpoints - Very conservative to prevent 429 errors during initialization
    RateLimit(limit_id=TICKER_PRICE_PATH_URL, limit=2, time_interval=1),
    RateLimit(limit_id=EXCHANGE_INFO_PATH_URL, limit=1, time_interval=2),  # Critical for initialization
    RateLimit(limit_id=SNAPSHOT_PATH_URL, limit=5, time_interval=1),
    RateLimit(limit_id=SERVER_TIME_PATH_URL, limit=1, time_interval=3),   # Critical for time sync
    RateLimit(limit_id=PING_PATH_URL, limit=1, time_interval=2),
    RateLimit(limit_id=PAIRS_PATH_URL, limit=1, time_interval=5),         # Critical for symbol mapping
    # Private endpoints - Reduced limits to prevent 429 errors  
    RateLimit(limit_id=ACCOUNTS_PATH_URL, limit=5, time_interval=1),
    RateLimit(limit_id=MY_TRADES_PATH_URL, limit=5, time_interval=1),
    RateLimit(limit_id=ORDER_PATH_URL, limit=10, time_interval=1),
    RateLimit(limit_id=PLACE_ORDER_PATH_URL, limit=20, time_interval=1),
    RateLimit(limit_id=CANCEL_ORDER_PATH_URL, limit=20, time_interval=1),
    RateLimit(limit_id=CANCEL_ALL_ORDERS_PATH_URL, limit=2, time_interval=1),
    RateLimit(limit_id=ORDER_STATUS_PATH_URL, limit=10, time_interval=1),
    RateLimit(limit_id=USER_STREAM_PATH_URL, limit=1, time_interval=5),
    RateLimit(limit_id=SET_LEVERAGE_PATH_URL, limit=1, time_interval=2),
    RateLimit(limit_id=GET_INCOME_HISTORY_PATH_URL, limit=5, time_interval=1),
    RateLimit(limit_id=POSITION_INFORMATION_PATH_URL, limit=5, time_interval=1),
    RateLimit(limit_id=ACCOUNT_INFO_PATH_URL, limit=2, time_interval=1),
    RateLimit(limit_id=ORDER_MODIFY_PATH_URL, limit=10, time_interval=1),
    RateLimit(limit_id=ORDER_HISTORY_PATH_URL, limit=5, time_interval=1),
    # WebSocket connections - Much more conservative to prevent 429 handshake errors
    RateLimit(limit_id=WSS_ACCOUNT_URL, limit=1, time_interval=120),      # Very conservative
    RateLimit(limit_id=WSS_TRADE_URL, limit=1, time_interval=120),        # Very conservative
]

# WebSocket connection limits
WS_CONNECTION_LIMIT_ID = "WSConnection"
WS_REQUEST_LIMIT_ID = "WSRequest"

# Error codes
RATE_LIMIT_ERROR_CODE = -4003
ORDER_NOT_EXIST_ERROR_CODE = -4013
ORDER_NOT_EXIST_MESSAGE = "Order does not exist"

# Hummingbot to VALR trading pair conversion
HUMMINGBOT_TO_VALR_PAIR_SEPARATOR_MAP = {"-": ""}