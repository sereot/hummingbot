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

    async def _get_snapshot(self, trading_pair: str) -> Dict[str, Any]:
        """
        Get orderbook snapshot for a trading pair.
        
        Args:
            trading_pair: The trading pair to get snapshot for
            
        Returns:
            The orderbook snapshot data
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

    async def get_order_book_data(self, trading_pair: str) -> Dict[str, Any]:
        """
        Get order book data for a trading pair.
        
        Args:
            trading_pair: The trading pair to get order book for
            
        Returns:
            The order book data including snapshot
        """
        snapshot = await self._get_snapshot(trading_pair)
        
        # Add required metadata
        snapshot["trading_pair"] = trading_pair
        snapshot["timestamp"] = time.time()
        
        return snapshot

    async def listen_for_order_book_snapshots(self, ev_loop: asyncio.AbstractEventLoop, output: asyncio.Queue):
        """
        Listen for orderbook snapshots via REST API.
        """
        while True:
            try:
                for trading_pair in self._trading_pairs:
                    try:
                        snapshot = await self._get_snapshot(trading_pair)
                        
                        # Create snapshot message
                        snapshot_msg = OrderBookMessage(
                            message_type=OrderBookMessageType.SNAPSHOT,
                            content={
                                "trading_pair": trading_pair,
                                "bids": snapshot.get("Bids", []),
                                "asks": snapshot.get("Asks", []),
                                "update_id": int(time.time() * 1e3),
                                "timestamp": time.time(),
                            },
                            timestamp=time.time(),
                        )
                        
                        output.put_nowait(snapshot_msg)
                        
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        self.logger().exception(
                            f"Error fetching snapshot for {trading_pair}. Retrying after {self.HEARTBEAT_TIME_INTERVAL} seconds..."
                        )
                        
                await self._sleep(self.FULL_ORDER_BOOK_RESET_DELTA_SECONDS)
                
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger().exception(
                    "Unexpected error in order book snapshot listener. Retrying after 5 seconds..."
                )
                await self._sleep(5.0)

    async def listen_for_order_book_diffs(self, ev_loop: asyncio.AbstractEventLoop, output: asyncio.Queue):
        """
        Listen for orderbook updates via WebSocket.
        """
        while True:
            try:
                ws_assistant = await self._get_ws_assistant()
                await ws_assistant.connect(
                    ws_url=CONSTANTS.WSS_TRADE_URL,
                    ping_timeout=CONSTANTS.PING_TIMEOUT
                )
                
                # Subscribe to orderbook updates for all trading pairs
                await self._subscribe_to_order_book_streams(ws_assistant)
                
                async for ws_response in ws_assistant.iter_messages():
                    data = ws_response.data
                    
                    if data.get("type") == CONSTANTS.WS_MARKET_AGGREGATED_ORDERBOOK_UPDATE_EVENT:
                        await self._process_order_book_diff(data, output)
                    elif data.get("type") == CONSTANTS.WS_MARKET_FULL_ORDERBOOK_SNAPSHOT_EVENT:
                        await self._process_order_book_snapshot(data, output)
                        
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger().exception(
                    "Unexpected error in order book diff listener. Retrying after 5 seconds..."
                )
                await self._sleep(5.0)
            finally:
                await ws_assistant.disconnect()

    async def listen_for_trades(self, ev_loop: asyncio.AbstractEventLoop, output: asyncio.Queue):
        """
        Listen for trades via WebSocket.
        """
        while True:
            try:
                ws_assistant = await self._get_ws_assistant()
                await ws_assistant.connect(
                    ws_url=CONSTANTS.WSS_TRADE_URL,
                    ping_timeout=CONSTANTS.PING_TIMEOUT
                )
                
                # Subscribe to trade streams for all trading pairs
                await self._subscribe_to_trade_streams(ws_assistant)
                
                async for ws_response in ws_assistant.iter_messages():
                    data = ws_response.data
                    
                    if data.get("type") == CONSTANTS.WS_MARKET_TRADE_EVENT:
                        await self._process_trade_message(data, output)
                        
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger().exception(
                    "Unexpected error in trades listener. Retrying after 5 seconds..."
                )
                await self._sleep(5.0)
            finally:
                await ws_assistant.disconnect()

    async def _get_ws_assistant(self) -> WSAssistant:
        """Get or create a WebSocket assistant."""
        if self._ws_assistant is None:
            self._ws_assistant = await self._api_factory.get_ws_assistant()
        return self._ws_assistant

    async def _subscribe_to_order_book_streams(self, ws_assistant: WSAssistant):
        """Subscribe to orderbook update streams for all trading pairs."""
        for trading_pair in self._trading_pairs:
            exchange_pair = web_utils.convert_to_exchange_trading_pair(trading_pair)
            
            # Subscribe to aggregated orderbook updates
            subscribe_request = WSJSONRequest({
                "type": CONSTANTS.WS_SUBSCRIBE_EVENT,
                "subscriptions": [{
                    "event": "AGGREGATED_ORDERBOOK_UPDATE",
                    "pairs": [exchange_pair]
                }]
            })
            
            await ws_assistant.send(subscribe_request)
            
    async def _subscribe_to_trade_streams(self, ws_assistant: WSAssistant):
        """Subscribe to trade streams for all trading pairs."""
        for trading_pair in self._trading_pairs:
            exchange_pair = web_utils.convert_to_exchange_trading_pair(trading_pair)
            
            # Subscribe to trade updates
            subscribe_request = WSJSONRequest({
                "type": CONSTANTS.WS_SUBSCRIBE_EVENT,
                "subscriptions": [{
                    "event": "NEW_TRADE",
                    "pairs": [exchange_pair]
                }]
            })
            
            await ws_assistant.send(subscribe_request)

    async def _process_order_book_diff(self, data: Dict[str, Any], output: asyncio.Queue):
        """Process orderbook diff message."""
        try:
            pair = data.get("currencyPairSymbol", "")
            trading_pair = web_utils.convert_from_exchange_trading_pair(pair)
            
            if trading_pair not in self._trading_pairs:
                return
                
            # Extract bids and asks
            bids = data.get("Bids", [])
            asks = data.get("Asks", [])
            
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
            
            output.put_nowait(diff_msg)
            
        except Exception:
            self.logger().exception("Error processing order book diff message")

    async def _process_order_book_snapshot(self, data: Dict[str, Any], output: asyncio.Queue):
        """Process full orderbook snapshot message."""
        try:
            pair = data.get("currencyPairSymbol", "")
            trading_pair = web_utils.convert_from_exchange_trading_pair(pair)
            
            if trading_pair not in self._trading_pairs:
                return
                
            # Extract bids and asks
            bids = data.get("Bids", [])
            asks = data.get("Asks", [])
            
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
            
            output.put_nowait(snapshot_msg)
            
        except Exception:
            self.logger().exception("Error processing order book snapshot message")

    async def _process_trade_message(self, data: Dict[str, Any], output: asyncio.Queue):
        """Process trade message."""
        try:
            pair = data.get("currencyPair", "")
            trading_pair = web_utils.convert_from_exchange_trading_pair(pair)
            
            if trading_pair not in self._trading_pairs:
                return
            
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
            
            output.put_nowait(trade_msg)
            
        except Exception:
            self.logger().exception("Error processing trade message")