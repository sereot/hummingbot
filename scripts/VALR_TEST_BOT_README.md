# VALR Test Bot

## Overview

The VALR Test Bot is a simple market making bot designed specifically to test the VALR exchange connector's order placement functionality. It places limit orders on both sides of the DOGE-USDT trading pair at a safe distance from the mid price to verify that the connector can successfully place and manage orders.

## Key Features

- **Safe Testing**: Uses 100 bps (1%) spread to prevent accidental order fills
- **Minimal Risk**: Uses minimum order amounts (4 DOGE)
- **Post-Only Orders**: Uses LIMIT_MAKER orders to ensure maker status
- **Automatic Refresh**: Cancels and replaces orders every 30 seconds
- **Comprehensive Logging**: Detailed logs for monitoring and verification
- **Real-time Status**: Status display showing current orders and balances

## Configuration

### Trading Pair
- **Requested Pair**: DOGEUSDT ✅
- **Actual Pair**: DOGE-USDT (DOGEUSDT is ACTIVE and available on VALR)
- **Status**: Successfully verified and implemented

### Order Parameters
- **Exchange**: valr
- **Trading Pair**: DOGE-USDT
- **Order Amount**: 4 DOGE (VALR minimum)
- **Bid Spread**: 100 bps (1.0%) below mid price
- **Ask Spread**: 100 bps (1.0%) above mid price
- **Refresh Interval**: 30 seconds
- **Order Type**: LIMIT_MAKER (post-only)

### VALR Trading Rules for DOGEUSDT
- Symbol: "DOGEUSDT"
- Min Base Amount: 4 DOGE ✅
- Max Base Amount: 680,000 DOGE
- Min Quote Amount: 0.5 USDT
- Max Quote Amount: 54,000 USDT
- Tick Size: 0.00001 USDT
- Base Decimal Places: 1
- Status: Active ✅
- Margin Trading Allowed: true
- Currency Pair Type: SPOT

## How to Use

### Prerequisites
1. VALR exchange connector properly configured
2. VALR API credentials set up in Hummingbot
3. Sufficient DOGE and USDT balances for testing

### Running the Bot

1. **Copy the script to Hummingbot scripts directory**:
   ```bash
   cp valr_test_bot.py /path/to/hummingbot/scripts/
   ```

2. **Start Hummingbot and load the script**:
   ```bash
   start
   script --file valr_test_bot.py
   ```

3. **Monitor the bot**:
   - Watch the logs for order placement confirmations
   - Use `status` command to see current orders
   - Check VALR interface to verify orders appear in the order book

### Expected Behavior

The bot should:
1. ✅ Connect to VALR exchange successfully
2. ✅ Retrieve DOGE-USDT mid price
3. ✅ Calculate bid/ask prices at ±1% from mid
4. ✅ Place LIMIT_MAKER orders on both sides
5. ✅ Display orders in the order book
6. ✅ Cancel and replace orders every 30 seconds
7. ❌ **NOT** have orders filled (due to wide spread)

### Sample Log Output

```
2025-01-07 10:00:00 - VALR Test Bot initialized - Pair: DOGE-USDT, Exchange: valr, Spread: 1.0%, Order Amount: 4 DOGE
2025-01-07 10:00:01 - Starting order refresh cycle
2025-01-07 10:00:01 - Created orders - Mid: 0.32500, Bid: 0.32175 (-1.0%), Ask: 0.32825 (+1.0%), Amount: 4 DOGE
2025-01-07 10:00:01 - Placing BUY order 1/2: 4 DOGE-USDT @ 0.32175
2025-01-07 10:00:01 - Placing SELL order 2/2: 4 DOGE-USDT @ 0.32825
2025-01-07 10:00:02 - Placed 2 orders, next refresh in 30s
```

## Testing Verification

### Order Placement Tests
- [ ] Bot successfully connects to VALR
- [ ] Mid price is retrieved correctly
- [ ] Bid/ask prices calculated with 1% spread
- [ ] Orders placed in VALR order book
- [ ] Orders have correct amounts and prices
- [ ] Orders use LIMIT_MAKER type

### Order Management Tests
- [ ] Orders are cancelled after 30 seconds
- [ ] New orders are placed after cancellation
- [ ] Order refresh cycle continues automatically
- [ ] Error handling works for failed orders

### Safety Tests
- [ ] Orders are NOT filled (due to wide spread)
- [ ] Minimum order amounts are respected
- [ ] Post-only orders prevent market orders
- [ ] Budget checking prevents over-trading

## Troubleshooting

### Common Issues

1. **"Invalid reference price"**
   - Check VALR connection
   - Verify DOGE-USDT is trading

2. **"No orders placed - insufficient budget"**
   - Check DOGE and USDT balances
   - Verify minimum amounts available

3. **"Error placing order"**
   - Check VALR API credentials
   - Verify exchange permissions
   - Check network connectivity

### Debug Commands

```bash
# Check connector status
status

# View active orders
orders

# Check balances
balance

# View exchange status
exchange_status
```

## Risk Management

### Built-in Safety Features
- **Wide Spread**: 1% spread prevents accidental fills
- **Minimum Amounts**: Uses smallest possible order sizes (4 DOGE)
- **Post-Only**: LIMIT_MAKER orders ensure maker status
- **Timeout**: Orders refreshed every 30 seconds
- **Error Handling**: Comprehensive error catching and logging

### Manual Safety Measures
- Monitor logs continuously during testing
- Keep small test balances in VALR account (minimal DOGE and USDT)
- Have manual stop procedures ready
- Test during low volatility periods

## Success Criteria

The test is successful if:
1. ✅ Orders appear in VALR order book for DOGE-USDT
2. ✅ Orders maintain 1% spread from mid price
3. ✅ Orders refresh every 30 seconds
4. ✅ No orders are filled during testing
5. ✅ DOGE and USDT balances are tracked correctly
6. ✅ No errors in order placement/cancellation
7. ✅ Order amounts respect VALR minimums (4 DOGE)
8. ✅ Prices use correct tick size (0.00001 USDT)

This confirms that the VALR connector can successfully place and manage limit orders on the DOGEUSDT pair, which was the primary goal of this testing bot.