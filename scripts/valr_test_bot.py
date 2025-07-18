import asyncio
import logging
import os
import sys
from decimal import Decimal
from typing import Dict, List, Optional

from pydantic import Field

# Add the hummingbot directory to the path
sys.path.insert(0, '/home/mailr/hummingbot-private/hummingbot')

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
    order_refresh_time: int = Field(15)  # 15 seconds - optimized for performance
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
            
            # Persistent order tracking for backup
            self.placed_orders = {}  # client_order_id -> order_info
            self.last_order_placement_time = 0
            
            # Clear any stale order tracking data on startup
            self.placed_orders.clear()
            self.log_with_clock(logging.INFO, "🧹 Cleared stale order tracking data on startup")
            
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
            
            # Create market trading pair tuple for order operations
            self.market_trading_pair_tuple = self._market_trading_pair_tuple(
                self.config.exchange, 
                self.config.trading_pair
            )
            
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

    def _check_essential_readiness(self, connector, status: dict) -> bool:
        """
        Check if the connector has essential functionality available for trading,
        even if it's not in full 'ready' state.
        
        Args:
            connector: The exchange connector
            status: The connector status dictionary
            
        Returns:
            True if essential functionality is available, False otherwise
        """
        try:
            # Essential requirements for trading:
            # 1. Symbol mapping initialized (required for trading pair conversions)
            # 2. Trading rules initialized (required for order validation)
            # 3. Account balance available (required for order placement)
            
            symbols_ready = status.get('symbols_mapping_initialized', False)
            trading_rules_ready = status.get('trading_rule_initialized', False)
            account_balance_ready = status.get('account_balance', False)
            
            essential_ready = symbols_ready and trading_rules_ready and account_balance_ready
            
            # Additional checks for VALR-specific requirements
            if essential_ready:
                # Check if we have the required trading pairs
                if hasattr(connector, 'trading_pairs') and connector.trading_pairs:
                    # Check if we can access trading rules for our pair
                    trading_pair = self.config.trading_pair
                    if hasattr(connector, 'trading_rules') and connector.trading_rules:
                        if trading_pair in connector.trading_rules:
                            self.log_with_clock(logging.INFO, f"Essential functionality check: ✅ symbols: {symbols_ready}, ✅ trading_rules: {trading_rules_ready}, ✅ account_balance: {account_balance_ready}")
                            return True
                        else:
                            self.log_with_clock(logging.WARNING, f"Trading rules not available for {trading_pair}")
                    else:
                        self.log_with_clock(logging.WARNING, "Trading rules not available")
                else:
                    self.log_with_clock(logging.WARNING, "Trading pairs not available")
            
            self.log_with_clock(logging.WARNING, f"Essential functionality check: ❌ symbols: {symbols_ready}, ❌ trading_rules: {trading_rules_ready}, ❌ account_balance: {account_balance_ready}")
            return False
            
        except Exception as e:
            self.log_with_clock(logging.ERROR, f"Error checking essential readiness: {e}")
            return False

    def did_process_tick(self, timestamp: float):
        """
        Override the base class method to bypass the ready_to_trade check.
        This allows our custom on_tick logic to run even when the connector
        is not in full 'ready' state but has essential functionality.
        """
        # Log that we're processing a tick
        self.log_with_clock(logging.INFO, "🔄 Processing tick - bypassing base class ready check")
        
        # Call our custom on_tick method directly
        self.on_tick()

    def on_tick(self):
        # Enhanced logging for diagnostics
        tick_start = self.current_timestamp
        self.log_with_clock(logging.INFO, "🚀 on_tick method called - bot is executing!")
        
        try:
            # Check connector status with more tolerant logic
            connector = self.connectors.get(self.config.exchange)
            if connector:
                connector_ready = connector.ready
                
                # Enhanced status logging every 10 seconds
                if not hasattr(self, '_last_detailed_status_log'):
                    self._last_detailed_status_log = 0
                    
                if self.current_timestamp - self._last_detailed_status_log >= 10:
                    self._last_detailed_status_log = self.current_timestamp
                    status = connector.status_dict
                    
                    # Log detailed status with icons
                    self.log_with_clock(logging.INFO, f"📊 DETAILED STATUS REPORT:")
                    self.log_with_clock(logging.INFO, f"   🔗 Overall Ready: {'✅' if connector_ready else '❌'}")
                    self.log_with_clock(logging.INFO, f"   📈 Network Status: {connector.network_status}")
                    self.log_with_clock(logging.INFO, f"   📋 Component Status:")
                    
                    for key, value in status.items():
                        status_icon = "✅" if value else "❌"
                        self.log_with_clock(logging.INFO, f"      {status_icon} {key}: {value}")
                    
                    # Log WebSocket connection stats if available
                    if hasattr(connector, '_user_stream_data_source'):
                        ws_source = connector._user_stream_data_source
                        if hasattr(ws_source, '_websocket_connection_stats'):
                            stats = ws_source._websocket_connection_stats
                            self.log_with_clock(logging.INFO, f"   🔌 WebSocket Stats:")
                            self.log_with_clock(logging.INFO, f"      Success Rate: {stats.get('success_rate', 0):.1f}%")
                            self.log_with_clock(logging.INFO, f"      Total Connections: {stats.get('total_connections', 0)}")
                
                self.log_with_clock(logging.INFO, f"Connector ready: {connector_ready}")
                
                if not connector_ready:
                    status = connector.status_dict
                    self.log_with_clock(logging.WARNING, f"Connector not ready - status: {status}")
                    
                    # Check if we have essential functionality despite "not ready" status
                    essential_ready = self._check_essential_readiness(connector, status)
                    
                    if essential_ready:
                        self.log_with_clock(logging.INFO, "✅ Essential functionality available - continuing with trading despite 'not ready' status")
                        # Reset any timeout tracking since we can continue
                        if hasattr(self, '_connector_wait_start'):
                            delattr(self, '_connector_wait_start')
                    else:
                        # Implement timeout mechanism for connector readiness
                        if not hasattr(self, '_connector_wait_start'):
                            self._connector_wait_start = self.current_timestamp
                            self.log_with_clock(logging.INFO, "⏱️ Starting connector readiness timeout timer")
                        
                        # Wait up to 60 seconds for connector to become ready (reduced from 2 minutes)
                        wait_time = self.current_timestamp - self._connector_wait_start
                        if wait_time > 60:  # 1 minute timeout
                            self.log_with_clock(logging.ERROR, f"⚠️ Connector failed to become ready after {wait_time:.1f}s")
                            self.log_with_clock(logging.ERROR, "🔍 Detailed status:")
                            for key, value in status.items():
                                status_icon = "✅" if value else "❌"
                                self.log_with_clock(logging.ERROR, f"  {status_icon} {key}: {value}")
                            
                            # After timeout, try to continue with essential functionality
                            essential_ready = self._check_essential_readiness(connector, status)
                            if essential_ready:
                                self.log_with_clock(logging.WARNING, "⏰ Timeout reached - continuing with essential functionality")
                                # Reset timer to prevent spam
                                self._connector_wait_start = self.current_timestamp
                            else:
                                self.log_with_clock(logging.ERROR, "❌ Essential functionality not available - bot will keep waiting")
                                # Reset timer to prevent spam
                                self._connector_wait_start = self.current_timestamp
                                return
                        else:
                            # Still waiting for readiness - log progress
                            if int(wait_time) % 10 == 0:  # Log every 10 seconds
                                self.log_with_clock(logging.INFO, f"⏳ Still waiting for connector readiness ({wait_time:.1f}s elapsed)")
                            return
                else:
                    # Connector is ready, reset any timeout tracking
                    if hasattr(self, '_connector_wait_start'):
                        wait_time = self.current_timestamp - self._connector_wait_start
                        self.log_with_clock(logging.INFO, f"🎉 Connector became ready after {wait_time:.1f}s")
                        delattr(self, '_connector_wait_start')
            else:
                self.log_with_clock(logging.ERROR, "❌ No connector found in on_tick")
                return
            
            # Skip WebSocket test for now (synchronous execution)
            if not self.websocket_test_completed:
                self.log_with_clock(logging.INFO, "Skipping WebSocket test - using synchronous execution")
                self.websocket_test_completed = True
            
            if self.create_timestamp <= self.current_timestamp:
                # Add timing diagnostics
                self.log_with_clock(logging.INFO, f"🔄 Order refresh triggered - current: {self.current_timestamp}, next was: {self.create_timestamp}")
                self.log_with_clock(logging.INFO, f"📅 Time since last refresh: {self.current_timestamp - (self.create_timestamp - self.config.order_refresh_time):.1f}s")
                self.log_with_clock(logging.INFO, "Starting order refresh cycle")
                
                # Monitor order health before starting
                self.monitor_order_health()
                
                # Cancel existing orders with validation and detailed logging
                cancellation_successful = True
                active_orders = []
                
                try:
                    # Use comprehensive method to get active orders from all sources
                    active_orders = self.get_all_active_orders_comprehensive(connector_name=self.config.exchange)
                    
                    self.log_with_clock(logging.INFO, f"📊 Comprehensive order detection: {len(active_orders)} active orders to process")
                    
                    # Log details of existing orders
                    if active_orders:
                        self.log_with_clock(logging.INFO, f"📋 Active orders details:")
                        for i, order in enumerate(active_orders):
                            # Handle different order object types
                            if hasattr(order, 'client_order_id'):
                                order_id = order.client_order_id
                            elif hasattr(order, 'exchange_order_id'):
                                order_id = order.exchange_order_id
                            else:
                                order_id = str(order)
                            
                            if hasattr(order, 'trade_type'):
                                side = order.trade_type.name
                            elif hasattr(order, 'order_side'):
                                side = order.order_side.name
                            else:
                                side = "UNKNOWN"
                            
                            if hasattr(order, 'amount'):
                                amount = order.amount
                            elif hasattr(order, 'quantity'):
                                amount = order.quantity
                            else:
                                amount = "UNKNOWN"
                            
                            if hasattr(order, 'price'):
                                price = order.price
                            else:
                                price = "UNKNOWN"
                            
                            self.log_with_clock(logging.INFO, f"  Order {i+1}: {order_id} - {side} {amount} @ {price}")
                    
                    # Fast cancellation: Simple PMM pattern - no validation, no waiting
                    self.cancel_all_orders()
                            
                except Exception as e:
                    self.log_with_clock(logging.ERROR, f"❌ Error during order cancellation: {e}")
                
                # Simple PMM pattern: Create and place orders immediately after cancellation  
                proposal = self.create_proposal()
                proposal_adjusted = self.adjust_proposal_to_budget(proposal)
                self.place_orders(proposal_adjusted)
                self.create_timestamp = self.current_timestamp + self.config.order_refresh_time
                
        except Exception as e:
            self.log_with_clock(logging.ERROR, f"Critical error in on_tick: {e}")
            # Set next refresh time even on error to prevent tight loop
            self.create_timestamp = self.config.order_refresh_time + self.current_timestamp
            # Don't re-raise to prevent strategy from crashing

    def create_proposal(self) -> List[OrderCandidate]:
        try:
            self.log_with_clock(logging.INFO, f"🔍 Getting reference price for {self.config.trading_pair} using {self.price_source}")
            
            # Get reference price (mid price)
            ref_price = self.connectors[self.config.exchange].get_price_by_type(
                self.config.trading_pair, 
                self.price_source
            )
            
            if ref_price is None or ref_price <= 0:
                self.log_with_clock(logging.ERROR, f"❌ Invalid reference price: {ref_price}")
                
                # Try to get order book data for debugging
                try:
                    order_book = self.connectors[self.config.exchange].get_order_book(self.config.trading_pair)
                    if order_book:
                        self.log_with_clock(logging.ERROR, f"Order book available - Best bid: {order_book.get_price(False)}, Best ask: {order_book.get_price(True)}")
                    else:
                        self.log_with_clock(logging.ERROR, "No order book data available")
                except Exception as book_error:
                    self.log_with_clock(logging.ERROR, f"Error getting order book: {book_error}")
                
                return []
            
            self.log_with_clock(logging.INFO, f"✅ Got reference price: {ref_price}")
            
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
            # Log original proposal amounts
            self.log_with_clock(logging.INFO, "📊 Original proposal amounts:")
            for i, order in enumerate(proposal):
                self.log_with_clock(logging.INFO, f"  Order {i+1}: {order.order_side.name} {order.amount} @ {order.price}")
            
            # Try budget adjustment
            proposal_adjusted = self.connectors[self.config.exchange].budget_checker.adjust_candidates(
                proposal, 
                all_or_none=True
            )
            
            # Log adjusted proposal amounts
            self.log_with_clock(logging.INFO, "📊 After budget adjustment:")
            for i, order in enumerate(proposal_adjusted):
                self.log_with_clock(logging.INFO, f"  Order {i+1}: {order.order_side.name} {order.amount} @ {order.price}")
            
            # Validate adjusted amounts - detect corruption
            valid_adjusted = []
            for order in proposal_adjusted:
                if order.amount is None or order.amount <= 0 or str(order.amount).startswith('0E+'):
                    self.log_with_clock(logging.ERROR, f"❌ Invalid amount detected: {order.amount} for {order.order_side.name} order")
                    # Find original order and use its amount
                    original_order = None
                    for orig in proposal:
                        if orig.order_side == order.order_side:
                            original_order = orig
                            break
                    if original_order:
                        # Create new order with original amount
                        fixed_order = OrderCandidate(
                            trading_pair=order.trading_pair,
                            is_maker=order.is_maker,
                            order_type=order.order_type,
                            order_side=order.order_side,
                            amount=original_order.amount,  # Use original amount
                            price=order.price
                        )
                        valid_adjusted.append(fixed_order)
                        self.log_with_clock(logging.INFO, f"✅ Fixed order amount: {original_order.amount} for {order.order_side.name}")
                else:
                    valid_adjusted.append(order)
            
            # Log final results
            if len(valid_adjusted) != len(proposal):
                self.log_with_clock(
                    logging.WARNING, 
                    f"Budget adjustment: {len(proposal)} -> {len(valid_adjusted)} orders"
                )
            
            self.log_with_clock(logging.INFO, f"✅ Final valid orders: {len(valid_adjusted)}")
            return valid_adjusted
            
        except Exception as e:
            self.log_with_clock(logging.ERROR, f"Error adjusting proposal to budget: {str(e)}")
            # Return original proposal if budget adjustment fails
            self.log_with_clock(logging.WARNING, "Using original proposal due to budget adjustment error")
            return proposal

    def place_orders(self, proposal: List[OrderCandidate]) -> None:
        for i, order in enumerate(proposal):
            try:
                # Validate order amount before placement
                if not self.validate_order_amount(order):
                    self.log_with_clock(
                        logging.ERROR, 
                        f"❌ Skipping invalid order: {order.order_side.name} {order.amount} @ {order.price}"
                    )
                    continue
                
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
    
    def validate_order_amount(self, order: OrderCandidate) -> bool:
        """Validate that an order amount is valid for VALR placement."""
        try:
            # Check if amount is None or zero
            if order.amount is None or order.amount <= 0:
                self.log_with_clock(logging.ERROR, f"❌ Invalid amount: {order.amount} (None or zero)")
                return False
            
            # Check for scientific notation issues (0E+28, etc.)
            amount_str = str(order.amount)
            if 'E+' in amount_str or 'e+' in amount_str:
                self.log_with_clock(logging.ERROR, f"❌ Invalid amount format: {amount_str}")
                return False
            
            # Check minimum order size for VALR (4 DOGE)
            min_order_size = Decimal("4.0")
            if order.amount < min_order_size:
                self.log_with_clock(logging.ERROR, f"❌ Amount {order.amount} below minimum {min_order_size}")
                return False
            
            # Check that amount is a valid decimal
            try:
                float(order.amount)
            except (ValueError, TypeError):
                self.log_with_clock(logging.ERROR, f"❌ Amount not convertible to float: {order.amount}")
                return False
            
            self.log_with_clock(logging.DEBUG, f"✅ Valid order amount: {order.amount}")
            return True
            
        except Exception as e:
            self.log_with_clock(logging.ERROR, f"❌ Error validating order amount: {e}")
            return False

    def place_order(self, connector_name: str, order: OrderCandidate):
        try:
            # Place the order and capture the order ID
            order_id = None
            
            if order.order_side == TradeType.SELL:
                order_id = self.sell(
                    connector_name=connector_name, 
                    trading_pair=order.trading_pair, 
                    amount=order.amount,
                    order_type=order.order_type, 
                    price=order.price
                )
            elif order.order_side == TradeType.BUY:
                order_id = self.buy(
                    connector_name=connector_name, 
                    trading_pair=order.trading_pair, 
                    amount=order.amount,
                    order_type=order.order_type, 
                    price=order.price
                )
            
            # Track the order with the actual order ID
            self.track_placed_order(
                order_side=order.order_side.name,
                amount=order.amount,
                price=order.price,
                client_order_id=order_id
            )
            
            if order_id:
                self.log_with_clock(logging.DEBUG, f"✅ Placed and tracked order: {order_id}")
            else:
                self.log_with_clock(logging.WARNING, f"⚠️ Order placed but no ID returned for {order.order_side.name} order")
                
        except Exception as e:
            self.log_with_clock(
                logging.ERROR, 
                f"Error executing {order.order_side.name} order: {str(e)}"
            )

    def get_connector_active_orders(self, connector_name: str) -> List:
        """
        Get active orders directly from connector, bypassing strategy order tracker timing issues.
        This method queries the connector's order storage directly, which is more reliable
        than waiting for the strategy's order tracker to be updated asynchronously.
        """
        try:
            connector = self.connectors[connector_name]
            
            # Primary source: in_flight_orders (these are InFlightOrder objects)
            in_flight_orders = []
            if hasattr(connector, 'in_flight_orders'):
                in_flight_orders = list(connector.in_flight_orders.values())
            
            # Alternative source: limit_orders (these are LimitOrder objects)
            limit_orders = []
            if hasattr(connector, 'limit_orders'):
                limit_orders = list(connector.limit_orders)
            
            # Additional sources to check
            order_tracker_orders = []
            if hasattr(connector, '_order_tracker') and hasattr(connector._order_tracker, 'active_orders'):
                order_tracker_orders = list(connector._order_tracker.active_orders.values())
            
            # Log all sources for debugging
            self.log_with_clock(
                logging.DEBUG, 
                f"Order sources - in_flight: {len(in_flight_orders)}, limit: {len(limit_orders)}, tracker: {len(order_tracker_orders)}"
            )
            
            # Use in_flight_orders as primary source (most reliable)
            if in_flight_orders:
                active_orders = []
                for order in in_flight_orders:
                    # Check if order is still active
                    if hasattr(order, 'is_done') and not order.is_done:
                        active_orders.append(order)
                    elif hasattr(order, 'current_state') and order.current_state in ['SUBMITTED', 'PARTIALLY_FILLED', 'PENDING_CREATE']:
                        active_orders.append(order)
                    elif not hasattr(order, 'is_done') and not hasattr(order, 'current_state'):
                        # If we can't determine state, include it for safety
                        active_orders.append(order)
                
                self.log_with_clock(
                    logging.DEBUG, 
                    f"Using in_flight_orders: {len(active_orders)} active from {len(in_flight_orders)} total"
                )
                return active_orders
            
            # Fallback to limit_orders if in_flight_orders is empty
            elif limit_orders:
                self.log_with_clock(
                    logging.DEBUG, 
                    f"Using limit_orders fallback: {len(limit_orders)} orders"
                )
                return limit_orders
            
            # Final fallback to order tracker
            elif order_tracker_orders:
                active_orders = []
                for order in order_tracker_orders:
                    if hasattr(order, 'is_done') and not order.is_done:
                        active_orders.append(order)
                    elif not hasattr(order, 'is_done'):
                        active_orders.append(order)
                
                self.log_with_clock(
                    logging.DEBUG, 
                    f"Using order_tracker fallback: {len(active_orders)} active from {len(order_tracker_orders)} total"
                )
                return active_orders
            
            # No orders found
            self.log_with_clock(
                logging.DEBUG, 
                f"No active orders found in any source"
            )
            return []
            
        except Exception as e:
            self.log_with_clock(logging.ERROR, f"Error getting connector active orders: {e}")
            import traceback
            self.log_with_clock(logging.ERROR, f"Traceback: {traceback.format_exc()}")
            return []

    def get_active_orders_via_rest(self, connector_name: str) -> List:
        """
        Get active orders by directly querying the VALR REST API.
        This is a fallback method when connector internal storage is unreliable.
        """
        try:
            connector = self.connectors[connector_name]
            
            # This is a synchronous call in an async context, so we need to be careful
            # For now, let's implement it as a stub that logs and returns empty
            # In a real implementation, we would make an HTTP request to VALR's API
            
            self.log_with_clock(
                logging.DEBUG, 
                f"REST API fallback not implemented yet - would query VALR API for active orders"
            )
            
            # TODO: Implement actual REST API call to VALR
            # This would require:
            # 1. Making HTTP GET request to VALR's open orders endpoint
            # 2. Parsing the response to extract order details
            # 3. Converting to compatible order objects
            
            return []
            
        except Exception as e:
            self.log_with_clock(logging.ERROR, f"Error getting orders via REST API: {e}")
            return []

    def get_all_active_orders_comprehensive(self, connector_name: str) -> List:
        """
        Comprehensive method to get active orders using multiple sources and fallbacks.
        This method tries all available sources to ensure we never miss active orders.
        """
        try:
            # Try the enhanced connector method first
            connector_orders = self.get_connector_active_orders(connector_name)
            
            # Try the strategy tracker method
            strategy_orders = self.get_active_orders(connector_name)
            
            # Try our persistent tracking as backup
            tracked_orders = self.get_tracked_orders()
            
            # Log comparison
            self.log_with_clock(
                logging.DEBUG, 
                f"Comprehensive order check - Connector: {len(connector_orders)}, Strategy: {len(strategy_orders)}, Tracked: {len(tracked_orders)}"
            )
            
            # CRITICAL FIX: Prioritize sources that have proper order IDs for cancellation
            # The connector represents the actual exchange state, but we need IDs to cancel orders
            
            def has_cancellable_order_ids(orders):
                """Check if orders have proper IDs for cancellation"""
                if not orders:
                    return False
                for order in orders:
                    if hasattr(order, 'client_order_id') and order.client_order_id:
                        return True
                    elif hasattr(order, 'exchange_order_id') and order.exchange_order_id:
                        return True
                    elif isinstance(order, dict) and order.get('client_order_id'):
                        return True
                return False
            
            # Check which sources have cancellable IDs
            connector_has_ids = has_cancellable_order_ids(connector_orders)
            strategy_has_ids = has_cancellable_order_ids(strategy_orders)
            tracked_has_ids = has_cancellable_order_ids(tracked_orders)
            
            self.log_with_clock(
                logging.DEBUG, 
                f"Cancellable IDs available - Connector: {connector_has_ids}, Strategy: {strategy_has_ids}, Tracked: {tracked_has_ids}"
            )
            
            # Prioritize sources with proper order IDs for cancellation
            if connector_orders and connector_has_ids:
                primary_source = "connector"
                result_orders = connector_orders
                self.log_with_clock(logging.INFO, f"✅ Using connector orders: {len(connector_orders)} (exchange reality with IDs)")
                
                # Clean up stale tracking data if it doesn't match reality
                if len(tracked_orders) > len(connector_orders):
                    excess_count = len(tracked_orders) - len(connector_orders)
                    self.log_with_clock(logging.WARNING, f"🧹 Cleaning {excess_count} stale tracked orders")
                    self._cleanup_stale_tracking(connector_orders)
                
            elif strategy_orders and strategy_has_ids:
                primary_source = "strategy"
                result_orders = strategy_orders
                self.log_with_clock(logging.INFO, f"✅ Using strategy orders: {len(strategy_orders)} (reliable with IDs)")
                
            elif tracked_orders and tracked_has_ids:
                primary_source = "tracked"
                result_orders = tracked_orders
                self.log_with_clock(logging.INFO, f"✅ Using tracked orders: {len(tracked_orders)} (fallback with IDs)")
                
            # Fallback to any orders even without proper IDs (better than nothing)
            elif connector_orders:
                primary_source = "connector"
                result_orders = connector_orders
                self.log_with_clock(logging.WARNING, f"⚠️ Using connector orders: {len(connector_orders)} (no cancellable IDs)")
                
            elif strategy_orders:
                primary_source = "strategy"
                result_orders = strategy_orders
                self.log_with_clock(logging.WARNING, f"⚠️ Using strategy orders: {len(strategy_orders)} (no cancellable IDs)")
                
            elif tracked_orders:
                primary_source = "tracked"
                result_orders = tracked_orders
                self.log_with_clock(logging.WARNING, f"⚠️ Using tracked orders: {len(tracked_orders)} (no cancellable IDs)")
                
            else:
                primary_source = "none"
                result_orders = []
                self.log_with_clock(logging.INFO, "ℹ️ No orders found in any source")
            
            # If there are significant differences, log warnings
            if result_orders:
                counts = [len(connector_orders), len(strategy_orders), len(tracked_orders)]
                if max(counts) - min(counts) > 1:
                    self.log_with_clock(
                        logging.WARNING, 
                        f"Order count discrepancy! Connector: {len(connector_orders)}, Strategy: {len(strategy_orders)}, Tracked: {len(tracked_orders)}"
                    )
            
            # Return the prioritized source (connector orders preferred for proper cancellation)
            if result_orders:
                self.log_with_clock(logging.DEBUG, f"Using {primary_source} orders (exchange reality)")
                return result_orders
            else:
                self.log_with_clock(logging.DEBUG, "No active orders found in any source")
                return []
                
        except Exception as e:
            self.log_with_clock(logging.ERROR, f"Error in comprehensive order check: {e}")
            # Final fallback to strategy tracker
            try:
                return self.get_active_orders(connector_name)
            except:
                return []

    def _cleanup_stale_tracking(self, current_orders: List):
        """
        Clean up stale tracking data by removing orders that don't exist in the current order list.
        """
        try:
            # Get current order IDs from connector
            current_order_ids = set()
            for order in current_orders:
                if hasattr(order, 'client_order_id') and order.client_order_id:
                    current_order_ids.add(order.client_order_id)
                elif hasattr(order, 'exchange_order_id') and order.exchange_order_id:
                    current_order_ids.add(order.exchange_order_id)
            
            # Remove tracked orders that don't exist in current orders
            stale_keys = []
            for key, order_info in self.placed_orders.items():
                if isinstance(order_info, dict):
                    tracked_id = order_info.get('client_order_id')
                    if tracked_id and tracked_id not in current_order_ids:
                        stale_keys.append(key)
                elif key not in current_order_ids:
                    stale_keys.append(key)
            
            # Remove stale entries
            for key in stale_keys:
                del self.placed_orders[key]
                self.log_with_clock(logging.DEBUG, f"🧹 Removed stale tracking for: {key}")
            
            if stale_keys:
                self.log_with_clock(logging.INFO, f"🧹 Cleaned up {len(stale_keys)} stale tracking entries")
            
        except Exception as e:
            self.log_with_clock(logging.ERROR, f"Error cleaning up stale tracking: {e}")

    def _validate_cancellation_via_rest(self, connector_name: str, order_ids: List[str]) -> bool:
        """
        Validate that orders were actually cancelled by checking directly via REST API.
        This is critical because the cancellation might appear successful but fail silently.
        """
        try:
            connector = self.connectors[connector_name]
            
            # Check if orders still exist via REST API
            if hasattr(connector, 'get_open_orders'):
                # Try to get open orders directly from the exchange
                open_orders = connector.get_open_orders()
                
                # Check if any of our cancelled orders are still in the open orders
                still_open = []
                for order_id in order_ids:
                    for open_order in open_orders:
                        if (hasattr(open_order, 'client_order_id') and open_order.client_order_id == order_id) or \
                           (hasattr(open_order, 'exchange_order_id') and open_order.exchange_order_id == order_id):
                            still_open.append(order_id)
                
                if still_open:
                    self.log_with_clock(logging.ERROR, f"❌ REST validation failed: {len(still_open)} orders still open: {still_open}")
                    return False
                else:
                    self.log_with_clock(logging.INFO, f"✅ REST validation passed: All {len(order_ids)} orders cancelled successfully")
                    return True
                    
            else:
                # Fallback: check if the connector's internal tracking shows the specific orders are gone
                current_orders = self.get_connector_active_orders(connector_name)
                
                # Check if any of the specific cancelled orders are still in current orders
                still_open = []
                for order_id in order_ids:
                    for current_order in current_orders:
                        if (hasattr(current_order, 'client_order_id') and current_order.client_order_id == order_id) or \
                           (hasattr(current_order, 'exchange_order_id') and current_order.exchange_order_id == order_id):
                            still_open.append(order_id)
                
                if still_open:
                    self.log_with_clock(logging.WARNING, f"⚠️ Connector validation failed: {len(still_open)} specific orders still active: {still_open}")
                    return False
                else:
                    self.log_with_clock(logging.INFO, f"✅ Connector validation passed: All {len(order_ids)} specific orders cancelled")
                    return True
                    
        except Exception as e:
            self.log_with_clock(logging.ERROR, f"❌ Error validating cancellation via REST: {e}")
            return False



    def track_placed_order(self, order_side: str, amount: Decimal, price: Decimal, client_order_id: str = None):
        """
        Track orders we've placed for backup order management.
        """
        try:
            order_info = {
                'side': order_side,
                'amount': amount,
                'price': price,
                'timestamp': self.current_timestamp,
                'client_order_id': client_order_id
            }
            
            # If we have a client order ID, use it as key
            if client_order_id:
                self.placed_orders[client_order_id] = order_info
                self.log_with_clock(logging.DEBUG, f"Tracked order: {client_order_id} - {order_side} {amount} @ {price}")
            else:
                # Use timestamp as key if no client order ID
                timestamp_key = f"{order_side}_{self.current_timestamp}"
                self.placed_orders[timestamp_key] = order_info
                self.log_with_clock(logging.DEBUG, f"Tracked order: {timestamp_key} - {order_side} {amount} @ {price}")
            
            self.last_order_placement_time = self.current_timestamp
            
        except Exception as e:
            self.log_with_clock(logging.ERROR, f"Error tracking placed order: {e}")

    def get_tracked_orders(self) -> List:
        """
        Get orders from our persistent tracking.
        This is a backup method when connector tracking fails.
        """
        try:
            # Clean up old tracked orders (older than 5 minutes)
            current_time = self.current_timestamp
            cutoff_time = current_time - 300  # 5 minutes
            
            # Remove old orders
            old_keys = [key for key, info in self.placed_orders.items() if info['timestamp'] < cutoff_time]
            for key in old_keys:
                del self.placed_orders[key]
                self.log_with_clock(logging.DEBUG, f"Removed old tracked order: {key}")
            
            # Return remaining orders
            tracked_orders = list(self.placed_orders.values())
            self.log_with_clock(logging.DEBUG, f"Tracked orders: {len(tracked_orders)} orders in memory")
            
            return tracked_orders
            
        except Exception as e:
            self.log_with_clock(logging.ERROR, f"Error getting tracked orders: {e}")
            return []


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
            active_orders = self.get_all_active_orders_comprehensive(connector_name=self.config.exchange)
            order_count = len(active_orders)
            
            # Log order health summary
            self.log_with_clock(logging.DEBUG, f"🔍 Order health check: {order_count} orders detected")
            
            # Check for order accumulation
            if order_count > 10:
                self.log_with_clock(logging.ERROR, f"CRITICAL: {order_count} orders active - potential runaway condition")
            elif order_count > 6:
                self.log_with_clock(logging.WARNING, f"HIGH: {order_count} orders active - check cancellation logic")
            elif order_count > 2:
                self.log_with_clock(logging.INFO, f"MODERATE: {order_count} orders active - above expected")
            
            # Check for uneven order distribution
            if active_orders:
                buy_orders = []
                sell_orders = []
                
                for order in active_orders:
                    if hasattr(order, 'trade_type'):
                        if order.trade_type == TradeType.BUY:
                            buy_orders.append(order)
                        elif order.trade_type == TradeType.SELL:
                            sell_orders.append(order)
                    elif hasattr(order, 'order_side'):
                        if order.order_side == TradeType.BUY:
                            buy_orders.append(order)
                        elif order.order_side == TradeType.SELL:
                            sell_orders.append(order)
                
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
    
    def cancel_all_orders(self):
        """Fast order cancellation using Simple PMM pattern - no validation, no waiting."""
        try:
            active_orders = self.get_active_orders(connector_name=self.config.exchange)
            for order in active_orders:
                self.cancel(self.config.exchange, order.trading_pair, order.client_order_id)
        except Exception as e:
            self.log_with_clock(logging.ERROR, f"Error in cancel_all_orders: {e}")

    # Async helper methods to prevent blocking
# Removed async helper methods - now using synchronous operations directly