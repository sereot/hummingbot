from decimal import Decimal
from typing import Any, Dict

from pydantic import ConfigDict, Field, SecretStr

from hummingbot.client.config.config_data_types import BaseConnectorConfigMap
from hummingbot.core.data_type.trade_fee import TradeFeeSchema

CENTRALIZED = True
EXAMPLE_PAIR = "BTC-ZAR"

DEFAULT_FEES = TradeFeeSchema(
    maker_percent_fee_decimal=Decimal("0.0005"),  # 0.05%
    taker_percent_fee_decimal=Decimal("0.001"),   # 0.1%
    buy_percent_fee_deducted_from_returns=True
)


def is_exchange_information_valid(exchange_info: Dict[str, Any]) -> bool:
    """
    Verifies if exchange information is valid.
    VALR pairs have an 'active' field to indicate if trading is enabled.
    
    Args:
        exchange_info: The exchange information for a trading pair
        
    Returns:
        True if the trading pair is enabled, False otherwise
    """
    return exchange_info.get("active", False) is True


def is_pair_information_valid(pair_info: Dict[str, Any]) -> bool:
    """
    Verifies if a trading pair is valid for spot trading.
    
    Args:
        pair_info: The pair information from VALR API
        
    Returns:
        True if the pair is valid for spot trading
    """
    # Check if pair is active
    if not pair_info.get("active", False):
        return False
    
    # Check if it's a spot pair (not futures/perp)
    # VALR spot pairs don't have special markers, but futures have "_PERP" suffix
    symbol = pair_info.get("symbol", "")
    if "_PERP" in symbol or "_FUTURES" in symbol:
        return False
    
    return True


class ValrConfigMap(BaseConnectorConfigMap):
    connector: str = "valr"
    valr_api_key: SecretStr = Field(
        default=...,
        json_schema_extra={
            "prompt": lambda cm: "Enter your VALR API key",
            "is_secure": True,
            "is_connect_key": True,
            "prompt_on_new": True,
        }
    )
    valr_api_secret: SecretStr = Field(
        default=...,
        json_schema_extra={
            "prompt": lambda cm: "Enter your VALR API secret", 
            "is_secure": True,
            "is_connect_key": True,
            "prompt_on_new": True,
        }
    )
    model_config = ConfigDict(title="valr")


KEYS = ValrConfigMap.model_construct()

# VALR HFT domain configuration
OTHER_DOMAINS = ["valr_hft"]
OTHER_DOMAINS_PARAMETER = {"valr_hft": "valr_hft"}
OTHER_DOMAINS_EXAMPLE_PAIR = {"valr_hft": "BTC-ZAR"}
OTHER_DOMAINS_DEFAULT_FEES = {"valr_hft": DEFAULT_FEES}


class ValrHFTConfigMap(BaseConnectorConfigMap):
    """Configuration for VALR HFT connector variant."""
    connector: str = "valr_hft"
    valr_api_key: SecretStr = Field(
        default=...,
        json_schema_extra={
            "prompt": lambda cm: "Enter your VALR API key",
            "is_secure": True,
            "is_connect_key": True,
            "prompt_on_new": True,
        }
    )
    valr_api_secret: SecretStr = Field(
        default=...,
        json_schema_extra={
            "prompt": lambda cm: "Enter your VALR API secret", 
            "is_secure": True,
            "is_connect_key": True,
            "prompt_on_new": True,
        }
    )
    # HFT-specific configuration
    use_websocket_order_placement: bool = Field(
        default=True,
        json_schema_extra={
            "prompt": lambda cm: "Enable WebSocket order placement for ultra-low latency? (Yes/No)",
            "prompt_on_new": False,
        }
    )
    order_timeout_ms: int = Field(
        default=100,
        json_schema_extra={
            "prompt": lambda cm: "Order timeout in milliseconds (default: 100)",
            "prompt_on_new": False,
        }
    )
    enable_l1_orderbook_optimization: bool = Field(
        default=True,
        json_schema_extra={
            "prompt": lambda cm: "Enable L1 order book optimization? (Yes/No)",
            "prompt_on_new": False,
        }
    )
    connection_pool_size: int = Field(
        default=3,
        json_schema_extra={
            "prompt": lambda cm: "WebSocket connection pool size (default: 3)",
            "prompt_on_new": False,
        }
    )
    max_batch_orders: int = Field(
        default=10,
        json_schema_extra={
            "prompt": lambda cm: "Maximum orders per batch (default: 10)",
            "prompt_on_new": False,
        }
    )
    circuit_breaker_failure_threshold: int = Field(
        default=5,
        json_schema_extra={
            "prompt": lambda cm: "Circuit breaker failure threshold (default: 5)",
            "prompt_on_new": False,
        }
    )
    adaptive_reconnect_enabled: bool = Field(
        default=True,
        json_schema_extra={
            "prompt": lambda cm: "Enable adaptive reconnection for VALR's 30s disconnects? (Yes/No)",
            "prompt_on_new": False,
        }
    )
    model_config = ConfigDict(title="valr_hft")


OTHER_DOMAINS_KEYS = {"valr_hft": ValrHFTConfigMap.model_construct()}