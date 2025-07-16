import asyncio
import time
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from hummingbot.connector.exchange.valr import valr_constants as CONSTANTS, valr_web_utils as web_utils
from hummingbot.core.data_type.common import TradeType
from hummingbot.core.data_type.order_book_message import OrderBookMessage, OrderBookMessageType
from hummingbot.core.data_type.order_book_tracker_data_source import OrderBookTrackerDataSource
from hummingbot.core.web_assistant.connections.data_types import RESTMethod, WSJSONRequest
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory
from hummingbot.core.web_assistant.ws_assistant import WSAssistant
from hummingbot.logger import HummingbotLogger

if TYPE_CHECKING:
    from hummingbot.connector.exchange.valr.valr_exchange import ValrExchange


class ValrAPIOrderBookDataSource(OrderBookTrackerDataSource):
    HEARTBEAT_TIME_INTERVAL = 30.0
    TRADE_STREAM_ID = 1
    DIFF_STREAM_ID = 2
    FULL_ORDER_BOOK_RESET_DELTA_SECONDS = 60 * 60

    _logger: Optional[HummingbotLogger] = None

    def __init__(
        self,
        trading_pairs: List[str],
        connector: "ValrExchange",
        api_factory: WebAssistantsFactory,
        domain: str = CONSTANTS.DEFAULT_DOMAIN,
    ):
        super().__init__(trading_pairs)
        self._connector = connector
        self._api_factory = api_factory
        self._domain = domain
        self._trading_pairs = trading_pairs
        self._ws_assistant: Optional[WSAssistant] = None
        
        # Set up proper queue keys for base class integration
        self._trade_messages_queue_key = CONSTANTS.WS_MARKET_TRADE_EVENT
        self._diff_messages_queue_key = CONSTANTS.WS_MARKET_AGGREGATED_ORDERBOOK_UPDATE_EVENT
        self._snapshot_messages_queue_key = CONSTANTS.WS_MARKET_FULL_ORDERBOOK_SNAPSHOT_EVENT

    async def get_last_traded_prices(
        self, trading_pairs: List[str], domain: Optional[str] = None
    ) -> Dict[str, float]:
        """
        Get the last traded prices for the specified trading pairs.
        
        Args:
            trading_pairs: List of trading pairs to get prices for
            domain: Optional domain parameter
            
        Returns:
            Dictionary mapping trading pairs to their last traded prices
        """
        results = {}
        
        rest_assistant = await self._api_factory.get_rest_assistant()
        url = web_utils.public_rest_url(path_url=CONSTANTS.TICKER_PRICE_PATH_URL, domain=domain or self._domain)
        
        response = await rest_assistant.execute_request(
            url=url,
            method=RESTMethod.GET,
            throttler_limit_id=CONSTANTS.TICKER_PRICE_PATH_URL,
        )
        
        for market_data in response:
            try:
                pair = market_data.get("currencyPair", "")
                hb_pair = web_utils.convert_from_exchange_trading_pair(pair)
                
                if hb_pair in trading_pairs:
                    last_price = float(market_data.get("lastTradedPrice", 0))
                    results[hb_pair] = last_price
            except Exception:
                continue
                
        return results

    async def _request_order_book_snapshot(self, trading_pair: str) -> Dict[str, Any]:
        """
        Retrieves a copy of the full order book from the exchange, for a particular trading pair.
        
        Args:
            trading_pair: The trading pair for which the order book will be retrieved
            
        Returns:
            The response from the exchange (JSON dictionary)
        """
        rest_assistant = await self._api_factory.get_rest_assistant()
        exchange_pair = web_utils.convert_to_exchange_trading_pair(trading_pair)
        
        url = web_utils.public_rest_url(
            path_url=CONSTANTS.SNAPSHOT_PATH_URL.format(exchange_pair),
            domain=self._domain
        )
        
        snapshot = await rest_assistant.execute_request(
            url=url,
            method=RESTMethod.GET,
            throttler_limit_id=CONSTANTS.SNAPSHOT_PATH_URL,
        )
        
        return snapshot

    async def _order_book_snapshot(self, trading_pair: str) -> OrderBookMessage:
        """
        Creates an OrderBookMessage containing the snapshot information from the exchange.
        
        Args:
            trading_pair: The trading pair for which to get the snapshot
            
        Returns:
            OrderBookMessage with snapshot data
        """
        snapshot = await self._request_order_book_snapshot(trading_pair)
        
        # Transform VALR API response format to Hummingbot expected format
        # VALR returns: [{"side": "buy", "quantity": "100", "price": "0.5", ...}, ...]
        # Hummingbot expects: [["0.5", "100"], ...]
        
        def transform_orders(orders):
            """Transform VALR order format to Hummingbot format"""
            return [[order["price"], order["quantity"]] for order in orders]
        
        raw_bids = snapshot.get("Bids", [])
        raw_asks = snapshot.get("Asks", [])
        
        # Transform the orders to the expected format
        bids = transform_orders(raw_bids)
        asks = transform_orders(raw_asks)
        
        # Create snapshot message
        snapshot_msg = OrderBookMessage(
            message_type=OrderBookMessageType.SNAPSHOT,
            content={
                "trading_pair": trading_pair,
                "bids": bids,
                "asks": asks,
                "update_id": snapshot.get("SequenceNumber", int(time.time() * 1e3)),
                "timestamp": time.time(),
            },
            timestamp=time.time(),
        )
        
        return snapshot_msg

    async def _connected_websocket_assistant(self) -> WSAssistant:
        """
        Creates a websocket assistant connected to the exchange's WebSocket endpoint.
        Required by the base class.
        
        Returns:
            A websocket assistant instance connected to the exchange
        """
        ws_assistant = await self._api_factory.get_ws_assistant()
        
        # Connect to the WebSocket endpoint
        await ws_assistant.connect(
            ws_url=CONSTANTS.WSS_TRADE_URL,
            ping_timeout=CONSTANTS.PING_TIMEOUT
        )
        
        return ws_assistant

    async def _get_ws_assistant(self) -> WSAssistant:
        """
        Creates or returns a cached websocket assistant.
        Some base class implementations may require this method.
        
        Returns:
            A websocket assistant instance
        """
        if not hasattr(self, '_ws_assistant') or self._ws_assistant is None:
            self._ws_assistant = await self._connected_websocket_assistant()
        return self._ws_assistant

    async def _on_order_stream_interruption(self, websocket_assistant: Optional[WSAssistant]):
        """
        Called when the WebSocket connection is interrupted.
        Cleanup the WebSocket assistant.
        
        Args:
            websocket_assistant: The websocket assistant that was interrupted
        """
        self._ws_assistant = None
        if websocket_assistant is not None:
            await websocket_assistant.disconnect()

    async def _subscribe_channels(self, ws: WSAssistant):
        """
        Subscribes to the trade events and diff orders events through the provided websocket connection.
        Required by the base class.
        
        Args:
            ws: The websocket assistant used to connect to the exchange
        """
        try:
            for trading_pair in self._trading_pairs:
                exchange_pair = web_utils.convert_to_exchange_trading_pair(trading_pair)
                
                # Subscribe to aggregated orderbook updates
                orderbook_subscribe_request = WSJSONRequest({
                    "type": CONSTANTS.WS_SUBSCRIBE_EVENT,
                    "subscriptions": [{
                        "event": "AGGREGATED_ORDERBOOK_UPDATE",
                        "pairs": [exchange_pair]
                    }]
                })
                
                # Subscribe to trade updates
                trade_subscribe_request = WSJSONRequest({
                    "type": CONSTANTS.WS_SUBSCRIBE_EVENT,
                    "subscriptions": [{
                        "event": "NEW_TRADE",
                        "pairs": [exchange_pair]
                    }]
                })
                
                await ws.send(orderbook_subscribe_request)
                await ws.send(trade_subscribe_request)
                
            self.logger().info("Subscribed to public order book and trade channels...")
            
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger().error(
                "Unexpected error occurred subscribing to order book trading and delta streams...",
                exc_info=True
            )
            raise

    def _channel_originating_message(self, event_message: Dict[str, Any]) -> str:
        """
        Identifies the channel for a particular event message. Used to find the correct queue to add the message in.
        Required by the base class.
        
        Args:
            event_message: The event received through the websocket connection
            
        Returns:
            The message channel
        """
        channel = ""
        event_type = event_message.get("type", "")
        
        if event_type == CONSTANTS.WS_MARKET_TRADE_EVENT:
            channel = self._trade_messages_queue_key
        elif event_type == CONSTANTS.WS_MARKET_AGGREGATED_ORDERBOOK_UPDATE_EVENT:
            channel = self._diff_messages_queue_key
        elif event_type == CONSTANTS.WS_MARKET_FULL_ORDERBOOK_SNAPSHOT_EVENT:
            channel = self._snapshot_messages_queue_key
            
        return channel

    async def _parse_trade_message(self, raw_message: Dict[str, Any], message_queue: asyncio.Queue):
        """
        Create an instance of OrderBookMessage of type OrderBookMessageType.TRADE
        Required by the base class.
        
        Args:
            raw_message: The JSON dictionary of the public trade event
            message_queue: Queue where the parsed messages should be stored in
        """
        try:
            pair = raw_message.get("currencyPair", "")
            trading_pair = web_utils.convert_from_exchange_trading_pair(pair)
            
            if trading_pair not in self._trading_pairs:
                return
            
            # Determine trade type
            side = raw_message.get("takerSide", "").upper()
            trade_type = TradeType.BUY if side == "BUY" else TradeType.SELL
            
            # Create trade message
            trade_msg = OrderBookMessage(
                message_type=OrderBookMessageType.TRADE,
                content={
                    "trading_pair": trading_pair,
                    "trade_type": float(trade_type.value),
                    "trade_id": raw_message.get("id", ""),
                    "price": raw_message.get("price", "0"),
                    "amount": raw_message.get("quantity", "0"),
                    "timestamp": raw_message.get("tradedAt", time.time()),
                },
                timestamp=time.time(),
            )
            
            message_queue.put_nowait(trade_msg)
            
        except Exception:
            self.logger().exception("Error processing trade message")

    async def _parse_order_book_diff_message(self, raw_message: Dict[str, Any], message_queue: asyncio.Queue):
        """
        Create an instance of OrderBookMessage of type OrderBookMessageType.DIFF
        Required by the base class.
        
        Args:
            raw_message: The JSON dictionary of the public order book diff event
            message_queue: Queue where the parsed messages should be stored in
        """
        try:
            pair = raw_message.get("currencyPairSymbol", "")
            trading_pair = web_utils.convert_from_exchange_trading_pair(pair)
            
            if trading_pair not in self._trading_pairs:
                return
                
            # Extract bids and asks
            raw_bids = raw_message.get("Bids", [])
            raw_asks = raw_message.get("Asks", [])
            
            # Transform VALR API response format to Hummingbot expected format
            def transform_orders(orders):
                """Transform VALR order format to Hummingbot format"""
                return [[order["price"], order["quantity"]] for order in orders]
            
            bids = transform_orders(raw_bids)
            asks = transform_orders(raw_asks)
            
            # Create diff message
            diff_msg = OrderBookMessage(
                message_type=OrderBookMessageType.DIFF,
                content={
                    "trading_pair": trading_pair,
                    "bids": bids,
                    "asks": asks,
                    "update_id": raw_message.get("SequenceNumber", int(time.time() * 1e3)),
                    "timestamp": time.time(),
                },
                timestamp=time.time(),
            )
            
            message_queue.put_nowait(diff_msg)
            
        except Exception:
            self.logger().exception("Error processing order book diff message")

    async def _parse_order_book_snapshot_message(self, raw_message: Dict[str, Any], message_queue: asyncio.Queue):
        """
        Create an instance of OrderBookMessage of type OrderBookMessageType.SNAPSHOT
        Required by the base class.
        
        Args:
            raw_message: The JSON dictionary of the public order book snapshot event
            message_queue: Queue where the parsed messages should be stored in
        """
        try:
            pair = raw_message.get("currencyPairSymbol", "")
            trading_pair = web_utils.convert_from_exchange_trading_pair(pair)
            
            if trading_pair not in self._trading_pairs:
                return
                
            # Extract bids and asks
            raw_bids = raw_message.get("Bids", [])
            raw_asks = raw_message.get("Asks", [])
            
            # Transform VALR API response format to Hummingbot expected format
            def transform_orders(orders):
                """Transform VALR order format to Hummingbot format"""
                return [[order["price"], order["quantity"]] for order in orders]
            
            bids = transform_orders(raw_bids)
            asks = transform_orders(raw_asks)
            
            # Create snapshot message
            snapshot_msg = OrderBookMessage(
                message_type=OrderBookMessageType.SNAPSHOT,
                content={
                    "trading_pair": trading_pair,
                    "bids": bids,
                    "asks": asks,
                    "update_id": raw_message.get("SequenceNumber", int(time.time() * 1e3)),
                    "timestamp": time.time(),
                },
                timestamp=time.time(),
            )
            
            message_queue.put_nowait(snapshot_msg)
            
        except Exception:
            self.logger().exception("Error processing order book snapshot message")