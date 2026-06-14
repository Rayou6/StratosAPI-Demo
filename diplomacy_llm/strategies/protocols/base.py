from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

from diplomacy_llm.strategies.models import (
    StrategyContext,
    StrategyIdentity,
    StrategyRuntimeContext,
)
from diplomacy_llm.strategies.prompts import render_strategy_context

if TYPE_CHECKING:
    from diplomacy_llm.phase_snapshot import PowerPhaseSnapshot


class BaseStrategyProtocol(ABC):
    """
    Base class for per-power strategy protocols.

    The shared player runtime owns LLM calls, schema enforcement, validation,
    healing, fallback, and metrics. Strategy protocols own only strategy-specific
    prompt context and future strategy-specific response conventions.
    """

    name: ClassVar[str]
    version: ClassVar[str]

    @property
    def identity(self) -> StrategyIdentity:
        """Return the stable strategy identity."""
        return StrategyIdentity(name=self.name, version=self.version)

    @abstractmethod
    def build_context(self, snapshot: PowerPhaseSnapshot) -> StrategyContext | None:
        """Return optional strategy context for one phase snapshot."""

    def render_prompt_section(
        self,
        runtime_context: StrategyRuntimeContext,
    ) -> str | None:
        """Return the prompt-facing strategy section for one model decision."""
        context = self.build_context(runtime_context.snapshot)
        if context is None:
            return None
        return render_strategy_context(context)
