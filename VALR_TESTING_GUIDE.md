# VALR Exchange Connector Testing Guide

This guide provides comprehensive instructions for testing the VALR spot exchange connector implementation in Hummingbot.

## Table of Contents
- [Overview](#overview)
- [Test Results Summary](#test-results-summary)
- [Manual Testing Steps](#manual-testing-steps)
- [Automated Tests](#automated-tests)
- [Troubleshooting](#troubleshooting)
- [Next Steps](#next-steps)

## Overview

The VALR exchange connector has been successfully implemented and integrated into Hummingbot. This connector supports:

- **Exchange Type**: Centralized Exchange (CEX)
- **Trading Type**: Spot trading
- **Example Trading Pair**: BTC-ZAR
- **Fees**: 0.05% maker, 0.1% taker
- **API Endpoints**: Full REST API and WebSocket support
- **Authentication**: HMAC-SHA512 signing

## Test Results Summary

### ✅ **PASSED TESTS**

1. **Connector Registration**: VALR is properly registered in Hummingbot's connector settings
2. **Configuration**: Config map structure is correct with required API key/secret fields
3. **API Connectivity**: Successfully connects to VALR API endpoints
4. **Market Data**: Retrieves trading pairs, market summary, and ticker data
5. **Integration**: Properly integrates with Hummingbot's core systems
6. **Fee Calculations**: Correctly calculates maker/taker fees

### ⚠️ **AREAS REQUIRING LIVE TESTING**

1. **Authentication**: Requires real API credentials to test private endpoints
2. **Order Management**: Needs live trading to test order placement/cancellation
3. **WebSocket Streams**: Real-time data streams need live testing
4. **Paper Trading**: Paper trading mode needs configuration update

## Manual Testing Steps

### Prerequisites

1. **Environment Setup**
   ```bash
   conda activate hummingbot
   cd /path/to/hummingbot
   ```

2. **VALR Account**
   - Create account at https://valr.com
   - Generate API key and secret
   - Ensure API key has trading permissions

### Step 1: Basic Connectivity Test

Run the automated test script:
```bash
python test_valr_connector.py
```

Expected output:
```
✓ VALR connector registered successfully
✓ Market data retrieval working
✓ API connectivity test passed
```

### Step 2: Connect to VALR Exchange

1. Start Hummingbot:
   ```bash
   ./start
   ```

2. Connect to VALR:
   ```
   connect valr
   ```

3. Enter your API credentials when prompted

### Step 3: Test Market Data

1. Check available trading pairs:
   ```
   ticker valr
   ```

2. Check specific pair data:
   ```
   ticker BTC-ZAR
   ```

3. Check account balance:
   ```
   balance
   ```

### Step 4: Test Strategy Integration

1. Create a Pure Market Making strategy:
   ```
   create
   ```

2. Select Pure Market Making strategy

3. Configure with VALR:
   - Exchange: `valr`
   - Trading pair: `BTC-ZAR` (or other available pair)
   - Bid/ask spread: `0.01` (1%)
   - Order amount: `0.001` (small test amount)

4. Start the strategy:
   ```
   start
   ```

### Step 5: Monitor Performance

1. Check strategy status:
   ```
   status
   ```

2. Monitor order book:
   ```
   book
   ```

3. Check active orders:
   ```
   orders
   ```

## Automated Tests

### Test Scripts Included

1. **`test_valr_connector.py`**: Basic connector functionality
2. **`test_valr_strategy.py`**: Strategy integration testing

### Running Tests

```bash
# Basic connector tests
python test_valr_connector.py

# Strategy integration tests  
python test_valr_strategy.py
```

### Test Coverage

- [x] Connector registration and settings
- [x] Configuration map validation
- [x] Public API endpoints
- [x] Market data retrieval
- [x] Fee calculations
- [x] Strategy integration
- [ ] Private API endpoints (requires credentials)
- [ ] Order management (requires live trading)
- [ ] WebSocket streams (requires credentials)

## Troubleshooting

### Common Issues

#### 1. **Import Errors**
```
ModuleNotFoundError: No module named 'hexbytes'
```
**Solution**: Ensure conda environment is activated:
```bash
conda activate hummingbot
```

#### 2. **Connector Not Found**
```
✗ VALR connector not found in settings
```
**Solution**: Check that VALR connector files are in the correct location:
```bash
ls hummingbot/connector/exchange/valr/
```

#### 3. **API Connection Failed**
```
✗ API connectivity test failed: 403
```
**Solution**: 
- Check internet connection
- Verify VALR API is accessible
- Some endpoints require authentication

#### 4. **Configuration Validation Errors**
```
ValidationError: Field required [type=missing]
```
**Solution**: Use `model_construct()` for testing:
```python
config = ValrConfigMap.model_construct()
```

### API Limitations

1. **Rate Limits**: VALR has various rate limits (10-100 req/sec)
2. **Authentication**: Some endpoints require valid API credentials
3. **Order Book**: Full order book access requires authentication
4. **WebSocket**: Account data streams require authentication

### Known Working Features

- ✅ Public market data (pairs, ticker, market summary)
- ✅ Server time and connectivity
- ✅ Trading rules and pair validation
- ✅ Fee calculations
- ✅ Connector registration in Hummingbot

### Features Requiring Live Testing

- ⏳ Private account data (balances, orders)
- ⏳ Order placement and management
- ⏳ Real-time WebSocket streams
- ⏳ Trade execution and updates

## Next Steps

### For Production Use

1. **API Credentials**: Obtain real VALR API credentials
2. **Paper Trading**: Configure paper trading mode for VALR
3. **Strategy Testing**: Test with small amounts first
4. **Performance Testing**: Monitor latency and throughput
5. **Risk Management**: Set appropriate limits and safeguards

### Recommended Testing Sequence

1. **Start Small**: Use minimum order sizes
2. **Monitor Closely**: Watch for errors or unexpected behavior
3. **Test Incrementally**: Gradually increase order sizes
4. **Document Issues**: Report any problems found
5. **Backup Plans**: Have manual trading access ready

### Configuration Recommendations

```yaml
# Example strategy configuration for VALR
exchange: valr
trading_pair: BTC-ZAR
bid_spread: 0.001  # 0.1%
ask_spread: 0.001  # 0.1%
order_amount: 0.001  # 0.001 BTC
order_refresh_time: 30  # 30 seconds
```

## Support and Resources

### VALR API Documentation
- **API Docs**: https://docs.valr.com/
- **Rate Limits**: https://docs.valr.com/#rate-limiting
- **Trading Pairs**: https://api.valr.com/v1/public/pairs

### Hummingbot Resources
- **Discord**: https://discord.hummingbot.org (#developer-chat)
- **Documentation**: https://hummingbot.org/developers/
- **GitHub Issues**: https://github.com/hummingbot/hummingbot/issues

### Connector Implementation
- **Location**: `hummingbot/connector/exchange/valr/`
- **Main Files**: 
  - `valr_exchange.py` - Main connector
  - `valr_auth.py` - Authentication
  - `valr_constants.py` - API constants
  - `valr_utils.py` - Configuration

---

## Test Environment Information

- **Python Version**: 3.10+
- **Hummingbot Version**: Latest development
- **Test Date**: 2025-01-07
- **Conda Environment**: hummingbot
- **Platform**: Linux/WSL2

---

**Note**: This connector is ready for testing but should be used with caution on live funds. Always start with paper trading or small amounts when testing with real API credentials.