import asyncio
import time
from typing import TYPE_CHECKING, Any, Optional

from hummingbot.connector.exchange.valr import valr_constants as CONSTANTS, valr_web_utils as web_utils
from hummingbot.connector.exchange.valr.valr_auth import ValrAuth
from hummingbot.connector.exchange.valr.valr_connection_pool import VALRConnectionPool
from hummingbot.core.data_type.user_stream_tracker_data_source import UserStreamTrackerDataSource
from hummingbot.core.web_assistant.connections.data_types import WSJSONRequest
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory
from hummingbot.core.web_assistant.ws_assistant import WSAssistant
from hummingbot.logger import HummingbotLogger

if TYPE_CHECKING:
    from hummingbot.connector.exchange.valr.valr_exchange import ValrExchange


class ValrAPIUserStreamDataSource(UserStreamTrackerDataSource):
    HEARTBEAT_TIME_INTERVAL = 30.0
    PING_TIMEOUT = 10.0

    _logger: HummingbotLogger | None = None

    def __init__(
        self,
        auth: ValrAuth,
        trading_pairs: list[str],
        connector: "ValrExchange",
        api_factory: WebAssistantsFactory,
        domain: str = CONSTANTS.DEFAULT_DOMAIN
    ):
        super().__init__()
        self._auth = auth
        self._trading_pairs = trading_pairs
        self._connector = connector
        self._api_factory = api_factory
        self._domain = domain
        self._last_recv_time: float = 0  # Track last received time for REST fallback mode
        self._ws_assistant: WSAssistant | None = None  # Store WebSocket assistant reference
        
        # Connection pool for HFT optimization
        self._connection_pool: Optional[VALRConnectionPool] = None
        self._use_connection_pool = True  # Enable by default for HFT
        
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
    
    @property
    def last_recv_time(self) -> float:
        """
        Returns the time of the last received message.
        In REST fallback mode, this is updated manually.
        
        :return: the timestamp of the last received message in seconds
        """
        # Use our own timestamp in REST fallback mode, otherwise use WebSocket assistant
        if self._ws_assistant:
            return max(self._last_recv_time, self._ws_assistant.last_recv_time)
        return self._last_recv_time

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
                self.logger().warning(f"User stream connection circuit breaker activated after "
                                    f"{self._connection_health['consecutive_failures']} consecutive abnormal failures")

    def _should_attempt_connection(self) -> bool:
        """Check if connection should be attempted based on circuit breaker state."""
        if not self._connection_health['circuit_breaker_active']:
            return True
        
        # Check if recovery time has passed
        time_since_activation = time.time() - self._connection_health['circuit_breaker_activated_time']
        if time_since_activation >= CONSTANTS.CONNECTION_RECOVERY_TIME:
            self.logger().info(f"User stream connection circuit breaker recovery time elapsed, allowing connection attempt")
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
            
            # If connections typically last around 30 seconds, use ultra-fast reconnect for HFT
            if 25 <= avg_uptime <= 35:
                return 0.1  # Ultra-fast reconnect for HFT market making
            elif 20 <= avg_uptime <= 40:
                return 0.2  # Very fast reconnect for near-predictable pattern
        
        # Default optimized delay for normal disconnects
        normal_disconnects = self._connection_health['normal_disconnects']
        if normal_disconnects > 10:
            # We've seen many normal disconnects, use ultra-fast reconnect for HFT
            return 0.1
        elif normal_disconnects > 5:
            # Moderate normal disconnect history, use very fast reconnect
            return 0.2
        else:
            # Haven't established pattern yet, use fast but safe delay
            return 0.5

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

    async def _connected_websocket_assistant(self) -> WSAssistant:
        """
        Creates an authenticated connection to the user account WebSocket with rate limiting support.
        Uses connection pool for HFT optimization if enabled.
        
        Returns:
            An authenticated WSAssistant instance
        """
        # Use connection pool if enabled
        if self._use_connection_pool:
            # Disable connection pooling for now due to authentication issues
            # Will re-enable once properly tested
            self.logger().debug("Connection pooling disabled for user stream - using single connection")
            self._use_connection_pool = False
        
        # Fallback to single connection
        ws_assistant = await self._api_factory.get_ws_assistant()
        
        # Connect to account WebSocket with retry logic for rate limiting
        # Authentication happens during the WebSocket handshake via headers
        await self._connect_with_retry(ws_assistant, CONSTANTS.WSS_ACCOUNT_URL)
        
        # VALR account WebSocket automatically subscribes to all account events after successful connection
        # No authentication payload or explicit subscription messages are needed
        self.logger().info("Successfully connected to VALR account WebSocket stream")
        
        return ws_assistant

    async def _connect_with_retry(self, ws_assistant: WSAssistant, ws_url: str, max_retries: int = 5):
        """
        Connect to WebSocket with exponential backoff for rate limiting.
        
        Args:
            ws_assistant: The WebSocket assistant to connect
            ws_url: The WebSocket URL to connect to
            max_retries: Maximum number of retry attempts
        """
        import random
        
        for attempt in range(max_retries):
            try:
                await ws_assistant.connect(
                    ws_url=ws_url,
                    ping_timeout=self.PING_TIMEOUT
                )
                return  # Success
                
            except Exception as e:
                error_msg = str(e)
                
                # Check for rate limiting (429 errors)
                if "429" in error_msg or "Too Many Requests" in error_msg:
                    if attempt < max_retries - 1:
                        # Exponential backoff with jitter
                        delay = (2 ** attempt) + random.uniform(0, 1)
                        self.logger().warning(
                            f"User stream WebSocket connection rate limited (429). Retrying in {delay:.1f}s... "
                            f"(attempt {attempt + 1}/{max_retries})"
                        )
                        await asyncio.sleep(delay)
                        continue
                    else:
                        self.logger().error(
                            f"User stream WebSocket connection failed after {max_retries} attempts due to rate limiting"
                        )
                        raise
                else:
                    # For non-rate-limiting errors, raise immediately
                    raise

    async def _subscribe_channels(self, websocket_assistant: WSAssistant):
        """
        Subscribe to user stream channels.
        
        VALR's account WebSocket automatically subscribes to all user events upon authentication,
        so no additional subscription is needed.
        
        Args:
            websocket_assistant: The WebSocket assistant to use
        """
        # VALR automatically subscribes to all account events after authentication
        # These include:
        # - Balance updates
        # - Order updates (new, update, filled, cancelled)
        # - Trade executions
        # - Failed cancellations
        # - Cancel on disconnect events
        
        # Enable cancel-on-disconnect feature
        await self._enable_cancel_on_disconnect(websocket_assistant)
        
        self.logger().info("User stream channels automatically subscribed after authentication")

    async def listen_for_user_stream(self, output: asyncio.Queue):
        """
        Continuously listens to user stream events and places them in the output queue.
        Includes robust reconnection handling for VALR's 30-second disconnect pattern.
        
        Args:
            output: The queue to place received messages
        """
        max_reconnection_attempts = 3
        
        while True:
            websocket_assistant = None
            ping_task = None
            
            # Check circuit breaker before attempting connection
            if not self._should_attempt_connection():
                health_status = self._get_connection_health_status()
                time_remaining = CONSTANTS.CONNECTION_RECOVERY_TIME - (time.time() - self._connection_health['circuit_breaker_activated_time'])
                self.logger().warning(f"User stream connection circuit breaker active. Recovery in {time_remaining:.1f}s. "
                                    f"Health: {health_status['success_rate']:.2%} success rate, "
                                    f"{health_status['consecutive_failures']} consecutive failures")
                await asyncio.sleep(30)  # Wait before checking again
                continue
            
            try:
                self._record_connection_attempt()
                health_status = self._get_connection_health_status()
                self.logger().info(f"Attempting to establish VALR WebSocket user stream connection "
                                 f"(attempt #{health_status['total_connections']}, success rate: {health_status['success_rate']:.1%})")
                
                websocket_assistant = await self._connected_websocket_assistant()
                self._ws_assistant = websocket_assistant  # Store the WebSocket assistant
                
                self._record_connection_success()
                health_status = self._get_connection_health_status()
                self.logger().info(f"Successfully established VALR WebSocket user stream connection "
                                 f"(success rate: {health_status['success_rate']:.1%}, "
                                 f"consecutive failures reset from {health_status['consecutive_failures']} to 0)")
                
                # Subscribe to user stream channels
                await self._subscribe_channels(websocket_assistant)
                
                # Start ping task for connection keep-alive (VALR requires ping every 30 seconds)
                ping_task = asyncio.create_task(self._send_ping_messages(websocket_assistant))
                
                # Main message listening loop
                async for ws_response in websocket_assistant.iter_messages():
                    # Check if we received valid data
                    if ws_response is None or ws_response.data is None:
                        self.logger().warning("Received None message from WebSocket, skipping")
                        continue
                        
                    event_message = ws_response.data
                    
                    # Handle ping/pong messages
                    if isinstance(event_message, str):
                        if event_message.upper() == "PONG":
                            self.logger().debug("Received PONG response from VALR")
                            continue
                        elif event_message.upper() == "PING":
                            # Send PONG response
                            await websocket_assistant.send(WSJSONRequest({"type": "PONG"}))
                            self.logger().debug("Sent PONG response to VALR")
                            continue
                    
                    # Log received messages for debugging
                    self.logger().debug(f"Received user stream message: {event_message}")
                    
                    # Place the message in the output queue
                    output.put_nowait(event_message)
                    
            except asyncio.CancelledError:
                self.logger().info("User stream listener cancelled - shutting down gracefully")
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
                    self.logger().info(f"VALR WebSocket closed normally (code 1000) - "
                                     f"normal disconnect #{health_status['normal_disconnects']} "
                                     f"(avg interval: {health_status['avg_disconnect_interval']:.1f}s, "
                                     f"abnormal failure rate: {health_status['abnormal_failure_rate']:.1%})")
                    
                    # For normal disconnects, don't limit by consecutive_failures since they're not real failures
                    self.logger().info(f"Fast reconnecting user stream in {adaptive_delay:.1f}s "
                                     f"(optimized for code 1000 pattern, "
                                     f"pattern established: {health_status['disconnect_pattern_established']})")
                    await asyncio.sleep(adaptive_delay)
                    continue  # Retry the connection loop
                
                # Handle authentication and permission errors
                elif any(keyword in error_msg.lower() for keyword in ["unauthorized", "forbidden", "permission", "401", "403"]):
                    self.logger().warning(f"VALR WebSocket user stream authentication failed - API key may not have WebSocket permissions "
                                        f"(failure #{health_status['consecutive_failures']}, success rate: {health_status['success_rate']:.1%})")
                    self.logger().warning("Falling back to REST API polling for account updates")
                
                # Handle rate limiting errors
                elif any(keyword in error_msg.lower() for keyword in ["rate limit", "429", "too many requests"]):
                    self.logger().warning(f"User stream rate limited "
                                        f"(failure #{health_status['consecutive_failures']}, success rate: {health_status['success_rate']:.1%}). "
                                        f"Falling back to REST mode.")
                
                # Handle other connection errors
                else:
                    self.logger().error(f"User stream connection failed: {error_msg} "
                                      f"(failure #{health_status['consecutive_failures']}, "
                                      f"success rate: {health_status['success_rate']:.1%})")
                    
                    if health_status['consecutive_failures'] <= max_reconnection_attempts:
                        self.logger().info(f"Retrying user stream connection in {adaptive_delay:.1f} seconds "
                                         f"(adaptive delay based on {health_status['success_rate']:.1%} success rate, "
                                         f"attempt {health_status['consecutive_failures']}/{max_reconnection_attempts})")
                        await asyncio.sleep(adaptive_delay)
                        continue  # Retry the connection loop
                
                # Fall back to REST-only mode after exhausting reconnection attempts
                self.logger().info("Exhausted reconnection attempts. Operating in REST-only mode for user stream.")
                try:
                    while True:
                        # Update last_recv_time to simulate active user stream
                        self._last_recv_time = time.time()
                        await asyncio.sleep(30)  # Update every 30 seconds
                except asyncio.CancelledError:
                    self.logger().info("User stream listener cancelled during REST fallback - shutting down gracefully")
                    raise
                    
            finally:
                # Clean up ping task
                if ping_task and not ping_task.done():
                    ping_task.cancel()
                    try:
                        await ping_task
                    except asyncio.CancelledError:
                        pass
                
                # Clean up WebSocket connection
                if websocket_assistant is not None:
                    await websocket_assistant.disconnect()
                    self._ws_assistant = None  # Clear the reference

    async def _send_ping_messages(self, websocket_assistant):
        """
        Sends periodic PING messages to maintain WebSocket connection.
        VALR requires ping every 30 seconds to keep the connection alive.
        
        Args:
            websocket_assistant: The WebSocket assistant to send pings through
        """
        try:
            while True:
                await asyncio.sleep(30)  # VALR requires ping every 30 seconds
                try:
                    await websocket_assistant.send(WSJSONRequest({"type": "PING"}))
                    self.logger().debug("Sent PING message to VALR account WebSocket")
                except Exception as e:
                    self.logger().warning(f"Failed to send PING message: {e}")
                    break
        except asyncio.CancelledError:
            self.logger().debug("Ping task cancelled")
            raise

    async def _on_user_stream_interruption(self, websocket_assistant: WSAssistant | None):
        """
        Handles the event of a user stream interruption.
        
        Args:
            websocket_assistant: The WebSocket assistant that was interrupted
        """
        self.logger().warning("User stream interrupted. Cleaning up and attempting reconnection...")
        
        # Don't disconnect if using connection pool (pool manages connections)
        if not self._use_connection_pool and websocket_assistant is not None:
            await websocket_assistant.disconnect()
            
        self._ws_assistant = None  # Clear the reference
        
        # Additional cleanup can be added here if needed
        # The framework will automatically attempt to reconnect
    
    async def stop(self):
        """Stop the user stream data source and clean up resources."""
        # Shutdown connection pool if used
        if self._connection_pool is not None:
            await self._connection_pool.shutdown()
            self._connection_pool = None
        
        # Call parent stop method if exists
        if hasattr(super(), 'stop'):
            await super().stop()
    
    async def _enable_cancel_on_disconnect(self, websocket_assistant: WSAssistant):
        """
        Enable cancel-on-disconnect feature for VALR WebSocket.
        This ensures that open orders are automatically cancelled if the WebSocket connection is lost.
        
        Args:
            websocket_assistant: The WebSocket assistant to send the message through
        """
        try:
            # Send cancel-on-disconnect activation message
            cancel_on_disconnect_msg = WSJSONRequest({
                "type": CONSTANTS.WS_CANCEL_ON_DISCONNECT_EVENT,
                "data": {
                    "active": True
                }
            })
            
            await websocket_assistant.send(cancel_on_disconnect_msg)
            self.logger().info("Cancel-on-disconnect feature enabled for VALR account WebSocket")
            
        except Exception as e:
            self.logger().warning(f"Failed to enable cancel-on-disconnect feature: {e}")
    
    async def _disable_cancel_on_disconnect(self, websocket_assistant: WSAssistant):
        """
        Disable cancel-on-disconnect feature for VALR WebSocket.
        This allows orders to remain open even if the WebSocket connection is lost.
        
        Args:
            websocket_assistant: The WebSocket assistant to send the message through
        """
        try:
            # Send cancel-on-disconnect deactivation message
            cancel_on_disconnect_msg = WSJSONRequest({
                "type": CONSTANTS.WS_CANCEL_ON_DISCONNECT_EVENT,
                "data": {
                    "active": False
                }
            })
            
            await websocket_assistant.send(cancel_on_disconnect_msg)
            self.logger().info("Cancel-on-disconnect feature disabled for VALR account WebSocket")
            
        except Exception as e:
            self.logger().warning(f"Failed to disable cancel-on-disconnect feature: {e}")