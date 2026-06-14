import concurrent.futures
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import cast

from diplomacy import Game

from diplomacy_llm.config import Settings
from diplomacy_llm.llm_client import LLMClient
from diplomacy_llm.llm_player import LLMPlayer
from diplomacy_llm.messaging import (
    DiplomacyMessage,
    DroppedMessage,
    MessageValidationResult,
    visible_messages_for_power,
)
from diplomacy_llm.messaging.protocols import get_messaging_protocol
from diplomacy_llm.messaging.protocols.latency_pairwise_private import (
    LatencyPairwisePrivateMessagingProtocol,
)
from diplomacy_llm.metrics_collector import MetricsCollector
from diplomacy_llm.phase_snapshot import PowerPhaseSnapshot, build_power_phase_snapshots
from diplomacy_llm.saved_games import resolve_map_path
from diplomacy_llm.strategies import StrategyResolution
from diplomacy_llm.strategies.protocols import get_strategy_protocol

logger: logging.Logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PhaseRecord:
    phase: str
    phase_index: int
    phase_type: str
    orders: tuple[str, ...]
    reasoning: str
    unit_occupants: Mapping[str, str] = field(default_factory=dict)


OrdersLog = dict[str, list[PhaseRecord]]


@dataclass(frozen=True)
class MessageWindowArtifact:
    """Accepted and dropped messages from one simultaneous message window."""

    message_window: int
    accepted: tuple[DiplomacyMessage, ...]
    dropped: tuple[DroppedMessage, ...]

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe representation for message artifacts."""
        return {
            "message_window": self.message_window,
            "accepted": [message.to_dict() for message in self.accepted],
            "dropped": [drop.to_dict() for drop in self.dropped],
        }


@dataclass(frozen=True)
class MessagePhaseArtifact:
    """Messaging audit data for one game phase."""

    phase: str
    phase_index: int
    phase_type: str
    enabled_powers: tuple[str, ...]
    windows: tuple[MessageWindowArtifact, ...]

    @property
    def accepted(self) -> tuple[DiplomacyMessage, ...]:
        """Return all accepted messages in phase/window order."""
        return tuple(message for window in self.windows for message in window.accepted)

    @property
    def dropped(self) -> tuple[DroppedMessage, ...]:
        """Return all dropped messages in phase/window order."""
        return tuple(drop for window in self.windows for drop in window.dropped)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe representation for message artifacts."""
        return {
            "phase": self.phase,
            "phase_index": self.phase_index,
            "phase_type": self.phase_type,
            "enabled_powers": list(self.enabled_powers),
            "windows": [window.to_dict() for window in self.windows],
        }


class GameRunObserver:
    """Optional no-op callbacks for consumers that watch a game run live."""

    def on_run_started(  # noqa: PLR0913
        self,
        *,
        game: Game,
        settings: Settings,
        power_models: Mapping[str, str],
        power_strategies: Mapping[str, StrategyResolution] | None,
        initial_sc_counts: Mapping[str, int],
        calendar_year: int,
    ) -> None:
        """Called after the game is initialized and baseline scores are known."""
        _ = (
            game,
            settings,
            power_models,
            power_strategies,
            initial_sc_counts,
            calendar_year,
        )

    def on_phase_started(
        self,
        *,
        game: Game,
        phase: str,
        phase_index: int,
        phase_type: str,
    ) -> None:
        """Called immediately before a phase starts generating messages/orders."""
        _ = game, phase, phase_index, phase_type

    def on_messages_generated(self, phase_messages: MessagePhaseArtifact) -> None:
        """Called after private-press messages are accepted for a phase."""
        _ = phase_messages

    def on_message_window_delivered(
        self,
        *,
        phase: str,
        phase_index: int,
        phase_type: str,
        window: MessageWindowArtifact,
    ) -> None:
        """Called when one private-press message window has been accepted."""
        _ = phase, phase_index, phase_type, window

    def on_orders_submitted(  # noqa: PLR0913
        self,
        *,
        phase: str,
        phase_index: int,
        phase_type: str,
        power: str,
        orders: Sequence[str],
        reasoning: str,
        is_fallback: bool = False,
    ) -> None:
        """Called when one power has generated validated orders."""
        _ = phase, phase_index, phase_type, power, orders, reasoning, is_fallback

    def on_phase_resolved(  # noqa: PLR0913
        self,
        *,
        game: Game,
        phase: str,
        phase_index: int,
        phase_type: str,
        next_phase: str,
        next_phase_index: int,
        result_history: Mapping[str, Sequence[str]],
        sc_counts: Mapping[str, int],
    ) -> None:
        """Called after game.process() resolves a phase."""
        _ = (
            game,
            phase,
            phase_index,
            phase_type,
            next_phase,
            next_phase_index,
            result_history,
            sc_counts,
        )

    def on_year_summary(
        self,
        *,
        phase: str,
        phase_index: int,
        year: int,
        calendar_year: int,
        sc_counts: Mapping[str, int],
    ) -> None:
        """Called after a completed game year is recorded."""
        _ = phase, phase_index, year, calendar_year, sc_counts

    def on_game_finished(
        self,
        *,
        game: Game,
        phase: str,
        phase_index: int,
        winner: str | None,
        sc_counts: Mapping[str, int],
    ) -> None:
        """Called after final game outcome is known."""
        _ = game, phase, phase_index, winner, sc_counts


