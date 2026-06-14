from diplomacy_llm.messaging.protocols.base import BaseMessagingProtocol
from diplomacy_llm.messaging.protocols.latency_pairwise_private import (
    LatencyPairwisePrivateMessagingProtocol,
)

_LATENCY_PAIRWISE_ALIASES = {
    "",
    "none",
    "disabled",
    "press_disabled",
    "latency_pairwise_private",
    "pairwise_latency_private",
    "latency_pairwise",
}


def available_messaging_protocol_names() -> tuple[str, ...]:
    """Return registered messaging protocol names in deterministic order."""
    return ("latency_pairwise_private",)


def get_messaging_protocol(name: str | None) -> BaseMessagingProtocol:
    """Instantiate a registered messaging protocol by name."""
    normalized = "" if name is None else name.strip().lower()
    if normalized in _LATENCY_PAIRWISE_ALIASES:
        return LatencyPairwisePrivateMessagingProtocol()

    available = ", ".join(available_messaging_protocol_names())
    msg = f"Unknown messaging protocol '{name}'. Available protocols: {available}"
    raise ValueError(msg)
