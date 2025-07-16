#!/usr/bin/env python3
"""
Test different signature generation methods for VALR WebSocket authentication.
"""

import asyncio
import hashlib
import hmac
import json
import sys
import time
from typing import Dict, Any

# Add the hummingbot directory to the path
sys.path.insert(0, '/home/mailr/hummingbot-private/hummingbot')

from hummingbot.connector.exchange.valr.valr_auth import ValrAuth
from hummingbot.connector.exchange.valr import valr_constants as CONSTANTS
from hummingbot.connector.exchange.valr import valr_web_utils as web_utils
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory
from hummingbot.core.web_assistant.connections.data_types import WSJSONRequest
from hummingbot.core.api_throttler.async_throttler import AsyncThrottler


async def test_signature_methods():
    """Test different signature generation methods for WebSocket authentication."""
    api_key = "7a66934ebab38005a97641271a99ffaf95a01f85d3c70272673cb83713a70308"
    api_secret = "ab356d3d77b0f4b9d7c6ffa7399a073782fd7afbcb0d2c830bf868f6eeb8dde5"
    
    auth = ValrAuth(api_key, api_secret)
    throttler = AsyncThrottler(CONSTANTS.RATE_LIMITS)
    web_assistants_factory = web_utils.build_api_factory(
        throttler=throttler,
        auth=auth
    )
    
    timestamp = int(time.time() * 1000)
    print(f"Using timestamp: {timestamp}")
    
    # Test different signature methods
    signature_methods = [
        # Method 1: Current implementation
        ("Current: GET /ws/account", lambda: auth._generate_signature(timestamp, "GET", "/ws/account", "")),
        
        # Method 2: Try with different paths
        ("Different path: GET /account", lambda: auth._generate_signature(timestamp, "GET", "/account", "")),
        ("Different path: GET ws/account", lambda: auth._generate_signature(timestamp, "GET", "ws/account", "")),
        ("Different path: GET account", lambda: auth._generate_signature(timestamp, "GET", "account", "")),
        
        # Method 3: Try with different verbs
        ("Different verb: POST /ws/account", lambda: auth._generate_signature(timestamp, "POST", "/ws/account", "")),
        ("Different verb: WS /ws/account", lambda: auth._generate_signature(timestamp, "WS", "/ws/account", "")),
        
        # Method 4: Try with body (JSON string)
        ("With body: GET /ws/account", lambda: auth._generate_signature(timestamp, "GET", "/ws/account", json.dumps({"apiKey": api_key, "timestamp": timestamp}))),
        
        # Method 5: Try without timestamp in payload
        ("No timestamp: GET /ws/account", lambda: auth._generate_signature(int(time.time()), "GET", "/ws/account", "")),
        
        # Method 6: Try with different timestamp formats
        ("Timestamp seconds: GET /ws/account", lambda: auth._generate_signature(int(time.time()), "GET", "/ws/account", "")),
        
        # Method 7: Manual signature with different components
        ("Manual method 1", lambda: hmac.new(
            api_secret.encode('utf-8'),
            f"{timestamp}GET/ws/account".encode('utf-8'),
            hashlib.sha512
        ).hexdigest()),
        
        # Method 8: Try to sign the entire payload
        ("Sign entire payload", lambda: hmac.new(
            api_secret.encode('utf-8'),
            json.dumps({"type": "AUTHENTICATE", "apiKey": api_key, "timestamp": timestamp}).encode('utf-8'),
            hashlib.sha512
        ).hexdigest()),
        
        # Method 9: Try different HMAC algorithms
        ("SHA256 instead of SHA512", lambda: hmac.new(
            api_secret.encode('utf-8'),
            f"{timestamp}GET/ws/account".encode('utf-8'),
            hashlib.sha256
        ).hexdigest()),
    ]
    
    print("\n=== SIGNATURE GENERATION TESTS ===")
    for name, method in signature_methods:
        try:
            signature = method()
            print(f"{name}: {signature}")
        except Exception as e:
            print(f"{name}: ERROR - {e}")
    
    print("\n=== TESTING SIGNATURES WITH WEBSOCKET ===")
    
    # Test a few promising signatures
    test_signatures = [
        ("Current implementation", auth._generate_signature(timestamp, "GET", "/ws/account", "")),
        ("Manual method", hmac.new(
            api_secret.encode('utf-8'),
            f"{timestamp}GET/ws/account".encode('utf-8'),
            hashlib.sha512
        ).hexdigest()),
        ("Sign entire payload", hmac.new(
            api_secret.encode('utf-8'),
            json.dumps({"type": "AUTHENTICATE", "apiKey": api_key, "timestamp": timestamp}).encode('utf-8'),
            hashlib.sha512
        ).hexdigest()),
    ]
    
    for name, signature in test_signatures:
        print(f"\n--- Testing {name} ---")
        success = await test_websocket_with_signature(web_assistants_factory, api_key, timestamp, signature)
        if success:
            print(f"✅ {name} worked!")
            break
        else:
            print(f"❌ {name} failed")
    
    # Additionally, try a few different approaches to the auth payload structure
    print("\n=== TESTING DIFFERENT AUTH PAYLOAD STRUCTURES ===")
    
    # Test method inspired by other exchanges
    alternative_payloads = [
        # Method 1: Just the essentials
        {
            "type": "AUTHENTICATE",
            "apiKey": api_key,
            "signature": auth._generate_signature(timestamp, "GET", "/ws/account", ""),
            "timestamp": timestamp
        },
        
        # Method 2: Different field order
        {
            "apiKey": api_key,
            "signature": auth._generate_signature(timestamp, "GET", "/ws/account", ""),
            "timestamp": timestamp,
            "type": "AUTHENTICATE"
        },
        
        # Method 3: Include the method and path
        {
            "type": "AUTHENTICATE",
            "apiKey": api_key,
            "signature": auth._generate_signature(timestamp, "GET", "/ws/account", ""),
            "timestamp": timestamp,
            "method": "GET",
            "path": "/ws/account"
        },
        
        # Method 4: Different timestamp format
        {
            "type": "AUTHENTICATE",
            "apiKey": api_key,
            "signature": auth._generate_signature(int(time.time()), "GET", "/ws/account", ""),
            "timestamp": int(time.time())  # seconds instead of milliseconds
        },
    ]
    
    for i, payload in enumerate(alternative_payloads):
        print(f"\n--- Testing alternative payload {i+1} ---")
        success = await test_websocket_with_payload(web_assistants_factory, payload)
        if success:
            print(f"✅ Alternative payload {i+1} worked!")
            break
        else:
            print(f"❌ Alternative payload {i+1} failed")


