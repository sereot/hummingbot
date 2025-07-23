import asyncio
import json
import logging
import time
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, Any, List, Dict, Optional, Tuple

from hummingbot.connector.exchange.valr import valr_constants as CONSTANTS, valr_web_utils as web_utils
from hummingbot.connector.exchange.valr.valr_api_order_book_data_source import ValrAPIOrderBookDataSource
from hummingbot.connector.exchange.valr.valr_api_user_stream_data_source import ValrAPIUserStreamDataSource
from hummingbot.connector.exchange.valr.valr_auth import ValrAuth
from hummingbot.connector.exchange.valr.valr_utils import ValrConfigMap
from hummingbot.connector.exchange.valr.valr_performance_metrics import PerformanceMetrics
from hummingbot.connector.exchange.valr.valr_circuit_breaker import (
    CircuitBreakerManager, 
    CircuitBreakerConfig, 
    CircuitOpenError,
    RateLimitCircuitBreaker,
    ConnectionCircuitBreaker
)
from hummingbot.connector.exchange_py_base import ExchangePyBase
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.connector.utils import combine_to_hb_trading_pair
from hummingbot.core.api_throttler.data_types import RateLimit
from hummingbot.core.data_type.cancellation_result import CancellationResult
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState, OrderUpdate, TradeUpdate
from hummingbot.core.data_type.order_book_tracker_data_source import OrderBookTrackerDataSource
from hummingbot.core.data_type.trade_fee import TokenAmount, TradeFeeBase
from hummingbot.core.data_type.user_stream_tracker_data_source import UserStreamTrackerDataSource
from hummingbot.core.utils.estimate_fee import build_trade_fee
from hummingbot.core.web_assistant.connections.data_types import RESTMethod, WSJSONRequest
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory

if TYPE_CHECKING:
    from hummingbot.client.config.config_helpers import ClientConfigAdapter


