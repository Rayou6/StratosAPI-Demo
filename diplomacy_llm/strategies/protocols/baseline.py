from __future__ import annotations

from typing import TYPE_CHECKING

from diplomacy_llm.strategies.protocols.base import BaseStrategyProtocol

if TYPE_CHECKING:
    from diplomacy_llm.phase_snapshot import PowerPhaseSnapshot
    from diplomacy_llm.strategies.models import StrategyContext


class BaselineStrategy(BaseStrategyProtocol):
    """
    Preserve the current baseline behavior.

    The baseline strategy intentionally contributes no additional strategy
    context so the existing baseline prompt can remain byte-for-byte stable
    once the strategy layer is wired into the player runtime.
    """

    name = "baseline"
    version = "v1"

    def build_context(self, snapshot: PowerPhaseSnapshot) -> StrategyContext | None:
        """Return no strategy context for baseline behavior."""
        _ = snapshot
        return None
