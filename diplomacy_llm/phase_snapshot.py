from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import cast

from diplomacy import Game


@dataclass(frozen=True)
class PowerPhaseSnapshot:
    """
    Immutable data contract between the Diplomacy engine and one LLM player.

    Game reads happen once in game_runner on the main thread. LLM worker threads
    receive this plain-data snapshot instead of the live mutable Game object.
    """

    power_name: str
    phase: str
    phase_type: str
    own_units: tuple[str, ...]
    own_centers: tuple[str, ...]
    all_units: Mapping[str, tuple[str, ...]]
    all_centers: Mapping[str, tuple[str, ...]]
    last_phase: str | None
    last_phase_results: Mapping[str, tuple[str, ...]]
    orderable_locations: tuple[str, ...]
    possible_orders: Mapping[str, tuple[str, ...]]
    adjustment_build_count: int = 0
    adjustment_disband_count: int = 0
    phase_index: int = 0


def build_power_phase_snapshots(
    game: Game,
    powers: Sequence[str],
    *,
    phase_index: int = 0,
) -> dict[str, PowerPhaseSnapshot]:
    """
    Extract all per-power LLM context from the live Game object.

    This is the only place the LLM decision layer should get game-state data
    from. If prompts or validation need new game information later, add it to
    PowerPhaseSnapshot here first.

    Args:
        game: The current live Diplomacy Game object, owned by game_runner.
        powers: Active powers that need an LLM order this phase.
        phase_index: Zero-based phase index used by live demo events.

    Returns:
        A snapshot per requested power, keyed by power name.

    """
    phase: str = game.get_current_phase()
    phase_type: str = cast("str", game.phase_type) if game.phase_type else ""
    all_units_raw: dict[str, list[str]] = cast("dict[str, list[str]]", game.get_units())
    all_centers_raw: dict[str, list[str]] = cast(
        "dict[str, list[str]]",
        game.get_centers(),
    )
    all_possible: dict[str, list[str]] = cast(
        "dict[str, list[str]]",
        game.get_all_possible_orders(),
    )
    last_phase, last_phase_results = _last_phase_results(game)

    all_units: Mapping[str, tuple[str, ...]] = _freeze_list_mapping(all_units_raw)
    all_centers: Mapping[str, tuple[str, ...]] = _freeze_list_mapping(all_centers_raw)

    snapshots: dict[str, PowerPhaseSnapshot] = {}
    for power_name in powers:
        orderable_locations: tuple[str, ...] = tuple(
            cast("list[str]", game.get_orderable_locations(power_name)),
        )
        possible_orders: Mapping[str, tuple[str, ...]] = MappingProxyType(
            {
                loc: tuple(sorted(all_possible.get(loc, []), key=_order_sort_key))
                for loc in orderable_locations
            },
        )
        adjustment_build_count, adjustment_disband_count = _adjustment_counts(
            phase_type,
            own_units=tuple(all_units_raw.get(power_name, [])),
            own_centers=tuple(all_centers_raw.get(power_name, [])),
            orderable_locations=orderable_locations,
        )
        snapshots[power_name] = PowerPhaseSnapshot(
            power_name=power_name,
            phase=phase,
            phase_type=phase_type,
            own_units=tuple(all_units_raw.get(power_name, [])),
            own_centers=tuple(all_centers_raw.get(power_name, [])),
            all_units=all_units,
            all_centers=all_centers,
            last_phase=last_phase,
            last_phase_results=last_phase_results,
            orderable_locations=orderable_locations,
            possible_orders=possible_orders,
            adjustment_build_count=adjustment_build_count,
            adjustment_disband_count=adjustment_disband_count,
            phase_index=phase_index,
        )

    return snapshots


def _freeze_list_mapping(
    raw: dict[str, list[str]],
) -> Mapping[str, tuple[str, ...]]:
    """Convert dict[str, list[str]] into a read-only dict[str, tuple[str, ...]]."""
    return MappingProxyType({key: tuple(value) for key, value in raw.items()})


def _last_phase_results(
    game: Game,
) -> tuple[str | None, Mapping[str, tuple[str, ...]]]:
    """Return notable latest phase results as plain strings."""
    if not game.result_history:
        return None, MappingProxyType({})

    last_phase = list(game.result_history.keys())[-1]
    results = game.result_history[last_phase]
    notable = {
        unit: tuple(str(result) for result in order_results)
        for unit, order_results in results.items()
        if order_results
    }
    return last_phase, MappingProxyType(notable)


def _adjustment_counts(
    phase_type: str,
    *,
    own_units: tuple[str, ...],
    own_centers: tuple[str, ...],
    orderable_locations: tuple[str, ...],
) -> tuple[int, int]:
    """Return the build/disband quota for an Adjustment phase."""
    if phase_type != "A":
        return 0, 0

    unit_count = len(tuple(unit for unit in own_units if not unit.startswith("*")))
    delta = len(own_centers) - unit_count
    if delta > 0:
        return min(delta, len(orderable_locations)), 0
    if delta < 0:
        return 0, min(-delta, len(orderable_locations))
    return 0, 0


def _order_sort_key(order: str) -> tuple[int, str]:
    """Make fallback choices reproducible across Python processes."""
    parts = order.split()
    action = parts[2] if len(parts) >= 3 else order
    priority = {
        "H": 0,
        "R": 1,
        "D": 2,
        "B": 3,
        "S": 4,
        "C": 5,
        "-": 6,
        "WAIVE": 7,
    }.get(action, 99)
    return priority, order