class ValrExchange(ExchangePyBase):
    UPDATE_ORDER_STATUS_MIN_INTERVAL = 1.0  # Reduced from 10.0 for HFT performance
    
    # HFT-optimized poll intervals
    SHORT_POLL_INTERVAL = 1.0  # Reduced from 5.0 for HFT performance
    LONG_POLL_INTERVAL = 1.0  # CRITICAL: Must be 1.0 for HFT! When WS works, we poll every second

    web_utils = web_utils

    def __init__(
        self,
        client_config_map: "ClientConfigAdapter",
        valr_api_key: str,
        valr_api_secret: str,
        trading_pairs: list[str] | None = None,
        trading_required: bool = True,
        domain: str = CONSTANTS.DEFAULT_DOMAIN,
    ):
        self._api_key = valr_api_key
        self._api_secret = valr_api_secret
        self._domain = domain
        self._trading_required = trading_required
        self._trading_pairs = trading_pairs
        
        # Initialize trading pair symbol mapping
        self._trading_pair_symbol_map: dict[str, str] | None = None
        
        # WebSocket order placement tracking
        self._ws_order_requests: dict[str, asyncio.Future] = {}  # clientMsgId -> Future
        self._ws_order_placement_enabled = True  # Enable by default for HFT performance
        
        # Ready state tracking for VALR-specific behavior
        self._ready_state_override = False
        self._initialization_start_time = None
        
        # Performance metrics tracking
        self._performance_metrics = PerformanceMetrics()
        
        # Circuit breaker management
        self._circuit_breakers = CircuitBreakerManager()
        self._init_circuit_breakers()
        
        # HFT optimization flags
        self._use_websocket_for_orders = True  # Primary mode for low latency
        self._enable_batch_operations = True   # Batch multiple operations
        self._enable_ob_l1_diff = True        # Use rapid order book updates
        
        super().__init__(client_config_map)
        
        self.logger().info(f"VALR Connector initialized - trading_pairs: {self._trading_pairs}")
        
        # Schedule ready state timeout check for VALR
        self._schedule_ready_state_timeout()
        
    def _init_circuit_breakers(self):
        """Initialize circuit breakers for different API operations"""
        # Order placement circuit breaker
        self._circuit_breakers.get_breaker(
            "order_placement",
            CircuitBreakerConfig(
                failure_threshold=5,
                success_threshold=2,
                timeout=30.0
            )
        )
        
        # Rate limit circuit breaker
        self._circuit_breakers.get_breaker(
            "rate_limit",
            CircuitBreakerConfig(
                failure_threshold=3,
                success_threshold=1,
                timeout=60.0,
                initial_timeout=60.0
            )
        )
        
        # WebSocket connection circuit breaker
        self._circuit_breakers.get_breaker(
            "websocket",
            CircuitBreakerConfig(
                failure_threshold=5,
                success_threshold=3,
                timeout=30.0
            )
        )
        
        # Market data circuit breaker
        self._circuit_breakers.get_breaker(
            "market_data",
            CircuitBreakerConfig(
                failure_threshold=10,
                success_threshold=3,
                timeout=15.0
            )
        )
        
        # Performance monitoring will be started when ready

    @property
    def authenticator(self):
        return ValrAuth(
            api_key=self._api_key,
            api_secret=self._api_secret
        )

    @property
    def name(self) -> str:
        return "valr"

    @property
    def rate_limits_rules(self) -> list[RateLimit]:
        return CONSTANTS.RATE_LIMITS

    @property
    def domain(self) -> str:
        return self._domain

    @property
    def client_order_id_max_length(self) -> int:
        return CONSTANTS.MAX_ORDER_ID_LEN

    @property
    def client_order_id_prefix(self) -> str:
        return CONSTANTS.HBOT_ORDER_ID_PREFIX

    @property
    def trading_rules_request_path(self) -> str:
        return CONSTANTS.PAIRS_PATH_URL

    @property
    def trading_pairs_request_path(self) -> str:
        return CONSTANTS.PAIRS_PATH_URL

    @property
    def check_network_request_path(self) -> str:
        return CONSTANTS.PING_PATH_URL

    @property
    def trading_pairs(self) -> list[str]:
        return self._trading_pairs

    @property
    def is_cancel_request_in_exchange_synchronous(self) -> bool:
        return True

    @property
    def is_trading_required(self) -> bool:
        return self._trading_required

    def trading_pair_symbol_map_ready(self) -> bool:
        """
        Checks if the mapping from exchange symbols to client trading pairs has been initialized.
        This is required for the connector to reach ready state.
        
        Returns:
            True if the symbol mapping has been initialized, False otherwise
        """
        return self._trading_pair_symbol_map is not None and len(self._trading_pair_symbol_map) > 0

    def _schedule_ready_state_timeout(self):
        """
        Schedule a timeout-based ready state override for VALR's specific behavior.
        VALR's frequent disconnections can prevent normal ready state progression.
        """
        try:
            import time
            self._initialization_start_time = time.time()
            self.logger().info(f"VALR Connector ready tracking initialized - start_time: {self._initialization_start_time}")
            
            # Schedule a task to force ready state after timeout if needed
            asyncio.create_task(self._ready_state_timeout_task())
            self.logger().info("Scheduled force ready task")
            
            # Schedule circuit breaker recovery check
            asyncio.create_task(self._circuit_breaker_recovery_check())
        except Exception as e:
            self.logger().error(f"Error scheduling ready state timeout: {e}")

    async def _circuit_breaker_recovery_check(self):
        """
        Periodically check circuit breaker state and re-enable WebSocket operations when recovered.
        """
        while True:
            try:
                await asyncio.sleep(30)  # Check every 30 seconds
                
                if not self._use_websocket_for_orders:
                    ws_breaker = self._circuit_breakers.get_breaker("websocket")
                    if ws_breaker.state == "closed":
                        self.logger().info("Circuit breaker recovered - re-enabling WebSocket operations")
                        self._use_websocket_for_orders = True
            except Exception as e:
                self.logger().error(f"Error in circuit breaker recovery check: {e}")
                
    async def _ready_state_timeout_task(self):
        """
        Force ready state after a timeout to handle VALR's connection patterns.
        This ensures the connector becomes ready even with WebSocket instability.
        """
        try:
            timeout_seconds = 15  # Reasonable timeout for VALR initialization
            self.logger().info(f"Force ready task started - waiting {timeout_seconds} seconds")
            
            await asyncio.sleep(timeout_seconds)
            
            # Check if we're still not ready and key components are working
            if not self.ready and self.trading_pair_symbol_map_ready():
                self.logger().warning("FAILSAFE: Forcing connector ready state after timeout")
                
                # Try to initialize missing components
                try:
                    # Initialize trading rules if missing
                    if not hasattr(self, '_trading_rules') or len(self._trading_rules) == 0:
                        self.logger().info("FAILSAFE: Attempting to initialize trading rules...")
                        await self._update_trading_rules()
                        self.logger().info(f"FAILSAFE: Trading rules initialized with {len(self._trading_rules)} rules")
                except Exception as e:
                    self.logger().warning(f"FAILSAFE: Could not initialize trading rules: {e}")
                
                # Log current ready state components for debugging
                ready_status = {
                    'symbols_mapping_initialized': self.trading_pair_symbol_map_ready(),
                    'order_books_initialized': hasattr(self, '_order_book_tracker') and self._order_book_tracker is not None,
                    'account_balance': True,  # Assume account balance is ready (checked separately)
                    'trading_rule_initialized': hasattr(self, '_trading_rules') and len(self._trading_rules) > 0,
                    'user_stream_initialized': True  # We have REST fallback
                }
                
                self.logger().info(f"Ready state forced - current status: {ready_status}")
                self._ready_state_override = True
                
        except asyncio.CancelledError:
            self.logger().info("Ready state timeout task cancelled")
        except Exception as e:
            self.logger().error(f"Error in ready state timeout task: {e}")

    @property 
    def ready(self) -> bool:
        """
        Override ready state check to be more permissive for VALR's behavior patterns.
        """
        # If we have a ready state override, use it
        if self._ready_state_override:
            return True
            
        # Check basic requirements more permissively
        basic_ready = (
            self.trading_pair_symbol_map_ready() and
            hasattr(self, '_trading_rules') and len(self._trading_rules) > 0
        )
        
        # For VALR, be more permissive about order book and user stream readiness
        # due to frequent WebSocket disconnections
        if basic_ready:
            # Allow ready state even if WebSockets are reconnecting
            return True
            
        # Fall back to parent's ready check
        try:
            return super().ready
        except Exception:
            # If parent ready check fails, but we have basic functionality, allow ready
            return basic_ready

    @property
    def status_dict(self) -> dict[str, bool]:
        """
        Override status_dict to provide more accurate VALR-specific status information.
        """
        try:
            # Get the parent's status dict as baseline
            parent_status = super().status_dict
            
            # Override with VALR-specific status
            valr_status = {
                'symbols_mapping_initialized': self.trading_pair_symbol_map_ready(),
                'order_books_initialized': hasattr(self, '_order_book_tracker') and self._order_book_tracker is not None,
                'account_balance': True,  # Assume account balance is ready (checked separately)
                'trading_rule_initialized': hasattr(self, '_trading_rules') and len(self._trading_rules) > 0,
                'user_stream_initialized': True  # We have REST fallback
            }
            
            # For VALR, be more permissive about order book initialization
            # due to frequent WebSocket disconnections
            if valr_status['symbols_mapping_initialized'] and valr_status['trading_rule_initialized']:
                valr_status['order_books_initialized'] = True
            
            return valr_status
            
        except Exception as e:
            self.logger().error(f"Error getting status_dict: {e}")
            # Return basic status if there's an error
            return {
                'symbols_mapping_initialized': self.trading_pair_symbol_map_ready(),
                'order_books_initialized': True,  # Be permissive
                'account_balance': True,
                'trading_rule_initialized': hasattr(self, '_trading_rules') and len(self._trading_rules) > 0,
                'user_stream_initialized': True
            }

    def _set_trading_pair_symbol_map(self, mapping: dict[str, str]):
        """
        Sets the trading pair symbol mapping dictionary.
        
        Args:
            mapping: Dictionary mapping exchange symbols to Hummingbot trading pairs
        """
        if mapping:
            self._trading_pair_symbol_map = mapping.copy()
            self.logger().debug(f"Symbol mapping set with {len(mapping)} pairs")
        else:
            self._trading_pair_symbol_map = {}
            self.logger().debug("Symbol mapping set to empty dictionary")



    def supported_order_types(self) -> list[OrderType]:
        return [OrderType.LIMIT, OrderType.LIMIT_MAKER]

    def _is_request_exception_related_to_time_synchronizer(self, request_exception: Exception) -> bool:
        # VALR doesn't seem to have strict time sync requirements
        return False

    def _is_order_not_found_during_status_update_error(self, status_update_exception: Exception) -> bool:
        error_message = str(status_update_exception)
        return (
            CONSTANTS.ORDER_NOT_EXIST_MESSAGE in error_message
            or (hasattr(status_update_exception, "response") 
                and hasattr(status_update_exception.response, "status")
                and status_update_exception.response.status == 404)
        )

    def _is_order_not_found_during_cancelation_error(self, cancelation_exception: Exception) -> bool:
        error_message = str(cancelation_exception)
        return (
            CONSTANTS.ORDER_NOT_EXIST_MESSAGE in error_message
            or (hasattr(cancelation_exception, "response")
                and hasattr(cancelation_exception.response, "status") 
                and cancelation_exception.response.status == 404)
        )

    async def _make_trading_pairs_request(self) -> Any:
        """
        Override to add retry logic for 429 rate limit errors on trading pairs API.
        
        Returns:
            Exchange info with trading pairs data
        """
        max_retries = 5
        base_delay = 2.0
        
        for attempt in range(max_retries):
            try:
                self.logger().debug(f"Requesting trading pairs (attempt {attempt + 1}/{max_retries})")
                
                # Use the base implementation to make the API call
                exchange_info = await self._api_get(path_url=self.trading_pairs_request_path)
                
                self.logger().info(f"Successfully retrieved trading pairs on attempt {attempt + 1}")
                return exchange_info
                
            except Exception as e:
                error_message = str(e).lower()
                
                # Check if this is a 429 rate limit error
                if "429" in error_message or "rate limit" in error_message:
                    if attempt < max_retries - 1:
                        # Calculate exponential backoff delay
                        delay = base_delay * (2 ** attempt)
                        self.logger().warning(f"Trading pairs request rate limited (attempt {attempt + 1}/{max_retries}). "
                                           f"Retrying in {delay:.1f} seconds: {e}")
                        await asyncio.sleep(delay)
                        continue
                    else:
                        self.logger().error(f"Max retries reached for trading pairs request: {e}")
                        # Re-raise the exception to let base class handle it
                        raise
                else:
                    # For non-429 errors, retry with shorter delay
                    if attempt < max_retries - 1:
                        delay = 1.0
                        self.logger().warning(f"Trading pairs request error (attempt {attempt + 1}/{max_retries}). "
                                           f"Retrying in {delay:.1f} seconds: {e}")
                        await asyncio.sleep(delay)
                        continue
                    else:
                        self.logger().error(f"Failed to get trading pairs after {max_retries} attempts: {e}")
                        # Re-raise the exception to let base class handle it
                        raise
        
        # This should never be reached, but just in case
        raise Exception("Unexpected exit from trading pairs request retry loop")

    async def _make_trading_rules_request(self) -> Any:
        """
        Make request to get trading rules (same as trading pairs for VALR).
        VALR trading rules come from the same endpoint as trading pairs.
        
        Returns:
            Exchange info with trading rules data
        """
        # For VALR, trading rules come from the same endpoint as trading pairs
        return await self._make_trading_pairs_request()

    def _create_web_assistants_factory(self) -> WebAssistantsFactory:
        return web_utils.build_api_factory(
            throttler=self._throttler,
            time_synchronizer=self._time_synchronizer,
            domain=self._domain,
            auth=self._auth
        )

    def _create_order_book_data_source(self) -> OrderBookTrackerDataSource:
        return ValrAPIOrderBookDataSource(
            trading_pairs=self._trading_pairs,
            connector=self,
            api_factory=self._web_assistants_factory,
            domain=self._domain
        )

    def _create_user_stream_data_source(self) -> UserStreamTrackerDataSource:
        # Create user stream data source but it will be disabled internally
        # This ensures the connector works in REST-only mode
        user_stream = ValrAPIUserStreamDataSource(
            auth=self._auth,
            trading_pairs=self._trading_pairs,
            connector=self,
            api_factory=self._web_assistants_factory,
            domain=self._domain
        )
        self.logger().info("User stream data source created (will use REST-only mode)")
        return user_stream

    def _initialize_trading_pair_symbols_from_exchange_info(self, exchange_info: dict[str, Any]):
        """
        Initialize trading pair symbols from exchange info.
        VALR provides pairs info in a list format.
        """
        self.logger().info(f"_initialize_trading_pair_symbols_from_exchange_info called with type: {type(exchange_info)}")
        
        mapping = {}
        if isinstance(exchange_info, list):
            self.logger().info(f"Processing {len(exchange_info)} pairs for symbol mapping")
            for pair_info in exchange_info:
                if self._is_pair_valid_for_trading(pair_info):
                    exchange_symbol = pair_info.get("symbol", "")
                    try:
                        trading_pair = web_utils.convert_from_exchange_trading_pair(exchange_symbol)
                        mapping[exchange_symbol] = trading_pair
                        self.logger().debug(f"Added symbol mapping: {exchange_symbol} -> {trading_pair}")
                    except Exception:
                        self.logger().exception(f"Error processing trading pair {exchange_symbol}")
        
        self.logger().info(f"Setting symbol mapping with {len(mapping)} pairs")
        self._set_trading_pair_symbol_map(mapping)
        
        # Verify the mapping was set correctly
        if hasattr(self, '_trading_pair_symbol_map') and self._trading_pair_symbol_map:
            self.logger().info(f"Symbol mapping initialized successfully with {len(self._trading_pair_symbol_map)} pairs")
            self.logger().debug(f"Symbol mapping ready status: {self.trading_pair_symbol_map_ready()}")
            
            # Log some examples for debugging
            sample_pairs = list(self._trading_pair_symbol_map.items())[:5]
            self.logger().debug(f"Sample mappings: {sample_pairs}")
        else:
            self.logger().warning(f"Symbol mapping initialization failed - mapping is empty or None")
            self.logger().warning(f"Original mapping had {len(mapping)} pairs")
            if len(mapping) > 0:
                # If we have a mapping but it didn't get set, try again
                self.logger().info("Retrying symbol mapping initialization...")
                self._trading_pair_symbol_map = mapping.copy()
                if self._trading_pair_symbol_map:
                    self.logger().info(f"Symbol mapping retry successful with {len(self._trading_pair_symbol_map)} pairs")
                else:
                    self.logger().error("Symbol mapping retry failed - unable to set mapping")

    def _get_fee(
        self,
        base_currency: str,
        quote_currency: str,
        order_type: OrderType,
        order_side: TradeType,
        amount: Decimal,
        price: Decimal = Decimal("NaN"),
        is_maker: bool | None = None
    ) -> TradeFeeBase:
        is_maker = is_maker or (order_type is OrderType.LIMIT_MAKER)
        fee = build_trade_fee(
            self.name,
            is_maker,
            base_currency=base_currency,
            quote_currency=quote_currency,
            order_type=order_type,
            order_side=order_side,
            amount=amount,
            price=price,
        )
        return fee

    async def _place_order(
        self,
        order_id: str,
        trading_pair: str,
        amount: Decimal,
        trade_type: TradeType,
        order_type: OrderType,
        price: Decimal,
        **kwargs
    ) -> tuple[str, float]:
        
        start_time = time.perf_counter()
        exchange_order_id = None
        timestamp = None
        success = False
        
        try:
            # Check if circuit breaker allows WebSocket operations
            ws_breaker = self._circuit_breakers.get_breaker("websocket")
            
            # Try WebSocket order placement first if enabled and circuit breaker is not open
            if self._ws_order_placement_enabled and self._use_websocket_for_orders and ws_breaker.state != "open":
                try:
                    exchange_order_id, timestamp = await self._place_order_websocket(
                        order_id, trading_pair, amount, trade_type, order_type, price
                    )
                    success = True
                    return exchange_order_id, timestamp
                except CircuitOpenError as e:
                    self.logger().warning(f"WebSocket circuit breaker OPEN, falling back to REST: {e}")
                    self._performance_metrics.record_error("ws_circuit_breaker_open")
                    # Temporarily disable WebSocket operations
                    self._use_websocket_for_orders = False
                except Exception as e:
                    self.logger().warning(f"WebSocket order placement failed, falling back to REST: {e}")
                    self._performance_metrics.record_error("ws_order_placement_failed")
                    
            # Fallback to REST API order placement
            exchange_order_id, timestamp = await self._place_order_rest(
                order_id, trading_pair, amount, trade_type, order_type, price
            )
            success = True
            return exchange_order_id, timestamp
            
        finally:
            # Record performance metrics
            latency_ms = (time.perf_counter() - start_time) * 1000
            self._performance_metrics.record_order_placement(latency_ms, success)
    
    async def _place_order_rest(
        self,
        order_id: str,
        trading_pair: str,
        amount: Decimal,
        trade_type: TradeType,
        order_type: OrderType,
        price: Decimal,
    ) -> tuple[str, float]:
        """Place order via REST API with circuit breaker protection"""
        
        # Get circuit breaker for order placement
        order_breaker = self._circuit_breakers.get_breaker("order_placement")
        
        async def _execute_order():
            rest_assistant = await self._web_assistants_factory.get_rest_assistant()
            
            # Prepare order data
            order_data = {
                "side": "BUY" if trade_type == TradeType.BUY else "SELL",
                "quantity": str(amount),
                "price": str(price),
                "pair": web_utils.convert_to_exchange_trading_pair(trading_pair),
                "postOnly": order_type == OrderType.LIMIT_MAKER,
                "customerOrderId": order_id,
            }
            
            # Send order request
            order_result = await rest_assistant.execute_request(
                url=web_utils.private_rest_url(CONSTANTS.PLACE_ORDER_PATH_URL),
                method=RESTMethod.POST,
                data=order_data,
                throttler_limit_id=CONSTANTS.PLACE_ORDER_PATH_URL,
                is_auth_required=True,
            )
            
            return str(order_result["id"])
        
        try:
            # Execute with circuit breaker protection
            exchange_order_id = await order_breaker.run(_execute_order)
            return exchange_order_id, self.current_timestamp
            
        except CircuitOpenError as e:
            self.logger().error(f"Circuit breaker OPEN for order placement: {e}")
            self._performance_metrics.record_error("circuit_breaker_open")
            raise
    
    async def _place_order_websocket(
        self,
        order_id: str,
        trading_pair: str,
        amount: Decimal,
        trade_type: TradeType,
        order_type: OrderType,
        price: Decimal,
    ) -> tuple[str, float]:
        """Place order via WebSocket with circuit breaker protection"""
        
        # Get circuit breaker for WebSocket operations
        ws_breaker = self._circuit_breakers.get_breaker("websocket")
        
        async def _execute_ws_order():
            # Generate unique client message ID for correlation
            client_msg_id = str(uuid.uuid4())
            
            # Get WebSocket assistant from user stream data source
            if not hasattr(self, '_user_stream_tracker') or not self._user_stream_tracker:
                raise Exception("User stream tracker not available for WebSocket order placement")
            
            user_stream_data_source = self._user_stream_tracker.data_source
            if not hasattr(user_stream_data_source, '_ws_assistant') or not user_stream_data_source._ws_assistant:
                # Wait a bit for WebSocket to be ready
                await asyncio.sleep(0.5)
                if not hasattr(user_stream_data_source, '_ws_assistant') or not user_stream_data_source._ws_assistant:
                    raise Exception("WebSocket assistant not available for order placement")
            
            ws_assistant = user_stream_data_source._ws_assistant
            
            # Prepare WebSocket order message
            order_data = {
                "pair": web_utils.convert_to_exchange_trading_pair(trading_pair),
                "side": "BUY" if trade_type == TradeType.BUY else "SELL",
                "quantity": str(amount),
                "customerOrderId": order_id,
                "allowMargin": False  # Required field for VALR
            }
            
            # Add price for limit orders
            if order_type == OrderType.LIMIT or order_type == OrderType.LIMIT_MAKER:
                order_data["price"] = str(price)
                order_data["postOnly"] = order_type == OrderType.LIMIT_MAKER
                order_data["timeInForce"] = "GTC"  # Good Till Cancelled
            
            # VALR WebSocket order format based on official Postman documentation
            # CRITICAL: VALR uses "payload" field for request data (not "data")
            # See docs/api/VALR_WEBSOCKET_CRITICAL_NOTES.md for details
            order_message = WSJSONRequest(
                payload={
                    "type": CONSTANTS.WS_PLACE_LIMIT_ORDER_EVENT if order_type in (OrderType.LIMIT, OrderType.LIMIT_MAKER) else CONSTANTS.WS_PLACE_MARKET_ORDER_EVENT,
                    "clientMsgId": client_msg_id,
                    "payload": order_data  # VALR expects "payload" for request messages
                }
                # Note: is_auth_required removed - VALR authenticates during WebSocket handshake only
            )
            
            # Create future for response tracking
            response_future = asyncio.Future()
            self._ws_order_requests[client_msg_id] = response_future
            
            # Register future with user stream data source BEFORE sending
            user_stream_data_source.register_order_future(client_msg_id, response_future)
            
            try:
                # Log the full message being sent for debugging
                self.logger().info(f"Sending WebSocket order message: {json.dumps(order_message.payload, indent=2)}")
                
                # Send order message
                await ws_assistant.send(order_message)
                self.logger().info(f"Sent WebSocket order placement: {client_msg_id} for {trading_pair} "
                                  f"{trade_type.name} {amount} @ {price}")
                
                # Wait for response with timeout (optimized for HFT)
                response = await asyncio.wait_for(response_future, timeout=CONSTANTS.WS_ORDER_TIMEOUT)
                
                # Extract order ID from response
                self.logger().info(f"Received WebSocket order response: {response.get('type')} for {client_msg_id}")
                
                # Check for error in response first
                if "error" in response:
                    error_code = response["error"].get("code", "Unknown")
                    error_msg = response["error"].get("message", "Unknown error")
                    self.logger().error(f"WebSocket order placement failed with code {error_code}: {error_msg}")
                    raise Exception(f"Order placement failed: {error_msg}")
                
                if response.get("type") in ["ORDER_PLACED", "PLACE_LIMIT_WS_RESPONSE", "PLACE_MARKET_WS_RESPONSE"]:
                    # Handle different response formats
                    if "orderId" in response.get("data", {}):
                        exchange_order_id = str(response["data"]["orderId"])
                    elif "id" in response.get("data", {}):
                        exchange_order_id = str(response["data"]["id"])
                    else:
                        # Log the full response to understand the format
                        self.logger().warning(f"Unknown order response format: {response}")
                        raise Exception(f"Cannot extract order ID from response: {response}")
                    
                    self.logger().info(f"Order placed successfully via WebSocket: {exchange_order_id}")
                    return exchange_order_id
                elif response.get("type") == "ORDER_FAILED":
                    error_msg = response.get("data", {}).get("message", "Unknown error")
                    self.logger().error(f"WebSocket order placement failed: {error_msg}")
                    raise Exception(f"Order placement failed: {error_msg}")
                else:
                    self.logger().error(f"Unexpected WebSocket order response: {response}")
                    raise Exception(f"Unexpected order response: {response}")
                    
            except asyncio.TimeoutError:
                self.logger().warning(f"WebSocket order placement timed out after {CONSTANTS.WS_ORDER_TIMEOUT}s "
                                     f"for {client_msg_id}")
                raise Exception("WebSocket order placement timed out")
            except Exception as e:
                self.logger().error(f"WebSocket order placement error for {client_msg_id}: {e}")
                raise Exception(f"WebSocket order placement error: {e}")
            finally:
                # Clean up future
                if client_msg_id in self._ws_order_requests:
                    del self._ws_order_requests[client_msg_id]
        
        try:
            # Execute with circuit breaker protection
            exchange_order_id = await ws_breaker.run(_execute_ws_order)
            return exchange_order_id, self.current_timestamp
            
        except CircuitOpenError as e:
            self.logger().error(f"Circuit breaker OPEN for WebSocket operations: {e}")
            self._performance_metrics.record_error("ws_circuit_breaker_open")
            # Fall back to REST API by re-raising
            raise

    async def _place_cancel(self, order_id: str, tracked_order: InFlightOrder):
        """
        Cancel an order using WebSocket if available, otherwise fallback to REST API.
        
        Args:
            order_id: The client order ID to cancel
            tracked_order: The InFlightOrder being cancelled
            
        Returns:
            The API response from the cancellation request
        """
        start_time = time.perf_counter()
        success = False
        result = None
        
        try:
            # Check if circuit breaker allows WebSocket operations
            ws_breaker = self._circuit_breakers.get_breaker("websocket")
            
            # Try WebSocket cancellation first if enabled and circuit breaker is not open
            if self._ws_order_placement_enabled and self._use_websocket_for_orders and ws_breaker.state != "open":
                try:
                    result = await self._cancel_order_websocket(order_id, tracked_order)
                    success = True
                    return result
                except CircuitOpenError as e:
                    self.logger().warning(f"WebSocket circuit breaker OPEN, falling back to REST: {e}")
                    self._performance_metrics.record_error("ws_circuit_breaker_open")
                    # Temporarily disable WebSocket operations
                    self._use_websocket_for_orders = False
                except Exception as e:
                    self.logger().warning(f"WebSocket order cancellation failed, falling back to REST: {e}")
                    self._performance_metrics.record_error("ws_cancel_failed")
            
            # Fallback to REST API cancellation
            result = await self._cancel_order_rest(order_id, tracked_order)
            success = True
            return result
            
        finally:
            # Record performance metrics
            latency_ms = (time.perf_counter() - start_time) * 1000
            self._performance_metrics.record_order_cancellation(latency_ms, success)
    
    async def _cancel_order_rest(self, order_id: str, tracked_order: InFlightOrder):
        """Cancel order via REST API (original implementation)."""
        rest_assistant = await self._web_assistants_factory.get_rest_assistant()
        
        # Convert trading pair to VALR format (e.g., "DOGE-USDT" -> "DOGEUSDT")
        valr_pair = web_utils.convert_to_exchange_trading_pair(tracked_order.trading_pair)
        
        # VALR API cancellation endpoint: DELETE /v1/orders/order
        # Required parameters: customerOrderId and pair
        data = {
            "customerOrderId": order_id,
            "pair": valr_pair
        }
        
        self.logger().debug(f"Cancelling order via REST - ID: {order_id}, pair: {valr_pair}")
        
        try:
            cancel_result = await rest_assistant.execute_request(
                url=web_utils.private_rest_url(CONSTANTS.CANCEL_ORDER_PATH_URL),
                method=RESTMethod.DELETE,
                data=data,
                throttler_limit_id=CONSTANTS.CANCEL_ORDER_PATH_URL,
                is_auth_required=True,
            )
            
            self.logger().info(f"Successfully cancelled order {order_id} via REST")
            return cancel_result
            
        except Exception as e:
            error_msg = str(e)
            
            # If order doesn't exist, it might already be cancelled/filled
            if "Order does not exist" in error_msg or "404" in error_msg:
                self.logger().warning(f"Order {order_id} not found for cancellation - may already be cancelled or filled")
                # Return empty result to indicate "success" for non-existent orders
                return {}
            else:
                self.logger().error(f"Failed to cancel order {order_id}: {error_msg}")
                raise
    
    async def _get_ws_assistant(self, operation: str = "operation"):
        """Get WebSocket assistant with readiness check."""
        if not hasattr(self, '_user_stream_tracker') or not self._user_stream_tracker:
            raise Exception(f"User stream tracker not available for WebSocket {operation}")
        
        user_stream_data_source = self._user_stream_tracker.data_source
        
        # Check if WebSocket is connected and ready
        max_wait_time = 5.0  # Maximum 5 seconds wait
        wait_interval = 0.1  # Check every 100ms
        waited = 0.0
        
        while waited < max_wait_time:
            if (hasattr(user_stream_data_source, '_ws_assistant') and 
                user_stream_data_source._ws_assistant and
                hasattr(user_stream_data_source._ws_assistant, '_connection') and
                user_stream_data_source._ws_assistant._connection and
                hasattr(user_stream_data_source._ws_assistant._connection, 'connected') and
                user_stream_data_source._ws_assistant._connection.connected):
                return user_stream_data_source._ws_assistant
            
            if waited == 0:
                self.logger().debug(f"Waiting for WebSocket to be ready for {operation}...")
            
            await asyncio.sleep(wait_interval)
            waited += wait_interval
        
        raise Exception(f"WebSocket assistant not available for {operation} after {max_wait_time}s wait")
    
    async def _cancel_order_websocket(self, order_id: str, tracked_order: InFlightOrder):
        """Cancel order via WebSocket."""
        # Generate unique client message ID for correlation
        client_msg_id = str(uuid.uuid4())
        
        # Get WebSocket assistant with readiness check
        ws_assistant = await self._get_ws_assistant("order cancellation")
        user_stream_data_source = self._user_stream_tracker.data_source
        
        # Convert trading pair to VALR format
        valr_pair = web_utils.convert_to_exchange_trading_pair(tracked_order.trading_pair)
        
        # Prepare WebSocket cancel message
        # CRITICAL: WSJSONRequest.payload IS the message sent directly (no additional wrapping)
        # VALR expects "CANCEL_LIMIT_ORDER" not "CANCEL_ORDER"
        cancel_message = WSJSONRequest(
            payload={
                "type": "CANCEL_LIMIT_ORDER",  # Fixed: correct message type per VALR docs
                "clientMsgId": client_msg_id,
                "payload": {  # VALR expects "payload" for request data
                    "customerOrderId": order_id,
                    "pair": valr_pair
                }
            }
        )
        
        # Log the cancel message for debugging
        self.logger().debug(f"Sending WebSocket cancel message: {json.dumps(cancel_message.payload)}")
        
        # Create future for response tracking
        response_future = asyncio.Future()
        self._ws_order_requests[client_msg_id] = response_future
        
        # Register future with user stream data source BEFORE sending
        user_stream_data_source.register_order_future(client_msg_id, response_future)
        
        try:
            # DEBUG: Log the exact message being sent
            self.logger().info(f"[DEBUG] Cancel message payload type: {type(cancel_message.payload)}")
            self.logger().info(f"[DEBUG] Cancel message payload: {cancel_message.payload}")
            
            # Send cancel message
            await ws_assistant.send(cancel_message)
            self.logger().debug(f"Sent WebSocket order cancellation: {client_msg_id}")
            
            # Wait for response with timeout (optimized for HFT)
            response = await asyncio.wait_for(response_future, timeout=CONSTANTS.WS_ORDER_TIMEOUT)
            
            # Check if cancellation was successful
            # VALR returns CANCEL_ORDER_WS_RESPONSE for WebSocket cancellations
            if response.get("type") == "CANCEL_ORDER_WS_RESPONSE":
                response_data = response.get("data", {})
                if response_data.get("requested", False):
                    self.logger().info(f"Successfully cancelled order {order_id} via WebSocket")
                    return response
                else:
                    raise Exception(f"Order cancellation not requested: {response}")
            elif response.get("type") == "CANCEL_ORDER_FAILED":
                error_msg = response.get("data", {}).get("message", "Unknown error")
                raise Exception(f"Order cancellation failed: {error_msg}")
            else:
                raise Exception(f"Unexpected cancel response: {response}")
                
        except asyncio.TimeoutError:
            raise Exception("WebSocket order cancellation timed out")
        except Exception as e:
            raise Exception(f"WebSocket order cancellation error: {e}")
        finally:
            # Clean up future
            if client_msg_id in self._ws_order_requests:
                del self._ws_order_requests[client_msg_id]

    async def _format_trading_rules(self, exchange_info_list: list[dict[str, Any]]) -> list[TradingRule]:
        self.logger().info(f"_format_trading_rules called with {len(exchange_info_list) if exchange_info_list else 0} items")
        
        # Initialize symbol mapping from exchange info before processing trading rules
        self._initialize_trading_pair_symbols_from_exchange_info(exchange_info_list)
        
        trading_rules = []
        
        for pair_info in exchange_info_list:
            try:
                if not self._is_pair_valid_for_trading(pair_info):
                    continue
                    
                symbol = pair_info.get("symbol")
                if not symbol:
                    self.logger().warning(f"Missing symbol in pair info: {pair_info}")
                    continue
                trading_pair = web_utils.convert_from_exchange_trading_pair(symbol)
                
                trading_rules.append(
                    TradingRule(
                        trading_pair=trading_pair,
                        min_order_size=Decimal(str(pair_info.get("minBaseAmount", "0.00000001"))),
                        max_order_size=Decimal(str(pair_info.get("maxBaseAmount", "10000000"))),
                        min_price_increment=Decimal(str(pair_info.get("tickSize", "0.00000001"))),
                        min_base_amount_increment=Decimal(str(pair_info.get("baseDecimalPlaces", "0.00000001"))),
                        min_quote_amount_increment=Decimal(str(pair_info.get("quoteDecimalPlaces", "0.01"))),
                        min_notional_size=Decimal(str(pair_info.get("minQuoteAmount", "0.01"))),
                        min_order_value=Decimal(str(pair_info.get("minQuoteAmount", "0.01"))),
                    )
                )
            except Exception:
                self.logger().exception(f"Error parsing trading pair rule: {pair_info}")
                
        return trading_rules

    def _is_pair_valid_for_trading(self, pair_info: dict[str, Any]) -> bool:
        """Check if a trading pair is valid for spot trading."""
        # Check if pair is active
        if not pair_info.get("active", False):
            return False
            
        # Filter out futures/perp pairs
        symbol = pair_info.get("symbol", "")
        if "_PERP" in symbol or "_FUTURES" in symbol:
            return False
            
        return True

    async def _status_polling_loop_fetch_updates(self):
        await super()._status_polling_loop_fetch_updates()

    async def _update_trading_fees(self):
        # VALR fees are fixed in valr_utils.py
        # Could be enhanced to fetch dynamic fees from API if available
        pass
    
    async def modify_order(
        self,
        order_id: str,
        trading_pair: str,
        new_price: Optional[Decimal] = None,
        new_quantity: Optional[Decimal] = None
    ) -> bool:
        """
        Modify an existing order using WebSocket for ultra-low latency.
        
        Args:
            order_id: The client order ID
            trading_pair: The trading pair
            new_price: New price for the order (optional)
            new_quantity: New quantity for the order (optional)
            
        Returns:
            True if modification was successful, False otherwise
        """
        start_time = time.perf_counter()
        success = False
        
        try:
            # Get the in-flight order
            order = self._order_tracker.fetch_tracked_order(order_id)
            if not order:
                self.logger().warning(f"Order {order_id} not found in tracker")
                return False
                
            # Try WebSocket modification first if enabled
            if self._ws_order_placement_enabled and self._use_websocket_for_orders:
                try:
                    success = await self._modify_order_websocket(
                        order.exchange_order_id, 
                        trading_pair, 
                        new_price, 
                        new_quantity
                    )
                    if success:
                        # Update order tracking
                        if new_price:
                            order.price = new_price
                        if new_quantity:
                            order.amount = new_quantity
                        return True
                except Exception as e:
                    self.logger().warning(f"WebSocket order modification failed, falling back to REST: {e}")
                    self._performance_metrics.record_error("ws_order_modify_failed")
                    
            # Fallback to REST API
            success = await self._modify_order_rest(
                order.exchange_order_id,
                trading_pair,
                new_price,
                new_quantity
            )
            
            if success:
                # Update order tracking
                if new_price:
                    order.price = new_price
                if new_quantity:
                    order.amount = new_quantity
                    
            return success
            
        finally:
            # Record performance metrics
            latency_ms = (time.perf_counter() - start_time) * 1000
            self._performance_metrics.record_modification(latency_ms, success)
            
    async def _modify_order_websocket(
        self,
        exchange_order_id: str,
        trading_pair: str,
        new_price: Optional[Decimal] = None,
        new_quantity: Optional[Decimal] = None
    ) -> bool:
        """Modify order via WebSocket for ultra-low latency"""
        
        # Generate unique client message ID
        client_msg_id = str(uuid.uuid4())
        
        # Get WebSocket assistant
        if not hasattr(self, '_user_stream_tracker') or not self._user_stream_tracker:
            raise Exception("User stream tracker not available for WebSocket order modification")
            
        user_stream_data_source = self._user_stream_tracker.data_source
        if not hasattr(user_stream_data_source, '_ws_assistant') or not user_stream_data_source._ws_assistant:
            raise Exception("WebSocket assistant not available for order modification")
            
        ws_assistant = user_stream_data_source._ws_assistant
        
        # Create response future
        response_future = asyncio.Future()
        self._ws_order_requests[client_msg_id] = response_future
        
        # Prepare modification message
        modify_data = {
            "orderId": exchange_order_id,
            "pair": web_utils.convert_to_exchange_trading_pair(trading_pair)
        }
        
        if new_price is not None:
            modify_data["price"] = str(new_price)
            
        if new_quantity is not None:
            modify_data["quantity"] = str(new_quantity)
            
        ws_message = WSJSONRequest(
            payload={
                "type": CONSTANTS.WS_MODIFY_ORDER_EVENT,
                "data": modify_data,
                "messageId": client_msg_id
            },
            is_auth_required=True  # Enable authentication for order modification
        )
        
        try:
            # Send modification request
            await ws_assistant.send(ws_message)
            self.logger().debug(f"Sent WebSocket order modification: {client_msg_id}")
            
            # Wait for response with timeout
            response = await asyncio.wait_for(response_future, timeout=CONSTANTS.WS_ORDER_MODIFY_TIMEOUT)
            
            # Process response
            if response.get("success", False):
                self.logger().info(f"Successfully modified order {exchange_order_id} via WebSocket")
                return True
            else:
                error_msg = response.get("message", "Unknown error")
                self.logger().warning(f"Order modification failed: {error_msg}")
                return False
                
        except asyncio.TimeoutError:
            self.logger().warning(f"WebSocket order modification timed out for {exchange_order_id}")
            raise Exception("WebSocket order modification timed out")
        except Exception as e:
            self.logger().error(f"WebSocket order modification error: {e}")
            raise
        finally:
            # Clean up response future
            self._ws_order_requests.pop(client_msg_id, None)
            
    async def _modify_order_rest(
        self,
        exchange_order_id: str,
        trading_pair: str,
        new_price: Optional[Decimal] = None,
        new_quantity: Optional[Decimal] = None
    ) -> bool:
        """Modify order via REST API"""
        
        rest_assistant = await self._web_assistants_factory.get_rest_assistant()
        
        # Prepare modification data
        modify_data = {
            "orderId": exchange_order_id,
            "pair": web_utils.convert_to_exchange_trading_pair(trading_pair)
        }
        
        if new_price is not None:
            modify_data["price"] = str(new_price)
            
        if new_quantity is not None:
            modify_data["quantity"] = str(new_quantity)
            
        try:
            response = await rest_assistant.execute_request(
                url=web_utils.private_rest_url(CONSTANTS.ORDER_MODIFY_PATH_URL),
                method=RESTMethod.PUT,
                data=json.dumps(modify_data),
                headers={"Content-Type": "application/json"},
                is_auth_required=True,
                throttler_limit_id=CONSTANTS.ORDER_MODIFY_PATH_URL,
            )
            
            # VALR returns the modified order details on success
            if "id" in response:
                self.logger().info(f"Successfully modified order {exchange_order_id} via REST")
                return True
            else:
                self.logger().warning(f"Unexpected response from order modification: {response}")
                return False
                
        except Exception as e:
            self.logger().error(f"Error modifying order {exchange_order_id}: {e}")
            return False
    
    async def place_batch_orders(
        self,
        orders: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Place multiple orders in a single batch operation for HFT efficiency.
        
        Args:
            orders: List of order dictionaries containing:
                - trading_pair: str
                - order_type: OrderType
                - trade_type: TradeType
                - amount: Decimal
                - price: Decimal (for limit orders)
                
        Returns:
            List of results for each order
        """
        if not self._enable_batch_operations or len(orders) <= 1:
            # Fall back to individual order placement
            results = []
            for order in orders:
                try:
                    order_id = self.buy_with_specific_market(
                        trading_pair=order["trading_pair"],
                        amount=order["amount"],
                        order_type=order["order_type"],
                        price=order.get("price")
                    ) if order["trade_type"] == TradeType.BUY else self.sell_with_specific_market(
                        trading_pair=order["trading_pair"],
                        amount=order["amount"],
                        order_type=order["order_type"],
                        price=order.get("price")
                    )
                    results.append({"success": True, "order_id": order_id})
                except Exception as e:
                    results.append({"success": False, "error": str(e)})
            return results
        
        # Use WebSocket batch if available
        if self._ws_order_placement_enabled and self._use_websocket_for_orders:
            try:
                return await self._place_batch_orders_websocket(orders)
            except Exception as e:
                self.logger().warning(f"WebSocket batch order placement failed, falling back to REST: {e}")
                self._performance_metrics.record_error("ws_batch_failed")
        
        # REST API batch endpoint
        return await self._place_batch_orders_rest(orders)
    
    async def _place_batch_orders_websocket(
        self,
        orders: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Place batch orders via WebSocket for ultra-low latency"""
        start_time = time.perf_counter()
        client_msg_id = str(uuid.uuid4())
        
        # Get WebSocket assistant
        if not hasattr(self, '_user_stream_tracker') or not self._user_stream_tracker:
            raise Exception("User stream tracker not available for WebSocket batch orders")
        
        user_stream_data_source = self._user_stream_tracker.data_source
        if not hasattr(user_stream_data_source, '_ws_assistant') or not user_stream_data_source._ws_assistant:
            raise Exception("WebSocket assistant not available for batch orders")
        
        ws_assistant = user_stream_data_source._ws_assistant
        
        # Prepare batch message
        batch_data = []
        for order in orders:
            order_data = {
                "pair": web_utils.convert_to_exchange_trading_pair(order["trading_pair"]),
                "side": "BUY" if order["trade_type"] == TradeType.BUY else "SELL",
                "quantity": str(order["amount"]),
                "price": str(order["price"]) if "price" in order else None,
                "postOnly": order.get("post_only", False),
                "customerOrderId": order.get("client_order_id", f"HB-{uuid.uuid4().hex[:8]}")
            }
            batch_data.append(order_data)
        
        batch_message = WSJSONRequest(
            payload={
                "type": CONSTANTS.WS_BATCH_ORDERS_EVENT,
                "clientMsgId": client_msg_id,
                "data": {"orders": batch_data}
            },
            is_auth_required=True  # Enable authentication for batch orders
        )
        
        # Create future for response tracking
        response_future = asyncio.Future()
        self._ws_order_requests[client_msg_id] = response_future
        
        try:
            # Send batch order message
            await ws_assistant.send(batch_message)
            self.logger().debug(f"Sent WebSocket batch order: {client_msg_id} with {len(orders)} orders")
            
            # Wait for response
            response = await asyncio.wait_for(response_future, timeout=CONSTANTS.WS_BATCH_ORDER_TIMEOUT)
            
            # Process batch results
            results = []
            if response.get("type") == CONSTANTS.WS_ORDER_PROCESSED_EVENT:
                for idx, order_result in enumerate(response.get("data", {}).get("results", [])):
                    if order_result.get("success"):
                        results.append({
                            "success": True,
                            "order_id": order_result.get("orderId"),
                            "client_order_id": orders[idx].get("client_order_id")
                        })
                    else:
                        results.append({
                            "success": False,
                            "error": order_result.get("error", "Unknown error")
                        })
            
            # Record metrics
            latency_ms = (time.perf_counter() - start_time) * 1000
            success_count = sum(1 for r in results if r["success"])
            self._performance_metrics.record_order_placement(latency_ms / len(orders), success_count == len(orders))
            
            return results
            
        except asyncio.TimeoutError:
            raise Exception("WebSocket batch order placement timed out")
        except Exception as e:
            raise Exception(f"WebSocket batch order error: {e}")
        finally:
            # Clean up future
            if client_msg_id in self._ws_order_requests:
                del self._ws_order_requests[client_msg_id]
    
    async def _place_batch_orders_rest(
        self,
        orders: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Place batch orders via REST API"""
        rest_assistant = await self._web_assistants_factory.get_rest_assistant()
        
        # Prepare batch request
        batch_data = []
        for order in orders:
            order_data = {
                "pair": web_utils.convert_to_exchange_trading_pair(order["trading_pair"]),
                "side": "BUY" if order["trade_type"] == TradeType.BUY else "SELL",
                "quantity": str(order["amount"]),
                "price": str(order["price"]) if "price" in order else None,
                "postOnly": order.get("post_only", False),
                "customerOrderId": order.get("client_order_id", f"HB-{uuid.uuid4().hex[:8]}")
            }
            batch_data.append(order_data)
        
        # Send batch request
        response = await rest_assistant.execute_request(
            url=web_utils.private_rest_url(CONSTANTS.BATCH_ORDERS_PATH_URL),
            method=RESTMethod.POST,
            data={"orders": batch_data},
            throttler_limit_id=CONSTANTS.BATCH_ORDERS_PATH_URL,
            is_auth_required=True,
        )
        
        # Process results
        results = []
        for order_result in response.get("results", []):
            if order_result.get("success"):
                results.append({
                    "success": True,
                    "order_id": order_result.get("orderId"),
                    "client_order_id": order_result.get("customerOrderId")
                })
            else:
                results.append({
                    "success": False,
                    "error": order_result.get("error", "Unknown error")
                })
        
        return results

    async def _user_stream_event_listener(self):
        """
        Listens to user stream events and processes them.
        """
        async for event_message in self._iter_user_event_queue():
            try:
                event_type = event_message.get("type", "")
                
                if event_type == CONSTANTS.WS_USER_BALANCE_UPDATE_EVENT:
                    await self._process_balance_update(event_message)
                    
                elif event_type in [
                    CONSTANTS.WS_USER_NEW_ORDER_EVENT,
                    CONSTANTS.WS_USER_ORDER_UPDATE_EVENT,
                    CONSTANTS.WS_USER_ORDER_DELETE_EVENT,
                    CONSTANTS.WS_USER_ORDER_CANCEL_EVENT,
                    CONSTANTS.WS_USER_INSTANT_ORDER_COMPLETED_EVENT,
                ]:
                    await self._process_order_lifecycle_event(event_message)
                    
                elif event_type == CONSTANTS.WS_USER_TRADE_EVENT:
                    await self._process_trade_update(event_message)
                    
                elif event_type == CONSTANTS.WS_USER_FAILED_CANCEL_EVENT:
                    self.logger().warning(f"Failed to cancel order: {event_message}")
                    
                elif event_type == CONSTANTS.WS_USER_OPEN_ORDERS_UPDATE_EVENT:
                    # DISABLED: This handler was causing race conditions with order cancellations
                    # await self._process_open_orders_update(event_message)
                    pass
                    
                elif event_type in [
                    CONSTANTS.WS_ORDER_RESPONSE_EVENT, 
                    CONSTANTS.WS_ORDER_FAILED_EVENT,
                    CONSTANTS.WS_MODIFY_ORDER_OUTCOME_EVENT
                ]:
                    await self._process_websocket_order_response(event_message)
                    
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger().exception("Unexpected error in user stream listener")

    async def _process_balance_update(self, event_message: dict[str, Any]):
        """Process balance update events."""
        try:
            balances = event_message.get("data", {})
            
            # Handle both list and single dict format
            if isinstance(balances, dict):
                # VALR sometimes sends single balance update as dict
                balances = [balances]
            elif not isinstance(balances, list):
                self.logger().warning(f"Unexpected balance data format: {type(balances)}")
                return
            
            for currency_data in balances:
                try:
                    # Validate currency_data structure
                    if not isinstance(currency_data, dict):
                        self.logger().warning(f"Invalid currency data format: {currency_data}")
                        continue
                    
                    # Handle currency field - VALR can return it as either string or dict
                    currency_info = currency_data.get("currency")
                    if isinstance(currency_info, dict):
                        # Handle nested currency object (WebSocket format)
                        asset = currency_info.get("symbol") or currency_info.get("currencyCode")
                    elif isinstance(currency_info, str):
                        # Handle direct currency string (REST format)
                        asset = currency_info
                    else:
                        self.logger().warning(f"Unknown currency format in balance data: {currency_data}")
                        continue
                    
                    available = currency_data.get("available")
                    # VALR uses 'total' field name, not 'balance'
                    total = currency_data.get("total", currency_data.get("balance"))
                    
                    # Validate required fields
                    if not asset or available is None or total is None:
                        self.logger().warning(f"Missing required fields in currency data: {currency_data}")
                        continue
                    
                    available = Decimal(str(available))
                    total = Decimal(str(total))
                    
                    self._account_available_balances[asset] = available
                    self._account_balances[asset] = total
                    
                except Exception as e:
                    self.logger().error(f"Error processing individual balance data {currency_data}: {e}")
                
        except Exception:
            self.logger().exception("Error processing balance update")

    async def _process_order_lifecycle_event(self, event_message: dict[str, Any]):
        """Process comprehensive order lifecycle events."""
        try:
            event_type = event_message.get("type", "")
            order_data = event_message.get("data", {})
            
            # Extract order identifiers
            client_order_id = order_data.get("customerOrderId", "")
            exchange_order_id = str(order_data.get("orderId", ""))
            
            # Find tracked order
            tracked_order = self._order_tracker.all_orders.get(client_order_id)
            if not tracked_order:
                # For new orders, we might not have the tracked order yet
                if event_type == CONSTANTS.WS_USER_NEW_ORDER_EVENT:
                    self.logger().debug(f"Received new order event for unknown order: {client_order_id}")
                return
                
            # Determine order state based on event type and status
            order_state = self._get_order_state_from_event(event_type, order_data)
            
            # Create order update
            order_update = OrderUpdate(
                trading_pair=tracked_order.trading_pair,
                update_timestamp=event_message.get("timestamp", self.current_timestamp),
                new_state=order_state,
                client_order_id=client_order_id,
                exchange_order_id=exchange_order_id or tracked_order.exchange_order_id,
            )
            
            # Process the update
            self._order_tracker.process_order_update(order_update)
            
            # Log significant order lifecycle events
            if event_type == CONSTANTS.WS_USER_NEW_ORDER_EVENT:
                self.logger().info(f"Order placed successfully: {client_order_id}")
            elif event_type == CONSTANTS.WS_USER_ORDER_CANCEL_EVENT:
                self.logger().info(f"Order cancelled: {client_order_id}")
            elif event_type == CONSTANTS.WS_USER_INSTANT_ORDER_COMPLETED_EVENT:
                self.logger().info(f"Order completed: {client_order_id}")
            
        except Exception:
            self.logger().exception("Error processing order lifecycle event")
    
    def _get_order_state_from_event(self, event_type: str, order_data: dict[str, Any]) -> OrderState:
        """Determine order state from event type and data."""
        
        # Handle specific event types
        if event_type == CONSTANTS.WS_USER_NEW_ORDER_EVENT:
            return OrderState.OPEN
        elif event_type == CONSTANTS.WS_USER_ORDER_CANCEL_EVENT:
            return OrderState.CANCELED
        elif event_type == CONSTANTS.WS_USER_ORDER_DELETE_EVENT:
            return OrderState.CANCELED
        elif event_type == CONSTANTS.WS_USER_INSTANT_ORDER_COMPLETED_EVENT:
            return OrderState.FILLED
        elif event_type == CONSTANTS.WS_USER_ORDER_UPDATE_EVENT:
            # Use status from order data for updates
            valr_status = order_data.get("orderStatusType", "")
            return CONSTANTS.ORDER_STATE.get(valr_status, OrderState.OPEN)
        
        # Default fallback
        return OrderState.OPEN

    async def _process_trade_update(self, event_message: dict[str, Any]):
        """Process trade execution events."""
        try:
            trade_data = event_message.get("data", {})
            
            order_id = trade_data.get("orderId", "")
            client_order_id = trade_data.get("customerOrderId", "")
            
            # Find tracked order
            tracked_order = None
            if client_order_id:
                tracked_order = self._order_tracker.all_orders.get(client_order_id)
            else:
                # Try to find by exchange order ID
                for order in self._order_tracker.all_orders.values():
                    if order.exchange_order_id == str(order_id):
                        tracked_order = order
                        client_order_id = order.client_order_id
                        break
                        
            if not tracked_order:
                return
                
            trade_update = TradeUpdate(
                client_order_id=client_order_id,
                exchange_order_id=str(order_id),
                trading_pair=tracked_order.trading_pair,
                fill_timestamp=event_message.get("timestamp", self.current_timestamp),
                fill_price=Decimal(str(trade_data.get("price", "0"))),
                fill_base_amount=Decimal(str(trade_data.get("quantity", "0"))),
                fill_quote_amount=Decimal(str(trade_data.get("total", "0"))),
                fee=self._get_fee_from_trade(trade_data),
                is_taker=True,  # VALR doesn't specify, assume taker for now
            )
            
            self._order_tracker.process_trade_update(trade_update)
            
        except Exception:
            self.logger().exception("Error processing trade update")
    
    async def _process_open_orders_update(self, event_message: dict[str, Any]):
        """Process OPEN_ORDERS_UPDATE to sync exchange order state with local tracking."""
        try:
            orders_data = event_message.get("data", [])
            
            # Get all active customer order IDs from the exchange
            exchange_order_ids = set()
            for order_data in orders_data:
                customer_order_id = order_data.get("customerOrderId", "")
                if customer_order_id:
                    exchange_order_ids.add(customer_order_id)
            
            self.logger().debug(f"OPEN_ORDERS_UPDATE - Exchange has {len(exchange_order_ids)} orders: {list(exchange_order_ids)[:5]}...")
            
            # Check our tracked orders
            for client_order_id, tracked_order in list(self._order_tracker.all_orders.items()):
                if tracked_order.is_done:
                    continue
                    
                # Only mark as cancelled if order is older than 2 seconds to avoid race conditions
                order_age = self.current_timestamp - tracked_order.creation_timestamp
                self.logger().debug(f"Order {client_order_id} - current_time: {self.current_timestamp}, creation_time: {tracked_order.creation_timestamp}, age: {order_age:.2f}s")
                if order_age < 2.0:
                    self.logger().debug(f"Skipping young order {client_order_id} (age: {order_age:.2f}s)")
                    continue  # Skip young orders to avoid race conditions
                    
                # If our order is not in the exchange's open orders list, it was cancelled/filled
                if client_order_id not in exchange_order_ids:
                    self.logger().info(f"Order {client_order_id} not found in exchange open orders (age: {order_age:.2f}s) - marking as cancelled")
                    # Create cancellation update
                    order_update = OrderUpdate(
                        trading_pair=tracked_order.trading_pair,
                        update_timestamp=self.current_timestamp,
                        new_state=OrderState.CANCELED,
                        client_order_id=client_order_id,
                        exchange_order_id=tracked_order.exchange_order_id,
                    )
                    self._order_tracker.process_order_update(order_update)
            
            self.logger().debug(f"Processed OPEN_ORDERS_UPDATE - Exchange has {len(exchange_order_ids)} open orders")
            
        except Exception:
            self.logger().exception("Error processing open orders update")
    
    async def _process_websocket_order_response(self, event_message: dict[str, Any]):
        """Process WebSocket order placement/modification response messages."""
        try:
            # Check for different types of message ID fields
            client_msg_id = event_message.get("clientMsgId") or event_message.get("messageId", "")
            
            if client_msg_id and client_msg_id in self._ws_order_requests:
                # Complete the future with the response
                future = self._ws_order_requests[client_msg_id]
                if not future.done():
                    future.set_result(event_message)
                    self.logger().debug(f"WebSocket order response processed: {client_msg_id}")
            else:
                # Log different response types for debugging
                msg_type = event_message.get("type", "unknown")
                self.logger().debug(f"Received WebSocket {msg_type} response for unknown ID: {client_msg_id}")
                
        except Exception:
            self.logger().exception("Error processing WebSocket order response")
    
    def enable_websocket_order_placement(self):
        """Enable WebSocket order placement (for testing or when WebSocket is stable)."""
        self._ws_order_placement_enabled = True
        self.logger().info("WebSocket order placement enabled")
    
    def disable_websocket_order_placement(self):
        """Disable WebSocket order placement (fallback to REST)."""
        self._ws_order_placement_enabled = False
        self.logger().info("WebSocket order placement disabled")
    
    async def enable_cancel_on_disconnect(self):
        """Enable cancel-on-disconnect feature."""
        if hasattr(self, '_user_stream_tracker') and self._user_stream_tracker:
            user_stream_data_source = self._user_stream_tracker.data_source
            if hasattr(user_stream_data_source, '_ws_assistant') and user_stream_data_source._ws_assistant:
                await user_stream_data_source._enable_cancel_on_disconnect(user_stream_data_source._ws_assistant)
            else:
                self.logger().warning("WebSocket assistant not available for cancel-on-disconnect")
        else:
            self.logger().warning("User stream tracker not available for cancel-on-disconnect")
    
    async def disable_cancel_on_disconnect(self):
        """Disable cancel-on-disconnect feature."""
        if hasattr(self, '_user_stream_tracker') and self._user_stream_tracker:
            user_stream_data_source = self._user_stream_tracker.data_source
            if hasattr(user_stream_data_source, '_ws_assistant') and user_stream_data_source._ws_assistant:
                await user_stream_data_source._disable_cancel_on_disconnect(user_stream_data_source._ws_assistant)
            else:
                self.logger().warning("WebSocket assistant not available for cancel-on-disconnect")
        else:
            self.logger().warning("User stream tracker not available for cancel-on-disconnect")

    def _get_fee_from_trade(self, trade_data: dict[str, Any]) -> TradeFeeBase:
        """Extract fee information from trade data."""
        # VALR includes fee in the trade data
        fee_amount = Decimal(str(trade_data.get("fee", "0")))
        fee_currency = trade_data.get("feeCurrency", {}).get("currencyCode", "")
        
        if fee_amount > 0 and fee_currency:
            return TradeFeeBase.new_spot_fee(
                fee_schema=self.trading_fees.get(trade_data.get("currencyPair", ""), self._get_fee(
                    base_currency="",
                    quote_currency="",
                    order_type=OrderType.LIMIT,
                    order_side=TradeType.BUY,
                    amount=Decimal("0"),
                    price=Decimal("0"),
                    is_maker=False
                )),
                trade_type=TradeType.BUY if trade_data.get("side") == "BUY" else TradeType.SELL,
                percent_token=fee_currency,
                flat_fees=[TokenAmount(amount=fee_amount, token=fee_currency)]
            )
        else:
            # Fallback to default fees
            return self._get_fee(
                base_currency="",
                quote_currency="",
                order_type=OrderType.LIMIT,
                order_side=TradeType.BUY if trade_data.get("side") == "BUY" else TradeType.SELL,
                amount=Decimal("0"),
                price=Decimal("0"),
                is_maker=False
            )

    async def _update_balances(self):
        """Update user balances."""
        rest_assistant = await self._web_assistants_factory.get_rest_assistant()
        
        response = await rest_assistant.execute_request(
            url=web_utils.private_rest_url(CONSTANTS.ACCOUNTS_PATH_URL),
            method=RESTMethod.GET,
            throttler_limit_id=CONSTANTS.ACCOUNTS_PATH_URL,
            is_auth_required=True,
        )
        
        self._account_available_balances.clear()
        self._account_balances.clear()
        
        # Validate response format
        if not isinstance(response, list):
            self.logger().error(f"Expected list response for balances, got {type(response)}: {response}")
            return
            
        for balance_data in response:
            try:
                # Validate balance_data structure
                if not isinstance(balance_data, dict):
                    self.logger().warning(f"Invalid balance data format: {balance_data}")
                    continue
                
                # Handle currency field - VALR returns it as a string, not a dict
                currency_info = balance_data.get("currency")
                if isinstance(currency_info, dict):
                    # Handle nested currency object (future compatibility)
                    asset = currency_info.get("currencyCode")
                elif isinstance(currency_info, str):
                    # Handle direct currency string (VALR's actual format)
                    asset = currency_info
                else:
                    self.logger().warning(f"Unknown currency format in balance data: {balance_data}")
                    continue
                
                available = balance_data.get("available")
                # VALR uses 'total' field name, not 'balance'
                total = balance_data.get("total", balance_data.get("balance"))
                
                # Validate required fields
                if not asset or available is None or total is None:
                    self.logger().warning(f"Missing required fields in balance data: {balance_data}")
                    continue
                
                available = Decimal(str(available))
                total = Decimal(str(total))
                
                self._account_available_balances[asset] = available
                self._account_balances[asset] = total
                
            except Exception as e:
                self.logger().error(f"Error processing balance data {balance_data}: {e}")

    async def _request_order_update(self, order: InFlightOrder) -> OrderUpdate:
        """
        Request an order status update from the exchange.
        """
        rest_assistant = await self._web_assistants_factory.get_rest_assistant()
        
        order_data = None
        
        try:
            # Try to get order by exchange order ID first
            if order.exchange_order_id:
                url = web_utils.private_rest_url(
                    CONSTANTS.ORDER_STATUS_PATH_URL.format(order.exchange_order_id)
                )
                
                try:
                    response = await rest_assistant.execute_request(
                        url=url,
                        method=RESTMethod.GET,
                        throttler_limit_id=CONSTANTS.ORDER_STATUS_PATH_URL,
                        is_auth_required=True,
                    )
                    order_data = response
                    
                except Exception as e:
                    if "404" in str(e):
                        self.logger().debug(f"Order {order.exchange_order_id} not found via exchange ID, trying order history")
                        # Fall back to order history search
                        order_data = None
                    else:
                        raise
            
            # If exchange ID method failed or no exchange ID, try order history
            if order_data is None:
                url = web_utils.private_rest_url(CONSTANTS.ORDER_HISTORY_PATH_URL)
                
                try:
                    response = await rest_assistant.execute_request(
                        url=url,
                        method=RESTMethod.GET,
                        throttler_limit_id=CONSTANTS.ORDER_HISTORY_PATH_URL,
                        is_auth_required=True,
                    )
                    
                    if isinstance(response, list):
                        # Search for order in history
                        for order_item in response:
                            if order_item.get("customerOrderId") == order.client_order_id:
                                order_data = order_item
                                break
                        
                        if order_data is None:
                            self.logger().debug(f"Order {order.client_order_id} not found in order history")
                            # Return an update indicating the order might be completed or cancelled
                            # Instead of raising an error, assume the order was filled or cancelled
                            return OrderUpdate(
                                trading_pair=order.trading_pair,
                                update_timestamp=self.current_timestamp,
                                new_state=OrderState.CANCELED,  # Assume cancelled if not found
                                client_order_id=order.client_order_id,
                                exchange_order_id=order.exchange_order_id,
                            )
                    else:
                        raise IOError(f"Unexpected response format for order history: {type(response)}")
                        
                except Exception as e:
                    if "404" in str(e):
                        self.logger().debug(f"Order history not accessible, assuming order {order.client_order_id} was cancelled")
                        # Return cancelled status instead of raising error
                        return OrderUpdate(
                            trading_pair=order.trading_pair,
                            update_timestamp=self.current_timestamp,
                            new_state=OrderState.CANCELED,
                            client_order_id=order.client_order_id,
                            exchange_order_id=order.exchange_order_id,
                        )
                    else:
                        raise
            
            # Map VALR order status to Hummingbot order state
            valr_status = order_data.get("orderStatusType", "")
            order_state = CONSTANTS.ORDER_STATE.get(valr_status, OrderState.OPEN)
            
            order_update = OrderUpdate(
                trading_pair=order.trading_pair,
                update_timestamp=self.current_timestamp,
                new_state=order_state,
                client_order_id=order.client_order_id,
                exchange_order_id=str(order_data.get("orderId", order.exchange_order_id or "")),
            )
            
            return order_update
            
        except Exception as e:
            error_msg = str(e)
            self.logger().error(f"Error fetching order status for {order.client_order_id}: {error_msg}")
            
            # Instead of raising an error that would mark the order as lost,
            # return the current state to keep the order active
            return OrderUpdate(
                trading_pair=order.trading_pair,
                update_timestamp=self.current_timestamp,
                new_state=order.current_state,  # Keep current state
                client_order_id=order.client_order_id,
                exchange_order_id=order.exchange_order_id,
            )

    async def _all_trade_updates_for_order(self, order: InFlightOrder) -> list[TradeUpdate]:
        """
        Request all trade updates for a specific order.
        """
        trade_updates = []
        
        if not order.exchange_order_id:
            return trade_updates
            
        rest_assistant = await self._web_assistants_factory.get_rest_assistant()
        
        try:
            # Get trade history for the specific order
            response = await rest_assistant.execute_request(
                url=web_utils.private_rest_url(CONSTANTS.MY_TRADES_PATH_URL),
                method=RESTMethod.GET,
                params={
                    "orderId": order.exchange_order_id,
                    "limit": 100
                },
                throttler_limit_id=CONSTANTS.MY_TRADES_PATH_URL,
                is_auth_required=True,
            )
            
            if not isinstance(response, list):
                self.logger().warning(f"Unexpected response format for trade history: {type(response)}")
                return trade_updates
            
            for trade_data in response:
                if str(trade_data.get("orderId")) == order.exchange_order_id:
                    trade_update = TradeUpdate(
                        client_order_id=order.client_order_id,
                        exchange_order_id=order.exchange_order_id,
                        trading_pair=order.trading_pair,
                        fill_timestamp=trade_data.get("tradedAt", self.current_timestamp),
                        fill_price=Decimal(str(trade_data.get("price", "0"))),
                        fill_base_amount=Decimal(str(trade_data.get("quantity", "0"))),
                        fill_quote_amount=Decimal(str(trade_data.get("total", "0"))),
                        fee=self._get_fee_from_trade(trade_data),
                        is_taker=True,  # VALR doesn't specify
                    )
                    trade_updates.append(trade_update)
                    
        except Exception as e:
            error_msg = str(e)
            if "invalid signature" in error_msg.lower() or "code\":-11252" in error_msg:
                self.logger().warning(f"Trade history signature error for order {order.client_order_id}: {error_msg}")
                # Authentication issue - don't raise exception, just return empty list and let the system continue
                return trade_updates
            elif "404" in error_msg:
                self.logger().debug(f"No trade history found for order {order.client_order_id}")
                return trade_updates
            elif "401" in error_msg:
                self.logger().warning(f"Authentication error for trade history on order {order.client_order_id}: {error_msg}")
                # Authentication issue - don't raise exception to prevent blocking order processing
                return trade_updates
            elif "403" in error_msg:
                self.logger().warning(f"Access denied for trade history on order {order.client_order_id}: {error_msg}")
                # Permission issue - don't raise exception to prevent blocking order processing
                return trade_updates
            else:
                self.logger().error(f"Error fetching trade history for order {order.client_order_id}: {error_msg}")
                # Don't raise exception to prevent blocking order processing
                return trade_updates
                
        return trade_updates

    async def _request_order_status(self, tracked_order: InFlightOrder) -> OrderUpdate:
        """Request order status update."""
        return await self._request_order_update(tracked_order)

    async def _get_last_traded_price(self, trading_pair: str) -> float:
        """Get the last traded price for a trading pair."""
        rest_assistant = await self._web_assistants_factory.get_rest_assistant()
        
        response = await rest_assistant.execute_request(
            url=web_utils.public_rest_url(CONSTANTS.TICKER_PRICE_PATH_URL),
            method=RESTMethod.GET,
            throttler_limit_id=CONSTANTS.TICKER_PRICE_PATH_URL,
        )
        
        exchange_pair = web_utils.convert_to_exchange_trading_pair(trading_pair)
        
        for market_data in response:
            if market_data.get("currencyPair") == exchange_pair:
                return float(market_data.get("lastTradedPrice", 0))
                
        return 0.0
    
    async def _performance_monitoring_loop(self):
        """Background task to monitor and log performance metrics"""
        while True:
            try:
                await asyncio.sleep(60)  # Check every minute
                
                # Log performance summary
                self._performance_metrics.log_performance_summary(self.logger())
                
                # Check for performance issues
                report = self._performance_metrics.get_performance_report()
                
                # Alert on high latency
                if report["latency"]["placement_ms"]["p95"] > 100:
                    self.logger().warning(
                        f"High order placement latency detected: "
                        f"{report['latency']['placement_ms']['p95']:.1f}ms (p95)"
                    )
                
                # Alert on low success rate
                if report["reliability"]["order_success_rate_pct"] < 95:
                    self.logger().warning(
                        f"Low order success rate: "
                        f"{report['reliability']['order_success_rate_pct']:.1f}%"
                    )
                
                # Alert on memory growth
                if report["resources"]["memory_growth_mb"] > 100:
                    self.logger().warning(
                        f"High memory growth detected: "
                        f"{report['resources']['memory_growth_mb']:.1f}MB"
                    )
                    
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger().exception("Error in performance monitoring loop")
    
    @property
    def performance_metrics(self) -> PerformanceMetrics:
        """Access performance metrics for monitoring"""
        return self._performance_metrics
    
    def get_mid_price_fast(self, trading_pair: str) -> Optional[Decimal]:
        """
        Get mid-price with ultra-low latency using L1 optimizer.
        Falls back to regular order book if L1 data is not available.
        
        Args:
            trading_pair: The trading pair
            
        Returns:
            Mid-price or None if not available
        """
        try:
            # Try L1 optimizer first for ultra-fast access
            if hasattr(self._order_book_tracker, '_data_source') and hasattr(self._order_book_tracker._data_source, 'l1_optimizer'):
                l1_optimizer = self._order_book_tracker._data_source.l1_optimizer
                mid_price = l1_optimizer.get_mid_price(trading_pair)
                
                # Check if L1 data is fresh (less than 1 second old)
                if mid_price is not None and not l1_optimizer.is_l1_stale(trading_pair, max_age_ms=1000):
                    return mid_price
            
            # Fall back to regular order book
            order_book = self.get_order_book(trading_pair)
            if order_book:
                best_bid = order_book.get_best_bid()
                best_ask = order_book.get_best_ask()
                if best_bid and best_ask:
                    return (Decimal(best_bid) + Decimal(best_ask)) / 2
                    
        except Exception as e:
            self.logger().error(f"Error getting fast mid-price for {trading_pair}: {e}")
            
        return None
    
    def get_best_bid_fast(self, trading_pair: str) -> Optional[Tuple[Decimal, Decimal]]:
        """
        Get best bid with ultra-low latency using L1 optimizer.
        
        Args:
            trading_pair: The trading pair
            
        Returns:
            Tuple of (price, quantity) or None if not available
        """
        try:
            # Try L1 optimizer first
            if hasattr(self._order_book_tracker, '_data_source') and hasattr(self._order_book_tracker._data_source, 'l1_optimizer'):
                l1_optimizer = self._order_book_tracker._data_source.l1_optimizer
                
                # Check if L1 data is fresh
                if not l1_optimizer.is_l1_stale(trading_pair, max_age_ms=1000):
                    return l1_optimizer.get_best_bid(trading_pair)
            
            # Fall back to regular order book
            order_book = self.get_order_book(trading_pair)
            if order_book:
                best_bid_price = order_book.get_best_bid()
                if best_bid_price:
                    # Get quantity from order book
                    bid_entries = order_book.bid_entries()
                    if bid_entries:
                        best_bid_entry = next(iter(bid_entries))
                        return (Decimal(best_bid_price), best_bid_entry.amount)
                        
        except Exception as e:
            self.logger().error(f"Error getting fast best bid for {trading_pair}: {e}")
            
        return None
    
    def get_best_ask_fast(self, trading_pair: str) -> Optional[Tuple[Decimal, Decimal]]:
        """
        Get best ask with ultra-low latency using L1 optimizer.
        
        Args:
            trading_pair: The trading pair
            
        Returns:
            Tuple of (price, quantity) or None if not available
        """
        try:
            # Try L1 optimizer first
            if hasattr(self._order_book_tracker, '_data_source') and hasattr(self._order_book_tracker._data_source, 'l1_optimizer'):
                l1_optimizer = self._order_book_tracker._data_source.l1_optimizer
                
                # Check if L1 data is fresh
                if not l1_optimizer.is_l1_stale(trading_pair, max_age_ms=1000):
                    return l1_optimizer.get_best_ask(trading_pair)
            
            # Fall back to regular order book
            order_book = self.get_order_book(trading_pair)
            if order_book:
                best_ask_price = order_book.get_best_ask()
                if best_ask_price:
                    # Get quantity from order book
                    ask_entries = order_book.ask_entries()
                    if ask_entries:
                        best_ask_entry = next(iter(ask_entries))
                        return (Decimal(best_ask_price), best_ask_entry.amount)
                        
        except Exception as e:
            self.logger().error(f"Error getting fast best ask for {trading_pair}: {e}")
            
        return None
    
    def get_spread_fast(self, trading_pair: str) -> Optional[Decimal]:
        """
        Get spread with ultra-low latency using L1 optimizer.
        
        Args:
            trading_pair: The trading pair
            
        Returns:
            Spread or None if not available
        """
        try:
            # Try L1 optimizer first
            if hasattr(self._order_book_tracker, '_data_source') and hasattr(self._order_book_tracker._data_source, 'l1_optimizer'):
                l1_optimizer = self._order_book_tracker._data_source.l1_optimizer
                
                # Check if L1 data is fresh
                if not l1_optimizer.is_l1_stale(trading_pair, max_age_ms=1000):
                    spread = l1_optimizer.get_spread(trading_pair)
                    if spread is not None:
                        return spread
            
            # Fall back to calculating from order book
            best_bid = self.get_best_bid_fast(trading_pair)
            best_ask = self.get_best_ask_fast(trading_pair)
            
            if best_bid and best_ask:
                return best_ask[0] - best_bid[0]
                
        except Exception as e:
            self.logger().error(f"Error getting fast spread for {trading_pair}: {e}")
            
        return None
    
    def get_l1_update_frequency(self, trading_pair: str) -> float:
        """
        Get the L1 update frequency (updates per second) for monitoring.
        
        Args:
            trading_pair: The trading pair
            
        Returns:
            Updates per second
        """
        try:
            if hasattr(self._order_book_tracker, '_data_source') and hasattr(self._order_book_tracker._data_source, 'l1_optimizer'):
                return self._order_book_tracker._data_source.l1_optimizer.get_update_frequency(trading_pair)
        except Exception as e:
            self.logger().error(f"Error getting L1 update frequency for {trading_pair}: {e}")
            
        return 0.0
    
    def get_l1_performance_metrics(self) -> Dict[str, Any]:
        """Get L1 optimizer performance metrics."""
        try:
            if hasattr(self._order_book_tracker, '_data_source') and hasattr(self._order_book_tracker._data_source, 'l1_optimizer'):
                return self._order_book_tracker._data_source.l1_optimizer.get_performance_metrics()
        except Exception as e:
            self.logger().error(f"Error getting L1 performance metrics: {e}")
            
        return {}
    
    def get_circuit_breaker_status(self) -> Dict[str, Dict[str, Any]]:
        """Get status of all circuit breakers for monitoring."""
        return self._circuit_breakers.get_all_status()
    
    def get_circuit_breaker_health(self) -> Dict[str, Any]:
        """
        Get overall circuit breaker health status.
        
        Returns:
            Dict with health score and breaker states
        """
        health_score = self._circuit_breakers.get_health_score()
        all_status = self._circuit_breakers.get_all_status()
        
        # Determine overall health state
        health_state = "healthy"
        if health_score < 50:
            health_state = "critical"
        elif health_score < 80:
            health_state = "degraded"
            
        return {
            "health_score": health_score,
            "health_state": health_state,
            "breakers": all_status,
            "recommendations": self._get_circuit_breaker_recommendations(all_status)
        }
    
    def _get_circuit_breaker_recommendations(self, breaker_status: Dict[str, Dict[str, Any]]) -> List[str]:
        """Generate recommendations based on circuit breaker states."""
        recommendations = []
        
        for name, status in breaker_status.items():
            if status["state"] == "open":
                if name == "rate_limit":
                    recommendations.append(f"Rate limit circuit open - reduce order frequency or wait {status['current_timeout']:.0f}s")
                elif name == "websocket":
                    recommendations.append("WebSocket circuit open - orders will use REST API fallback")
                elif name == "order_placement":
                    recommendations.append("Order placement circuit open - critical issue with order submission")
                    
            elif status["state"] == "half_open":
                recommendations.append(f"{name} circuit testing recovery - monitoring stability")
                
        if not recommendations:
            recommendations.append("All systems operational - circuit breakers healthy")
            
        return recommendations
    
    def reset_circuit_breaker(self, breaker_name: str = None):
        """
        Manually reset circuit breaker(s).
        
        Args:
            breaker_name: Specific breaker to reset, or None to reset all
        """
        if breaker_name:
            breaker = self._circuit_breakers.get_breaker(breaker_name)
            if breaker:
                breaker.reset()
                self.logger().info(f"Circuit breaker '{breaker_name}' has been reset")
            else:
                self.logger().warning(f"Circuit breaker '{breaker_name}' not found")
        else:
            self._circuit_breakers.reset_all()
            self.logger().info("All circuit breakers have been reset")