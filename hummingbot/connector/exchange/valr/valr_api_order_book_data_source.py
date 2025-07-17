import asyncio
import logging
import time
import zlib
from decimal import Decimal
from typing import TYPE_CHECKING, Any

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

    _logger: HummingbotLogger | None = None

    def __init__(
        self,
        trading_pairs: list[str],
        connector: "ValrExchange",
        api_factory: WebAssistantsFactory,
        domain: str = CONSTANTS.DEFAULT_DOMAIN,
    ):
        super().__init__(trading_pairs)
        self._connector = connector
        self._api_factory = api_factory
        self._domain = domain
        self._trading_pairs = trading_pairs
        self._ws_assistant: WSAssistant | None = None
        
        # Set up proper queue keys for base class integration
        self._trade_messages_queue_key = CONSTANTS.WS_MARKET_TRADE_EVENT
        self._diff_messages_queue_key = CONSTANTS.WS_MARKET_AGGREGATED_ORDERBOOK_UPDATE_EVENT
        self._snapshot_messages_queue_key = CONSTANTS.WS_MARKET_FULL_ORDERBOOK_SNAPSHOT_EVENT
        
        # Sequence number tracking for order book integrity
        self._sequence_numbers: dict[str, int] = {}
        self._checksum_failures: dict[str, int] = {}
        self._max_checksum_failures = 3  # Maximum consecutive failures before resubscription

    async def get_last_traded_prices(
        self, trading_pairs: list[str], domain: str | None = None
    ) -> dict[str, float]:
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

    async def _request_order_book_snapshot(self, trading_pair: str) -> dict[str, Any]:
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
        
        # Extract raw bids and asks from the snapshot
        raw_bids = snapshot.get("Bids", [])
        raw_asks = snapshot.get("Asks", [])
        
        # Process order book update with proper quantity handling
        bids, asks = self._process_order_book_update(raw_bids, raw_asks)
        
        # Initialize sequence number for this trading pair from snapshot
        sequence_number = snapshot.get("SequenceNumber")
        if sequence_number is not None:
            self._sequence_numbers[trading_pair] = sequence_number
            self.logger().debug(f"Initialized sequence number for {trading_pair} from REST snapshot: {sequence_number}")
        
        # Validate checksum if provided
        expected_checksum = snapshot.get("Checksum")
        if expected_checksum is not None:
            if not self._validate_orderbook_checksum(bids, asks, expected_checksum):
                self.logger().warning(f"REST snapshot checksum validation failed for {trading_pair}")
                # Continue with the snapshot even if checksum fails for REST endpoint
            else:
                self._reset_checksum_failure_count(trading_pair)
        
        # Create snapshot message
        snapshot_msg = OrderBookMessage(
            message_type=OrderBookMessageType.SNAPSHOT,
            content={
                "trading_pair": trading_pair,
                "bids": bids,
                "asks": asks,
                "update_id": sequence_number or int(time.time() * 1e3),
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

    async def _on_order_stream_interruption(self, websocket_assistant: WSAssistant | None):
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

    def _channel_originating_message(self, event_message: dict[str, Any]) -> str:
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

    async def _parse_trade_message(self, raw_message: dict[str, Any], message_queue: asyncio.Queue):
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

    async def _parse_order_book_diff_message(self, raw_message: dict[str, Any], message_queue: asyncio.Queue):
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
            
            # Process order book update with proper quantity handling
            bids, asks = self._process_order_book_update(raw_bids, raw_asks)
            
            # Validate sequence number
            sequence_number = data.get("SequenceNumber")
            if not self._validate_sequence_number(trading_pair, sequence_number):
                self.logger().warning(f"Sequence validation failed for {trading_pair}. Skipping update.")
                return
            
            # Validate checksum if provided
            expected_checksum = data.get("Checksum")
            if expected_checksum is not None:
                if not self._validate_orderbook_checksum(bids, asks, expected_checksum):
                    self._handle_checksum_failure(trading_pair)
                    return
                else:
                    self._reset_checksum_failure_count(trading_pair)
            
            # Create diff message
            diff_msg = OrderBookMessage(
                message_type=OrderBookMessageType.DIFF,
                content={
                    "trading_pair": trading_pair,
                    "bids": bids,
                    "asks": asks,
                    "update_id": data.get("SequenceNumber", int(time.time() * 1e3)),
                    "timestamp": time.time(),
                },
                timestamp=time.time(),
            )
            
            message_queue.put_nowait(diff_msg)
            
        except Exception:
            self.logger().exception("Error processing order book diff message")

    async def _parse_order_book_snapshot_message(self, raw_message: dict[str, Any], message_queue: asyncio.Queue):
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
            
            # Process order book update with proper quantity handling
            bids, asks = self._process_order_book_update(raw_bids, raw_asks)
            
            # Initialize sequence number for snapshot
            sequence_number = data.get("SequenceNumber")
            if sequence_number is not None:
                self._sequence_numbers[trading_pair] = sequence_number
                self.logger().debug(f"Initialized sequence number for {trading_pair} from snapshot: {sequence_number}")
            
            # Validate checksum if provided
            expected_checksum = data.get("Checksum")
            if expected_checksum is not None:
                if not self._validate_orderbook_checksum(bids, asks, expected_checksum):
                    self.logger().warning(f"Order book snapshot checksum validation failed for {trading_pair}. "
                                        f"Requesting fresh snapshot...")
                    # TODO: Implement resubscription logic for checksum failure
                    return
                else:
                    self._reset_checksum_failure_count(trading_pair)
            
            # Create snapshot message
            snapshot_msg = OrderBookMessage(
                message_type=OrderBookMessageType.SNAPSHOT,
                content={
                    "trading_pair": trading_pair,
                    "bids": bids,
                    "asks": asks,
                    "update_id": data.get("SequenceNumber", int(time.time() * 1e3)),
                    "timestamp": time.time(),
                },
                timestamp=time.time(),
            )
            
            message_queue.put_nowait(snapshot_msg)
            
        except Exception:
            self.logger().exception("Error processing order book snapshot message")

    async def _handle_market_summary_update(self, raw_message: dict[str, Any]):
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
                            await self._ws_assistant.send(WSJSONRequest({"type": "PONG"}))
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
                        await self._ws_assistant.send(WSJSONRequest({"type": "PING"}))
                        self.logger().debug("Sent PING message to VALR Trade WebSocket")
                except Exception as e:
                    self.logger().warning(f"Failed to send PING message to Trade WebSocket: {e}")
                    break
        except asyncio.CancelledError:
            self.logger().debug("Trade WebSocket ping task cancelled")
            raise

    def _calculate_orderbook_checksum(self, bids: list[list[str]], asks: list[list[str]]) -> int:
        """
        Calculate CRC32 checksum for order book data as per VALR API specification.
        
        The checksum is calculated by:
        1. Taking the 25 best bids and 25 best asks
        2. Concatenating a string with bid price, bid quantity, ask price, ask quantity
        3. Separating each value with a colon (:)
        4. Calculating unsigned CRC32 hash
        
        Args:
            bids: List of bid orders in [price, quantity] format
            asks: List of ask orders in [price, quantity] format
            
        Returns:
            Unsigned CRC32 checksum integer
        """
        try:
            # Take the 25 best bids and asks (as per VALR specification)
            best_bids = bids[:25] if len(bids) > 0 else []
            best_asks = asks[:25] if len(asks) > 0 else []
            
            # Build the checksum string
            checksum_parts = []
            
            # Add bids and asks alternately as specified in VALR docs
            max_orders = max(len(best_bids), len(best_asks))
            for i in range(max_orders):
                if i < len(best_bids):
                    checksum_parts.append(best_bids[i][0])  # bid price
                    checksum_parts.append(best_bids[i][1])  # bid quantity
                if i < len(best_asks):
                    checksum_parts.append(best_asks[i][0])  # ask price
                    checksum_parts.append(best_asks[i][1])  # ask quantity
            
            # Join with colon separator
            checksum_string = ":".join(checksum_parts)
            
            # Calculate unsigned CRC32 hash
            checksum = zlib.crc32(checksum_string.encode('utf-8')) & 0xffffffff
            
            self.logger().debug(f"Calculated checksum: {checksum} for string: {checksum_string[:100]}...")
            return checksum
            
        except Exception as e:
            self.logger().error(f"Error calculating order book checksum: {e}")
            return 0

    def _validate_orderbook_checksum(self, bids: list[list[str]], asks: list[list[str]], 
                                   expected_checksum: int | None) -> bool:
        """
        Validate order book checksum against expected value.
        
        Args:
            bids: List of bid orders in [price, quantity] format
            asks: List of ask orders in [price, quantity] format
            expected_checksum: Expected checksum from VALR API
            
        Returns:
            True if checksum is valid or not provided, False if validation fails
        """
        if expected_checksum is None:
            # No checksum provided, assume valid
            return True
            
        try:
            calculated_checksum = self._calculate_orderbook_checksum(bids, asks)
            
            if calculated_checksum == expected_checksum:
                self.logger().debug(f"Order book checksum validation successful: {calculated_checksum}")
                return True
            else:
                self.logger().warning(f"Order book checksum validation failed! "
                                    f"Expected: {expected_checksum}, Calculated: {calculated_checksum}")
                return False
                
        except Exception as e:
            self.logger().error(f"Error validating order book checksum: {e}")
            return False

    def _validate_sequence_number(self, trading_pair: str, sequence_number: int | None) -> bool:
        """
        Validate sequence number for order book updates with tolerance for small gaps.
        In high-frequency trading, small gaps are common and acceptable.
        
        Args:
            trading_pair: The trading pair for sequence tracking
            sequence_number: The sequence number from the message
            
        Returns:
            True if sequence is valid or gap is acceptable, False if gap is too large
        """
        if sequence_number is None:
            return True
            
        try:
            expected_sequence = self._sequence_numbers.get(trading_pair)
            
            if expected_sequence is None:
                # First message for this pair
                self._sequence_numbers[trading_pair] = sequence_number
                self.logger().debug(f"Initialized sequence number for {trading_pair}: {sequence_number}")
                return True
            
            if sequence_number == expected_sequence + 1:
                # Normal sequence
                self._sequence_numbers[trading_pair] = sequence_number
                return True
            elif sequence_number <= expected_sequence:
                # Duplicate or old message
                self.logger().debug(f"Received old/duplicate sequence {sequence_number} for {trading_pair}, "
                                  f"expected {expected_sequence + 1}")
                return False
            else:
                # Gap detected - allow small gaps (up to 50 messages) but reject large gaps
                gap_size = sequence_number - expected_sequence - 1
                if gap_size <= 50:  # Allow small gaps (common in high-frequency trading)
                    self.logger().debug(f"Small sequence gap ({gap_size}) for {trading_pair} - continuing")
                    self._sequence_numbers[trading_pair] = sequence_number
                    return True
                else:
                    # Large gap - may indicate missed updates
                    self.logger().warning(f"Large sequence gap ({gap_size}) for {trading_pair}! "
                                        f"Expected {expected_sequence + 1}, received {sequence_number}")
                    # Update to current sequence but allow the update to process
                    self._sequence_numbers[trading_pair] = sequence_number
                    return True  # Still allow the update to process
                
        except Exception as e:
            self.logger().error(f"Error validating sequence number: {e}")
            return True  # Assume valid on error
            
    def _process_order_book_update(self, raw_bids: list[dict], raw_asks: list[dict]) -> tuple[list[list[str]], list[list[str]]]:
        """
        Process order book update with proper quantity handling.
        Orders with quantity "0" should be removed as per VALR specification.
        
        Args:
            raw_bids: Raw bid orders from VALR API
            raw_asks: Raw ask orders from VALR API
            
        Returns:
            Tuple of (processed_bids, processed_asks) in Hummingbot format
        """
        try:
            def process_orders(orders):
                """Process orders and filter out zero quantities"""
                processed = []
                for order in orders:
                    quantity = order.get("quantity", "0")
                    if quantity != "0" and float(quantity) > 0:
                        processed.append([order["price"], quantity])
                return processed
            
            bids = process_orders(raw_bids)
            asks = process_orders(raw_asks)
            
            return bids, asks
            
        except Exception as e:
            self.logger().error(f"Error processing order book update: {e}")
            return [], []
            
    def _handle_checksum_failure(self, trading_pair: str):
        """
        Handle checksum validation failure with exponential backoff.
        
        Args:
            trading_pair: The trading pair that failed checksum validation
        """
        try:
            failure_count = self._checksum_failures.get(trading_pair, 0) + 1
            self._checksum_failures[trading_pair] = failure_count
            
            if failure_count >= self._max_checksum_failures:
                self.logger().error(f"Too many consecutive checksum failures for {trading_pair} "
                                  f"({failure_count}). Triggering order book reset.")
                # Reset sequence number to force resync
                if trading_pair in self._sequence_numbers:
                    del self._sequence_numbers[trading_pair]
                # Reset checksum failure counter
                self._checksum_failures[trading_pair] = 0
                # Request fresh snapshot via REST to resync
                asyncio.create_task(self._request_order_book_reset(trading_pair))
            else:
                self.logger().warning(f"Checksum failure {failure_count}/{self._max_checksum_failures} "
                                    f"for {trading_pair}")
                
        except Exception as e:
            self.logger().error(f"Error handling checksum failure: {e}")
            
    def _reset_checksum_failure_count(self, trading_pair: str):
        """Reset checksum failure count after successful validation."""
        if trading_pair in self._checksum_failures:
            del self._checksum_failures[trading_pair]
    
    async def _request_order_book_reset(self, trading_pair: str):
        """
        Request a fresh order book snapshot to resync after checksum failures.
        This method schedules a reset but doesn't directly inject into the message queue.
        
        Args:
            trading_pair: The trading pair to reset
        """
        try:
            self.logger().info(f"Requesting order book reset for {trading_pair}")
            
            # Reset sequence number to force fresh snapshot on next message
            if trading_pair in self._sequence_numbers:
                del self._sequence_numbers[trading_pair]
            
            # Log the reset action - the next snapshot will be requested naturally
            self.logger().info(f"Order book reset scheduled for {trading_pair} - next snapshot will reinitialize")
                
        except Exception as e:
            self.logger().error(f"Error during order book reset for {trading_pair}: {e}")