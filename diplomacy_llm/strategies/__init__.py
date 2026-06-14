from diplomacy_llm.strategies.models import (
    StrategyContext,
    StrategyIdentity,
    StrategyRuntimeContext,
)
from diplomacy_llm.strategies.prompts import render_strategy_context
from diplomacy_llm.strategies.protocols import (
    BaselineStrategy,
    BaseStrategyProtocol,
    available_strategy_protocol_names,
    get_strategy_protocol,
)
from diplomacy_llm.strategies.resolver import (
    StrategyResolution,
    StrategyResolutionSource,
    resolve_power_strategies,
    strategy_resolution_payload,
)

BaseOrderStrategy = BaseStrategyProtocol
available_strategy_names = available_strategy_protocol_names
get_strategy = get_strategy_protocol

__all__ = [
    "BaseOrderStrategy",
    "BaseStrategyProtocol",
    "BaselineStrategy",
    "StrategyContext",
    "StrategyIdentity",
    "StrategyResolution",
    "StrategyResolutionSource",
    "StrategyRuntimeContext",
    "available_strategy_names",
    "available_strategy_protocol_names",
    "get_strategy",
    "get_strategy_protocol",
    "render_strategy_context",
    "resolve_power_strategies",
    "strategy_resolution_payload",
]
