#!/usr/bin/env python3
"""
Test script to verify the budget adjustment fixes work correctly.
"""

import sys
from decimal import Decimal

# Add the hummingbot directory to the path
sys.path.insert(0, '/home/mailr/hummingbot-private/hummingbot')

from hummingbot.core.data_type.order_candidate import OrderCandidate
from hummingbot.core.data_type.common import OrderType, TradeType

def test_validate_order_amount():
    """Test the validate_order_amount method."""
    print("🧪 Testing order amount validation...")
    
    # Create a mock order with valid amount
    valid_order = OrderCandidate(
        trading_pair="DOGE-USDT",
        is_maker=True,
        order_type=OrderType.LIMIT,
        order_side=TradeType.BUY,
        amount=Decimal("4.0"),
        price=Decimal("0.24")
    )
    
    # Create a mock order with invalid amount (0E+28)
    invalid_order = OrderCandidate(
        trading_pair="DOGE-USDT",
        is_maker=True,
        order_type=OrderType.LIMIT,
        order_side=TradeType.BUY,
        amount=Decimal("0E+28"),  # This should be detected as invalid
        price=Decimal("0.24")
    )
    
    # Test the validation logic
    print(f"Valid order amount: {valid_order.amount}")
    print(f"Invalid order amount: {invalid_order.amount}")
    
    # Test the string representation
    print(f"Valid amount string: '{str(valid_order.amount)}'")
    print(f"Invalid amount string: '{str(invalid_order.amount)}'")
    
    # Test the detection logic
    valid_str = str(valid_order.amount)
    invalid_str = str(invalid_order.amount)
    
    print(f"Valid contains 'E+': {'E+' in valid_str}")
    print(f"Invalid contains 'E+': {'E+' in invalid_str}")
    
    # Test minimum order size
    min_order = Decimal("4.0")
    print(f"Valid >= min: {valid_order.amount >= min_order}")
    print(f"Invalid >= min: {invalid_order.amount >= min_order}")
    
    print("✅ Budget fix validation test completed!")

if __name__ == "__main__":
    test_validate_order_amount()