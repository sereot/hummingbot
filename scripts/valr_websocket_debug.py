#!/usr/bin/env python3
"""
VALR WebSocket Authentication Debug Script
=========================================

This script performs detailed testing of VALR WebSocket authentication
to understand why authentication is failing.
"""

import asyncio
import json
import logging
import sys
import time
import hashlib
import hmac
from typing import Dict, Any, Optional

# Add the hummingbot directory to the path
sys.path.insert(0, '/home/mailr/hummingbot-private/hummingbot')

from hummingbot.connector.exchange.valr.valr_auth import ValrAuth
from hummingbot.connector.exchange.valr import valr_constants as CONSTANTS
from hummingbot.connector.exchange.valr import valr_web_utils as web_utils
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory
from hummingbot.core.web_assistant.connections.data_types import WSJSONRequest
from hummingbot.core.api_throttler.async_throttler import AsyncThrottler


class ValrWebSocketDebug:
    """Detailed WebSocket authentication debugging."""
    
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
            level=logging.DEBUG,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)
    
    def test_signature_generation(self, timestamp: int, path: str = "/ws/account") -> str:
        """Test signature generation with different methods."""
        print(f"\n=== SIGNATURE GENERATION TEST ===")
        print(f"Timestamp: {timestamp}")
        print(f"Path: {path}")
        print(f"API Key: {self.api_key}")
        print(f"API Secret: {self.api_secret[:10]}...")
        
        # Method 1: Using the auth class
        signature1 = self.auth._generate_signature(timestamp, "GET", path, "")
        print(f"Method 1 (auth class): {signature1}")
        
        # Method 2: Manual generation
        payload_to_sign = f"{timestamp}GET{path}"
        signature2 = hmac.new(
            self.api_secret.encode('utf-8'),
            payload_to_sign.encode('utf-8'),
            hashlib.sha512
        ).hexdigest()
        print(f"Method 2 (manual): {signature2}")
        
        # Method 3: Try different path variations
        for test_path in ["/ws/account", "ws/account", "/account", "account"]:
            test_payload = f"{timestamp}GET{test_path}"
            test_signature = hmac.new(
                self.api_secret.encode('utf-8'),
                test_payload.encode('utf-8'),
                hashlib.sha512
            ).hexdigest()
            print(f"Path '{test_path}': {test_signature}")
        
        return signature1
    
    def test_different_auth_payloads(self, timestamp: int) -> list:
        """Test different authentication payload formats."""
        print(f"\n=== AUTHENTICATION PAYLOAD VARIATIONS ===")
        
        payloads = []
        
        # Original payload
        payload1 = {
            "type": "AUTHENTICATE", 
            "apiKey": self.api_key,
            "timestamp": timestamp,
            "signature": self.auth._generate_signature(timestamp, "GET", "/ws/account", "")
        }
        payloads.append(("Original", payload1))
        
        # Try with different field names
        payload2 = {
            "type": "AUTHENTICATE", 
            "api_key": self.api_key,
            "timestamp": timestamp,
            "signature": self.auth._generate_signature(timestamp, "GET", "/ws/account", "")
        }
        payloads.append(("snake_case api_key", payload2))
        
        # Try with string timestamp
        payload3 = {
            "type": "AUTHENTICATE", 
            "apiKey": self.api_key,
            "timestamp": str(timestamp),
            "signature": self.auth._generate_signature(timestamp, "GET", "/ws/account", "")
        }
        payloads.append(("String timestamp", payload3))
        
        # Try with different type
        payload4 = {
            "type": "AUTH", 
            "apiKey": self.api_key,
            "timestamp": timestamp,
            "signature": self.auth._generate_signature(timestamp, "GET", "/ws/account", "")
        }
        payloads.append(("AUTH type", payload4))
        
        # Try with additional fields
        payload5 = {
            "type": "AUTHENTICATE", 
            "apiKey": self.api_key,
            "timestamp": timestamp,
            "signature": self.auth._generate_signature(timestamp, "GET", "/ws/account", ""),
            "method": "GET",
            "path": "/ws/account"
        }
        payloads.append(("With method and path", payload5))
        
        for name, payload in payloads:
            print(f"{name}: {json.dumps(payload, indent=2)}")
        
        return payloads
    
    async def test_websocket_auth_variations(self):
        """Test WebSocket authentication with different payload variations."""
        print(f"\n=== WEBSOCKET AUTHENTICATION VARIATION TEST ===")
        
        timestamp = int(time.time() * 1000)
        
        # Test signature generation
        self.test_signature_generation(timestamp)
        
        # Test different payload formats
        payloads = self.test_different_auth_payloads(timestamp)
        
        # Test each payload
        for name, payload in payloads:
            print(f"\n--- Testing {name} ---")
            success = await self.test_single_auth_payload(payload, name)
            if success:
                print(f"✅ {name} worked!")
                return True
            else:
                print(f"❌ {name} failed")
        
        return False
    
    async def test_single_auth_payload(self, payload: Dict[str, Any], name: str) -> bool:
        """Test a single authentication payload."""
        try:
            ws_assistant = await self.web_assistants_factory.get_ws_assistant()
            
            # Connect to account WebSocket
            await ws_assistant.connect(
                ws_url=CONSTANTS.WSS_ACCOUNT_URL,
                ping_timeout=10.0
            )
            
            # Send authentication message
            auth_request = WSJSONRequest(payload)
            await ws_assistant.send(auth_request)
            
            # Wait for response
            auth_success = False
            try:
                async with asyncio.timeout(10.0):
                    async for msg in ws_assistant.iter_messages():
                        data = msg.data
                        print(f"    Response: {data}")
                        
                        if data.get("type") == "AUTHENTICATED":
                            auth_success = True
                            break
                        elif data.get("type") == "AUTH_FAILED":
                            print(f"    Auth failed: {data.get('message', 'Unknown error')}")
                            break
                        elif data.get("type") == "UNAUTHORIZED":
                            print(f"    Unauthorized response")
                            break
                        else:
                            print(f"    Unknown response type: {data.get('type')}")
                            
            except asyncio.TimeoutError:
                print(f"    Timeout waiting for response")
            
            await ws_assistant.disconnect()
            return auth_success
            
        except Exception as e:
            print(f"    Error: {e}")
            return False
    
    async def test_manual_websocket_connection(self):
        """Test manual WebSocket connection without using the factory."""
        print(f"\n=== MANUAL WEBSOCKET CONNECTION TEST ===")
        
        try:
            import websockets
            import ssl
            
            # Create SSL context (if needed)
            ssl_context = ssl.create_default_context()
            
            # Connect directly
            async with websockets.connect(
                CONSTANTS.WSS_ACCOUNT_URL,
                ssl=ssl_context,
                ping_timeout=10
            ) as websocket:
                print("✅ Connected to WebSocket directly")
                
                # Test authentication
                timestamp = int(time.time() * 1000)
                payload = {
                    "type": "AUTHENTICATE", 
                    "apiKey": self.api_key,
                    "timestamp": timestamp,
                    "signature": self.auth._generate_signature(timestamp, "GET", "/ws/account", "")
                }
                
                print(f"Sending: {json.dumps(payload, indent=2)}")
                await websocket.send(json.dumps(payload))
                
                # Wait for response
                try:
                    response = await asyncio.wait_for(websocket.recv(), timeout=10.0)
                    data = json.loads(response)
                    print(f"Response: {data}")
                    
                    if data.get("type") == "AUTHENTICATED":
                        print("✅ Authentication successful!")
                        return True
                    else:
                        print(f"❌ Authentication failed: {data}")
                        return False
                        
                except asyncio.TimeoutError:
                    print("❌ Timeout waiting for response")
                    return False
                    
        except ImportError:
            print("⚠️  websockets library not available, skipping manual test")
            return False
        except Exception as e:
            print(f"❌ Manual connection failed: {e}")
            return False
    
    async def run_debug_tests(self):
        """Run all debug tests."""
        print("VALR WebSocket Authentication Debug Suite")
        print("=" * 60)
        
        # Test with variations
        success = await self.test_websocket_auth_variations()
        
        if not success:
            print("\n=== All auth variations failed, trying manual connection ===")
            await self.test_manual_websocket_connection()
        
        return success


async def main():
    """Main debug function."""
    api_key = "7a66934ebab38005a97641271a99ffaf95a01f85d3c70272673cb83713a70308"
    api_secret = "ab356d3d77b0f4b9d7c6ffa7399a073782fd7afbcb0d2c830bf868f6eeb8dde5"
    
    debug_tool = ValrWebSocketDebug(api_key, api_secret)
    await debug_tool.run_debug_tests()


if __name__ == "__main__":
    asyncio.run(main())