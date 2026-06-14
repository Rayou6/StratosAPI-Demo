"""
Compatibility facade for order prompt rendering and response schemas.

The concrete baseline prompt template lives in prompt_templates.py, and the
OpenRouter structured response contract lives in response_schemas.py. This
module keeps the original public imports stable for the rest of the project.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from diplomacy_llm.prompt_templates import get_order_prompt_template
from diplomacy_llm.response_schemas import ORDERS_RESPONSE_SCHEMA

if TYPE_CHECKING:
    from diplomacy_llm.config import Settings
    from diplomacy_llm.phase_snapshot import PowerPhaseSnapshot

__all__ = ["ORDERS_RESPONSE_SCHEMA", "get_system_prompt", "get_user_prompt"]


def get_system_prompt(power_name: str, settings: Settings) -> str:
    """
    Build the static system prompt for a given power.

    This prompt covers game rules, unit types, phase types, order formats, and
    the power's identity. The JSON response shape is enforced separately through
    ORDERS_RESPONSE_SCHEMA.
    """
    return get_order_prompt_template(settings.prompt_variant).render_system(
        power_name,
        settings,
    )


def get_user_prompt(
    snapshot: PowerPhaseSnapshot,
    settings: Settings | None = None,
) -> str:
    """
    Build the dynamic user prompt for a power at the current game phase.

    This is rebuilt and sent at every phase from a PowerPhaseSnapshot. It contains
    everything the LLM needs to make a decision: the current phase, its own units
    and SCs, all other powers' visible state, the results of the last phase, and
    critically the exact list of valid orders it must choose from.

    Pass settings when the configured prompt variant matters. Calls without
    settings keep the historical baseline behavior for compatibility.
    """
    variant = "baseline_orders" if settings is None else settings.prompt_variant
    return get_order_prompt_template(variant).render_user(snapshot)
