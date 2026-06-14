from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from diplomacy_llm.strategies.protocols import get_strategy_protocol

StrategyResolutionSource = Literal["default", "by_power", "by_model"]


class StrategyAssignmentConfig(Protocol):
    """Config shape needed by the strategy resolver."""

    default: str
    by_power: Mapping[str, str]
    by_model: Mapping[str, str]


@dataclass(frozen=True)
class StrategyResolution:
    """Resolved strategy identity and provenance for one power."""

    strategy_name: str
    strategy_version: str
    source: StrategyResolutionSource
    matched_model: str | None = None


def resolve_power_strategies(
    effective_power_models: Mapping[str, str],
    assignment: StrategyAssignmentConfig,
) -> dict[str, StrategyResolution]:
    """
    Resolve one strategy per power from the effective model assignment.

    This must run after model shuffling so by-model strategy rules follow the
    model that actually controls each power.
    """
    return {
        power: _resolve_power_strategy(power, model, assignment)
        for power, model in effective_power_models.items()
    }


def strategy_resolution_payload(
    effective_strategies: Mapping[str, StrategyResolution],
) -> dict[str, dict[str, object]]:
    """Return JSON-safe per-power strategy resolution metadata."""
    return {
        power: {
            "name": resolution.strategy_name,
            "version": resolution.strategy_version,
            "source": resolution.source,
            "matched_model": resolution.matched_model,
        }
        for power, resolution in effective_strategies.items()
    }


def _resolve_power_strategy(
    power: str,
    effective_model: str,
    assignment: StrategyAssignmentConfig,
) -> StrategyResolution:
    """Resolve the strategy for one already-assigned model."""
    if power in assignment.by_power:
        return _strategy_resolution(
            assignment.by_power[power],
            source="by_power",
            matched_model=None,
        )

    if effective_model in assignment.by_model:
        return _strategy_resolution(
            assignment.by_model[effective_model],
            source="by_model",
            matched_model=effective_model,
        )

    return _strategy_resolution(
        assignment.default,
        source="default",
        matched_model=None,
    )


def _strategy_resolution(
    strategy_name: str,
    *,
    source: StrategyResolutionSource,
    matched_model: str | None,
) -> StrategyResolution:
    """Validate a strategy name and build its resolution record."""
    identity = get_strategy_protocol(strategy_name).identity
    return StrategyResolution(
        strategy_name=identity.name,
        strategy_version=identity.version,
        source=source,
        matched_model=matched_model,
    )
