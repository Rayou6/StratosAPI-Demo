from __future__ import annotations

import json
import random
import threading
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from diplomacy import Game
from openrouter import OpenRouter

from demo_app.labels import (
    demo_mode_label,
    demo_run_label,
    demo_run_name,
    demo_setup_label,
)
from diplomacy_llm.config import PROJECT_ROOT, Settings, validate_safe_artifact_name
from diplomacy_llm.demo_paths import (
    DEMO_DATA_DIR,
    DEMO_SETUPS_DIR,
    DemoRunPaths,
    available_demo_setup_names,
    demo_run_paths,
    demo_setup_path_for,
    load_demo_settings,
)
from diplomacy_llm.game_runner import (
    GameRunObserver,
    MessagePhaseArtifact,
    MessageWindowArtifact,
    run_game,
)
from diplomacy_llm.llm_player import LLMProviderCriticalError
from diplomacy_llm.metrics_collector import MetricsCollector
from diplomacy_llm.saved_games import export_saved_game
from diplomacy_llm.strategies import StrategyResolution, resolve_power_strategies

DemoRunMode = Literal["live"]

DEMO_RUN_SCHEMA = "stratosapi-demo-run-v1"
OPENROUTER_NO_CREDITS_ERROR_TYPE = "openrouter_no_credits"
OPENROUTER_NO_CREDITS_REASON = "payment_required_or_insufficient_credits"
OPENROUTER_NO_CREDITS_MESSAGE = (
    "No OpenRouter credits are available. Live runs cannot continue or start "
    "right now. Open the Replay tab to inspect existing runs."
)
_MODEL_ASSIGNMENT_SEED_LIMIT = 2**32
_FINAL_STATUSES = {"done", "error"}


@dataclass(frozen=True)
class _ModelAssignment:
    effective_models: dict[str, str]
    policy: str
    seed: int | None


class DemoLiveRunError(ValueError):
    """Raised when a live demo run request is invalid."""


class DemoLiveRunNotFoundError(FileNotFoundError):
    """Raised when a live demo run is not known by this server process."""


