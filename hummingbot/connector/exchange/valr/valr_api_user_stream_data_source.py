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
        
        # Wait for authentication response
        async for msg in ws_assistant.iter_messages():
            data = msg.data
            if data.get("type") == "AUTHENTICATED":
                self.logger().info("Successfully authenticated user stream connection")
                break
            elif data.get("type") == "AUTH_FAILED":
                raise IOError(f"User stream authentication failed: {data.get('message', 'Unknown error')}")
        
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
        while True:
            ws_assistant = None
            try:
                ws_assistant = await self._connected_websocket_assistant()
                await self._subscribe_channels(ws_assistant)
                
                async for ws_response in ws_assistant.iter_messages():
                    data = ws_response.data
                    event_type = data.get("type", "")
                    
                    # Process different event types
                    if event_type in [
                        CONSTANTS.WS_USER_BALANCE_UPDATE_EVENT,
                        CONSTANTS.WS_USER_NEW_ORDER_EVENT,
                        CONSTANTS.WS_USER_ORDER_UPDATE_EVENT,
                        CONSTANTS.WS_USER_ORDER_DELETE_EVENT,
                        CONSTANTS.WS_USER_TRADE_EVENT,
                        CONSTANTS.WS_USER_FAILED_CANCEL_EVENT,
                        CONSTANTS.WS_USER_ORDER_CANCEL_EVENT,
                        CONSTANTS.WS_USER_INSTANT_ORDER_COMPLETED_EVENT,
                        CONSTANTS.WS_CANCEL_ON_DISCONNECT_EVENT,
                    ]:
                        # Add timestamp if not present
                        if "timestamp" not in data:
                            data["timestamp"] = time.time()
                        
                        output.put_nowait(data)
                    
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger().exception(
                    "Unexpected error in user stream listener. Retrying after 5 seconds..."
                )
                await self._sleep(5.0)
            finally:
                if ws_assistant is not None:
                    await ws_assistant.disconnect()

    async def _on_user_stream_interruption(self, websocket_assistant: Optional[WSAssistant]):
        """
        Handles the event of a user stream interruption.
        
        Args:
            websocket_assistant: The WebSocket assistant that was interrupted
        """
        self.logger().info("User stream interrupted. Cleaning up...")
        
        # Disconnect the websocket if it exists
        if websocket_assistant is not None:
            await websocket_assistant.disconnect()
        
        # Additional cleanup can be added here if needed