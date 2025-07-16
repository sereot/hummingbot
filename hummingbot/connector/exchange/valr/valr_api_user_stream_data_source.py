import asyncio
import time
from typing import TYPE_CHECKING, List, Optional

from hummingbot.connector.exchange.valr import valr_constants as CONSTANTS, valr_web_utils as web_utils
from hummingbot.connector.exchange.valr.valr_auth import ValrAuth
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

    _logger: Optional[HummingbotLogger] = None

    def __init__(
        self,
        auth: ValrAuth,
        trading_pairs: List[str],
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

    async def _connected_websocket_assistant(self) -> WSAssistant:
        """
        Creates an authenticated connection to the user account WebSocket with rate limiting support.
        
        Returns:
            An authenticated WSAssistant instance
        """
        ws_assistant = await self._api_factory.get_ws_assistant()
        
        # Connect to account WebSocket with retry logic for rate limiting
        await self._connect_with_retry(ws_assistant, CONSTANTS.WSS_ACCOUNT_URL)
        
        # Send authentication message after connection
        auth_payload = self._auth.get_ws_auth_payload("/ws/account")
        auth_request = WSJSONRequest(auth_payload)
        await ws_assistant.send(auth_request)
        
        # Wait for authentication response with timeout
        auth_success = False
        try:
            # Use asyncio.wait_for for Python 3.10+ compatibility instead of asyncio.timeout
            async def wait_for_auth():
                async for msg in ws_assistant.iter_messages():
                    data = msg.data
                    if data.get("type") == "AUTHENTICATED":
                        self.logger().info("Successfully authenticated user stream connection")
                        return True
                    elif data.get("type") == "AUTH_FAILED":
                        error_msg = data.get('message', 'Unknown error')
                        self.logger().error(f"User stream authentication failed: {error_msg}")
                        raise IOError(f"User stream authentication failed: {error_msg}")
                    elif data.get("type") == "UNAUTHORIZED":
                        self.logger().error("User stream authentication unauthorized - API key may lack WebSocket permissions")
                        raise IOError("User stream authentication unauthorized - API key may lack WebSocket permissions")
                    else:
                        self.logger().debug(f"Received unexpected message during auth: {data}")
                return False
            
            # Use asyncio.wait_for instead of asyncio.timeout for Python 3.10+ compatibility
            auth_success = await asyncio.wait_for(wait_for_auth(), timeout=15.0)
            
        except asyncio.TimeoutError:
            self.logger().error("User stream authentication timeout - no response received")
            raise IOError("User stream authentication timeout - no response received")
        
        if not auth_success:
            raise IOError("User stream authentication failed - no valid response received")
        
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
        
        self.logger().info("User stream channels automatically subscribed after authentication")

    async def listen_for_user_stream(self, output: asyncio.Queue):
        """
        Continuously listens to user stream events and places them in the output queue.
        
        Args:
            output: The queue to place received messages
        """
        # First, attempt WebSocket connection with proper authentication
        websocket_assistant = None
        
        try:
            self.logger().info("Attempting to establish VALR WebSocket user stream connection")
            websocket_assistant = await self._connected_websocket_assistant()
            self.logger().info("Successfully established VALR WebSocket user stream connection")
            
            # Subscribe to user stream channels
            await self._subscribe_channels(websocket_assistant)
            
            # Main message listening loop
            async for ws_response in websocket_assistant.iter_messages():
                event_message = ws_response.data
                
                # Log received messages for debugging
                self.logger().debug(f"Received user stream message: {event_message}")
                
                # Place the message in the output queue
                output.put_nowait(event_message)
                
        except asyncio.CancelledError:
            self.logger().info("User stream listener cancelled - shutting down gracefully")
            raise
        except Exception as e:
            error_msg = str(e)
            self.logger().error(f"Error in user stream connection: {error_msg}")
            
            # Check if it's a permission/authentication error
            if any(keyword in error_msg.lower() for keyword in ["unauthorized", "forbidden", "permission", "401", "403"]):
                self.logger().warning("VALR WebSocket user stream authentication failed - API key may not have WebSocket permissions")
                self.logger().warning("Falling back to REST API polling for account updates")
                
                # Fall back to REST-only mode by keeping the method running but doing nothing
                # This allows the connector to work with REST API polling
                try:
                    while True:
                        await asyncio.sleep(60)  # Keep the method running but do nothing
                except asyncio.CancelledError:
                    self.logger().info("User stream listener cancelled during REST fallback - shutting down gracefully")
                    raise
            else:
                # For other errors, attempt reconnection
                self.logger().error(f"User stream connection failed: {error_msg}")
                raise
        finally:
            # Clean up WebSocket connection
            if websocket_assistant is not None:
                await websocket_assistant.disconnect()

    async def _on_user_stream_interruption(self, websocket_assistant: Optional[WSAssistant]):
        """
        Handles the event of a user stream interruption.
        
        Args:
            websocket_assistant: The WebSocket assistant that was interrupted
        """
        self.logger().warning("User stream interrupted. Cleaning up and attempting reconnection...")
        
        # Disconnect the websocket if it exists
        if websocket_assistant is not None:
            await websocket_assistant.disconnect()
        
        # Additional cleanup can be added here if needed
        # The framework will automatically attempt to reconnect