@dataclass
class _PairwisePowerContext:
    """Current-phase private messaging context visible to one power."""

    power: str
    timeline: list[str] = field(default_factory=list)
    version: int = 0
    sent_count: int = 0

    def add_event(self, text: str) -> None:
        """Record one visible messaging event and advance the context version."""
        self.timeline.append(text)
        self.version += 1


@dataclass
class _PairwiseThreadState:
    """Runtime state for one private pairwise thread."""

    powers: tuple[str, str]
    turn_count: int = 0
    waiting_reply_from: str | None = None
    closed: bool = False


@dataclass(frozen=True)
class _PairwiseCall:
    """Metadata needed to validate a completed pairwise LLM call."""

    power: str
    call_kind: str
    call_index: int
    phase: str
    phase_index: int
    context_version: int
    timeline_start_index: int
    pending_recipients: tuple[str, ...] = ()
    stale_messages: tuple[DiplomacyMessage, ...] = ()


def run_game(  # noqa: C901, PLR0912, PLR0913, PLR0915
    client: LLMClient,
    collector: MetricsCollector,
    settings: Settings,
    power_models: dict[str, str],
    power_strategies: Mapping[str, StrategyResolution] | None = None,
    orders_path: Path | None = None,
    messages_path: Path | None = None,
    observer: GameRunObserver | None = None,
) -> Game:
    """
    Run a complete Diplomacy game for up to max_years years.

    Creates one LLMPlayer per power, then iterates through all game phases
    (Movement, Retreats, Adjustments) until max_years full years are complete
    or a power achieves the victory condition (whichever comes first).

    Args:
        client:       Shared LLM client instance.
        collector:    Shared MetricsCollector instance for live-run bookkeeping.
        settings:     Explicit game/run settings.
        power_models: Effective power→model mapping (may be shuffled from config values).
        power_strategies: Effective power-to-strategy mapping resolved after models.
        orders_path:  Optional path for the orders recap .txt file.
                      Parent directory is created automatically if provided.
        messages_path: Deprecated compatibility argument.
        observer: Optional live-run observer.

    Returns:
        The completed Game object, ready for saving.

    """
    game: Game = Game(map_name=_resolve_map_name(settings.map_name))
    game.win = settings.win_score
    game.victory = [settings.win_score]
    logger.info(
        "Game initialized. Map: %s, Powers: %s, Win score: %d",
        settings.map_name,
        settings.powers,
        settings.win_score,
    )

    initial_centers: dict[str, list[str]] = cast(
        "dict[str, list[str]]",
        game.get_centers(),
    )
    initial_sc_counts: dict[str, int] = {
        p: len(initial_centers.get(p, [])) for p in settings.powers
    }
    start_calendar_year: int = int(game.get_current_phase()[1:5])
    logger.info(
        "Initial SC counts (year %d): %s",
        start_calendar_year,
        initial_sc_counts,
    )
    collector.record_game_start(
        initial_sc_counts,
        calendar_year=start_calendar_year,
    )
    if observer is not None:
        observer.on_run_started(
            game=game,
            settings=settings,
            power_models=power_models,
            power_strategies=power_strategies,
            initial_sc_counts=initial_sc_counts,
            calendar_year=start_calendar_year,
        )

    if orders_path is not None:
        orders_path.parent.mkdir(parents=True, exist_ok=True)

    # Create one LLMPlayer per power — system prompts are built once here and reused
    players: dict[str, LLMPlayer] = {
        power: LLMPlayer(
            power_name=power,
            model=power_models[power],
            client=client,
            collector=collector,
            settings=settings,
            strategy=get_strategy_protocol(
                _strategy_name_for_power(power, power_strategies),
            ),
            strategy_resolution=_strategy_resolution_for_power(
                power,
                power_strategies,
            ),
        )
        for power in settings.powers
    }

    orders_log: OrdersLog = {p: [] for p in settings.powers}
    message_artifacts: list[MessagePhaseArtifact] = []
    years_completed: int = 0
    last_year_logged: int = start_calendar_year
    phase_index: int = 0

    while not game.is_game_done:
        current_phase: str = game.get_current_phase()
        phase_type: str = cast("str", game.phase_type) if game.phase_type else ""
        calendar_year: int = int(current_phase[1:5])

        # Winter Adjustment was skipped (nobody needed to adjust): the calendar year
        # advanced without a W{year}A phase, so we log the completed year here before
        # processing the new Spring — SC counts at this point reflect end of previous Fall.
        if calendar_year > last_year_logged:
            years_completed += 1
            all_centers: dict[str, list[str]] = cast(
                "dict[str, list[str]]",
                game.get_centers(),
            )
            logger.info(
                "Year %d / %d complete (game year %d — winter adjustment skipped)",
                years_completed,
                settings.max_years,
                last_year_logged,
            )
            sc_counts = {p: len(all_centers.get(p, [])) for p in settings.powers}
            collector.log_year_summary(
                year=years_completed,
                calendar_year=last_year_logged,
                sc_counts=sc_counts,
            )
            if observer is not None:
                observer.on_year_summary(
                    phase=current_phase,
                    phase_index=phase_index,
                    year=years_completed,
                    calendar_year=last_year_logged,
                    sc_counts=sc_counts,
                )
            last_year_logged = calendar_year
            if years_completed >= settings.max_years:
                logger.info(
                    "Reached max_years (%d) — stopping game", settings.max_years
                )
                break

        logger.info("--- Phase: %s ---", current_phase)
        if observer is not None:
            observer.on_phase_started(
                game=game,
                phase=current_phase,
                phase_index=phase_index,
                phase_type=phase_type,
            )

        if phase_type == "R":
            collector.record_retreat_phase()

        phase_unit_occupants = _unit_occupants(game)
        phase_results: dict[str, tuple[list[str], str]] = _run_phase(
            game,
            players,
            settings,
            phase_index=phase_index,
            message_artifacts=message_artifacts,
            observer=observer,
        )
        collector.record_phase()
        phase_index += 1

        # Accumulate orders (game state has already advanced, but current_phase still refers to what was just played)
        for power_name, (orders, reasoning) in phase_results.items():
            orders_log[power_name].append(
                PhaseRecord(
                    phase=current_phase,
                    phase_index=phase_index - 1,
                    phase_type=phase_type,
                    orders=tuple(orders),
                    reasoning=reasoning,
                    unit_occupants=phase_unit_occupants,
                ),
            )

        # Standard year end: Adjustment phase processed, log after so builds/disbands are included
        if phase_type == "A":
            years_completed += 1
            last_year_logged = (
                calendar_year + 1
            )  # advance past this year so next Spring doesn't trigger the skip block
            all_centers = cast("dict[str, list[str]]", game.get_centers())
            logger.info(
                "Year %d / %d complete (game year %d)",
                years_completed,
                settings.max_years,
                calendar_year,
            )
            sc_counts = {p: len(all_centers.get(p, [])) for p in settings.powers}
            collector.log_year_summary(
                year=years_completed,
                calendar_year=calendar_year,
                sc_counts=sc_counts,
            )
            if observer is not None:
                observer.on_year_summary(
                    phase=game.get_current_phase(),
                    phase_index=phase_index,
                    year=years_completed,
                    calendar_year=calendar_year,
                    sc_counts=sc_counts,
                )
            if years_completed >= settings.max_years:
                logger.info(
                    "Reached max_years (%d) — stopping game", settings.max_years
                )
                break

    winner, final_sc_counts = _record_game_end(game, collector, settings)
    if observer is not None:
        observer.on_game_finished(
            game=game,
            phase=game.get_current_phase(),
            phase_index=phase_index,
            winner=winner,
            sc_counts=final_sc_counts,
        )
    if orders_path is not None:
        _write_orders_recap(
            orders_log,
            orders_path,
            power_models,
            settings,
            power_strategies=power_strategies,
        )
        logger.info("Orders recap saved to %s", orders_path)

    _ = messages_path

    return game