async def test_websocket_with_signature(web_assistants_factory, api_key: str, timestamp: int, signature: str) -> bool:
    """Test WebSocket authentication with a specific signature."""
    try:
        ws_assistant = await web_assistants_factory.get_ws_assistant()
        
        await ws_assistant.connect(
            ws_url=CONSTANTS.WSS_ACCOUNT_URL,
            ping_timeout=10.0
        )
        
        payload = {
            "type": "AUTHENTICATE",
            "apiKey": api_key,
            "timestamp": timestamp,
            "signature": signature
        }
        
        auth_request = WSJSONRequest(payload)
        await ws_assistant.send(auth_request)
        
        # Wait for response
        try:
            async with asyncio.timeout(5.0):
                async for msg in ws_assistant.iter_messages():
                    data = msg.data
                    if data.get("type") == "AUTHENTICATED":
                        await ws_assistant.disconnect()
                        return True
                    elif data.get("type") in ["AUTH_FAILED", "UNAUTHORIZED"]:
                        await ws_assistant.disconnect()
                        return False
        except asyncio.TimeoutError:
            pass
        
        await ws_assistant.disconnect()
        return False
        
    except Exception as e:
        print(f"    Error: {e}")
        return False


async def test_websocket_with_payload(web_assistants_factory, payload: Dict[str, Any]) -> bool:
    """Test WebSocket authentication with a specific payload."""
    try:
        ws_assistant = await web_assistants_factory.get_ws_assistant()
        
        await ws_assistant.connect(
            ws_url=CONSTANTS.WSS_ACCOUNT_URL,
            ping_timeout=10.0
        )
        
        auth_request = WSJSONRequest(payload)
        await ws_assistant.send(auth_request)
        
        # Wait for response
        try:
            async with asyncio.timeout(5.0):
                async for msg in ws_assistant.iter_messages():
                    data = msg.data
                    print(f"    Response: {data}")
                    if data.get("type") == "AUTHENTICATED":
                        await ws_assistant.disconnect()
                        return True
                    elif data.get("type") in ["AUTH_FAILED", "UNAUTHORIZED"]:
                        await ws_assistant.disconnect()
                        return False
        except asyncio.TimeoutError:
            print("    Timeout waiting for response")
        
        await ws_assistant.disconnect()
        return False
        
    except Exception as e:
        print(f"    Error: {e}")
        return False


if __name__ == "__main__":
    asyncio.run(test_signature_methods())