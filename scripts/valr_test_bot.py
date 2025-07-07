import logging
import os
from decimal import Decimal
from typing import Dict, List

from pydantic import Field

from hummingbot.client.config.config_data_types import BaseClientModel
from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import OrderType, PriceType, TradeType
from hummingbot.core.data_type.order_candidate import OrderCandidate
from hummingbot.core.event.events import OrderFilledEvent
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase


class ValrTestBotConfig(BaseClientModel):
    script_file_name: str = os.path.basename(__file__)
    exchange: str = Field("valr")
    trading_pair: str = Field("DOGE-USDT")
    order_amount: Decimal = Field(4)  # VALR minimum for DOGEUSDT: 4 DOGE
    bid_spread: Decimal = Field(0.01)  # 100 bps = 1%
    ask_spread: Decimal = Field(0.01)  # 100 bps = 1%
    order_refresh_time: int = Field(30)  # 30 seconds
    price_type: str = Field("mid")
    use_post_only: bool = Field(True)  # Use LIMIT_MAKER for testing


class ValrTestBot(ScriptStrategyBase):
    """
    VALR Test Bot for Order Placement Testing
    
    Description:
    This bot places limit orders on both sides of the DOGE-USDT pair on VALR exchange
    at 100 bps (1%) away from the mid price. Orders are refreshed every 30 seconds.
    
    The purpose is to test VALR connector order placement functionality without
    the orders being filled due to the wide spread.
    
    Key Features:
    - Uses DOGE-USDT pair (DOGEUSDT active and available on VALR)
    - 100 bps spread to prevent accidental fills
    - Minimal order amounts (4 DOGE minimum)
    - Post-only orders (LIMIT_MAKER) for safety
    - 30-second refresh interval
    - Comprehensive logging for testing verification
    
    DOGEUSDT Trading Specifications:
    - Min Base Amount: 4 DOGE
    - Max Base Amount: 680,000 DOGE
    - Min Quote Amount: 0.5 USDT
    - Tick Size: 0.00001 USDT
    - Base Decimal Places: 1
    - Status: Active ✅
    """

    create_timestamp = 0
    price_source = PriceType.MidPrice

    @classmethod
    def init_markets(cls, config: ValrTestBotConfig):
        cls.markets = {config.exchange: {config.trading_pair}}
        cls.price_source = PriceType.LastTrade if config.price_type == "last" else PriceType.MidPrice

    def __init__(self, connectors: Dict[str, ConnectorBase], config: ValrTestBotConfig):
        super().__init__(connectors)
        self.config = config
        self.websocket_test_completed = False
        
        # Log initialization
        self.log_with_clock(
            logging.INFO, 
            f"VALR Test Bot initialized - Pair: {config.trading_pair}, "
            f"Exchange: {config.exchange}, Spread: {config.bid_spread*100}%, "
            f"Order Amount: {config.order_amount} DOGE"
        )

    def on_tick(self):
        # Run WebSocket test once at startup
        if not self.websocket_test_completed:
            self.test_websocket_orderbook_access()
            self.websocket_test_completed = True
        
        if self.create_timestamp <= self.current_timestamp:
            self.log_with_clock(logging.INFO, "Starting order refresh cycle")
            
            # Cancel existing orders
            active_orders = self.get_active_orders(connector_name=self.config.exchange)
            if active_orders:
                self.log_with_clock(logging.INFO, f"Cancelling {len(active_orders)} existing orders")
                self.cancel_all_orders()
            
            # Create and place new orders
            proposal: List[OrderCandidate] = self.create_proposal()
            if proposal:
                proposal_adjusted: List[OrderCandidate] = self.adjust_proposal_to_budget(proposal)
                if proposal_adjusted:
                    self.place_orders(proposal_adjusted)
                    self.log_with_clock(
                        logging.INFO, 
                        f"Placed {len(proposal_adjusted)} orders, next refresh in {self.config.order_refresh_time}s"
                    )
                else:
                    self.log_with_clock(logging.WARNING, "No orders placed - insufficient budget")
            else:
                self.log_with_clock(logging.WARNING, "No orders created - unable to get reference price")
                
            self.create_timestamp = self.config.order_refresh_time + self.current_timestamp

    def create_proposal(self) -> List[OrderCandidate]:
        try:
            # Get reference price (mid price)
            ref_price = self.connectors[self.config.exchange].get_price_by_type(
                self.config.trading_pair, 
                self.price_source
            )
            
            if ref_price is None or ref_price <= 0:
                self.log_with_clock(logging.ERROR, f"Invalid reference price: {ref_price}")
                return []
            
            # Calculate bid and ask prices
            buy_price = ref_price * Decimal(1 - self.config.bid_spread)
            sell_price = ref_price * Decimal(1 + self.config.ask_spread)
            
            # Determine order type (LIMIT_MAKER for post-only, LIMIT for regular)
            order_type = OrderType.LIMIT_MAKER if self.config.use_post_only else OrderType.LIMIT
            
            # Create order candidates
            buy_order = OrderCandidate(
                trading_pair=self.config.trading_pair, 
                is_maker=True, 
                order_type=order_type,
                order_side=TradeType.BUY, 
                amount=Decimal(self.config.order_amount), 
                price=buy_price
            )

            sell_order = OrderCandidate(
                trading_pair=self.config.trading_pair, 
                is_maker=True, 
                order_type=order_type,
                order_side=TradeType.SELL, 
                amount=Decimal(self.config.order_amount), 
                price=sell_price
            )
            
            # Log the order details
            self.log_with_clock(
                logging.INFO, 
                f"Created orders - Mid: {ref_price:.5f}, "
                f"Bid: {buy_price:.5f} (-{self.config.bid_spread*100:.1f}%), "
                f"Ask: {sell_price:.5f} (+{self.config.ask_spread*100:.1f}%), "
                f"Amount: {self.config.order_amount} DOGE"
            )

            return [buy_order, sell_order]
            
        except Exception as e:
            self.log_with_clock(logging.ERROR, f"Error creating proposal: {str(e)}")
            return []

    def adjust_proposal_to_budget(self, proposal: List[OrderCandidate]) -> List[OrderCandidate]:
        try:
            proposal_adjusted = self.connectors[self.config.exchange].budget_checker.adjust_candidates(
                proposal, 
                all_or_none=True
            )
            
            # Log budget adjustment results
            if len(proposal_adjusted) != len(proposal):
                self.log_with_clock(
                    logging.WARNING, 
                    f"Budget adjustment: {len(proposal)} -> {len(proposal_adjusted)} orders"
                )
            
            return proposal_adjusted
            
        except Exception as e:
            self.log_with_clock(logging.ERROR, f"Error adjusting proposal to budget: {str(e)}")
            return []

    def place_orders(self, proposal: List[OrderCandidate]) -> None:
        for i, order in enumerate(proposal):
            try:
                self.log_with_clock(
                    logging.INFO, 
                    f"Placing {order.order_side.name} order {i+1}/{len(proposal)}: "
                    f"{order.amount} {self.config.trading_pair} @ {order.price:.5f}"
                )
                self.place_order(connector_name=self.config.exchange, order=order)
            except Exception as e:
                self.log_with_clock(
                    logging.ERROR, 
                    f"Error placing {order.order_side.name} order: {str(e)}"
                )

    def place_order(self, connector_name: str, order: OrderCandidate):
        try:
            if order.order_side == TradeType.SELL:
                self.sell(
                    connector_name=connector_name, 
                    trading_pair=order.trading_pair, 
                    amount=order.amount,
                    order_type=order.order_type, 
                    price=order.price
                )
            elif order.order_side == TradeType.BUY:
                self.buy(
                    connector_name=connector_name, 
                    trading_pair=order.trading_pair, 
                    amount=order.amount,
                    order_type=order.order_type, 
                    price=order.price
                )
        except Exception as e:
            self.log_with_clock(
                logging.ERROR, 
                f"Error executing {order.order_side.name} order: {str(e)}"
            )

    def cancel_all_orders(self):
        try:
            active_orders = self.get_active_orders(connector_name=self.config.exchange)
            for order in active_orders:
                self.log_with_clock(
                    logging.INFO, 
                    f"Cancelling order: {order.client_order_id} "
                    f"({order.trade_type.name} {order.amount} @ {order.price:.5f})"
                )
                self.cancel(self.config.exchange, order.trading_pair, order.client_order_id)
        except Exception as e:
            self.log_with_clock(logging.ERROR, f"Error cancelling orders: {str(e)}")

    def did_fill_order(self, event: OrderFilledEvent):
        # Log filled orders (should be rare due to wide spread)
        msg = (
            f"ORDER FILLED: {event.trade_type.name} {round(event.amount, 1)} "
            f"{event.trading_pair} on {self.config.exchange} at {round(event.price, 5)} "
            f"(Fee: {round(event.trade_fee.flat_fees[0].amount, 4)} {event.trade_fee.flat_fees[0].token})"
        )
        self.log_with_clock(logging.WARNING, msg)  # Use WARNING since fills are unexpected
        self.notify_hb_app_with_timestamp(msg)

    def format_status(self) -> str:
        """
        Format status information for display
        """
        try:
            active_orders = self.get_active_orders(connector_name=self.config.exchange)
            
            # Get current mid price
            try:
                mid_price = self.connectors[self.config.exchange].get_price_by_type(
                    self.config.trading_pair, 
                    self.price_source
                )
                mid_price_str = f"{mid_price:.5f}" if mid_price else "N/A"
            except:
                mid_price_str = "N/A"
            
            # Get balance information
            try:
                connector = self.connectors[self.config.exchange]
                base_balance = connector.get_available_balance("DOGE")
                quote_balance = connector.get_available_balance("USDT")
                balance_str = f"DOGE: {base_balance:.1f}, USDT: {quote_balance:.2f}"
            except:
                balance_str = "N/A"
            
            status_lines = [
                "=" * 50,
                "VALR Test Bot Status",
                "=" * 50,
                f"Trading Pair: {self.config.trading_pair}",
                f"Exchange: {self.config.exchange}",
                f"Mid Price: {mid_price_str} USDT",
                f"Spread: ±{self.config.bid_spread*100:.1f}%",
                f"Order Amount: {self.config.order_amount} DOGE",
                f"Refresh Interval: {self.config.order_refresh_time}s",
                f"Active Orders: {len(active_orders)}",
                f"Balances: {balance_str}",
                ""
            ]
            
            # Add order details
            if active_orders:
                status_lines.append("Active Orders:")
                for order in active_orders:
                    side = "BUY" if order.trade_type == TradeType.BUY else "SELL"
                    status_lines.append(
                        f"  {side}: {order.amount:.1f} DOGE @ {order.price:.5f} USDT"
                    )
            else:
                status_lines.append("No active orders")
                
            status_lines.append("=" * 50)
            
            return "\n".join(status_lines)
            
        except Exception as e:
            return f"Error formatting status: {str(e)}"
    
    def test_websocket_orderbook_access(self):
        """Test WebSocket orderbook access and mid price calculation"""
        try:
            self.log_with_clock(logging.INFO, "=" * 60)
            self.log_with_clock(logging.INFO, "WEBSOCKET ORDERBOOK ACCESS TEST")
            self.log_with_clock(logging.INFO, "=" * 60)
            
            connector = self.connectors.get(self.config.exchange)
            if not connector:
                self.log_with_clock(logging.ERROR, "❌ No connector available for WebSocket test")
                return
            
            # Test 1: Check connector type and capabilities
            self.log_with_clock(logging.INFO, f"✅ Connector type: {type(connector).__name__}")
            
            # Test 2: Check if order book tracker is available
            if hasattr(connector, '_order_book_tracker'):
                self.log_with_clock(logging.INFO, "✅ OrderBook tracker available")
                
                # Test 3: Check data source
                if hasattr(connector._order_book_tracker, '_data_source'):
                    data_source = connector._order_book_tracker._data_source
                    self.log_with_clock(logging.INFO, f"✅ Data source available: {type(data_source).__name__}")
                    
                    # Test 4: Check WebSocket capabilities
                    if hasattr(data_source, 'listen_for_order_book_diffs'):
                        self.log_with_clock(logging.INFO, "✅ WebSocket orderbook diff listener available")
                    else:
                        self.log_with_clock(logging.WARNING, "❌ No WebSocket orderbook diff listener")
                        
                    if hasattr(data_source, 'listen_for_order_book_snapshots'):
                        self.log_with_clock(logging.INFO, "✅ WebSocket orderbook snapshot listener available")
                    else:
                        self.log_with_clock(logging.WARNING, "❌ No WebSocket orderbook snapshot listener")
                        
                else:
                    self.log_with_clock(logging.WARNING, "❌ No data source available")
                    
            else:
                self.log_with_clock(logging.WARNING, "❌ No order book tracker available")
            
            # Test 5: Test mid price access
            try:
                mid_price = connector.get_mid_price(self.config.trading_pair)
                if mid_price and mid_price > 0:
                    self.log_with_clock(logging.INFO, f"✅ Mid price accessible: {mid_price:.5f} USDT")
                    
                    # Test spread calculations
                    bid_price = mid_price * (1 - self.config.bid_spread)
                    ask_price = mid_price * (1 + self.config.ask_spread)
                    
                    self.log_with_clock(logging.INFO, f"✅ Bot order calculations:")
                    self.log_with_clock(logging.INFO, f"   Mid Price: {mid_price:.5f} USDT")
                    self.log_with_clock(logging.INFO, f"   Bid Price: {bid_price:.5f} USDT (-{self.config.bid_spread*100}%)")
                    self.log_with_clock(logging.INFO, f"   Ask Price: {ask_price:.5f} USDT (+{self.config.ask_spread*100}%)")
                    self.log_with_clock(logging.INFO, f"   Order Amount: {self.config.order_amount} DOGE")
                    
                else:
                    self.log_with_clock(logging.ERROR, "❌ Failed to get mid price")
                    
            except Exception as e:
                self.log_with_clock(logging.ERROR, f"❌ Mid price access failed: {e}")
            
            # Test 6: Test order book access
            try:
                order_book = connector.get_order_book(self.config.trading_pair)
                if order_book:
                    self.log_with_clock(logging.INFO, "✅ Order book accessible")
                    
                    try:
                        best_bid = order_book.get_best_bid()
                        best_ask = order_book.get_best_ask()
                        
                        if best_bid and best_ask:
                            self.log_with_clock(logging.INFO, f"   Best Bid: {best_bid.price:.5f} USDT (Size: {best_bid.amount:.1f})")
                            self.log_with_clock(logging.INFO, f"   Best Ask: {best_ask.price:.5f} USDT (Size: {best_ask.amount:.1f})")
                            
                            calculated_mid = (best_bid.price + best_ask.price) / 2
                            self.log_with_clock(logging.INFO, f"   Calculated Mid: {calculated_mid:.5f} USDT")
                            
                        else:
                            self.log_with_clock(logging.WARNING, "❌ No best bid/ask available")
                            
                    except Exception as e:
                        self.log_with_clock(logging.WARNING, f"❌ Best bid/ask access failed: {e}")
                        
                else:
                    self.log_with_clock(logging.WARNING, "❌ No order book data available")
                    
            except Exception as e:
                self.log_with_clock(logging.ERROR, f"❌ Order book access failed: {e}")
            
            # Test 7: Test price type source
            price_source = getattr(self.__class__, 'price_source', 'Unknown')
            self.log_with_clock(logging.INFO, f"✅ Price source configured: {price_source}")
            
            self.log_with_clock(logging.INFO, "=" * 60)
            self.log_with_clock(logging.INFO, "WEBSOCKET TEST COMPLETED")
            self.log_with_clock(logging.INFO, "=" * 60)
            
        except Exception as e:
            self.log_with_clock(logging.ERROR, f"❌ WebSocket test failed: {e}")
            import traceback
            self.log_with_clock(logging.ERROR, f"Traceback: {traceback.format_exc()}")