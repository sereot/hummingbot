import asyncio
import logging
import os
from decimal import Decimal
from typing import Dict, List, Optional

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
    - Order amounts of 4 DOGE (VALR minimum requirement)
    - Post-only orders (LIMIT_MAKER) for safety
    - 30-second refresh interval
    - Comprehensive logging for testing verification
    
    DOGEUSDT Trading Specifications (from VALR API):
    - Min Base Amount: 4 DOGE
    - Max Base Amount: 680,000 DOGE
    - Min Quote Amount: 0.5 USDT
    - Tick Size: 0.00001 USDT
    - Base Decimal Places: 1
    - Status: Active ✅
    """

    create_timestamp = 0
    price_source = PriceType.MidPrice
    markets = {"valr": {"DOGE-USDT"}}

    @classmethod
    def init_markets(cls, config: Optional[ValrTestBotConfig] = None):
        if config is None:
            config = ValrTestBotConfig()
        cls.markets = {config.exchange: {config.trading_pair}}
        cls.price_source = PriceType.LastTrade if config.price_type == "last" else PriceType.MidPrice

    def __init__(self, connectors: Dict[str, ConnectorBase], config: Optional[ValrTestBotConfig] = None):
        try:
            # Create config first (before parent constructor)
            if config is None:
                config = ValrTestBotConfig()
            
            # Validate config has required attributes
            if not hasattr(config, 'trading_pair') or not hasattr(config, 'exchange'):
                raise ValueError("Config must have trading_pair and exchange attributes")
                
            # Additional validation for critical config values
            if not config.trading_pair or not config.exchange:
                raise ValueError("Config trading_pair and exchange must not be empty")
            
            if config.order_amount <= 0:
                raise ValueError("Order amount must be positive")
                
            if config.bid_spread < 0 or config.ask_spread < 0:
                raise ValueError("Spreads must be non-negative")
            
            # Initialize markets based on config before calling super().__init__
            self.__class__.init_markets(config)
            
            # Call parent constructor (this may set self.config = None)
            super().__init__(connectors)
            
            # Set config AFTER parent constructor to prevent override
            # Double-check config is not None before assignment
            if config is None:
                raise ValueError("Config became None during initialization")
            
            self.config = config
            self.websocket_test_completed = False
            
            # Validate we have the required connector
            if self.config.exchange not in connectors:
                raise ValueError(f"Required connector '{self.config.exchange}' not found in connectors")
            
            # Validate connector is properly initialized
            connector = connectors[self.config.exchange]
            if not hasattr(connector, 'name') or connector.name != self.config.exchange:
                raise ValueError(f"Connector '{self.config.exchange}' is not properly initialized")
            
            # Final validation that config is still valid
            if not hasattr(self.config, 'trading_pair') or not self.config.trading_pair:
                raise ValueError("Config trading_pair was lost during initialization")
            
            # Log initialization after config is guaranteed to be set
            self.log_with_clock(
                logging.INFO, 
                f"VALR Test Bot initialized - Pair: {self.config.trading_pair}, "
                f"Exchange: {self.config.exchange}, Spread: {self.config.bid_spread*100}%, "
                f"Order Amount: {self.config.order_amount} DOGE"
            )
            
            # Add connector readiness monitoring
            self.log_with_clock(logging.INFO, "Bot initialized - waiting for connector to become ready...")
            self.initialization_complete = True
            
        except Exception as e:
            # Log the error with more context
            error_msg = f"Failed to initialize VALR Test Bot: {e}"
            if hasattr(self, 'logger'):
                self.logger().error(error_msg)
            else:
                # Fallback if logger is not available
                print(f"ERROR: {error_msg}")
            raise


    async def on_tick(self):
        # Log that on_tick is being called (this will help us debug if the method is actually executing)
        self.log_with_clock(logging.INFO, "🚀 on_tick method called - bot is executing!")
        
        try:
            # Check connector status immediately with timeout mechanism
            connector = self.connectors.get(self.config.exchange)
            if connector:
                connector_ready = connector.ready
                self.log_with_clock(logging.INFO, f"Connector ready: {connector_ready}")
                
                if not connector_ready:
                    status = connector.status_dict
                    self.log_with_clock(logging.WARNING, f"Connector not ready - status: {status}")
                    
                    # Implement timeout mechanism for connector readiness
                    if not hasattr(self, '_connector_wait_start'):
                        self._connector_wait_start = self.current_timestamp
                        self.log_with_clock(logging.INFO, "Starting connector readiness timeout timer")
                    
                    # Wait up to 2 minutes for connector to become ready
                    wait_time = self.current_timestamp - self._connector_wait_start
                    if wait_time > 120:  # 2 minutes timeout
                        self.log_with_clock(logging.ERROR, f"Connector failed to become ready after {wait_time:.1f}s")
                        self.log_with_clock(logging.ERROR, "Detailed status:")
                        for key, value in status.items():
                            status_icon = "✅" if value else "❌"
                            self.log_with_clock(logging.ERROR, f"  {status_icon} {key}: {value}")
                        self.log_with_clock(logging.ERROR, "Bot will continue attempting connection...")
                        # Reset timer to prevent spam
                        self._connector_wait_start = self.current_timestamp
                    
                    return
                else:
                    # Connector is ready, reset any timeout tracking
                    if hasattr(self, '_connector_wait_start'):
                        wait_time = self.current_timestamp - self._connector_wait_start
                        self.log_with_clock(logging.INFO, f"Connector became ready after {wait_time:.1f}s")
                        delattr(self, '_connector_wait_start')
            else:
                self.log_with_clock(logging.ERROR, "No connector found in on_tick")
                return
            
            # Run WebSocket test once at startup with timeout
            if not self.websocket_test_completed:
                try:
                    # Use asyncio.wait_for to timeout the WebSocket test
                    await asyncio.wait_for(
                        self.test_websocket_orderbook_access_async(),
                        timeout=30.0  # 30 second timeout
                    )
                    self.websocket_test_completed = True
                    self.log_with_clock(logging.INFO, "WebSocket test completed successfully")
                except asyncio.TimeoutError:
                    self.log_with_clock(logging.WARNING, "WebSocket test timed out after 30s, continuing without test")
                    self.websocket_test_completed = True  # Don't let this block the bot
                except Exception as ws_error:
                    self.log_with_clock(logging.WARNING, f"WebSocket test failed, continuing: {ws_error}")
                    self.websocket_test_completed = True  # Don't let this block the bot
            
            if self.create_timestamp <= self.current_timestamp:
                self.log_with_clock(logging.INFO, "Starting order refresh cycle")
                
                # Monitor order health before starting
                self.monitor_order_health()
                
                # Cancel existing orders with validation and timeout
                cancellation_successful = True
                active_orders = []
                
                try:
                    # Get active orders with timeout
                    active_orders = await asyncio.wait_for(
                        self.get_active_orders_async(connector_name=self.config.exchange),
                        timeout=10.0
                    )
                    
                    if active_orders:
                        self.log_with_clock(logging.INFO, f"Cancelling {len(active_orders)} existing orders")
                        
                        # Cancel each order individually and track success with timeout
                        cancelled_count = 0
                        for order in active_orders:
                            try:
                                await asyncio.wait_for(
                                    self.cancel_order_async(self.config.exchange, order.trading_pair, order.client_order_id),
                                    timeout=5.0
                                )
                                cancelled_count += 1
                                self.log_with_clock(logging.DEBUG, f"Cancelled order: {order.client_order_id}")
                            except asyncio.TimeoutError:
                                self.log_with_clock(logging.ERROR, f"Timeout cancelling order {order.client_order_id}")
                                cancellation_successful = False
                            except Exception as cancel_error:
                                self.log_with_clock(logging.ERROR, f"Failed to cancel order {order.client_order_id}: {cancel_error}")
                                cancellation_successful = False
                        
                        # Wait a moment for cancellations to process with timeout
                        try:
                            await asyncio.wait_for(asyncio.sleep(2), timeout=3.0)
                        except asyncio.TimeoutError:
                            self.log_with_clock(logging.WARNING, "Timeout waiting for cancellation processing")
                        
                        # Check if orders were actually cancelled with timeout
                        try:
                            remaining_orders = await asyncio.wait_for(
                                self.get_active_orders_async(connector_name=self.config.exchange),
                                timeout=10.0
                            )
                            if remaining_orders:
                                self.log_with_clock(logging.WARNING, f"Still have {len(remaining_orders)} active orders after cancellation")
                                cancellation_successful = False
                            else:
                                self.log_with_clock(logging.INFO, f"Successfully cancelled {cancelled_count} orders")
                        except asyncio.TimeoutError:
                            self.log_with_clock(logging.WARNING, "Timeout checking remaining orders after cancellation")
                            cancellation_successful = False
                            
                except asyncio.TimeoutError:
                    self.log_with_clock(logging.ERROR, "Timeout getting active orders for cancellation")
                    cancellation_successful = False
                except Exception as e:
                    self.log_with_clock(logging.ERROR, f"Error during order cancellation: {e}")
                    cancellation_successful = False
                
                # Safety check: Don't place new orders if we have too many already
                try:
                    current_active_orders = await asyncio.wait_for(
                        self.get_active_orders_async(connector_name=self.config.exchange),
                        timeout=10.0
                    )
                except asyncio.TimeoutError:
                    self.log_with_clock(logging.ERROR, "Timeout getting active orders for safety check")
                    current_active_orders = []
                except Exception as e:
                    self.log_with_clock(logging.ERROR, f"Error getting active orders for safety check: {e}")
                    current_active_orders = []
                    
                max_allowed_orders = 10  # Safety limit
                
                if len(current_active_orders) >= max_allowed_orders:
                    self.log_with_clock(logging.ERROR, f"Safety limit exceeded: {len(current_active_orders)} orders active (max: {max_allowed_orders})")
                    self.log_with_clock(logging.ERROR, "Skipping new order placement to prevent runaway order creation")
                    self.create_timestamp = self.config.order_refresh_time + self.current_timestamp
                    return
                
                # Enhanced condition: only place orders if cancellation was successful OR very few orders
                should_place_orders = cancellation_successful or len(current_active_orders) <= 2
                
                if should_place_orders:
                    # Verify connector is still ready before placing orders
                    connector = self.connectors.get(self.config.exchange)
                    if not connector:
                        self.log_with_clock(logging.ERROR, "Connector not found, skipping order placement")
                        self.create_timestamp = self.config.order_refresh_time + self.current_timestamp
                        return
                    
                    if not connector.ready:
                        # Log detailed status for debugging
                        status = connector.status_dict
                        not_ready_items = [k for k, v in status.items() if not v]
                        self.log_with_clock(logging.WARNING, f"Connector not ready, items not ready: {not_ready_items}")
                        self.log_with_clock(logging.DEBUG, f"Full connector status: {status}")
                        self.create_timestamp = self.config.order_refresh_time + self.current_timestamp
                        return
                    
                    # Create and place new orders with timeout
                    try:
                        proposal: List[OrderCandidate] = await asyncio.wait_for(
                            self.create_proposal_async(),
                            timeout=15.0
                        )
                        if proposal:
                            proposal_adjusted: List[OrderCandidate] = await asyncio.wait_for(
                                self.adjust_proposal_to_budget_async(proposal),
                                timeout=10.0
                            )
                            if proposal_adjusted:
                                await asyncio.wait_for(
                                    self.place_orders_async(proposal_adjusted),
                                    timeout=30.0
                                )
                                self.log_with_clock(
                                    logging.INFO, 
                                    f"Placed {len(proposal_adjusted)} orders, next refresh in {self.config.order_refresh_time}s"
                                )
                            else:
                                self.log_with_clock(logging.WARNING, "No orders placed - insufficient budget")
                        else:
                            self.log_with_clock(logging.WARNING, "No orders created - unable to get reference price")
                    except asyncio.TimeoutError:
                        self.log_with_clock(logging.ERROR, "Timeout creating or placing orders")
                    except Exception as e:
                        self.log_with_clock(logging.ERROR, f"Error creating or placing orders: {e}")
                        # Don't crash on order placement errors
                else:
                    self.log_with_clock(logging.WARNING, "Skipping new order placement - cancellation failed and too many orders active")
                    self.log_with_clock(logging.INFO, f"Current active orders: {len(current_active_orders)}")
                    
                self.create_timestamp = self.config.order_refresh_time + self.current_timestamp
                
        except asyncio.TimeoutError:
            self.log_with_clock(logging.ERROR, "Timeout in on_tick method - preventing freeze")
            # Set next refresh time even on timeout to prevent tight loop
            self.create_timestamp = self.config.order_refresh_time + self.current_timestamp
        except Exception as e:
            self.log_with_clock(logging.ERROR, f"Critical error in on_tick: {e}")
            # Set next refresh time even on error to prevent tight loop
            self.create_timestamp = self.config.order_refresh_time + self.current_timestamp
            # Don't re-raise to prevent strategy from crashing

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
        Format status information for display with enhanced monitoring
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
            
            # Check for order accumulation warning
            order_count = len(active_orders)
            order_warning = ""
            if order_count > 4:
                order_warning = " ⚠️ HIGH ORDER COUNT - Check for cancellation issues"
            elif order_count > 2:
                order_warning = " ⚠️ More orders than expected"
            
            # Get connector status
            connector_status = "Ready" if self.connectors[self.config.exchange].ready else "Not Ready"
            connector_status_details = self.connectors[self.config.exchange].status_dict
            
            status_lines = [
                "=" * 60,
                "VALR Test Bot Status (Enhanced Monitoring)",
                "=" * 60,
                f"Trading Pair: {self.config.trading_pair}",
                f"Exchange: {self.config.exchange}",
                f"Connector Status: {connector_status}",
                f"Mid Price: {mid_price_str} USDT",
                f"Spread: ±{self.config.bid_spread*100:.1f}%",
                f"Order Amount: {self.config.order_amount} DOGE",
                f"Refresh Interval: {self.config.order_refresh_time}s",
                f"Active Orders: {order_count}{order_warning}",
                f"Balances: {balance_str}",
                ""
            ]
            
            # Add connector status details
            status_lines.append("Connector Status Details:")
            for status_key, status_value in connector_status_details.items():
                status_icon = "✅" if status_value else "❌"
                status_lines.append(f"  {status_icon} {status_key}: {status_value}")
            status_lines.append("")
            
            # Add order details with enhanced information
            if active_orders:
                status_lines.append("Active Orders:")
                buy_orders = [o for o in active_orders if o.trade_type == TradeType.BUY]
                sell_orders = [o for o in active_orders if o.trade_type == TradeType.SELL]
                
                status_lines.append(f"  Buy Orders: {len(buy_orders)}")
                for order in buy_orders:
                    status_lines.append(
                        f"    {order.amount:.1f} DOGE @ {order.price:.5f} USDT (ID: {order.client_order_id[-8:]})"
                    )
                
                status_lines.append(f"  Sell Orders: {len(sell_orders)}")
                for order in sell_orders:
                    status_lines.append(
                        f"    {order.amount:.1f} DOGE @ {order.price:.5f} USDT (ID: {order.client_order_id[-8:]})"
                    )
                
                # Add warning if order distribution is uneven
                if len(buy_orders) != len(sell_orders):
                    status_lines.append("  ⚠️ Uneven order distribution - check for partial cancellation issues")
                    
            else:
                status_lines.append("No active orders")
            
            # Add next action information
            next_refresh = self.create_timestamp - self.current_timestamp
            if next_refresh > 0:
                status_lines.append(f"\nNext order refresh in: {next_refresh:.1f} seconds")
            else:
                status_lines.append("\nOrder refresh due now")
                
            status_lines.append("=" * 60)
            
            return "\n".join(status_lines)
            
        except Exception as e:
            return f"Error formatting status: {str(e)}"
    
    def monitor_order_health(self) -> None:
        """
        Monitor order health and log warnings if issues are detected
        """
        try:
            active_orders = self.get_active_orders(connector_name=self.config.exchange)
            order_count = len(active_orders)
            
            # Check for order accumulation
            if order_count > 10:
                self.log_with_clock(logging.ERROR, f"CRITICAL: {order_count} orders active - potential runaway condition")
            elif order_count > 6:
                self.log_with_clock(logging.WARNING, f"HIGH: {order_count} orders active - check cancellation logic")
            elif order_count > 2:
                self.log_with_clock(logging.INFO, f"MODERATE: {order_count} orders active - above expected")
            
            # Check for uneven order distribution
            if active_orders:
                buy_orders = [o for o in active_orders if o.trade_type == TradeType.BUY]
                sell_orders = [o for o in active_orders if o.trade_type == TradeType.SELL]
                
                if abs(len(buy_orders) - len(sell_orders)) > 1:
                    self.log_with_clock(logging.WARNING, f"Uneven orders: {len(buy_orders)} buy, {len(sell_orders)} sell")
            
            # Check connector health with detailed logging
            connector = self.connectors[self.config.exchange]
            if not connector.ready:
                status_dict = connector.status_dict
                not_ready = [k for k, v in status_dict.items() if not v]
                self.log_with_clock(logging.WARNING, f"Connector not ready: {not_ready}")
                self.log_with_clock(logging.DEBUG, f"Connector status details: {status_dict}")
            else:
                self.log_with_clock(logging.DEBUG, "Connector is ready and healthy")
                
        except Exception as e:
            self.log_with_clock(logging.ERROR, f"Error monitoring order health: {e}")
    
    async def test_websocket_orderbook_access_async(self):
        """Test WebSocket orderbook access and mid price calculation (async version)"""
        try:
            self.log_with_clock(logging.INFO, "=" * 60)
            self.log_with_clock(logging.INFO, "WEBSOCKET ORDERBOOK ACCESS TEST (ASYNC)")
            self.log_with_clock(logging.INFO, "=" * 60)
            
            # Add small delay to allow async operations
            await asyncio.sleep(0.1)
            
            # Call the sync version for the actual test logic
            self.test_websocket_orderbook_access()
            
        except Exception as e:
            self.log_with_clock(logging.ERROR, f"❌ Async WebSocket test failed: {e}")
            import traceback
            self.log_with_clock(logging.ERROR, f"Traceback: {traceback.format_exc()}")
    
    def test_websocket_orderbook_access(self):
        """Test WebSocket orderbook access and mid price calculation (sync version - deprecated)"""
        try:
            self.log_with_clock(logging.INFO, "=" * 60)
            self.log_with_clock(logging.INFO, "WEBSOCKET ORDERBOOK ACCESS TEST")
            self.log_with_clock(logging.INFO, "=" * 60)
            
            # Validate config is still available
            if not hasattr(self, 'config') or not self.config:
                self.log_with_clock(logging.ERROR, "❌ No config available for WebSocket test")
                return
            
            connector = self.connectors.get(self.config.exchange)
            if not connector:
                self.log_with_clock(logging.ERROR, "❌ No connector available for WebSocket test")
                return
            
            # Test 1: Check connector type and capabilities
            self.log_with_clock(logging.INFO, f"✅ Connector type: {type(connector).__name__}")
            
            # Test 2: Check connector readiness
            if hasattr(connector, 'ready') and connector.ready:
                self.log_with_clock(logging.INFO, "✅ Connector is ready")
            else:
                self.log_with_clock(logging.WARNING, "⚠️ Connector is not ready yet")
            
            # Test 3: Check if order book tracker is available
            if hasattr(connector, '_order_book_tracker'):
                self.log_with_clock(logging.INFO, "✅ OrderBook tracker available")
                
                # Test 4: Check data source
                if hasattr(connector._order_book_tracker, '_data_source'):
                    data_source = connector._order_book_tracker._data_source
                    self.log_with_clock(logging.INFO, f"✅ Data source available: {type(data_source).__name__}")
                    
                    # Test 5: Check WebSocket capabilities
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
            
            # Test 6: Test mid price access
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
            
            # Test 7: Test order book access
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
            
            # Test 8: Test price type source
            price_source = getattr(self.__class__, 'price_source', 'Unknown')
            self.log_with_clock(logging.INFO, f"✅ Price source configured: {price_source}")
            
            self.log_with_clock(logging.INFO, "=" * 60)
            self.log_with_clock(logging.INFO, "WEBSOCKET TEST COMPLETED")
            self.log_with_clock(logging.INFO, "=" * 60)
            
        except Exception as e:
            self.log_with_clock(logging.ERROR, f"❌ WebSocket test failed: {e}")
            import traceback
            self.log_with_clock(logging.ERROR, f"Traceback: {traceback.format_exc()}")
    
    # Async helper methods to prevent blocking
    async def get_active_orders_async(self, connector_name: str):
        """Get active orders asynchronously"""
        try:
            return self.get_active_orders(connector_name=connector_name)
        except Exception as e:
            self.log_with_clock(logging.ERROR, f"Error getting active orders: {e}")
            return []
    
    async def cancel_order_async(self, connector_name: str, trading_pair: str, client_order_id: str):
        """Cancel order asynchronously"""
        try:
            return self.cancel(connector_name, trading_pair, client_order_id)
        except Exception as e:
            self.log_with_clock(logging.ERROR, f"Error cancelling order {client_order_id}: {e}")
            raise
    
    async def create_proposal_async(self) -> List[OrderCandidate]:
        """Create order proposal asynchronously"""
        try:
            return self.create_proposal()
        except Exception as e:
            self.log_with_clock(logging.ERROR, f"Error creating proposal: {e}")
            return []
    
    async def adjust_proposal_to_budget_async(self, proposal: List[OrderCandidate]) -> List[OrderCandidate]:
        """Adjust proposal to budget asynchronously"""
        try:
            return self.adjust_proposal_to_budget(proposal)
        except Exception as e:
            self.log_with_clock(logging.ERROR, f"Error adjusting proposal to budget: {e}")
            return []
    
    async def place_orders_async(self, proposal: List[OrderCandidate]) -> None:
        """Place orders asynchronously"""
        try:
            self.place_orders(proposal)
        except Exception as e:
            self.log_with_clock(logging.ERROR, f"Error placing orders: {e}")
            raise