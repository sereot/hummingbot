import asyncio
import json
import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from hummingbot.connector.exchange.valr import valr_constants as CONSTANTS, valr_web_utils as web_utils
from hummingbot.connector.exchange.valr.valr_api_order_book_data_source import ValrAPIOrderBookDataSource
from hummingbot.connector.exchange.valr.valr_api_user_stream_data_source import ValrAPIUserStreamDataSource
from hummingbot.connector.exchange.valr.valr_auth import ValrAuth
from hummingbot.connector.exchange.valr.valr_utils import ValrConfigMap
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
from hummingbot.core.web_assistant.connections.data_types import RESTMethod
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory

if TYPE_CHECKING:
    from hummingbot.client.config.config_helpers import ClientConfigAdapter


class ValrExchange(ExchangePyBase):
    UPDATE_ORDER_STATUS_MIN_INTERVAL = 10.0

    web_utils = web_utils

    def __init__(
        self,
        client_config_map: "ClientConfigAdapter",
        valr_api_key: str,
        valr_api_secret: str,
        trading_pairs: Optional[List[str]] = None,
        trading_required: bool = True,
        domain: str = CONSTANTS.DEFAULT_DOMAIN,
    ):
        self._api_key = valr_api_key
        self._api_secret = valr_api_secret
        self._domain = domain
        self._trading_required = trading_required
        self._trading_pairs = trading_pairs
        
        super().__init__(client_config_map)
        
        self.logger().info(f"VALR Connector initialized - trading_pairs: {self._trading_pairs}")

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
    def rate_limits_rules(self) -> List[RateLimit]:
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
    def trading_pairs(self) -> List[str]:
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



    def supported_order_types(self) -> List[OrderType]:
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

    def _initialize_trading_pair_symbols_from_exchange_info(self, exchange_info: Dict[str, Any]):
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
        else:
            self.logger().warning("Symbol mapping initialization failed - mapping is empty or None")

    def _get_fee(
        self,
        base_currency: str,
        quote_currency: str,
        order_type: OrderType,
        order_side: TradeType,
        amount: Decimal,
        price: Decimal = Decimal("NaN"),
        is_maker: Optional[bool] = None
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
    ) -> Tuple[str, float]:
        
        exchange_order_id = ""
        
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
        
        exchange_order_id = str(order_result["id"])
        
        return exchange_order_id, self.current_timestamp

    async def _place_cancel(self, order_id: str, tracked_order: InFlightOrder):
        """
        Cancel an order using the proper VALR API endpoint.
        
        Args:
            order_id: The client order ID to cancel
            tracked_order: The InFlightOrder being cancelled
            
        Returns:
            The API response from the cancellation request
        """
        rest_assistant = await self._web_assistants_factory.get_rest_assistant()
        
        # Convert trading pair to VALR format (e.g., "DOGE-USDT" -> "DOGEUSDT")
        valr_pair = web_utils.convert_to_exchange_trading_pair(tracked_order.trading_pair)
        
        # VALR API cancellation endpoint: DELETE /v1/orders/order
        # Required parameters: customerOrderId and pair
        data = {
            "customerOrderId": order_id,
            "pair": valr_pair
        }
        
        self.logger().debug(f"Cancelling order - ID: {order_id}, pair: {valr_pair}")
        
        try:
            cancel_result = await rest_assistant.execute_request(
                url=web_utils.private_rest_url(CONSTANTS.CANCEL_ORDER_PATH_URL),
                method=RESTMethod.DELETE,
                data=data,
                throttler_limit_id=CONSTANTS.CANCEL_ORDER_PATH_URL,
                is_auth_required=True,
            )
            
            self.logger().info(f"Successfully cancelled order {order_id}")
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

    async def _format_trading_rules(self, exchange_info_list: List[Dict[str, Any]]) -> List[TradingRule]:
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

    def _is_pair_valid_for_trading(self, pair_info: Dict[str, Any]) -> bool:
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
                    await self._process_order_update(event_message)
                    
                elif event_type == CONSTANTS.WS_USER_TRADE_EVENT:
                    await self._process_trade_update(event_message)
                    
                elif event_type == CONSTANTS.WS_USER_FAILED_CANCEL_EVENT:
                    self.logger().warning(f"Failed to cancel order: {event_message}")
                    
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger().exception("Unexpected error in user stream listener")

    async def _process_balance_update(self, event_message: Dict[str, Any]):
        """Process balance update events."""
        try:
            balances = event_message.get("data", {})
            
            # Validate balances format
            if not isinstance(balances, list):
                self.logger().warning(f"Expected list for balance data, got {type(balances)}: {balances}")
                return
            
            for currency_data in balances:
                try:
                    # Validate currency_data structure
                    if not isinstance(currency_data, dict):
                        self.logger().warning(f"Invalid currency data format: {currency_data}")
                        continue
                    
                    # Handle currency field - VALR returns it as a string, not a dict
                    currency_info = currency_data.get("currency")
                    if isinstance(currency_info, dict):
                        # Handle nested currency object (future compatibility)
                        asset = currency_info.get("currencyCode")
                    elif isinstance(currency_info, str):
                        # Handle direct currency string (VALR's actual format)
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

    async def _process_order_update(self, event_message: Dict[str, Any]):
        """Process order update events."""
        try:
            order_data = event_message.get("data", {})
            client_order_id = order_data.get("customerOrderId", "")
            
            tracked_order = self._order_tracker.all_orders.get(client_order_id)
            if not tracked_order:
                return
                
            # Map VALR order status to Hummingbot order state
            valr_status = order_data.get("orderStatusType", "")
            order_state = CONSTANTS.ORDER_STATE.get(valr_status, OrderState.OPEN)
            
            order_update = OrderUpdate(
                trading_pair=tracked_order.trading_pair,
                update_timestamp=event_message.get("timestamp", self.current_timestamp),
                new_state=order_state,
                client_order_id=client_order_id,
                exchange_order_id=str(order_data.get("orderId", "")),
            )
            
            self._order_tracker.process_order_update(order_update)
            
        except Exception:
            self.logger().exception("Error processing order update")

    async def _process_trade_update(self, event_message: Dict[str, Any]):
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

    def _get_fee_from_trade(self, trade_data: Dict[str, Any]) -> TradeFeeBase:
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

    async def _all_trade_updates_for_order(self, order: InFlightOrder) -> List[TradeUpdate]:
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