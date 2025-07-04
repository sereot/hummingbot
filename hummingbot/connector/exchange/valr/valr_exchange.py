import asyncio
import json
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
        return ValrAPIUserStreamDataSource(
            auth=self._auth,
            trading_pairs=self._trading_pairs,
            connector=self,
            api_factory=self._web_assistants_factory,
            domain=self._domain
        )

    def _initialize_trading_pair_symbols_from_exchange_info(self, exchange_info: Dict[str, Any]):
        """
        Initialize trading pair symbols from exchange info.
        VALR provides pairs info in a list format.
        """
        if isinstance(exchange_info, list):
            for pair_info in exchange_info:
                if self._is_pair_valid_for_trading(pair_info):
                    exchange_symbol = pair_info.get("symbol", "")
                    try:
                        trading_pair = web_utils.convert_from_exchange_trading_pair(exchange_symbol)
                        self._set_trading_pair_symbol_map(
                            exchange_symbol=exchange_symbol,
                            trading_pair=trading_pair
                        )
                    except Exception:
                        self.logger().exception(f"Error processing trading pair {exchange_symbol}")

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
        
        rest_assistant = await self._web_assistants_factory.get_rest_assistant()
        
        if tracked_order.exchange_order_id:
            # Use order ID endpoint
            cancel_url = web_utils.private_rest_url(
                CONSTANTS.ORDER_PATH_URL.format(tracked_order.exchange_order_id)
            )
            cancel_result = await rest_assistant.execute_request(
                url=cancel_url,
                method=RESTMethod.DELETE,
                throttler_limit_id=CONSTANTS.CANCEL_ORDER_PATH_URL,
                is_auth_required=True,
            )
        else:
            # Try to cancel by client order ID
            data = {
                "customerOrderId": order_id,
                "pair": web_utils.convert_to_exchange_trading_pair(tracked_order.trading_pair)
            }
            
            cancel_result = await rest_assistant.execute_request(
                url=web_utils.private_rest_url(CONSTANTS.CANCEL_ORDER_PATH_URL),
                method=RESTMethod.DELETE,
                data=data,
                throttler_limit_id=CONSTANTS.CANCEL_ORDER_PATH_URL,
                is_auth_required=True,
            )
        
        return cancel_result

    async def _format_trading_rules(self, exchange_info_list: List[Dict[str, Any]]) -> List[TradingRule]:
        trading_rules = []
        
        for pair_info in exchange_info_list:
            try:
                if not self._is_pair_valid_for_trading(pair_info):
                    continue
                    
                trading_pair = web_utils.convert_from_exchange_trading_pair(pair_info["symbol"])
                
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
            
            for currency_data in balances:
                asset = currency_data["currency"]["currencyCode"]
                available = Decimal(str(currency_data["available"]))
                total = Decimal(str(currency_data["balance"]))
                
                self._account_available_balances[asset] = available
                self._account_balances[asset] = total
                
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
        
        for balance_data in response:
            asset = balance_data["currency"]["currencyCode"]
            available = Decimal(str(balance_data["available"]))
            total = Decimal(str(balance_data["balance"]))
            
            self._account_available_balances[asset] = available
            self._account_balances[asset] = total

    async def _request_order_update(self, order: InFlightOrder) -> OrderUpdate:
        """
        Request an order status update from the exchange.
        """
        rest_assistant = await self._web_assistants_factory.get_rest_assistant()
        
        # Try to get order by exchange order ID first
        if order.exchange_order_id:
            url = web_utils.private_rest_url(
                CONSTANTS.ORDER_STATUS_PATH_URL.format(order.exchange_order_id)
            )
        else:
            # Fall back to searching by client order ID
            url = web_utils.private_rest_url(CONSTANTS.ORDER_HISTORY_PATH_URL)
            
        response = await rest_assistant.execute_request(
            url=url,
            method=RESTMethod.GET,
            throttler_limit_id=CONSTANTS.ORDER_STATUS_PATH_URL,
            is_auth_required=True,
        )
        
        # Handle response based on endpoint used
        if isinstance(response, list):
            # Search for order in history
            for order_data in response:
                if order_data.get("customerOrderId") == order.client_order_id:
                    response = order_data
                    break
            else:
                raise IOError(f"Order {order.client_order_id} not found")
        
        # Map VALR order status to Hummingbot order state
        valr_status = response.get("orderStatusType", "")
        order_state = CONSTANTS.ORDER_STATE.get(valr_status, OrderState.OPEN)
        
        order_update = OrderUpdate(
            trading_pair=order.trading_pair,
            update_timestamp=self.current_timestamp,
            new_state=order_state,
            client_order_id=order.client_order_id,
            exchange_order_id=str(response.get("orderId", "")),
        )
        
        return order_update

    async def _all_trade_updates_for_order(self, order: InFlightOrder) -> List[TradeUpdate]:
        """
        Request all trade updates for a specific order.
        """
        trade_updates = []
        
        if not order.exchange_order_id:
            return trade_updates
            
        rest_assistant = await self._web_assistants_factory.get_rest_assistant()
        
        # Get trade history
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