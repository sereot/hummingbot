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

# VALR doesn't have other domains/sub-exchanges
OTHER_DOMAINS = []
OTHER_DOMAINS_PARAMETER = {}
OTHER_DOMAINS_EXAMPLE_PAIR = {}
OTHER_DOMAINS_DEFAULT_FEES = {}
OTHER_DOMAINS_KEYS = {}