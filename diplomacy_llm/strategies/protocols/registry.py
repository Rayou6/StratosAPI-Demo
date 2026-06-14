from __future__ import annotations

from typing import TYPE_CHECKING

from diplomacy_llm.strategies.protocols.baseline import BaselineStrategy

if TYPE_CHECKING:
    from diplomacy_llm.strategies.protocols.base import BaseStrategyProtocol

_STRATEGY_PROTOCOL_CLASSES: dict[str, type[BaseStrategyProtocol]] = {
    BaselineStrategy.name: BaselineStrategy,
}


def available_strategy_protocol_names() -> tuple[str, ...]:
    """Return registered strategy protocol names in deterministic order."""
    return tuple(sorted(_STRATEGY_PROTOCOL_CLASSES))


def get_strategy_protocol(name: str) -> BaseStrategyProtocol:
    """Instantiate a registered strategy protocol by canonical name."""
    try:
        strategy_class: type[BaseStrategyProtocol] = _STRATEGY_PROTOCOL_CLASSES[name]
    except KeyError as exc:
        available: str = ", ".join(available_strategy_protocol_names())
        msg = f"Unknown strategy '{name}'. Available strategies: {available}"
        raise ValueError(msg) from exc
    return strategy_class()
