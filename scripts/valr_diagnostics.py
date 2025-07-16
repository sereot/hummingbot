#!/usr/bin/env python3
"""
VALR Connector Diagnostic Script
==============================

This script performs comprehensive testing of the VALR connector to identify
and diagnose connectivity, authentication, and functionality issues.
"""

import asyncio
import json
import logging
import sys
import time
from decimal import Decimal
from typing import Dict, Any, Optional

# Add the hummingbot directory to the path
sys.path.insert(0, '/home/mailr/hummingbot-private/hummingbot')

from hummingbot.connector.exchange.valr.valr_auth import ValrAuth
from hummingbot.connector.exchange.valr import valr_constants as CONSTANTS
from hummingbot.connector.exchange.valr import valr_web_utils as web_utils
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory
from hummingbot.core.web_assistant.connections.data_types import RESTMethod, WSJSONRequest
from hummingbot.core.api_throttler.async_throttler import AsyncThrottler


class ValrDiagnostics:
    """Comprehensive diagnostic testing for VALR connector."""
    
    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.auth = ValrAuth(api_key, api_secret)
        self.throttler = AsyncThrottler(CONSTANTS.RATE_LIMITS)
        self.web_assistants_factory = web_utils.build_api_factory(
            throttler=self.throttler,
            auth=self.auth
        )
        
        # Set up logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)
    
    def print_header(self, title: str):
        """Print a formatted header."""
        print(f"\n{'='*60}")
        print(f"{title.center(60)}")
        print(f"{'='*60}")
    
    def print_result(self, test_name: str, success: bool, details: str = ""):
        """Print a formatted test result."""
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{status} {test_name}")
        if details:
            print(f"    {details}")
    
    async def test_rest_connectivity(self) -> bool:
        """Test basic REST API connectivity."""
        self.print_header("REST API CONNECTIVITY TEST")
        
        try:
            rest_assistant = await self.web_assistants_factory.get_rest_assistant()
            
            # Test public endpoint (ping)
            ping_url = web_utils.public_rest_url(CONSTANTS.PING_PATH_URL)
            response = await rest_assistant.execute_request(
                url=ping_url,
                method=RESTMethod.GET,
                throttler_limit_id=CONSTANTS.PING_PATH_URL
            )
            
            self.print_result("Public API Ping", True, f"Response: {response}")
            return True
            
        except Exception as e:
            self.print_result("Public API Ping", False, f"Error: {str(e)}")
            return False
    
    async def test_authentication(self) -> bool:
        """Test API authentication with private endpoints."""
        self.print_header("API AUTHENTICATION TEST")
        
        try:
            rest_assistant = await self.web_assistants_factory.get_rest_assistant()
            
            # Test private endpoint (account balances)
            balances_url = web_utils.private_rest_url(CONSTANTS.ACCOUNTS_PATH_URL)
            response = await rest_assistant.execute_request(
                url=balances_url,
                method=RESTMethod.GET,
                throttler_limit_id=CONSTANTS.ACCOUNTS_PATH_URL,
                is_auth_required=True
            )
            
            self.print_result("Private API Authentication", True, f"Got {len(response)} balances")
            
            # Print some balance info (without revealing actual amounts)
            if response:
                currencies = [bal.get('currency', 'Unknown') for bal in response[:5]]
                print(f"    Sample currencies: {currencies}")
            
            return True
            
        except Exception as e:
            self.print_result("Private API Authentication", False, f"Error: {str(e)}")
            return False
    
    async def test_trading_pairs(self) -> bool:
        """Test trading pairs retrieval."""
        self.print_header("TRADING PAIRS TEST")
        
        try:
            rest_assistant = await self.web_assistants_factory.get_rest_assistant()
            
            # Get trading pairs
            pairs_url = web_utils.public_rest_url(CONSTANTS.PAIRS_PATH_URL)
            response = await rest_assistant.execute_request(
                url=pairs_url,
                method=RESTMethod.GET,
                throttler_limit_id=CONSTANTS.PAIRS_PATH_URL
            )
            
            self.print_result("Trading Pairs Retrieval", True, f"Got {len(response)} pairs")
            
            # Check for DOGE-USDT specifically
            doge_pair = None
            for pair in response:
                if pair.get('symbol') == 'DOGEUSDT':
                    doge_pair = pair
                    break
            
            if doge_pair:
                self.print_result("DOGE-USDT Pair Available", True, 
                                f"Active: {doge_pair.get('active', False)}")
                print(f"    Min Base Amount: {doge_pair.get('minBaseAmount', 'N/A')}")
                print(f"    Max Base Amount: {doge_pair.get('maxBaseAmount', 'N/A')}")
                print(f"    Tick Size: {doge_pair.get('tickSize', 'N/A')}")
                print(f"    Min Quote Amount: {doge_pair.get('minQuoteAmount', 'N/A')}")
            else:
                self.print_result("DOGE-USDT Pair Available", False, "Not found")
            
            return True
            
        except Exception as e:
            self.print_result("Trading Pairs Retrieval", False, f"Error: {str(e)}")
            return False
    
    async def test_websocket_connection(self) -> bool:
        """Test WebSocket connection and authentication."""
        self.print_header("WEBSOCKET CONNECTION TEST")
        
        try:
            ws_assistant = await self.web_assistants_factory.get_ws_assistant()
            
            # Test connection to public WebSocket
            self.logger.info("Testing public WebSocket connection...")
            await ws_assistant.connect(
                ws_url=CONSTANTS.WSS_TRADE_URL,
                ping_timeout=10.0
            )
            
            self.print_result("Public WebSocket Connection", True, "Connected successfully")
            
            # Test subscription to DOGE-USDT
            subscribe_msg = WSJSONRequest({
                "type": "SUBSCRIBE",
                "subscriptions": [{
                    "event": "AGGREGATED_ORDERBOOK_UPDATE",
                    "pairs": ["DOGEUSDT"]
                }]
            })
            
            await ws_assistant.send(subscribe_msg)
            self.print_result("Public WebSocket Subscription", True, "Subscribed to DOGEUSDT")
            
            # Wait for a message
            try:
                async with asyncio.timeout(10.0):
                    async for msg in ws_assistant.iter_messages():
                        self.print_result("Public WebSocket Data", True, f"Got message: {msg.data.get('type', 'Unknown')}")
                        break
            except asyncio.TimeoutError:
                self.print_result("Public WebSocket Data", False, "No message received in 10 seconds")
            
            await ws_assistant.disconnect()
            
            return True
            
        except Exception as e:
            self.print_result("Public WebSocket Connection", False, f"Error: {str(e)}")
            return False
    
    async def test_websocket_authentication(self) -> bool:
        """Test WebSocket authentication for private streams."""
        self.print_header("WEBSOCKET AUTHENTICATION TEST")
        
        try:
            ws_assistant = await self.web_assistants_factory.get_ws_assistant()
            
            # Connect to account WebSocket
            self.logger.info("Connecting to account WebSocket...")
            await ws_assistant.connect(
                ws_url=CONSTANTS.WSS_ACCOUNT_URL,
                ping_timeout=10.0
            )
            
            self.print_result("Account WebSocket Connection", True, "Connected successfully")
            
            # Send authentication message
            auth_payload = self.auth.get_ws_auth_payload("/ws/account")
            self.logger.info(f"Auth payload structure: {list(auth_payload.keys())}")
            
            auth_request = WSJSONRequest(auth_payload)
            await ws_assistant.send(auth_request)
            
            self.print_result("Authentication Message Sent", True, "Sent authentication request")
            
            # Wait for authentication response
            auth_success = False
            try:
                async with asyncio.timeout(15.0):
                    async for msg in ws_assistant.iter_messages():
                        data = msg.data
                        self.logger.info(f"WebSocket response: {data}")
                        
                        if data.get("type") == "AUTHENTICATED":
                            self.print_result("WebSocket Authentication", True, "Successfully authenticated")
                            auth_success = True
                            break
                        elif data.get("type") == "AUTH_FAILED":
                            error_msg = data.get('message', 'Unknown error')
                            self.print_result("WebSocket Authentication", False, f"Auth failed: {error_msg}")
                            break
                        else:
                            # Log other message types
                            print(f"    Received: {data.get('type', 'Unknown type')}")
                            
            except asyncio.TimeoutError:
                self.print_result("WebSocket Authentication", False, "Authentication timeout - no response received")
            
            await ws_assistant.disconnect()
            
            return auth_success
            
        except Exception as e:
            self.print_result("WebSocket Authentication", False, f"Error: {str(e)}")
            return False
    
    async def test_auth_signature(self) -> bool:
        """Test the authentication signature generation."""
        self.print_header("AUTHENTICATION SIGNATURE TEST")
        
        try:
            # Test signature generation
            timestamp = int(time.time() * 1000)
            path = "/v1/account/balances"
            method = "GET"
            body = ""
            
            signature = self.auth._generate_signature(timestamp, method, path, body)
            
            self.print_result("Signature Generation", True, f"Generated signature: {signature[:20]}...")
            
            # Test auth dict generation
            auth_dict = self.auth.generate_auth_dict(path, method)
            expected_keys = ["X-VALR-API-KEY", "X-VALR-SIGNATURE", "X-VALR-TIMESTAMP"]
            
            has_all_keys = all(key in auth_dict for key in expected_keys)
            self.print_result("Auth Headers Generation", has_all_keys, 
                            f"Headers: {list(auth_dict.keys())}")
            
            # Test WebSocket auth payload
            ws_payload = self.auth.get_ws_auth_payload("/ws/account")
            expected_ws_keys = ["type", "apiKey", "timestamp", "signature"]
            
            has_all_ws_keys = all(key in ws_payload for key in expected_ws_keys)
            self.print_result("WebSocket Auth Payload", has_all_ws_keys,
                            f"Payload keys: {list(ws_payload.keys())}")
            
            if has_all_ws_keys:
                print(f"    Auth type: {ws_payload.get('type')}")
                print(f"    API Key: {ws_payload.get('apiKey')[:10]}...")
                print(f"    Timestamp: {ws_payload.get('timestamp')}")
                print(f"    Signature: {ws_payload.get('signature')[:20]}...")
            
            return has_all_keys and has_all_ws_keys
            
        except Exception as e:
            self.print_result("Authentication Signature Test", False, f"Error: {str(e)}")
            return False
    
    async def run_all_tests(self) -> Dict[str, bool]:
        """Run all diagnostic tests."""
        print("VALR Connector Diagnostic Suite")
        print("=" * 60)
        print(f"API Key: {self.api_key[:10]}...")
        print(f"Testing Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        results = {}
        
        # Run tests in order
        results['rest_connectivity'] = await self.test_rest_connectivity()
        results['authentication'] = await self.test_authentication()
        results['trading_pairs'] = await self.test_trading_pairs()
        results['auth_signature'] = await self.test_auth_signature()
        results['websocket_connection'] = await self.test_websocket_connection()
        results['websocket_authentication'] = await self.test_websocket_authentication()
        
        # Print summary
        self.print_header("DIAGNOSTIC SUMMARY")
        passed = sum(1 for success in results.values() if success)
        total = len(results)
        
        print(f"Tests Passed: {passed}/{total}")
        
        for test_name, success in results.items():
            status = "✅ PASS" if success else "❌ FAIL"
            print(f"{status} {test_name.replace('_', ' ').title()}")
        
        if passed == total:
            print("\n🎉 All tests passed! VALR connector should work correctly.")
        else:
            print(f"\n⚠️  {total - passed} test(s) failed. Review the issues above.")
            
        return results


async def main():
    """Main diagnostic function."""
    # Test credentials
    api_key = "7a66934ebab38005a97641271a99ffaf95a01f85d3c70272673cb83713a70308"
    api_secret = "ab356d3d77b0f4b9d7c6ffa7399a073782fd7afbcb0d2c830bf868f6eeb8dde5"
    
    diagnostics = ValrDiagnostics(api_key, api_secret)
    results = await diagnostics.run_all_tests()
    
    # Exit with appropriate code
    if all(results.values()):
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())