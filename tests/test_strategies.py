from types import MappingProxyType

import pytest

from diplomacy_llm.phase_snapshot import PowerPhaseSnapshot
from diplomacy_llm.strategies import (
    AggressiveExpansionV2Strategy,
    BaselineStrategy,
    BaseStrategyProtocol,
    StrategyContext,
    StrategyIdentity,
    StrategyRuntimeContext,
    available_strategy_names,
    available_strategy_protocol_names,
    get_strategy,
    get_strategy_protocol,
    render_strategy_context,
)


def make_snapshot(
    *,
    possible_orders: dict[str, tuple[str, ...]] | None = None,
) -> PowerPhaseSnapshot:
    """Build a compact immutable snapshot for strategy tests."""
    possible: dict[str, tuple[str, ...]] = possible_orders or {
        "PAR": ("A PAR H", "A PAR - BUR"),
    }
    return PowerPhaseSnapshot(
        power_name="FRANCE",
        phase="S1901M",
        phase_type="M",
        own_units=("A PAR",),
        own_centers=("PAR",),
        all_units=MappingProxyType({"FRANCE": ("A PAR",)}),
        all_centers=MappingProxyType({"FRANCE": ("PAR",)}),
        last_phase=None,
        last_phase_results=MappingProxyType({}),
        orderable_locations=("PAR",),
        possible_orders=MappingProxyType(possible),
        adjustment_build_count=0,
        adjustment_disband_count=0,
    )


def test_registry_exposes_demo_strategies() -> None:
    assert available_strategy_names() == ("aggressive_expansion_v2", "baseline")
    assert available_strategy_protocol_names() == (
        "aggressive_expansion_v2",
        "baseline",
    )
    assert isinstance(get_strategy("aggressive_expansion_v2"), AggressiveExpansionV2Strategy)
    assert isinstance(get_strategy("baseline"), BaselineStrategy)
    assert isinstance(get_strategy_protocol("baseline"), BaseStrategyProtocol)


def test_registry_rejects_unknown_strategy_with_available_names() -> None:
    with pytest.raises(
        ValueError,
        match="Available strategies: aggressive_expansion_v2, baseline",
    ):
        get_strategy("missing")


def test_baseline_strategy_produces_no_context() -> None:
    strategy = get_strategy("baseline")

    assert strategy.identity == StrategyIdentity(name="baseline", version="v1")
    assert strategy.build_context(make_snapshot()) is None
    assert (
        strategy.render_prompt_section(StrategyRuntimeContext(snapshot=make_snapshot()))
        is None
    )


def test_aggressive_expansion_v2_produces_targeted_context() -> None:
    strategy = get_strategy("aggressive_expansion_v2")
    context = strategy.build_context(make_snapshot())

    assert strategy.identity == StrategyIdentity(
        name="aggressive_expansion_v2",
        version="v1",
    )
    assert context is not None
    assert "Aggression is mandatory" in context.doctrine
    assert "Spring must create a named fight" in context.phase_objective
    assert any("Use only exact orders" in item for item in context.constraints)


def test_render_strategy_context_uses_stable_schema() -> None:
    rendered = render_strategy_context(
        StrategyContext(
            doctrine="Plan first.",
            phase_objective="Take a supply center.",
            priorities=("Expand carefully.",),
            constraints=("Use legal orders.",),
            risk_notes=(),
        ),
    )

    assert rendered == (
        "Doctrine: Plan first.\n"
        "Phase objective: Take a supply center.\n"
        "Priorities:\n"
        "  - Expand carefully.\n"
        "Constraints:\n"
        "  - Use legal orders.\n"
        "Risk notes:\n"
        "  - (none)"
    )
