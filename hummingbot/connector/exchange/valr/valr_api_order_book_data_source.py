import asyncio
import logging
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
        Creates a websocket assistant connected to the exchange's WebSocket endpoint with retry logic.
        Required by the base class.
        
        Returns:
            A websocket assistant instance connected to the exchange
        """
        max_retries = 5
        base_delay = 2.0
        
        for attempt in range(max_retries):
            try:
                self.logger().debug(f"Attempting WebSocket connection (attempt {attempt + 1}/{max_retries})")
                
                ws_assistant = await self._api_factory.get_ws_assistant()
                
                # Connect to the WebSocket endpoint
                await ws_assistant.connect(
                    ws_url=CONSTANTS.WSS_TRADE_URL,
                    ping_timeout=CONSTANTS.PING_TIMEOUT
                )
                
                self.logger().info(f"Successfully connected to WebSocket on attempt {attempt + 1}")
                return ws_assistant
                
            except Exception as e:
                error_message = str(e).lower()
                
                # Check if this is a 429 rate limit error
                if "429" in error_message or "rate limit" in error_message:
                    if attempt < max_retries - 1:
                        # Calculate exponential backoff delay
                        delay = base_delay * (2 ** attempt)
                        self.logger().warning(f"WebSocket connection rate limited (attempt {attempt + 1}/{max_retries}). "
                                           f"Retrying in {delay:.1f} seconds: {e}")
                        await asyncio.sleep(delay)
                        continue
                    else:
                        self.logger().error(f"Max retries reached for WebSocket connection. "
                                          f"Will operate in REST-only mode: {e}")
                        # Don't raise exception - allow connector to work in REST-only mode
                        raise Exception(f"WebSocket connection failed after {max_retries} attempts due to rate limiting")
                else:
                    # For non-429 errors, retry with shorter delay
                    if attempt < max_retries - 1:
                        delay = 1.0
                        self.logger().warning(f"WebSocket connection error (attempt {attempt + 1}/{max_retries}). "
                                           f"Retrying in {delay:.1f} seconds: {e}")
                        await asyncio.sleep(delay)
                        continue
                    else:
                        self.logger().error(f"Failed to establish WebSocket connection after {max_retries} attempts. "
                                          f"Operating in REST-only mode: {e}")
                        # Don't raise exception - allow connector to work in REST-only mode
                        raise Exception(f"WebSocket connection failed after {max_retries} attempts: {error_message}")
        
        # This should never be reached, but just in case
        raise Exception(f"Unexpected exit from WebSocket connection retry loop")

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
        Subscribes to the trade events and order book events through the Trade WebSocket connection.
        Based on VALR API documentation, the Trade WebSocket requires explicit subscription to events.
        
        Args:
            ws: The websocket assistant used to connect to the exchange
        """
        try:
            # Collect all trading pairs for batch subscription
            exchange_pairs = [web_utils.convert_to_exchange_trading_pair(pair) for pair in self._trading_pairs]
            
            # Subscribe to aggregated orderbook updates for all pairs at once
            # This provides top 40 bids/asks updates
            orderbook_subscribe_request = WSJSONRequest({
                "type": "SUBSCRIBE",
                "subscriptions": [{
                    "event": "AGGREGATED_ORDERBOOK_UPDATE",
                    "pairs": exchange_pairs
                }]
            })
            
            # Subscribe to trade updates for all pairs at once
            trade_subscribe_request = WSJSONRequest({
                "type": "SUBSCRIBE",
                "subscriptions": [{
                    "event": "NEW_TRADE",
                    "pairs": exchange_pairs
                }]
            })
            
            # Subscribe to market summary updates to get trading pair information
            # This helps with symbols mapping initialization
            market_summary_subscribe_request = WSJSONRequest({
                "type": "SUBSCRIBE",
                "subscriptions": [{
                    "event": "MARKET_SUMMARY_UPDATE",
                    "pairs": exchange_pairs
                }]
            })
            
            # Send all subscription requests
            await ws.send(orderbook_subscribe_request)
            await ws.send(trade_subscribe_request) 
            await ws.send(market_summary_subscribe_request)
            
            self.logger().info(f"Subscribed to Trade WebSocket channels for {len(exchange_pairs)} pairs: "
                             f"AGGREGATED_ORDERBOOK_UPDATE, NEW_TRADE, MARKET_SUMMARY_UPDATE")
            
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger().error(
                "Unexpected error occurred subscribing to Trade WebSocket streams...",
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
        
        if event_type == "NEW_TRADE":
            channel = self._trade_messages_queue_key
        elif event_type == "AGGREGATED_ORDERBOOK_UPDATE":
            channel = self._diff_messages_queue_key
        elif event_type == "FULL_ORDERBOOK_SNAPSHOT":
            channel = self._snapshot_messages_queue_key
        elif event_type == "FULL_ORDERBOOK_UPDATE":
            channel = self._snapshot_messages_queue_key
        elif event_type == "MARKET_SUMMARY_UPDATE":
            # Handle market summary updates to help with symbol mapping
            channel = "market_summary"
            
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
            # VALR NEW_TRADE format: {"type": "NEW_TRADE", "currencyPairSymbol": "BTCZAR", "data": {...}}
            pair = raw_message.get("currencyPairSymbol", "")
            trading_pair = web_utils.convert_from_exchange_trading_pair(pair)
            
            if trading_pair not in self._trading_pairs:
                return
            
            # Extract the data payload
            data = raw_message.get("data", {})
            
            # Determine trade type
            side = data.get("takerSide", "").upper()
            trade_type = TradeType.BUY if side == "BUY" else TradeType.SELL
            
            # Create trade message
            trade_msg = OrderBookMessage(
                message_type=OrderBookMessageType.TRADE,
                content={
                    "trading_pair": trading_pair,
                    "trade_type": float(trade_type.value),
                    "trade_id": data.get("id", ""),
                    "price": data.get("price", "0"),
                    "amount": data.get("quantity", "0"),
                    "timestamp": data.get("tradedAt", time.time()),
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
            # VALR AGGREGATED_ORDERBOOK_UPDATE format: {"type": "AGGREGATED_ORDERBOOK_UPDATE", "currencyPairSymbol": "BTCZAR", "data": {...}}
            pair = raw_message.get("currencyPairSymbol", "")
            trading_pair = web_utils.convert_from_exchange_trading_pair(pair)
            
            if trading_pair not in self._trading_pairs:
                return
            
            # Extract the data payload
            data = raw_message.get("data", {})
            
            # Extract bids and asks from the data payload
            raw_bids = data.get("Bids", [])
            raw_asks = data.get("Asks", [])
            
            # Transform VALR API response format to Hummingbot expected format
            # VALR format: [{"side": "sell", "quantity": "0.005", "price": "9500", "currencyPair": "BTCZAR", "orderCount": 1}, ...]
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
            # VALR FULL_ORDERBOOK_SNAPSHOT format: {"type": "FULL_ORDERBOOK_SNAPSHOT", "currencyPairSymbol": "BTCUSDC", "data": {...}}
            pair = raw_message.get("currencyPairSymbol", "")
            trading_pair = web_utils.convert_from_exchange_trading_pair(pair)
            
            if trading_pair not in self._trading_pairs:
                return
            
            # Extract the data payload
            data = raw_message.get("data", {})
            
            # Extract bids and asks from the data payload
            raw_bids = data.get("Bids", [])
            raw_asks = data.get("Asks", [])
            
            # Transform VALR API response format to Hummingbot expected format
            # VALR format: [{"side": "sell", "quantity": "0.005", "price": "9500", "currencyPair": "BTCZAR", "orderCount": 1}, ...]
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

    async def _handle_market_summary_update(self, raw_message: Dict[str, Any]):
        """
        Handle MARKET_SUMMARY_UPDATE messages to help with symbol mapping initialization.
        This helps the connector understand available trading pairs and reach ready state.
        
        Args:
            raw_message: The JSON dictionary of the market summary update event
        """
        try:
            # VALR MARKET_SUMMARY_UPDATE format: {"type": "MARKET_SUMMARY_UPDATE", "currencyPairSymbol": "BTCZAR", "data": {...}}
            pair = raw_message.get("currencyPairSymbol", "")
            
            if pair and self._connector:
                # Convert to hummingbot format
                try:
                    trading_pair = web_utils.convert_from_exchange_trading_pair(pair)
                    
                    # Update the connector's symbol mapping if not already done
                    if hasattr(self._connector, '_trading_pair_symbol_map') and self._connector._trading_pair_symbol_map is not None:
                        if pair not in self._connector._trading_pair_symbol_map:
                            self._connector._trading_pair_symbol_map[pair] = trading_pair
                            self.logger().debug(f"Added symbol mapping: {pair} -> {trading_pair}")
                    
                except Exception as e:
                    self.logger().debug(f"Could not process symbol mapping for {pair}: {e}")
                        
        except Exception:
            self.logger().exception("Error processing market summary update message")

    async def listen_for_order_book_stream(self, output: asyncio.Queue):
        """
        Override to handle VALR-specific message routing including market summary updates.
        Includes ping-pong mechanism to maintain Trade WebSocket connection.
        
        Args:
            output: The queue to send parsed messages to
        """
        ping_task = None
        
        # Message processor with ping/pong handling
        async def message_processor():
            async for raw_message in self._ws_assistant.iter_messages():
                try:
                    message_data = raw_message.data
                    
                    # Handle ping/pong messages for Trade WebSocket
                    if isinstance(message_data, str):
                        if message_data.upper() == "PONG":
                            self.logger().debug("Received PONG response from VALR Trade WebSocket")
                            continue
                        elif message_data.upper() == "PING":
                            # Send PONG response
                            await self._ws_assistant.send({"type": "PONG"})
                            self.logger().debug("Sent PONG response to VALR Trade WebSocket")
                            continue
                    
                    message_type = message_data.get("type", "")
                    
                    # Handle market summary updates separately for symbol mapping
                    if message_type == "MARKET_SUMMARY_UPDATE":
                        await self._handle_market_summary_update(message_data)
                        continue
                    
                    # Process other message types using parent's logic
                    channel = self._channel_originating_message(message_data)
                    if channel:
                        if channel == self._trade_messages_queue_key:
                            await self._parse_trade_message(message_data, output)
                        elif channel == self._diff_messages_queue_key:
                            await self._parse_order_book_diff_message(message_data, output)
                        elif channel in [self._snapshot_messages_queue_key]:
                            await self._parse_order_book_snapshot_message(message_data, output)
                            
                except Exception:
                    self.logger().exception("Error processing order book stream message")
        
        try:
            if not self._ws_assistant:
                self._ws_assistant = await self._connected_websocket_assistant()
                await self._subscribe_channels(self._ws_assistant)
            
            # Start ping task for connection keep-alive (VALR requires ping every 30 seconds)
            ping_task = asyncio.create_task(self._send_ping_messages())
            
            await message_processor()
            
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger().exception("Error in order book stream listener")
            raise
        finally:
            # Clean up ping task
            if ping_task and not ping_task.done():
                ping_task.cancel()
                try:
                    await ping_task
                except asyncio.CancelledError:
                    pass

    async def _send_ping_messages(self):
        """
        Sends periodic PING messages to maintain Trade WebSocket connection.
        VALR requires ping every 30 seconds to keep the connection alive.
        """
        try:
            while True:
                await asyncio.sleep(30)  # VALR requires ping every 30 seconds
                try:
                    if self._ws_assistant:
                        await self._ws_assistant.send({"type": "PING"})
                        self.logger().debug("Sent PING message to VALR Trade WebSocket")
                except Exception as e:
                    self.logger().warning(f"Failed to send PING message to Trade WebSocket: {e}")
                    break
        except asyncio.CancelledError:
            self.logger().debug("Trade WebSocket ping task cancelled")
            raise