def _strategy_name_for_power(
    power: str,
    power_strategies: Mapping[str, StrategyResolution] | None,
) -> str:
    """Return the strategy name assigned to one power, defaulting to baseline."""
    if power_strategies is None:
        return "baseline"
    resolution = power_strategies.get(power)
    if resolution is None:
        return "baseline"
    return resolution.strategy_name


def _strategy_resolution_for_power(
    power: str,
    power_strategies: Mapping[str, StrategyResolution] | None,
) -> StrategyResolution | None:
    """Return the resolved strategy metadata for one power, if available."""
    if power_strategies is None:
        return None
    return power_strategies.get(power)


def _resolve_map_name(map_name: str) -> str:
    """Resolve project-relative map paths without changing named built-in maps."""
    try:
        return resolve_map_path(map_name)
    except FileNotFoundError:
        return map_name


def _run_phase(  # noqa: C901, PLR0913
    game: Game,
    players: dict[str, LLMPlayer],
    settings: Settings,
    *,
    phase_index: int = 0,
    message_artifacts: list[MessagePhaseArtifact] | None = None,
    observer: GameRunObserver | None = None,
) -> dict[str, tuple[list[str], str]]:
    """
    Collect and submit orders for all powers, then advance the game state.

    Active powers' LLM calls are made in parallel (they all read the same
    game snapshot). Orders are submitted sequentially after all calls return,
    then game.process() advances the state — no power ever moves ahead of another.

    Args:
        game:    The current Game instance (mutated by set_orders and process).
        players: Map of power name → LLMPlayer.
        settings: Explicit game/run settings.
        phase_index: Zero-based phase index used in live events.
        message_artifacts: Optional mutable run-level list for messaging audit data.
        observer: Optional live-run observer.

    Returns:
        A dict mapping each power name to its (orders, reasoning) for this phase.

    """
    results: dict[str, tuple[list[str], str]] = {}
    active_powers: list[str] = []

    # Handle eliminated / absent powers first (no LLM call needed)
    for power_name in settings.powers:
        power = game.get_power(power_name)
        if power is None or power.is_eliminated():
            if power is not None:
                game.set_wait(power_name, False)
            results[power_name] = ([], "")
        else:
            active_powers.append(power_name)

    # Extract plain snapshots on the main thread before parallel LLM calls.
    if active_powers:
        snapshots: dict[str, PowerPhaseSnapshot] = build_power_phase_snapshots(
            game,
            active_powers,
            phase_index=phase_index,
        )
        messaging_powers = _active_messaging_powers(active_powers, settings)
        messaging_context_enabled = _message_windows_enabled(
            settings,
            snapshots,
            messaging_powers=messaging_powers,
        )
        phase_messages = _run_message_windows(
            players,
            settings,
            snapshots,
            messaging_powers=messaging_powers,
            observer=observer,
        )
        if message_artifacts is not None and phase_messages.windows:
            message_artifacts.append(phase_messages)
        if observer is not None and phase_messages.windows:
            observer.on_messages_generated(phase_messages)
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=len(active_powers),
        ) as executor:
            futures = {
                power: executor.submit(
                    players[power].get_orders,
                    snapshots[power],
                    visible_messages=(
                        visible_messages_for_power(phase_messages.accepted, power)
                        if messaging_context_enabled and power in messaging_powers
                        else None
                    ),
                )
                for power in active_powers
            }
            for power_name, future in futures.items():
                orders, reasoning = future.result()
                results[power_name] = (orders, reasoning)
                if observer is not None:
                    snapshot = snapshots[power_name]
                    order_call = players[power_name].collector.latest_order_call(
                        power=power_name,
                        phase=snapshot.phase,
                    )
                    observer.on_orders_submitted(
                        phase=snapshot.phase,
                        phase_index=snapshot.phase_index,
                        phase_type=snapshot.phase_type,
                        power=power_name,
                        orders=orders,
                        reasoning=reasoning,
                        is_fallback=bool(order_call and order_call["is_fallback"]),
                    )
    # Submit orders sequentially — game is mutated here
    for power_name in active_powers:
        orders, _ = results[power_name]
        if orders:
            game.set_orders(power_name, orders)
        game.set_wait(
            power_name,
            False,
        )  # MANDATORY — must follow every set_orders call

    resolved_phase = game.get_current_phase()
    resolved_phase_type = cast("str", game.phase_type) if game.phase_type else ""
    game.process()
    if observer is not None:
        observer.on_phase_resolved(
            game=game,
            phase=resolved_phase,
            phase_index=phase_index,
            phase_type=resolved_phase_type,
            next_phase=game.get_current_phase(),
            next_phase_index=phase_index + 1,
            result_history=_result_history_for_phase(game, resolved_phase),
            sc_counts=_supply_center_counts(game, settings),
        )
    return results


