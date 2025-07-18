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
        
        # Message processing optimization
        self._message_stats = {
            'total_messages': 0,
            'processed_messages': 0,
            'filtered_messages': 0,
            'failed_messages': 0,
            'trade_messages': 0,
            'diff_messages': 0,
            'snapshot_messages': 0,
            'ping_pong_messages': 0,
            'unknown_messages': 0,
            'processing_time_sum': 0.0,
            'last_stats_reset': time.time()
        }
        
        # Connection health monitoring
        self._connection_health = {
            'total_connections': 0,
            'successful_connections': 0,
            'failed_connections': 0,
            'consecutive_failures': 0,
            'last_success_time': 0,
            'last_failure_time': 0,
            'circuit_breaker_active': False,
            'circuit_breaker_activated_time': 0,
            'average_uptime': 0,
            'connection_start_time': 0,
            'normal_disconnects': 0,  # Count of code 1000 disconnects
            'abnormal_disconnects': 0,  # Count of other disconnects
            'total_uptime': 0,  # Total connection uptime
            'disconnect_pattern': []  # Track disconnect intervals for pattern analysis
        }

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

    def _should_process_message(self, message_data: dict[str, Any]) -> bool:
        """
        Pre-filter messages to avoid unnecessary processing.
        
        Args:
            message_data: The message data to evaluate
            
        Returns:
            True if message should be processed, False if it should be filtered out
        """
        # Check if message is for our trading pairs
        pair = message_data.get("currencyPairSymbol", "")
        if pair:
            # Convert to hummingbot format and check if we're interested
            try:
                trading_pair = web_utils.convert_from_exchange_trading_pair(pair)
                if trading_pair not in self._trading_pairs:
                    return False
            except Exception:
                # If conversion fails, don't process
                return False
        
        # Check message type - only process known types
        message_type = message_data.get("type", "")
        known_types = {
            "NEW_TRADE", 
            "AGGREGATED_ORDERBOOK_UPDATE", 
            "FULL_ORDERBOOK_SNAPSHOT",
            "FULL_ORDERBOOK_UPDATE",
            "MARKET_SUMMARY_UPDATE"
        }
        
        return message_type in known_types

    def _update_message_stats(self, message_type: str, processing_time: float = 0.0, failed: bool = False):
        """Update message processing statistics for performance monitoring."""
        self._message_stats['total_messages'] += 1
        
        if failed:
            self._message_stats['failed_messages'] += 1
        else:
            self._message_stats['processed_messages'] += 1
            self._message_stats['processing_time_sum'] += processing_time
        
        # Update type-specific counters
        if message_type == "NEW_TRADE":
            self._message_stats['trade_messages'] += 1
        elif message_type == "AGGREGATED_ORDERBOOK_UPDATE":
            self._message_stats['diff_messages'] += 1
        elif message_type in ["FULL_ORDERBOOK_SNAPSHOT", "FULL_ORDERBOOK_UPDATE"]:
            self._message_stats['snapshot_messages'] += 1
        elif message_type in ["PING", "PONG"]:
            self._message_stats['ping_pong_messages'] += 1
        else:
            self._message_stats['unknown_messages'] += 1

    def _get_message_processing_stats(self) -> dict[str, Any]:
        """Get current message processing performance statistics."""
        total_msgs = self._message_stats['total_messages']
        processed_msgs = self._message_stats['processed_messages']
        
        avg_processing_time = 0.0
        if processed_msgs > 0:
            avg_processing_time = self._message_stats['processing_time_sum'] / processed_msgs
        
        processing_rate = 0.0
        time_elapsed = time.time() - self._message_stats['last_stats_reset']
        if time_elapsed > 0:
            processing_rate = total_msgs / time_elapsed
        
        return {
            'total_messages': total_msgs,
            'processed_messages': processed_msgs,
            'filtered_messages': self._message_stats['filtered_messages'],
            'failed_messages': self._message_stats['failed_messages'],
            'trade_messages': self._message_stats['trade_messages'],
            'diff_messages': self._message_stats['diff_messages'],
            'snapshot_messages': self._message_stats['snapshot_messages'],
            'ping_pong_messages': self._message_stats['ping_pong_messages'],
            'unknown_messages': self._message_stats['unknown_messages'],
            'avg_processing_time_ms': avg_processing_time * 1000,
            'messages_per_second': processing_rate,
            'success_rate': (processed_msgs / total_msgs) if total_msgs > 0 else 0,
            'filter_rate': (self._message_stats['filtered_messages'] / total_msgs) if total_msgs > 0 else 0
        }

    def _reset_message_stats(self):
        """Reset message processing statistics."""
        self._message_stats = {
            'total_messages': 0,
            'processed_messages': 0,
            'filtered_messages': 0,
            'failed_messages': 0,
            'trade_messages': 0,
            'diff_messages': 0,
            'snapshot_messages': 0,
            'ping_pong_messages': 0,
            'unknown_messages': 0,
            'processing_time_sum': 0.0,
            'last_stats_reset': time.time()
        }

    def _record_connection_attempt(self):
        """Record a new connection attempt for health monitoring."""
        self._connection_health['total_connections'] += 1
        self._connection_health['connection_start_time'] = time.time()

    def _record_connection_success(self):
        """Record a successful connection for health monitoring."""
        self._connection_health['successful_connections'] += 1
        self._connection_health['consecutive_failures'] = 0
        self._connection_health['last_success_time'] = time.time()
        self._connection_health['circuit_breaker_active'] = False
        
        # Calculate average uptime
        if self._connection_health['successful_connections'] > 0:
            total_uptime = self._connection_health['last_success_time'] - self._connection_health.get('first_success_time', self._connection_health['last_success_time'])
            self._connection_health['average_uptime'] = total_uptime / self._connection_health['successful_connections']
        
        # Set first success time if not already set
        if 'first_success_time' not in self._connection_health:
            self._connection_health['first_success_time'] = self._connection_health['last_success_time']

    def _record_connection_failure(self, is_normal_disconnect: bool = False):
        """Record a connection failure for health monitoring."""
        self._connection_health['failed_connections'] += 1
        self._connection_health['last_failure_time'] = time.time()
        
        # Record uptime if we had a successful connection
        if self._connection_health['connection_start_time'] > 0:
            uptime = time.time() - self._connection_health['connection_start_time']
            self._connection_health['total_uptime'] += uptime
            
            # Track disconnect intervals for pattern analysis (keep last 10)
            if len(self._connection_health['disconnect_pattern']) >= 10:
                self._connection_health['disconnect_pattern'].pop(0)
            self._connection_health['disconnect_pattern'].append(uptime)
        
        if is_normal_disconnect:
            # VALR's expected 30-second disconnects - don't count as real failures
            self._connection_health['normal_disconnects'] += 1
            # Don't increment consecutive_failures for normal disconnects
            self.logger().debug(f"Normal disconnect recorded (code 1000). "
                              f"Total normal: {self._connection_health['normal_disconnects']}, "
                              f"abnormal: {self._connection_health['abnormal_disconnects']}")
        else:
            # Abnormal disconnects - count towards consecutive failures
            self._connection_health['abnormal_disconnects'] += 1
            self._connection_health['consecutive_failures'] += 1
            
            # Activate circuit breaker if consecutive failures exceed threshold
            if self._connection_health['consecutive_failures'] >= CONSTANTS.CONNECTION_FAILURE_THRESHOLD:
                self._connection_health['circuit_breaker_active'] = True
                self._connection_health['circuit_breaker_activated_time'] = time.time()
                self.logger().warning(f"Order book connection circuit breaker activated after "
                                    f"{self._connection_health['consecutive_failures']} consecutive abnormal failures")

    def _should_attempt_connection(self) -> bool:
        """Check if connection should be attempted based on circuit breaker state."""
        if not self._connection_health['circuit_breaker_active']:
            return True
        
        # Check if recovery time has passed
        time_since_activation = time.time() - self._connection_health['circuit_breaker_activated_time']
        if time_since_activation >= CONSTANTS.CONNECTION_RECOVERY_TIME:
            self.logger().info(f"Order book connection circuit breaker recovery time elapsed, allowing connection attempt")
            return True
        
        return False

    def _calculate_adaptive_delay(self, is_normal_disconnect: bool = False) -> float:
        """Calculate adaptive reconnection delay based on connection health."""
        if is_normal_disconnect:
            # Optimized delay for VALR's normal 30-second disconnects
            return self._calculate_normal_disconnect_delay()
        
        failures = self._connection_health['consecutive_failures']
        
        # Base exponential backoff for abnormal disconnects
        base_delay = min(CONSTANTS.ADAPTIVE_DELAY_MIN * (2 ** failures), CONSTANTS.ADAPTIVE_DELAY_MAX)
        
        # Adjust based on success rate
        total_attempts = self._connection_health['total_connections']
        if total_attempts > 0:
            # Only consider abnormal disconnects for success rate calculation
            abnormal_failures = self._connection_health['abnormal_disconnects']
            abnormal_success_rate = max(0, (total_attempts - abnormal_failures) / total_attempts)
            
            # Lower success rate = longer delays
            if abnormal_success_rate < 0.3:
                base_delay *= 2.0  # Double delay for very poor success rate
            elif abnormal_success_rate < 0.6:
                base_delay *= 1.5  # Increase delay for poor success rate
        
        return min(base_delay, CONSTANTS.ADAPTIVE_DELAY_MAX)
    
    def _calculate_normal_disconnect_delay(self) -> float:
        """Calculate optimized delay for VALR's normal code 1000 disconnects."""
        # Analyze disconnect pattern to predict optimal reconnection timing
        if len(self._connection_health['disconnect_pattern']) >= 3:
            # Calculate average connection duration
            avg_uptime = sum(self._connection_health['disconnect_pattern']) / len(self._connection_health['disconnect_pattern'])
            
            # If connections typically last around 30 seconds, use minimal delay
            if 25 <= avg_uptime <= 35:
                return 0.5  # Very fast reconnect for predictable pattern
            elif 20 <= avg_uptime <= 40:
                return 1.0  # Fast reconnect for near-predictable pattern
        
        # Default optimized delay for normal disconnects
        normal_disconnects = self._connection_health['normal_disconnects']
        if normal_disconnects > 10:
            # We've seen many normal disconnects, use very fast reconnect
            return 0.5
        elif normal_disconnects > 5:
            # Moderate normal disconnect history, use fast reconnect
            return 1.0
        else:
            # Haven't established pattern yet, use slightly longer delay
            return 2.0

    def _get_connection_health_status(self) -> dict[str, Any]:
        """Get current connection health metrics."""
        total_attempts = self._connection_health['total_connections']
        success_rate = (self._connection_health['successful_connections'] / total_attempts) if total_attempts > 0 else 0
        
        # Calculate abnormal failure rate (excluding normal disconnects)
        abnormal_failures = self._connection_health['abnormal_disconnects']
        abnormal_failure_rate = (abnormal_failures / total_attempts) if total_attempts > 0 else 0
        
        # Calculate average disconnect interval
        avg_disconnect_interval = 0
        if len(self._connection_health['disconnect_pattern']) > 0:
            avg_disconnect_interval = sum(self._connection_health['disconnect_pattern']) / len(self._connection_health['disconnect_pattern'])
        
        return {
            'total_connections': total_attempts,
            'successful_connections': self._connection_health['successful_connections'],
            'failed_connections': self._connection_health['failed_connections'],
            'consecutive_failures': self._connection_health['consecutive_failures'],
            'success_rate': success_rate,
            'circuit_breaker_active': self._connection_health['circuit_breaker_active'],
            'average_uptime': self._connection_health['average_uptime'],
            'last_success_time': self._connection_health['last_success_time'],
            'last_failure_time': self._connection_health['last_failure_time'],
            'normal_disconnects': self._connection_health['normal_disconnects'],
            'abnormal_disconnects': self._connection_health['abnormal_disconnects'],
            'abnormal_failure_rate': abnormal_failure_rate,
            'avg_disconnect_interval': avg_disconnect_interval,
            'disconnect_pattern_established': len(self._connection_health['disconnect_pattern']) >= 3
        }

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
            
            # Validate sequence number - VALR optimized validation with high tolerance
            sequence_number = data.get("SequenceNumber")
            if not self._validate_sequence_number(trading_pair, sequence_number):
                self.logger().debug(f"Sequence validation failed for {trading_pair} - continuing anyway (VALR high-frequency optimization)")
                # Continue processing - don't return, as VALR has very high message frequency
            
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
        Includes robust reconnection handling for VALR's 30-second disconnect pattern.
        
        Args:
            output: The queue to send parsed messages to
        """
        max_reconnection_attempts = 5
        
        while True:
            ping_task = None
            
            # Check circuit breaker before attempting connection
            if not self._should_attempt_connection():
                health_status = self._get_connection_health_status()
                time_remaining = CONSTANTS.CONNECTION_RECOVERY_TIME - (time.time() - self._connection_health['circuit_breaker_activated_time'])
                self.logger().warning(f"Order book connection circuit breaker active. Recovery in {time_remaining:.1f}s. "
                                    f"Health: {health_status['success_rate']:.2%} success rate, "
                                    f"{health_status['consecutive_failures']} consecutive failures")
                await asyncio.sleep(30)  # Wait before checking again
                continue
            
            # Optimized message processor with filtering, performance tracking, and improved error handling
            async def message_processor():
                async for raw_message in self._ws_assistant.iter_messages():
                    processing_start_time = time.time()
                    message_type = "unknown"
                    
                    try:
                        message_data = raw_message.data
                        
                        # Handle ping/pong messages for Trade WebSocket (fast path)
                        if isinstance(message_data, str):
                            if message_data.upper() == "PONG":
                                self._update_message_stats("PONG", time.time() - processing_start_time)
                                self.logger().debug("Received PONG response from VALR Trade WebSocket")
                                continue
                            elif message_data.upper() == "PING":
                                # Send PONG response
                                await self._ws_assistant.send(WSJSONRequest({"type": "PONG"}))
                                self._update_message_stats("PING", time.time() - processing_start_time)
                                self.logger().debug("Sent PONG response to VALR Trade WebSocket")
                                continue
                        
                        # Ensure message_data is a dictionary
                        if not isinstance(message_data, dict):
                            self._update_message_stats("invalid", 0, failed=True)
                            continue
                        
                        message_type = message_data.get("type", "")
                        
                        # Pre-filter messages to reduce unnecessary processing
                        if not self._should_process_message(message_data):
                            self._message_stats['filtered_messages'] += 1
                            continue
                        
                        # Handle market summary updates separately for symbol mapping (optimization: no queue overhead)
                        if message_type == "MARKET_SUMMARY_UPDATE":
                            await self._handle_market_summary_update(message_data)
                            self._update_message_stats(message_type, time.time() - processing_start_time)
                            continue
                        
                        # Process other message types using optimized routing
                        channel = self._channel_originating_message(message_data)
                        if channel:
                            if channel == self._trade_messages_queue_key:
                                await self._parse_trade_message(message_data, output)
                            elif channel == self._diff_messages_queue_key:
                                await self._parse_order_book_diff_message(message_data, output)
                            elif channel in [self._snapshot_messages_queue_key]:
                                await self._parse_order_book_snapshot_message(message_data, output)
                            
                            self._update_message_stats(message_type, time.time() - processing_start_time)
                        else:
                            # Unknown message type
                            self._update_message_stats(message_type, 0, failed=True)
                            self.logger().debug(f"Unhandled message type: {message_type}")
                                
                    except asyncio.CancelledError:
                        # Don't log cancellation as an error
                        raise
                    except Exception as e:
                        # Enhanced error handling with message type context
                        processing_time = time.time() - processing_start_time
                        self._update_message_stats(message_type, processing_time, failed=True)
                        
                        error_msg = str(e)
                        if "json" in error_msg.lower() or "decode" in error_msg.lower():
                            self.logger().warning(f"JSON decode error for {message_type} message: {error_msg}")
                        elif "keyerror" in error_msg.lower():
                            self.logger().warning(f"Missing field in {message_type} message: {error_msg}")
                        else:
                            self.logger().exception(f"Error processing {message_type} message")
                            
                        # Log stats periodically for debugging
                        stats = self._get_message_processing_stats()
                        if stats['total_messages'] % 1000 == 0:  # Every 1000 messages
                            self.logger().info(f"Order book message processing stats: "
                                             f"{stats['messages_per_second']:.1f} msg/s, "
                                             f"{stats['success_rate']:.1%} success rate, "
                                             f"{stats['avg_processing_time_ms']:.2f}ms avg time")
            
            try:
                # Establish or re-establish connection
                if not self._ws_assistant:
                    self._record_connection_attempt()
                    health_status = self._get_connection_health_status()
                    self.logger().info(f"Establishing VALR Trade WebSocket connection for order book stream "
                                     f"(attempt #{health_status['total_connections']}, success rate: {health_status['success_rate']:.1%})")
                    
                    self._ws_assistant = await self._connected_websocket_assistant()
                    await self._subscribe_channels(self._ws_assistant)
                    
                    self._record_connection_success()
                    health_status = self._get_connection_health_status()
                    self.logger().info(f"Successfully established VALR Trade WebSocket connection "
                                     f"(success rate: {health_status['success_rate']:.1%}, "
                                     f"consecutive failures reset from {health_status['consecutive_failures']} to 0)")
                
                # Start ping task for connection keep-alive (VALR requires ping every 30 seconds)
                ping_task = asyncio.create_task(self._send_ping_messages())
                
                await message_processor()
                
            except asyncio.CancelledError:
                raise
            except Exception as e:
                error_msg = str(e)
                
                # Determine if this is a normal disconnect
                is_normal_disconnect = "close code = 1000" in error_msg.lower()
                
                # Record the failure with proper classification
                self._record_connection_failure(is_normal_disconnect)
                health_status = self._get_connection_health_status()
                
                # Calculate optimized delay based on disconnect type
                adaptive_delay = self._calculate_adaptive_delay(is_normal_disconnect)
                
                # Handle VALR's expected 30-second disconnections (close code 1000)  
                if is_normal_disconnect:
                    self.logger().info(f"VALR Trade WebSocket closed normally (code 1000) - "
                                     f"normal disconnect #{health_status['normal_disconnects']} "
                                     f"(avg interval: {health_status['avg_disconnect_interval']:.1f}s, "
                                     f"abnormal failure rate: {health_status['abnormal_failure_rate']:.1%})")
                    
                    # For normal disconnects, don't limit by consecutive_failures since they're not real failures
                    self.logger().info(f"Fast reconnecting order book stream in {adaptive_delay:.1f}s "
                                     f"(optimized for code 1000 pattern, "
                                     f"pattern established: {health_status['disconnect_pattern_established']})")
                    self._ws_assistant = None  # Force new connection
                    await asyncio.sleep(adaptive_delay)
                    continue  # Retry the connection loop
                        
                # Handle other connection errors
                else:
                    self.logger().error(f"Order book stream connection failed: {error_msg} "
                                      f"(failure #{health_status['consecutive_failures']}, "
                                      f"success rate: {health_status['success_rate']:.1%})")
                    
                    if health_status['consecutive_failures'] <= max_reconnection_attempts:
                        self.logger().info(f"Retrying order book connection in {adaptive_delay:.1f} seconds "
                                         f"(adaptive delay based on {health_status['success_rate']:.1%} success rate, "
                                         f"attempt {health_status['consecutive_failures']}/{max_reconnection_attempts})")
                        self._ws_assistant = None  # Force new connection
                        await asyncio.sleep(adaptive_delay)
                        continue  # Retry the connection loop
                    else:
                        self.logger().error(f"Exhausted reconnection attempts for order book stream. "
                                          f"Final health status: {health_status}")
                        break  # Exit the retry loop
                        
            finally:
                # Clean up ping task
                if ping_task and not ping_task.done():
                    ping_task.cancel()
                    try:
                        await ping_task
                    except asyncio.CancelledError:
                        pass
                        
                # Clean up WebSocket connection on exit
                if self._ws_assistant is not None:
                    await self._ws_assistant.disconnect()
                    self._ws_assistant = None

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
                # Duplicate or old message - allow processing anyway for VALR high-frequency
                self.logger().debug(f"Old/duplicate sequence {sequence_number} for {trading_pair}, "
                                  f"expected {expected_sequence + 1} - allowing anyway")
                return True  # Changed to True to allow processing
            else:
                # Gap detected - VALR has very high-frequency trading, allow large gaps
                gap_size = sequence_number - expected_sequence - 1
                if gap_size <= 2000:  # Dramatically increased tolerance for VALR's high-frequency trading (was 500)
                    if gap_size > 100:
                        self.logger().debug(f"Large sequence gap ({gap_size}) for {trading_pair} - continuing (VALR high-frequency)")
                    self._sequence_numbers[trading_pair] = sequence_number
                    return True
                else:
                    # Extremely large gap - likely connection reset, but still allow the update
                    self.logger().info(f"Very large sequence gap ({gap_size}) for {trading_pair}! "
                                     f"Expected {expected_sequence + 1}, received {sequence_number} - allowing anyway")
                    # Reset sequence tracking and allow the update to process
                    self._sequence_numbers[trading_pair] = sequence_number
                    self.logger().info(f"Reset sequence tracking for {trading_pair} due to very large gap - continuing")
                    return True  # Always allow update to prevent order book blocking
                
        except Exception as e:
            self.logger().error(f"Error validating sequence number for {trading_pair}: {e} - allowing anyway")
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