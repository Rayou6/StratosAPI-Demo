from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from diplomacy_llm.phase_snapshot import PowerPhaseSnapshot


@dataclass(frozen=True)
class StrategyIdentity:
    """Stable metadata used to identify a strategy in run artifacts."""

    name: str
    version: str


@dataclass(frozen=True)
class StrategyContext:
    """
    Structured strategic guidance a strategy may add to an order prompt.

    Strategies are allowed to produce only this decision context. They do not
    produce final orders and must not own validation, healing, fallback, or LLM
    transport behavior.
    """

    doctrine: str
    phase_objective: str
    priorities: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    risk_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class StrategyRuntimeContext:
    """Typed runtime inputs available to one strategy protocol render pass."""

    snapshot: PowerPhaseSnapshot