def _run_message_windows(
    players: Mapping[str, LLMPlayer],
    settings: Settings,
    snapshots: Mapping[str, PowerPhaseSnapshot],
    *,
    messaging_powers: list[str],
    observer: GameRunObserver | None = None,
) -> MessagePhaseArtifact:
    """Run simultaneous private message windows for one Movement phase."""
    first_snapshot = next(iter(snapshots.values()))
    if not _message_windows_enabled(
        settings,
        snapshots,
        messaging_powers=messaging_powers,
    ):
        return MessagePhaseArtifact(
            phase=first_snapshot.phase,
            phase_index=first_snapshot.phase_index,
            phase_type=first_snapshot.phase_type,
            enabled_powers=tuple(messaging_powers),
            windows=(),
        )

    protocol = get_messaging_protocol(settings.messaging_variant)
    return _run_latency_pairwise_messages(
        players,
        settings,
        snapshots,
        messaging_powers=messaging_powers,
        protocol=protocol,
        observer=observer,
    )


def _run_latency_pairwise_messages(  # noqa: C901, PLR0913, PLR0915
    players: Mapping[str, LLMPlayer],
    settings: Settings,
    snapshots: Mapping[str, PowerPhaseSnapshot],
    *,
    messaging_powers: list[str],
    protocol: LatencyPairwisePrivateMessagingProtocol,
    observer: GameRunObserver | None = None,
) -> MessagePhaseArtifact:
    """Run latency-ordered pairwise private messaging for one Movement phase."""
    first_snapshot = next(iter(snapshots.values()))
    contexts = {power: _PairwisePowerContext(power=power) for power in messaging_powers}
    threads = _pairwise_thread_states(messaging_powers)
    accepted_messages: list[DiplomacyMessage] = []
    windows: list[MessageWindowArtifact] = []
    call_counter = 0

    def next_call_index() -> int:
        nonlocal call_counter
        call_counter += 1
        return call_counter

    def submit_call(  # noqa: PLR0913
        executor: concurrent.futures.ThreadPoolExecutor,
        active: dict[concurrent.futures.Future[MessageValidationResult], _PairwiseCall],
        active_by_power: set[str],
        *,
        power: str,
        call_kind: str,
        eligible_recipients: tuple[str, ...],
        pending_recipients: tuple[str, ...] = (),
        stale_messages: tuple[DiplomacyMessage, ...] = (),
        new_timeline_lines: tuple[str, ...] = (),
    ) -> None:
        if power in active_by_power or not eligible_recipients:
            return

        context = contexts[power]
        snapshot = snapshots[power]
        if call_kind == "initial":
            prompt = protocol.render_initial_outbox_prompt(
                snapshot,
                eligible_recipients=eligible_recipients,
                timeline_lines=tuple(context.timeline),
                settings=settings,
                sent_count=context.sent_count,
                thread_status_lines=_pairwise_thread_status_lines(
                    power,
                    messaging_powers,
                    threads,
                    settings,
                ),
            )
        elif call_kind == "revision":
            prompt = protocol.render_revision_prompt(
                snapshot,
                eligible_recipients=eligible_recipients,
                pending_recipients=pending_recipients,
                timeline_lines=tuple(context.timeline),
                new_timeline_lines=new_timeline_lines,
                stale_messages=stale_messages,
                settings=settings,
                sent_count=context.sent_count,
                thread_status_lines=_pairwise_thread_status_lines(
                    power,
                    messaging_powers,
                    threads,
                    settings,
                ),
            )
        else:
            prompt = protocol.render_grouped_reply_prompt(
                snapshot,
                pending_recipients=pending_recipients,
                timeline_lines=tuple(context.timeline),
                settings=settings,
                sent_count=context.sent_count,
                thread_status_lines=_pairwise_thread_status_lines(
                    power,
                    messaging_powers,
                    threads,
                    settings,
                ),
            )

        call_index = next_call_index()
        future = executor.submit(
            players[power].get_messages_with_prompt,
            snapshot,
            prompt=prompt,
            message_window=call_index,
            eligible_recipient_powers=eligible_recipients,
            start_sequence=0,
        )
        active[future] = _PairwiseCall(
            power=power,
            call_kind=call_kind,
            call_index=call_index,
            phase=snapshot.phase,
            phase_index=snapshot.phase_index,
            context_version=context.version,
            timeline_start_index=len(context.timeline),
            pending_recipients=pending_recipients,
            stale_messages=stale_messages,
        )
        active_by_power.add(power)

    def schedule_pending_replies(
        executor: concurrent.futures.ThreadPoolExecutor,
        active: dict[concurrent.futures.Future[MessageValidationResult], _PairwiseCall],
        active_by_power: set[str],
    ) -> None:
        for power in messaging_powers:
            if power in active_by_power:
                continue
            pending = _pairwise_pending_recipients(power, messaging_powers, threads)
            if not pending:
                continue
            recipients = _pairwise_allowed_recipients(
                power,
                messaging_powers,
                threads,
                contexts,
                settings,
                only_pending=True,
            )
            submit_call(
                executor,
                active,
                active_by_power,
                power=power,
                call_kind="reply",
                eligible_recipients=recipients,
                pending_recipients=pending,
            )

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max(1, len(messaging_powers)),
    ) as executor:
        active: dict[
            concurrent.futures.Future[MessageValidationResult], _PairwiseCall
        ] = {}
        active_by_power: set[str] = set()
        for power in messaging_powers:
            submit_call(
                executor,
                active,
                active_by_power,
                power=power,
                call_kind="initial",
                eligible_recipients=_pairwise_allowed_recipients(
                    power,
                    messaging_powers,
                    threads,
                    contexts,
                    settings,
                    only_pending=False,
                ),
            )

        while active:
            done, _ = concurrent.futures.wait(
                tuple(active),
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            for future in done:
                call = active.pop(future)
                active_by_power.discard(call.power)
                result = future.result()
                context = contexts[call.power]
                if context.version != call.context_version:
                    recipients = _pairwise_allowed_recipients(
                        call.power,
                        messaging_powers,
                        threads,
                        contexts,
                        settings,
                        only_pending=False,
                    )
                    pending = _pairwise_pending_recipients(
                        call.power,
                        messaging_powers,
                        threads,
                    )
                    submit_call(
                        executor,
                        active,
                        active_by_power,
                        power=call.power,
                        call_kind="revision",
                        eligible_recipients=recipients,
                        pending_recipients=pending,
                        stale_messages=result.accepted,
                        new_timeline_lines=tuple(
                            context.timeline[call.timeline_start_index :],
                        ),
                    )
                    continue

                window = _deliver_pairwise_result(
                    result,
                    call,
                    settings=settings,
                    threads=threads,
                    contexts=contexts,
                    accepted_messages=accepted_messages,
                )
                if window.accepted or window.dropped:
                    windows.append(window)
                    if observer is not None:
                        observer.on_message_window_delivered(
                            phase=first_snapshot.phase,
                            phase_index=first_snapshot.phase_index,
                            phase_type=first_snapshot.phase_type,
                            window=window,
                        )
                schedule_pending_replies(executor, active, active_by_power)

    return MessagePhaseArtifact(
        phase=first_snapshot.phase,
        phase_index=first_snapshot.phase_index,
        phase_type=first_snapshot.phase_type,
        enabled_powers=tuple(messaging_powers),
        windows=tuple(windows),
    )


def _deliver_pairwise_result(  # noqa: PLR0913
    result: MessageValidationResult,
    call: _PairwiseCall,
    *,
    settings: Settings,
    threads: dict[tuple[str, str], _PairwiseThreadState],
    contexts: dict[str, _PairwisePowerContext],
    accepted_messages: list[DiplomacyMessage],
) -> MessageWindowArtifact:
    """Deliver one non-stale pairwise result and update runtime state."""
    accepted: list[DiplomacyMessage] = []
    dropped: list[DroppedMessage] = list(result.dropped)
    handled_recipients: set[str] = set()

    for message in result.accepted:
        recipient = message.recipient
        if recipient in handled_recipients:
            continue
        if not _pairwise_can_send(call.power, recipient, threads, contexts, settings):
            dropped.append(
                DroppedMessage(
                    sender=call.power,
                    phase=message.phase,
                    phase_index=message.phase_index,
                    message_window=call.call_index,
                    reason="thread_not_available",
                    raw=message.to_dict(),
                ),
            )
            continue

        delivered = replace(
            message,
            sequence=len(accepted_messages) + len(accepted),
            message_window=call.call_index,
        )
        accepted.append(delivered)
        handled_recipients.add(recipient)
        contexts[call.power].sent_count += 1
        _record_pairwise_delivery(delivered, threads, contexts, settings)

    for recipient in call.pending_recipients:
        if recipient in handled_recipients:
            continue
        notice = _close_pairwise_thread(
            sender=call.power,
            recipient=recipient,
            call=call,
            accepted_sequence=len(accepted_messages) + len(accepted),
            close_reason=_pairwise_close_reason(
                call,
                delivered_reply_count=len(handled_recipients),
                context=contexts[call.power],
                settings=settings,
            ),
            threads=threads,
            contexts=contexts,
        )
        if notice is not None:
            accepted.append(notice)

    accepted_messages.extend(accepted)
    return MessageWindowArtifact(
        message_window=call.call_index,
        accepted=tuple(accepted),
        dropped=tuple(dropped),
    )


def _pairwise_thread_states(
    powers: Sequence[str],
) -> dict[tuple[str, str], _PairwiseThreadState]:
    threads: dict[tuple[str, str], _PairwiseThreadState] = {}
    for index, first in enumerate(powers):
        for second in powers[index + 1 :]:
            key = _pairwise_thread_key(first, second)
            threads[key] = _PairwiseThreadState(powers=key)
    return threads


def _pairwise_thread_key(first: str, second: str) -> tuple[str, str]:
    return tuple(sorted((first, second)))


def _pairwise_pending_recipients(
    power: str,
    messaging_powers: Sequence[str],
    threads: Mapping[tuple[str, str], _PairwiseThreadState],
) -> tuple[str, ...]:
    pending: list[str] = []
    for recipient in messaging_powers:
        if recipient == power:
            continue
        thread = threads[_pairwise_thread_key(power, recipient)]
        if not thread.closed and thread.waiting_reply_from == power:
            pending.append(recipient)
    return tuple(pending)


def _pairwise_thread_status_lines(
    power: str,
    messaging_powers: Sequence[str],
    threads: Mapping[tuple[str, str], _PairwiseThreadState],
    settings: Settings,
) -> tuple[str, ...]:
    """Return model-facing per-thread limits and status for one power."""
    max_turns = settings.messaging.latency_pairwise_private.max_turns_per_conversation
    lines: list[str] = []
    for recipient in messaging_powers:
        if recipient == power:
            continue
        thread = threads[_pairwise_thread_key(power, recipient)]
        remaining = max(0, max_turns - thread.turn_count)
        if thread.closed or remaining == 0:
            status = "closed; you cannot send in this thread"
        elif thread.waiting_reply_from == power:
            status = "reply pending from you"
        elif thread.waiting_reply_from is None:
            status = "no messages delivered yet; you may initiate"
        else:
            status = f"waiting for {thread.waiting_reply_from} to reply"
        lines.append(
            f"{recipient}: {thread.turn_count}/{max_turns} delivered message(s); "
            f"{remaining} remaining; {status}."
        )
    return tuple(lines)


def _pairwise_allowed_recipients(  # noqa: PLR0913
    power: str,
    messaging_powers: Sequence[str],
    threads: Mapping[tuple[str, str], _PairwiseThreadState],
    contexts: Mapping[str, _PairwisePowerContext],
    settings: Settings,
    *,
    only_pending: bool,
) -> tuple[str, ...]:
    if _pairwise_sent_cap_reached(contexts[power], settings):
        return ()

    recipients: list[str] = []
    for recipient in messaging_powers:
        if recipient == power:
            continue
        if only_pending and recipient not in _pairwise_pending_recipients(
            power,
            messaging_powers,
            threads,
        ):
            continue
        if _pairwise_can_send(power, recipient, threads, contexts, settings):
            recipients.append(recipient)
    return tuple(recipients)


def _pairwise_can_send(
    sender: str,
    recipient: str,
    threads: Mapping[tuple[str, str], _PairwiseThreadState],
    contexts: Mapping[str, _PairwisePowerContext],
    settings: Settings,
) -> bool:
    if _pairwise_sent_cap_reached(contexts[sender], settings):
        return False
    thread = threads[_pairwise_thread_key(sender, recipient)]
    if (
        thread.closed
        or thread.turn_count
        >= settings.messaging.latency_pairwise_private.max_turns_per_conversation
    ):
        return False
    return thread.waiting_reply_from in {None, sender}


def _pairwise_sent_cap_reached(
    context: _PairwisePowerContext,
    settings: Settings,
) -> bool:
    cap = settings.messaging.latency_pairwise_private.max_messages_sent_per_power
    return cap is not None and context.sent_count >= cap


def _record_pairwise_delivery(
    message: DiplomacyMessage,
    threads: dict[tuple[str, str], _PairwiseThreadState],
    contexts: dict[str, _PairwisePowerContext],
    settings: Settings,
) -> None:
    sender = message.sender
    recipient = message.recipient
    contexts[sender].add_event(
        f"You sent {recipient} [{message.intent}]: {message.body}",
    )
    contexts[recipient].add_event(
        f"{sender} sent you [{message.intent}]: {message.body}",
    )

    thread = threads[_pairwise_thread_key(sender, recipient)]
    thread.turn_count += 1
    thread.waiting_reply_from = recipient
    if (
        thread.turn_count
        >= settings.messaging.latency_pairwise_private.max_turns_per_conversation
    ):
        thread.closed = True
        thread.waiting_reply_from = None
        contexts[sender].add_event(
            f"Thread with {recipient} closed because the turn limit was reached.",
        )
        contexts[recipient].add_event(
            f"Thread with {sender} closed because the turn limit was reached.",
        )


def _close_pairwise_thread(  # noqa: PLR0913
    *,
    sender: str,
    recipient: str,
    call: _PairwiseCall,
    accepted_sequence: int,
    close_reason: str,
    threads: dict[tuple[str, str], _PairwiseThreadState],
    contexts: dict[str, _PairwisePowerContext],
) -> DiplomacyMessage | None:
    thread = threads[_pairwise_thread_key(sender, recipient)]
    if thread.closed or thread.waiting_reply_from != sender:
        return None

    thread.closed = True
    thread.waiting_reply_from = None
    notice_body = _thread_close_notice_body(
        sender=sender,
        recipient=recipient,
        close_reason=close_reason,
    )
    if close_reason == "message_limit_reached":
        contexts[sender].add_event(
            f"You had no message slot left to respond to {recipient}. Thread closed.",
        )
        contexts[recipient].add_event(
            f"{sender} had no message slot left to respond. Thread closed.",
        )
    else:
        contexts[sender].add_event(
            f"You chose not to respond to {recipient}. Thread closed.",
        )
        contexts[recipient].add_event(
            f"{sender} chose not to respond. Thread closed.",
        )
    return DiplomacyMessage(
        sequence=accepted_sequence,
        phase=call.phase,
        phase_index=call.phase_index,
        message_window=call.call_index,
        sender=sender,
        recipient=recipient,
        intent="other",
        body=notice_body,
        system_event="thread_closed",
        system_reason=close_reason,
    )


def _pairwise_close_reason(
    call: _PairwiseCall,
    *,
    delivered_reply_count: int,
    context: _PairwisePowerContext,
    settings: Settings,
) -> str:
    if _pairwise_sent_cap_reached(context, settings):
        return "message_limit_reached"
    if (
        call.call_kind in {"reply", "revision"}
        and delivered_reply_count
        >= settings.messaging.latency_pairwise_private.max_messages_per_response
    ):
        return "message_limit_reached"
    return "reply_declined"


def _thread_close_notice_body(
    *,
    sender: str,
    recipient: str,
    close_reason: str,
) -> str:
    if close_reason == "message_limit_reached":
        return (
            f"{sender} had no message slot left to respond to {recipient}. "
            "This private thread is closed for the messaging phase."
        )
    return (
        f"{sender} chose not to respond to {recipient}. "
        "This private thread is closed for the messaging phase."
    )


def _active_messaging_powers(active_powers: list[str], settings: Settings) -> list[str]:
    """Return active powers that can participate in the global messaging protocol."""
    return [
        power for power in active_powers if settings.messaging_enabled_for_power(power)
    ]


def _message_windows_enabled(
    settings: Settings,
    snapshots: Mapping[str, PowerPhaseSnapshot],
    *,
    messaging_powers: list[str],
) -> bool:
    """Return whether this phase should run messaging and expose message context."""
    if not snapshots or len(messaging_powers) < 2:
        return False
    first_snapshot = next(iter(snapshots.values()))
    return (
        first_snapshot.phase_type == "M"
        and settings.messaging.latency_pairwise_private.max_messages_per_response > 0
        and settings.messaging.latency_pairwise_private.max_turns_per_conversation > 0
    )


def _renumber_messages(
    messages: list[DiplomacyMessage],
    *,
    start_sequence: int,
) -> tuple[DiplomacyMessage, ...]:
    """Assign deterministic phase-local sequence numbers after simultaneous calls."""
    return tuple(
        replace(message, sequence=start_sequence + index)
        for index, message in enumerate(messages)
    )


def _log_dropped_messages(power: str, result: MessageValidationResult) -> None:
    """Log dropped messages without blocking the phase."""
    if not result.dropped:
        return
    reasons = sorted({drop.reason for drop in result.dropped})
    logger.warning(
        "[%s] Dropped %d invalid message(s): %s",
        power,
        len(result.dropped),
        reasons,
    )


def _result_history_for_phase(
    game: Game,
    phase: str,
) -> dict[str, tuple[str, ...]]:
    """Return normalized notable engine results for one resolved phase."""
    raw_results = game.result_history.get(phase, {})
    return {
        str(unit).lstrip("*"): tuple(str(result) for result in order_results)
        for unit, order_results in raw_results.items()
        if order_results
    }


def _record_game_end(
    game: Game,
    collector: MetricsCollector,
    settings: Settings,
) -> tuple[str | None, dict[str, int]]:
    sc_counts = _supply_center_counts(game, settings)

    winner: str | None = None
    for power_name in settings.powers:
        power = game.get_power(power_name)
        if (
            power is not None
            and not power.is_eliminated()
            and sc_counts.get(power_name, 0) >= settings.win_score
        ):
            winner = power_name
            break

    logger.info("Game over — winner: %s", winner or "none (max years reached)")
    collector.log_game_end(winner=winner, sc_counts=sc_counts)
    return winner, sc_counts


def _supply_center_counts(game: Game, settings: Settings) -> dict[str, int]:
    """Return current supply-center counts for configured powers."""
    all_centers: dict[str, list[str]] = cast(
        "dict[str, list[str]]",
        game.get_centers(),
    )
    return {p: len(all_centers.get(p, [])) for p in settings.powers}


def _unit_occupants(game: Game) -> dict[str, str]:
    all_units: dict[str, list[str]] = cast("dict[str, list[str]]", game.get_units())
    occupants: dict[str, str] = {}
    for power, units in all_units.items():
        for unit in units:
            location = _unit_location(unit)
            if location is not None:
                occupants[location] = power
    return occupants


def _unit_location(unit: object) -> str | None:
    parts = str(unit).lstrip("*").split()
    if len(parts) < 2:
        return None
    return _province_location(parts[1])


def _province_location(location: str) -> str:
    return location.split("/", 1)[0]


def _write_orders_recap(
    orders_log: OrdersLog,
    orders_path: Path,
    power_models: dict[str, str],
    settings: Settings,
    *,
    power_strategies: Mapping[str, StrategyResolution] | None = None,
) -> None:
    """
    Write a human-readable orders recap grouped by power to the given .txt file.

    No timestamps or log levels — just the sequence of decisions each power made
    over the game, one power per section. A fallback is indicated when no LLM
    reasoning was recorded (empty string).

    Args:
        orders_log:   Accumulated orders per power, in phase order.
        orders_path:  Destination file path.
        power_models: Effective power→model mapping used for this run.
        settings:     Explicit game/run settings loaded by the CLI runner.
        power_strategies: Effective strategy assignment metadata by power.

    """
    orders_path.write_text(
        _render_orders_recap(
            orders_log,
            power_models,
            settings,
            power_strategies=power_strategies,
        ),
        encoding="utf-8",
    )


def _render_orders_recap(
    orders_log: OrdersLog,
    power_models: Mapping[str, str],
    settings: Settings,
    *,
    power_strategies: Mapping[str, StrategyResolution] | None = None,
) -> str:
    """Return the human-readable orders recap text."""
    sep: str = "=" * 40
    lines: list[str] = []

    for power_name in settings.powers:
        model: str = power_models.get(power_name, "unknown")
        strategy_label: str = _strategy_label_for_power(power_name, power_strategies)
        lines.append(sep)
        lines.append(f"{power_name} ({model}, {strategy_label})")
        lines.append(sep)
        lines.append("")

        for record in orders_log[power_name]:
            is_fallback: bool = record.reasoning == "" and bool(record.orders)
            tag: str = "  [FALLBACK]" if is_fallback else ""
            lines.append(f"{record.phase}{tag}")

            if record.orders:
                for order in record.orders:
                    lines.append(f"  {order}")
            else:
                lines.append("  (no orders)")

            if record.reasoning:
                lines.append(f"  > {record.reasoning}")

            lines.append("")

        lines.append("")

    return "\n".join(lines)


def _strategy_label_for_power(
    power: str,
    power_strategies: Mapping[str, StrategyResolution] | None,
) -> str:
    """Format one power's strategy assignment for the orders recap."""
    resolution = _strategy_resolution_for_power(power, power_strategies)
    if resolution is None:
        return "strategy=baseline@v1, source=default"

    label = (
        f"strategy={resolution.strategy_name}@{resolution.strategy_version}, "
        f"source={resolution.source}"
    )
    if resolution.matched_model is not None:
        label += f", matched_model={resolution.matched_model}"
    return label
