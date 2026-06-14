from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from diplomacy_llm.strategies.models import StrategyContext


def render_strategy_context(context: StrategyContext) -> str:
    """Render a strategy context using the fixed prompt-facing schema."""
    return "\n".join(
        [
            f"Doctrine: {context.doctrine}",
            f"Phase objective: {context.phase_objective}",
            _render_list("Priorities", context.priorities),
            _render_list("Constraints", context.constraints),
            _render_list("Risk notes", context.risk_notes),
        ],
    )


def _render_list(label: str, values: tuple[str, ...]) -> str:
    """Render a tuple of strategy context entries as stable bullet lines."""
    if not values:
        return f"{label}:\n  - (none)"

    bullets: str = "\n".join(f"  - {value}" for value in values)
    return f"{label}:\n{bullets}"
