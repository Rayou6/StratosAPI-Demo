from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from diplomacy_llm.config import Settings
    from diplomacy_llm.phase_snapshot import PowerPhaseSnapshot


@dataclass(frozen=True)
class OrderPromptTemplate:
    """Template renderer for one order-generation prompt variant."""

    name: str
    system_template: str
    user_template: str

    @property
    def sha256(self) -> str:
        """Return a stable hash of the template text used by experiment identity."""
        content = f"{self.system_template}\0{self.user_template}"
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def render_system(self, power_name: str, settings: Settings) -> str:
        """Render the static system prompt for one power."""
        return self.system_template.format(
            power_name=power_name,
            power_count=len(settings.powers),
            powers=", ".join(settings.powers),
            win_score=settings.win_score,
            total_scs=settings.total_scs,
        )

    def render_user(
        self,
        snapshot: PowerPhaseSnapshot,
        strategy: str | None = None,
        messages: str | None = None,
    ) -> str:
        """Render the per-phase user prompt from an immutable snapshot."""
        my_units: list[str] = list(snapshot.own_units)
        my_centers: list[str] = list(snapshot.own_centers)

        return self.user_template.format(
            phase=snapshot.phase,
            phase_label=_phase_label(snapshot.phase_type),
            own_units=my_units,
            own_centers=my_centers,
            own_center_count=len(my_centers),
            others_block=_build_others_block(
                snapshot.power_name,
                snapshot.all_units,
                snapshot.all_centers,
            ),
            results_block=_build_results_block(snapshot),
            possible_orders=_format_possible_orders(snapshot.possible_orders),
            order_instructions=_build_order_instructions(snapshot),
            strategy_metadata=_format_strategy_metadata(strategy),
            messages=_format_messages(messages),
        )


_BASELINE_SYSTEM_TEMPLATE = """You are playing the board game Diplomacy as {power_name}.

## The Game
Diplomacy is a strategy game set on a map of early 1900s Europe.
There are {power_count} powers in this game: {powers}.
To win, you must control {win_score} supply centers (SCs). The map has {total_scs} SCs total.
The game is played in yearly turns, each consisting of up to 3 phases.

## Units
You control two types of units:
- Army (A): moves on land territories
- Fleet (F): moves on sea and coastal territories

## Phases
1. MOVEMENT (S = Spring, F = Fall): order each of your units to move, hold, support, or convoy.
2. RETREATS (R): if a unit was dislodged, it must retreat to an adjacent empty territory or disband.
3. ADJUSTMENTS (A): in Winter, build new units (if you gained SCs) or disband units (if you lost SCs).

## Order Types
Location codes are 3-letter abbreviations of territory names (e.g. PAR = Paris, MUN = Munich,
BER = Berlin, BRE = Brest, MAR = Marseilles, VIE = Vienna, TRI = Trieste, VEN = Venice,
ROM = Rome, NAP = Naples, KIE = Kiel). The valid orders list provided each turn is
the authority for which coded locations and order strings may be used in the current decision.

- Hold:           A PAR H              (unit stays in place)
- Move:           A PAR - BUR          (unit moves to adjacent territory)
- Support hold:   A MUN S A BER        (unit supports another unit holding)
- Support move:   A MUN S A BER - KIE  (unit supports another unit's move)
- Convoy:         F NTH C A LON - NWY  (fleet convoying an army across sea)
- Retreat:        A PAR R MAR          (only during Retreat phase)
- Disband:        A PAR D              (remove unit)
- Build:          A PAR B              (only during Adjustment phase, in home SC)
- Waive:          WAIVE                (skip a build during Adjustment phase)

## Key Rules
- All orders are submitted simultaneously - you cannot see what others play.
- If two units move to the same territory with equal strength, both BOUNCE and stay put.
- A supported unit has strength 2+ and can dislodge weaker units.
- You can only build in your HOME supply centers during Winter.
- In Movement and Retreat phases, you MUST submit exactly one order per orderable unit/location.
- In Adjustment phases, follow the build/disband/WAIVE requirement in the phase prompt.
- You MUST only use orders exactly as written in the valid orders list provided each turn.

## Legal Diplomacy Tactics
- When messaging is enabled, cooperation, negotiation, promises, bluffing, selective truth,
  deception, and betrayal are legal parts of Diplomacy.
- These tactics are options, not requirements. Use truthful or deceptive communication only
  when it serves your position in the current game.
- Agreements are not binding unless they are reflected in submitted orders and resolved by
  the game rules.

## Communication Language
- Use English for all natural-language outputs, including private message bodies, reasoning,
  and explanations.
- Do not switch languages for greetings or diplomacy unless quoting a prior message exactly.

## Your Identity
You are {power_name}. Your objective is to win by reaching {win_score} supply centers
under the rules of the current game.
Choose legal orders, and any enabled communication, according to your own assessment of
the current position. Do not assume a fixed play style; cooperation, defense, attack,
negotiation, and restraint can all be legal depending on context.
"""

_PHASE_STATE_TEMPLATE = """=== PHASE: {phase} ({phase_label}) ===

YOUR UNITS:           {own_units}
YOUR SUPPLY CENTERS:  {own_centers} ({own_center_count} SCs)

OTHER POWERS:
{others_block}

{results_block}"""

_CONTEXT_SECTIONS_TEMPLATE = """CONTEXT SECTIONS:
- Strategy metadata: configured decision guidance, not current messages.
- Messages: current-phase private press visible to you when enabled.

STRATEGY METADATA:
{strategy_metadata}

MESSAGES:
{messages}

"""

