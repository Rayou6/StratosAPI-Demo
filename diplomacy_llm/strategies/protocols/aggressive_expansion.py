from __future__ import annotations

from typing import TYPE_CHECKING

from diplomacy_llm.strategies.models import StrategyContext
from diplomacy_llm.strategies.protocols.base import BaseStrategyProtocol

if TYPE_CHECKING:
    from diplomacy_llm.phase_snapshot import PowerPhaseSnapshot


class AggressiveExpansionV2Strategy(BaseStrategyProtocol):
    """
    Force targeted, demo-oriented aggressive expansion.

    The model still chooses where and how to attack, but the strategy removes
    the option of playing for broad peace, low movement, or alliance comfort.
    """

    name = "aggressive_expansion_v2"
    version = "v1"

    def build_context(self, snapshot: PowerPhaseSnapshot) -> StrategyContext | None:
        """Build targeted aggressive-expansion guidance for one phase."""
        return StrategyContext(
            doctrine=(
                "Aggression is mandatory and must be targeted. Pick one main "
                "pressure target this phase: a rival power, a rival-owned center, "
                "a contested front, or an exposed rival unit. Your plan should "
                "force that target to react through a center threat, attack, "
                "support-attack, support cut, bounce, dislodgement attempt, or "
                "forward occupation. Do not choose broad peacekeeping, alliance "
                "maintenance, or neutral repositioning when a legal order can "
                "create pressure against a played power."
            ),
            phase_objective=_phase_objective(snapshot),
            priorities=_priorities(snapshot),
            constraints=_constraints(snapshot),
            risk_notes=_risk_notes(snapshot),
        )


def _phase_objective(snapshot: PowerPhaseSnapshot) -> str:
    """Return the phase-specific objective for targeted aggression."""
    if not snapshot.orderable_locations:
        return "No orders are available; identify the next pressure target."

    if snapshot.phase_type == "R":
        return (
            "Retreat toward the active fight. Choose the legal retreat that keeps "
            "the unit able to attack, block, cut support, or threaten a supply "
            "center next phase. Disband only when every retreat removes useful "
            "pressure."
        )

    if snapshot.phase_type == "A":
        return _adjustment_objective(snapshot)

    if snapshot.phase.startswith("F"):
        return (
            "Fall must decide ownership or force a hard contest. Choose a main "
            "target and order at least one capture attempt, supported attack, "
            "center contest, support cut, or move that directly threatens a "
            "played power's center this year."
        )

    return (
        "Spring must create a named fight. Choose a main pressure target, move "
        "units into contact, set up a supported attack, cut a support line, or "
        "occupy a province that creates a concrete Fall threat against a played "
        "power."
    )


def _adjustment_objective(snapshot: PowerPhaseSnapshot) -> str:
    """Return the aggressive objective for Adjustment phases."""
    if snapshot.adjustment_build_count > 0:
        return (
            "Build the unit that opens the fastest attack lane or support-attack "
            "lane against a played power. Prefer forward pressure over home "
            "comfort. Waive only when no legal build remains."
        )
    if snapshot.adjustment_disband_count > 0:
        return (
            "Disband the least aggressive unit. Preserve units on active fronts, "
            "units adjacent to rival centers, and units that can support attacks."
        )
    return "No adjustment decision is required; keep the next pressure target active."


def _priorities(snapshot: PowerPhaseSnapshot) -> tuple[str, ...]:
    """Return strict priorities for targeted aggressive expansion."""
    if snapshot.phase_type == "R":
        return (
            "Retreat toward a rival center, contested province, or support-cut lane.",
            "Avoid retreats that turn a front-line unit into a passive backfield unit.",
        )

    if snapshot.phase_type == "A":
        return (
            "Use every legal build that increases attacks, support-attacks, or fast center pressure.",
            "When forced to disband, keep front-line attackers over passive defenders.",
        )

    return (
        "Before writing orders, choose one main pressure target and make the orders serve that target.",
        "Prefer, in order: capture a played power's supply center, support an attack on a played power, attack an occupied rival province, cut support, contest a supply center, forward move.",
        "If a neighbor exposes a center you can contest or capture this year, treat that opportunity as more important than preserving the relationship.",
        "Coordinate temporary help only to create an attack, dislodgement, center contest, or support cut; do not coordinate merely to maintain peace.",
        "Use messages to make concrete demands, ask for specific support, threaten consequences, or distract the target.",
        "Keep the plan concentrated: scattered harmless moves are worse than one clear attack lane.",
    )


def _constraints(snapshot: PowerPhaseSnapshot) -> tuple[str, ...]:
    """Return non-negotiable constraints for targeted aggression."""
    constraints = [
        "Use only exact orders from the valid orders list.",
        "Do not invent map adjacency beyond the legal orders shown.",
        "Keep the final response inside the required orders JSON schema.",
        "Do not choose an all-hold Movement phase if any legal move, support-move, attack, support-attack, or support cut is available.",
        "Do not choose more than one hold or support-hold in a Movement phase unless every remaining legal order is passive or illegal for the plan.",
        "Do not preserve peace, trust, alliance stability, or a promise when a legal order can create a center threat, contest, dislodgement attempt, or support cut.",
        "Do not spend a Movement phase only repositioning against neutral or unplayed areas if a played power can be pressured now.",
    ]
    if snapshot.phase_type == "M":
        constraints.append(
            "At least one order must create pressure against the chosen target unless every legal order is purely passive.",
        )
    return tuple(constraints)


def _risk_notes(snapshot: PowerPhaseSnapshot) -> tuple[str, ...]:
    """Return risk notes that keep aggression controlled but forceful."""
    notes: list[str] = [
        "Risk is acceptable when it creates a visible fight, center pressure, a dislodgement chance, or tactical initiative.",
        (
            f"You currently own {len(snapshot.own_centers)} supply center(s); "
            "expand by taking space from rivals, not by freezing the board."
        ),
        "A failed attack, bounce, or cut support is still useful if it reveals the front and forces reaction.",
    ]
    if snapshot.phase_type == "M":
        if snapshot.phase.startswith("F"):
            notes.append(
                "A passive Fall is a strategic failure; force a supply-center decision or a direct contest.",
            )
        else:
            notes.append(
                "A passive Spring is a strategic failure; create contact and a Fall target now.",
            )
    if snapshot.last_phase_results:
        notes.append(
            "Escalate where prior bounces, dislodgements, or failed moves showed a contested front.",
        )
    if len(snapshot.own_units) > len(snapshot.own_centers):
        notes.append(
            "If disband risk exists, attack for centers rather than protecting every unit.",
        )
    return tuple(notes)
