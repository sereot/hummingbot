import hashlib
import hmac
import time
from typing import Dict, Any, Optional
from urllib.parse import urlencode

from hummingbot.connector.exchange.valr import valr_constants as CONSTANTS
from hummingbot.core.web_assistant.auth import AuthBase
from hummingbot.core.web_assistant.connections.data_types import RESTRequest, WSRequest


class ValrAuth(AuthBase):
    """
    Authentication class for VALR exchange.
    VALR uses HMAC-SHA512 signing for API requests.
    """

    def __init__(self, api_key: str, api_secret: str):
        self._api_key = api_key
        self._api_secret = api_secret

    @property
    def api_key(self) -> str:
        return self._api_key

    @property
    def api_secret(self) -> str:
        return self._api_secret

    def _generate_signature(
        self,
        timestamp: int,
        verb: str,
        path: str,
        body: str = ""
    ) -> str:
        """
        Generate HMAC-SHA512 signature for VALR API requests.
        
        Args:
            timestamp: Unix timestamp in milliseconds
            verb: HTTP method (GET, POST, DELETE, etc.)
            path: API endpoint path including query parameters
            body: Request body as string (empty string for GET requests)
            
        Returns:
            Base64 encoded signature
        """
        # Create the payload to sign
        payload_to_sign = f"{timestamp}{verb.upper()}{path}{body}"
        
        # Generate HMAC-SHA512 signature
        signature = hmac.new(
            self._api_secret.encode('utf-8'),
            payload_to_sign.encode('utf-8'),
            hashlib.sha512
        ).hexdigest()
        
        return signature

    async def rest_authenticate(self, request: RESTRequest) -> RESTRequest:
        """
        Adds authentication headers to REST API requests.
        
        Args:
            request: The request to authenticate
            
        Returns:
            The authenticated request
        """
        headers = {}
        if request.headers is not None:
            headers.update(request.headers)

        # Generate timestamp
        timestamp = int(time.time() * 1000)
        
        # Extract path and query parameters
        path = request.url.split(CONSTANTS.REST_URL)[-1]
        
        # Get HTTP verb
        verb = request.method.value
        
        # Get body (empty string for GET requests)
        body = ""
        if request.data is not None:
            if isinstance(request.data, dict):
                import json
                body = json.dumps(request.data, separators=(',', ':'))
            else:
                body = str(request.data)
        
        # Generate signature
        signature = self._generate_signature(timestamp, verb, path, body)
        
        # Add authentication headers
        headers["X-VALR-API-KEY"] = self._api_key
        headers["X-VALR-SIGNATURE"] = signature
        headers["X-VALR-TIMESTAMP"] = str(timestamp)
        
        # Add content type for POST/PUT requests
        if verb in ["POST", "PUT", "DELETE"] and body:
            headers["Content-Type"] = "application/json"
        
        request.headers = headers
        return request

    async def ws_authenticate(self, request: WSRequest) -> WSRequest:
        """
        Adds authentication headers to WebSocket connection requests.
        
        Args:
            request: The WebSocket request to authenticate
            
        Returns:
            The authenticated request
        """
        headers = {}
        if request.headers is not None:
            headers.update(request.headers)
        
        # WebSocket authentication uses the same headers as REST
        timestamp = int(time.time() * 1000)
        
        # For WebSocket, we authenticate the connection itself
        # VALR WebSocket auth is done through the connection URL with API key
        # or through initial message after connection
        headers["X-VALR-API-KEY"] = self._api_key
        
        request.headers = headers
        return request

    def get_ws_auth_payload(self) -> Dict[str, Any]:
        """
        Gets the authentication payload for WebSocket initial message.
        VALR requires sending API credentials after connection.
        
        Returns:
            Dictionary with authentication data
        """
        timestamp = int(time.time() * 1000)
        
        return {
            "type": "AUTHENTICATE",
            "apiKey": self._api_key,
            "timestamp": timestamp
        }

    def generate_auth_dict(
        self,
        path: str,
        method: str,
        body: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Generates authentication headers dict for direct use.
        
        Args:
            path: API endpoint path
            method: HTTP method
            body: Request body as dictionary
            
        Returns:
            Dictionary with authentication headers
        """
        timestamp = int(time.time() * 1000)
        
        body_str = ""
        if body is not None:
            import json
            body_str = json.dumps(body, separators=(',', ':'))
        
        signature = self._generate_signature(timestamp, method, path, body_str)
        
        return {
            "X-VALR-API-KEY": self._api_key,
            "X-VALR-SIGNATURE": signature,
            "X-VALR-TIMESTAMP": str(timestamp)
        }