class DemoLiveRunState:
    """Thread-safe process-local state for one local demo run."""

    def __init__(
        self,
        *,
        run_id: str,
        setup_name: str,
        mode: DemoRunMode,
        paths: DemoRunPaths,
        metadata: Mapping[str, Any],
    ) -> None:
        self.run_id = run_id
        self.setup_name = setup_name
        self.mode = mode
        self.paths = paths
        self._metadata: dict[str, Any] = dict(metadata)
        self._events: list[dict[str, object]] = []
        self._condition = threading.Condition()
        self.thread: threading.Thread | None = None

    def write_initial_files(self) -> None:
        """Create the run directory, metadata file, and empty event log."""
        self.paths.run_dir.mkdir(parents=True, exist_ok=False)
        self._write_metadata_unlocked()
        self.paths.events_path.write_text("", encoding="utf-8")

    def snapshot(self) -> dict[str, object]:
        """Return public run state without secrets or thread internals."""
        with self._condition:
            return {
                "run_id": self.run_id,
                "setup": self.setup_name,
                "mode": self.mode,
                "status": self._metadata.get("status", "waiting"),
                "created_at": self._metadata.get("created_at"),
                "finished_at": self._metadata.get("finished_at"),
                "error": self._metadata.get("error"),
                "event_count": len(self._events),
                "replay_ready": self.paths.game_path.exists(),
            }

    def metadata_snapshot(self) -> dict[str, Any]:
        """Return a public copy of metadata.json content."""
        with self._condition:
            return dict(self._metadata)

    def is_active(self) -> bool:
        """Return whether this process still considers the run in progress."""
        with self._condition:
            return self._metadata.get("status") not in _FINAL_STATUSES

    def update_metadata(self, **updates: object) -> None:
        """Update metadata.json and wake any listeners."""
        with self._condition:
            self._metadata.update(updates)
            self._write_metadata_unlocked()
            self._condition.notify_all()

    def append_event(
        self,
        event_type: str,
        payload: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Append one event to process state and events.jsonl."""
        payload = {} if payload is None else dict(payload)
        with self._condition:
            event = {
                **payload,
                "type": event_type,
                "sequence": len(self._events),
            }
            self._events.append(event)
            with self.paths.events_path.open("a", encoding="utf-8") as events_file:
                events_file.write(json.dumps(event, separators=(",", ":")) + "\n")
            self._condition.notify_all()
            return dict(event)

    def mark_error(self, error: Mapping[str, str]) -> None:
        """Persist and stream a sanitized run error."""
        finished_at = _utc_now()
        self.update_metadata(status="error", finished_at=finished_at, error=dict(error))
        self.append_event(
            "run_error",
            {
                "phase": None,
                "phase_index": None,
                "status": "error",
                "error": error["message"],
                "error_type": error["type"],
            },
        )

    def iter_events(self, *, after_sequence: int = -1) -> Iterator[dict[str, object]]:
        """Yield existing and future events until the run reaches a final status."""
        next_index = max(0, after_sequence + 1)
        while True:
            with self._condition:
                while (
                    next_index >= len(self._events)
                    and self._metadata.get("status") not in _FINAL_STATUSES
                ):
                    self._condition.wait(timeout=15)

                if next_index < len(self._events):
                    event = dict(self._events[next_index])
                    next_index += 1
                else:
                    return
            yield event

    def _write_metadata_unlocked(self) -> None:
        self.paths.metadata_path.write_text(
            json.dumps(self._metadata, indent=2),
            encoding="utf-8",
        )


class DemoLiveRunManager:
    """Owns local background demo runs for the HTTP server process."""

    def __init__(
        self,
        *,
        data_dir: Path = DEMO_DATA_DIR,
        setups_dir: Path = DEMO_SETUPS_DIR,
    ) -> None:
        self.data_dir = data_dir
        self.setups_dir = setups_dir
        self._runs: dict[str, DemoLiveRunState] = {}
        self._live_provider_error: dict[str, str] | None = None
        self._lock = threading.Lock()

    def list_setups(self) -> list[dict[str, str]]:
        """Return launchable demo setups from configs/demo_setups/ only."""
        return [
            {"name": name, "label": demo_setup_label(name)}
            for name in available_demo_setup_names(self.setups_dir)
        ]

    def start_run(
        self,
        *,
        setup_name: str,
        mode: str,
        openrouter_api_key: str | None,
    ) -> DemoLiveRunState:
        """Create a run under demo_data and start it in a background thread."""
        mode_name = _normalize_mode(mode)
        self._raise_if_active_run()
        self.raise_if_live_provider_unavailable()
        if not (openrouter_api_key or "").strip():
            msg = "OpenRouter API key is required for live demo runs"
            raise DemoLiveRunError(msg)

        settings = load_demo_settings(setup_name, setups_dir=self.setups_dir)
        safe_setup_name = validate_safe_artifact_name(
            setup_name,
            label="demo config name",
        )
        assignment = _resolve_demo_model_assignment(settings)
        effective_strategies = resolve_power_strategies(
            assignment.effective_models,
            settings.strategy_assignment,
        )
        run_id = self._next_run_id(safe_setup_name, mode_name)
        paths = demo_run_paths(run_id, data_dir=self.data_dir)
        metadata = _build_metadata(
            run_id=run_id,
            setup_name=safe_setup_name,
            setup_path=demo_setup_path_for(safe_setup_name, self.setups_dir),
            mode=mode_name,
            settings=settings,
            assignment=assignment,
            effective_strategies=effective_strategies,
        )
        state = DemoLiveRunState(
            run_id=run_id,
            setup_name=safe_setup_name,
            mode=mode_name,
            paths=paths,
            metadata=metadata,
        )
        state.write_initial_files()
        with self._lock:
            self._runs[run_id] = state

        api_key = (openrouter_api_key or "").strip()
        thread = threading.Thread(
            target=self._run_background,
            args=(state, settings, assignment, effective_strategies, api_key),
            name=f"demo-live-{run_id}",
            daemon=True,
        )
        state.thread = thread
        thread.start()
        return state

    def get_run(self, run_id: str) -> DemoLiveRunState:
        """Return a process-local live run by safe run id."""
        safe_run_id = validate_safe_artifact_name(run_id, label="demo run_id")
        with self._lock:
            state = self._runs.get(safe_run_id)
        if state is None:
            msg = f"Live demo run not found: {safe_run_id}"
            raise DemoLiveRunNotFoundError(msg)
        return state

    def active_run_snapshot(self) -> dict[str, object] | None:
        """Return the active run for this server process, if any."""
        with self._lock:
            state = self._active_run_unlocked()
        if state is None:
            return None
        return state.snapshot()

    def active_run_listing(self) -> dict[str, object] | None:
        """Return an /api/runs-style listing entry for the active run."""
        with self._lock:
            state = self._active_run_unlocked()
        if state is None:
            return None

        metadata = state.metadata_snapshot()
        snapshot = state.snapshot()
        return {
            "run_id": state.run_id,
            "label": demo_run_label(state.run_id, metadata),
            "metadata": metadata,
            "has_events": state.paths.events_path.exists(),
            "has_boards": state.paths.boards_dir.exists(),
            "is_active_live": True,
            "live_status": snapshot.get("status"),
            "replay_ready": snapshot.get("replay_ready"),
        }

    def live_provider_snapshot(self) -> dict[str, str]:
        """Return the current live-provider launch state for the local server."""
        with self._lock:
            provider_error = (
                None
                if self._live_provider_error is None
                else dict(self._live_provider_error)
            )
        if provider_error is None:
            return {"status": "available"}
        return {
            "status": "credit_exhausted",
            "error_type": provider_error["type"],
            "message": provider_error["message"],
        }

    def raise_if_live_provider_unavailable(self) -> None:
        """Reject new live runs after a known provider credit exhaustion."""
        with self._lock:
            provider_error = self._live_provider_error
        if provider_error is None:
            return
        raise DemoLiveRunError(provider_error["message"])

    def mark_live_provider_credit_exhausted(self) -> None:
        """Remember that live OpenRouter calls cannot currently be made."""
        self._set_live_provider_error(
            {
                "type": OPENROUTER_NO_CREDITS_ERROR_TYPE,
                "message": OPENROUTER_NO_CREDITS_MESSAGE,
            },
        )

    def _raise_if_active_run(self) -> None:
        with self._lock:
            active = self._active_run_unlocked()
        if active is None:
            return
        active_label = demo_run_label(active.run_id, active.metadata_snapshot())
        msg = (
            f"A demo run is already in progress: {active_label}. "
            "Open the Replay tab and select the active run to return to its live view, "
            "or wait for it to finish."
        )
        raise DemoLiveRunError(msg)

    def _active_run_unlocked(self) -> DemoLiveRunState | None:
        for state in reversed(list(self._runs.values())):
            if state.is_active():
                return state
        return None

    def _set_live_provider_error(self, error: Mapping[str, str]) -> None:
        with self._lock:
            self._live_provider_error = dict(error)

    def _next_run_id(self, setup_name: str, mode: DemoRunMode) -> str:
        _ = mode
        prefix = "demo_live"
        stamp = datetime.now(UTC).strftime("%d%m_%H%M%S")
        base = f"{prefix}_{setup_name}_{stamp}"
        for suffix in ["", *[f"_{index}" for index in range(2, 1000)]]:
            run_id = f"{base}{suffix}"
            if not demo_run_paths(run_id, data_dir=self.data_dir).run_dir.exists():
                return run_id
        msg = f"Could not allocate a demo run id for setup: {setup_name}"
        raise DemoLiveRunError(msg)

    def _run_background(
        self,
        state: DemoLiveRunState,
        settings: Settings,
        assignment: _ModelAssignment,
        effective_strategies: Mapping[str, StrategyResolution],
        openrouter_api_key: str | None,
    ) -> None:
        try:
            state.update_metadata(status="running")
            collector = MetricsCollector(
                run_id=state.run_id,
                metrics_dir=state.paths.run_dir,
            )
            client = OpenRouter(api_key=openrouter_api_key or "")
            observer = _DemoEventObserver(state)
            game = run_game(
                client,
                collector=collector,
                settings=settings,
                power_models=assignment.effective_models,
                power_strategies=effective_strategies,
                orders_path=None,
                messages_path=None,
                observer=observer,
            )
            _save_game_snapshot(game, state.paths.game_path)
            if state.snapshot()["status"] != "done":
                state.update_metadata(status="done", finished_at=_utc_now())
        except Exception as exc:  # noqa: BLE001
            safe_error = _safe_error(exc, secret=openrouter_api_key)
            if _is_openrouter_no_credits_error(exc):
                self._set_live_provider_error(safe_error)
            _report_background_error(state.run_id, safe_error)
            state.mark_error(safe_error)


class _DemoEventObserver(GameRunObserver):
    """Translate engine callbacks into Demo Live JSONL events."""

    def __init__(self, state: DemoLiveRunState) -> None:
        self._state = state

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
        _ = settings, power_models, power_strategies
        _save_game_snapshot(game, self._state.paths.game_path)
        self._state.update_metadata(initial_scores=dict(initial_sc_counts))
        self._state.append_event(
            "run_started",
            {
                "phase": game.get_current_phase(),
                "phase_index": 0,
                "status": "running",
                "scores": dict(initial_sc_counts),
                "calendar_year": calendar_year,
            },
        )

    def on_phase_started(
        self,
        *,
        game: Game,
        phase: str,
        phase_index: int,
        phase_type: str,
    ) -> None:
        _save_game_snapshot(game, self._state.paths.game_path)
        self._state.update_metadata(status="thinking")
        self._state.append_event(
            "phase_started",
            {
                "phase": phase,
                "phase_index": phase_index,
                "phase_type": phase_type,
                "status": "thinking",
            },
        )

    def on_messages_generated(self, phase_messages: MessagePhaseArtifact) -> None:
        _ = phase_messages

    def on_message_window_delivered(
        self,
        *,
        phase: str,
        phase_index: int,
        phase_type: str,
        window: MessageWindowArtifact,
    ) -> None:
        self._state.update_metadata(status="running")
        for message in window.accepted:
            payload = message.to_dict()
            payload["phase"] = phase
            payload["phase_index"] = phase_index
            payload["phase_type"] = phase_type
            payload["status"] = "running"
            self._state.append_event("message_sent", payload)

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
        submitted_orders = list(orders)
        if not submitted_orders:
            return

        self._state.update_metadata(status="running")
        self._state.append_event(
            "orders_submitted",
            {
                "phase": phase,
                "phase_index": phase_index,
                "phase_type": phase_type,
                "power": power,
                "orders": submitted_orders,
                "is_fallback": is_fallback,
                "status": "running",
            },
        )
        if reasoning:
            self._state.append_event(
                "reasoning_available",
                {
                    "phase": phase,
                    "phase_index": phase_index,
                    "phase_type": phase_type,
                    "power": power,
                    "reasoning": reasoning,
                    "is_fallback": is_fallback,
                    "status": "running",
                },
            )

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
        _ = phase_index, phase_type
        _save_game_snapshot(game, self._state.paths.game_path)
        self._state.update_metadata(status="running")
        self._state.append_event(
            "phase_resolved",
            {
                "phase": next_phase,
                "phase_index": next_phase_index,
                "phase_type": _phase_type(next_phase),
                "status": "running",
                "resolved_phase": phase,
                "results": _result_lines(result_history),
                "scores": dict(sc_counts),
            },
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
        self._state.append_event(
            "year_summary",
            {
                "phase": phase,
                "phase_index": phase_index,
                "status": "running",
                "year": year,
                "calendar_year": calendar_year,
                "scores": dict(sc_counts),
            },
        )

    def on_game_finished(
        self,
        *,
        game: Game,
        phase: str,
        phase_index: int,
        winner: str | None,
        sc_counts: Mapping[str, int],
    ) -> None:
        _save_game_snapshot(game, self._state.paths.game_path)
        final_status = "winner" if winner is not None else "completed_no_winner"
        self._state.update_metadata(
            status="done",
            finished_at=_utc_now(),
            final_status=final_status,
            winner=winner,
            final_scores=dict(sc_counts),
        )
        self._state.append_event(
            "game_finished",
            {
                "phase": phase,
                "phase_index": phase_index,
                "status": final_status,
                "winner": winner,
                "scores": dict(sc_counts),
            },
        )


def _resolve_demo_model_assignment(settings: Settings) -> _ModelAssignment:
    configured_models = dict(settings.power_models)
    if not settings.shuffle_models:
        return _ModelAssignment(
            effective_models=configured_models,
            policy="configured_order",
            seed=None,
        )

    seed = (
        settings.model_assignment_seed
        if settings.model_assignment_seed is not None
        else random.SystemRandom().randrange(_MODEL_ASSIGNMENT_SEED_LIMIT)
    )
    model_list = list(configured_models.values())
    random.Random(seed).shuffle(model_list)
    return _ModelAssignment(
        effective_models=dict(zip(settings.powers, model_list, strict=False)),
        policy="seeded_shuffle",
        seed=seed,
    )


def _build_metadata(  # noqa: PLR0913
    *,
    run_id: str,
    setup_name: str,
    setup_path: Path,
    mode: DemoRunMode,
    settings: Settings,
    assignment: _ModelAssignment,
    effective_strategies: Mapping[str, StrategyResolution],
) -> dict[str, object]:
    created_at = _utc_now()
    setup_label = demo_setup_label(setup_name)
    mode_label = demo_mode_label(mode)
    return {
        "schema": DEMO_RUN_SCHEMA,
        "run_id": run_id,
        "name": demo_run_name(setup_label, mode_label, created_at),
        "run_mode": mode,
        "status": "waiting",
        "created_at": created_at,
        "setup": setup_name,
        "setup_name": setup_name,
        "setup_label": setup_label,
        "setup_path": _project_relative(setup_path),
        "map": settings.map_name,
        "map_name": settings.map_name,
        "powers": settings.powers,
        "power_models": dict(settings.power_models),
        "effective_power_models": dict(assignment.effective_models),
        "model_assignment_policy": assignment.policy,
        "model_assignment_seed": assignment.seed,
        "strategies": {
            power: {
                "name": resolution.strategy_name,
                "version": resolution.strategy_version,
                "source": resolution.source,
                "matched_model": resolution.matched_model,
            }
            for power, resolution in effective_strategies.items()
        },
        "max_years": settings.max_years,
        "win_score": settings.win_score,
        "total_scs": settings.total_scs,
        "messaging": {
            "enabled": bool(settings.messaging_enabled_powers),
            "variant": settings.messaging_variant,
            "enabled_powers": settings.messaging_enabled_powers,
        },
    }


def _normalize_mode(mode: str) -> DemoRunMode:
    if mode == "live":
        return mode  # type: ignore[return-value]
    msg = "Demo run mode must be 'live'"
    raise DemoLiveRunError(msg)


def _save_game_snapshot(game: Game, game_path: Path) -> None:
    export_saved_game(game, game_path, output_mode="w")


def _safe_error(error: BaseException, *, secret: str | None) -> dict[str, str]:
    if _is_openrouter_no_credits_error(error):
        return {
            "type": OPENROUTER_NO_CREDITS_ERROR_TYPE,
            "message": OPENROUTER_NO_CREDITS_MESSAGE,
        }

    message = str(error) or error.__class__.__name__
    if secret:
        message = message.replace(secret, "[redacted]")
    return {
        "type": error.__class__.__name__,
        "message": message,
    }


def _is_openrouter_no_credits_error(error: BaseException) -> bool:
    return (
        isinstance(error, LLMProviderCriticalError)
        and error.reason == OPENROUTER_NO_CREDITS_REASON
    )


def _report_background_error(run_id: str, error: Mapping[str, str]) -> None:
    print(f"Demo Live run {run_id} stopped: {error['message']}")


def _result_lines(result_history: Mapping[str, Sequence[str]]) -> list[str]:
    return [
        f"{unit}: {', '.join(str(result) for result in results)}"
        for unit, results in sorted(result_history.items())
    ]


def _phase_type(phase: str) -> str | None:
    if phase.endswith(("M", "R", "A")):
        return phase[-1]
    return None


def _project_relative(path: Path) -> str:
    resolved_root = PROJECT_ROOT.resolve()
    resolved_path = path.resolve()
    try:
        return resolved_path.relative_to(resolved_root).as_posix()
    except ValueError:
        return str(resolved_path)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