_VALID_ORDERS_TEMPLATE = """VALID ORDERS FOR YOUR UNITS:
{possible_orders}

{order_instructions}
"""

_BASELINE_USER_TEMPLATE = _PHASE_STATE_TEMPLATE + _VALID_ORDERS_TEMPLATE
_CONTEXTUAL_USER_TEMPLATE = (
    _PHASE_STATE_TEMPLATE + _CONTEXT_SECTIONS_TEMPLATE + _VALID_ORDERS_TEMPLATE
)

BASELINE_ORDERS_TEMPLATE = OrderPromptTemplate(
    name="baseline_orders",
    system_template=_BASELINE_SYSTEM_TEMPLATE,
    user_template=_BASELINE_USER_TEMPLATE,
)

ORDERS_WITH_CONTEXT_TEMPLATE = OrderPromptTemplate(
    name="orders_with_context",
    system_template=_BASELINE_SYSTEM_TEMPLATE,
    user_template=_CONTEXTUAL_USER_TEMPLATE,
)

ORDER_PROMPT_TEMPLATES: dict[str, OrderPromptTemplate] = {
    BASELINE_ORDERS_TEMPLATE.name: BASELINE_ORDERS_TEMPLATE,
    ORDERS_WITH_CONTEXT_TEMPLATE.name: ORDERS_WITH_CONTEXT_TEMPLATE,
}


def get_order_prompt_template(name: str) -> OrderPromptTemplate:
    """Return a registered order prompt template by configuration name."""
    try:
        return ORDER_PROMPT_TEMPLATES[name]
    except KeyError as exc:
        available = ", ".join(sorted(ORDER_PROMPT_TEMPLATES))
        msg = f"Unknown prompt_variant '{name}'. Available variants: {available}"
        raise ValueError(msg) from exc


def get_order_prompt_template_sha256(name: str) -> str:
    """Return the content hash for a registered order prompt template."""
    return get_order_prompt_template(name).sha256


def _format_strategy_metadata(strategy: str | None) -> str:
    """Format an optional strategy-owned prompt section."""
    if strategy is None or not strategy.strip():
        return "  (no strategy metadata configured)"
    return strategy


def _format_messages(messages: str | None) -> str:
    """Format optional private messages for context-capable prompt variants."""
    if messages is None or not messages.strip():
        return "  (no messages provided)"
    return messages


def _phase_label(phase_type: str) -> str:
    """Convert a phase type code ('M', 'R', 'A') to a human-readable label."""
    return {
        "M": "Movement Phase",
        "R": "Retreat Phase",
        "A": "Adjustment Phase",
    }.get(phase_type, phase_type)


def _build_others_block(
    power_name: str,
    all_units: Mapping[str, tuple[str, ...]],
    all_centers: Mapping[str, tuple[str, ...]],
) -> str:
    """Format the board state of all powers except the current one."""
    lines: list[str] = []
    for name, units in all_units.items():
        if name == power_name:
            continue
        centers: tuple[str, ...] = all_centers.get(name, ())
        lines.append(
            f"  {name}: units={list(units)}  SCs={list(centers)} ({len(centers)})",
        )
    return "\n".join(lines) if lines else "  (none)"


def _build_results_block(snapshot: PowerPhaseSnapshot) -> str:
    """
    Show notable results from the last processed phase.

    Only non-empty result lists are shown. If every order succeeded cleanly, this
    block is omitted to keep the prompt concise.
    """
    if snapshot.last_phase is None or not snapshot.last_phase_results:
        return ""

    lines: list[str] = [f"LAST PHASE RESULTS ({snapshot.last_phase}):"]
    for unit, results in snapshot.last_phase_results.items():
        lines.append(f"  {unit}: {', '.join(results)}")

    return "\n".join(lines) + "\n\n"


def _format_possible_orders(possible: Mapping[str, tuple[str, ...]]) -> str:
    """Format valid orders by location into a readable prompt block."""
    if not possible:
        return "  (no orders required this phase)"
    lines: list[str] = []
    for loc, orders in possible.items():
        lines.append(f"  {loc}: {list(orders)}")
    return "\n".join(lines)


def _build_order_instructions(snapshot: PowerPhaseSnapshot) -> str:
    """Return phase-specific order count instructions for the LLM."""
    order_count = len(snapshot.orderable_locations)
    if snapshot.phase_type != "A":
        return (
            "Pick exactly one order per orderable location from the valid orders "
            "list above.\n"
            f"Your response must contain {order_count} order(s)."
        )

    if snapshot.adjustment_build_count > 0:
        count = snapshot.adjustment_build_count
        return (
            f"ADJUSTMENT REQUIREMENT: You may build up to {count} unit(s).\n"
            f"Return exactly {count} adjustment decision(s): one valid build order "
            "for each unit you want to build, plus WAIVE repeated once for each "
            "unused build.\n"
            "Do not submit more than one build for the same home center."
        )

    if snapshot.adjustment_disband_count > 0:
        count = snapshot.adjustment_disband_count
        return (
            f"ADJUSTMENT REQUIREMENT: You must disband exactly {count} unit(s).\n"
            f"Return exactly {count} disband order(s) from the valid orders list. "
            "Do not submit orders for units you want to keep."
        )

    return "No adjustment orders are required this phase."
