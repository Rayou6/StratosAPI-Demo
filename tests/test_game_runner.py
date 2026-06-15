import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from diplomacy_llm.config import (
    LatencyPairwisePrivateSettings,
    MessagingSettings,
    Settings,
)
from diplomacy_llm.game_runner import (
    MessagePhaseArtifact,
    MessageWindowArtifact,
    PhaseRecord,
    _run_message_windows,
    _run_phase,
    _write_orders_recap,
)
from diplomacy_llm.messaging import (
    DiplomacyMessage,
    MessageValidationResult,
)
from diplomacy_llm.phase_snapshot import PowerPhaseSnapshot
from diplomacy_llm.strategies import StrategyResolution


def test_orders_recap_includes_strategy_assignment_metadata(tmp_path: Path) -> None:
    settings = Settings(
        default_model="model/default",
        power_models={
            "ENGLAND": "model/planner",
            "FRANCE": "model/baseline",
        },
        map_name="configs/maps/EFGA_11.map",
        max_years=1,
        win_score=9,
    )
    output = tmp_path / "orders.txt"

    _write_orders_recap(
        {
            "ENGLAND": [
                PhaseRecord("S1901M", 0, "M", ("A LON H",), "held"),
            ],
            "FRANCE": [
                PhaseRecord("S1901M", 0, "M", ("A PAR H",), "held"),
            ],
        },
        output,
        {
            "ENGLAND": "model/planner",
            "FRANCE": "model/baseline",
        },
        settings,
        power_strategies={
            "ENGLAND": StrategyResolution(
                strategy_name="baseline",
                strategy_version="v1",
                source="default",
                matched_model=None,
            ),
            "FRANCE": StrategyResolution(
                strategy_name="baseline",
                strategy_version="v1",
                source="default",
                matched_model=None,
            ),
        },
    )

    content = output.read_text(encoding="utf-8")

    assert "ENGLAND (model/planner, strategy=baseline@v1, source=default)" in content
    assert "FRANCE (model/baseline, strategy=baseline@v1, source=default)" in content


def test_latency_pairwise_messages_stream_revise_and_close_threads() -> None:
    settings = Settings(
        default_model="model/default",
        power_models={
            "FRANCE": "model/default",
            "GERMANY": "model/default",
            "ENGLAND": "model/default",
        },
        map_name="configs/maps/EFGA_11.map",
        max_years=1,
        win_score=9,
        messaging_variant="latency_pairwise_private",
        messaging=MessagingSettings(
            enabled=True,
            latency_pairwise_private=LatencyPairwisePrivateSettings(
                max_messages_per_response=3,
                max_turns_per_conversation=4,
            ),
        ),
    )
    snapshots = {
        power: _snapshot(power, phase_type="M", phase_index=0)
        for power in ("FRANCE", "GERMANY", "ENGLAND")
    }
    players = {
        power: _FakePairwisePlayer(power) for power in ("FRANCE", "GERMANY", "ENGLAND")
    }

    artifact = _run_message_windows(
        players,
        settings,
        snapshots,
        messaging_powers=["FRANCE", "GERMANY", "ENGLAND"],
    )

    messages = [message for window in artifact.windows for message in window.accepted]
    bodies = [message.body for message in messages]
    assert "France opens to Germany." in bodies
    assert "France opens to England." in bodies
    assert "Germany revises for France." in bodies
    assert "Germany initial stale draft." not in bodies
    assert any(
        message.system_event == "thread_closed"
        and message.system_reason == "reply_declined"
        and message.body
        == (
            "ENGLAND chose not to respond to FRANCE. "
            "This private thread is closed for the messaging phase."
        )
        for message in messages
    )
    assert any("France replies with England context." in body for body in bodies)
    assert any("REVISE YOUR OUTBOX" in prompt for prompt in players["GERMANY"].prompts)
    assert any(
        "PENDING PRIVATE REPLIES" in prompt or "REVISE YOUR OUTBOX" in prompt
        for prompt in players["FRANCE"].prompts
    )
    assert any(
        "PAIRWISE THREAD STATUS:" in prompt for prompt in players["FRANCE"].prompts
    )
    assert any(
        "Conversation turn limit: 4 delivered message(s) per pairwise thread." in prompt
        for prompt in players["FRANCE"].prompts
    )
    assert any(
        "GERMANY: 0/4 delivered message(s); 4 remaining; no messages delivered yet; you may initiate."
        in prompt
        for prompt in players["FRANCE"].prompts
    )
    assert any(
        "FRANCE: 1/4 delivered message(s); 3 remaining; reply pending from you."
        in prompt
        for prompt in players["GERMANY"].prompts
    )
    assert any(
        "No global per-power message cap is configured." in prompt
        for prompt in players["FRANCE"].prompts
    )


def test_latency_pairwise_close_notice_marks_message_limit() -> None:
    settings = Settings(
        default_model="model/default",
        power_models={
            "FRANCE": "model/default",
            "GERMANY": "model/default",
            "ENGLAND": "model/default",
        },
        map_name="configs/maps/EFGA_11.map",
        max_years=1,
        win_score=9,
        messaging_variant="latency_pairwise_private",
        messaging=MessagingSettings(
            enabled=True,
            latency_pairwise_private=LatencyPairwisePrivateSettings(
                max_messages_per_response=1,
                max_turns_per_conversation=4,
            ),
        ),
    )
    snapshots = {
        power: _snapshot(power, phase_type="M", phase_index=0)
        for power in ("FRANCE", "GERMANY", "ENGLAND")
    }
    players = {
        power: _LimitNoticePairwisePlayer(power)
        for power in ("FRANCE", "GERMANY", "ENGLAND")
    }

    artifact = _run_message_windows(
        players,
        settings,
        snapshots,
        messaging_powers=["FRANCE", "GERMANY", "ENGLAND"],
    )

    messages = [message for window in artifact.windows for message in window.accepted]
    assert any(message.body == "France uses its one reply slot." for message in messages)
    assert any(
        message.sender == "FRANCE"
        and message.recipient == "ENGLAND"
        and message.system_event == "thread_closed"
        and message.system_reason == "message_limit_reached"
        and message.body
        == (
            "FRANCE had no message slot left to respond to ENGLAND. "
            "This private thread is closed for the messaging phase."
        )
        for message in messages
    )


def test_run_phase_skips_messages_outside_movement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        default_model="model/default",
        power_models={
            "FRANCE": "model/default",
            "ENGLAND": "model/default",
        },
        map_name="configs/maps/EFGA_11.map",
        max_years=1,
        win_score=9,
        messaging=MessagingSettings(enabled=True),
    )
    game = _FakePhaseGame(phase_type="R")
    players = {power: _FakePhasePlayer(power) for power in ("FRANCE", "ENGLAND")}
    monkeypatch.setattr(
        "diplomacy_llm.game_runner.build_power_phase_snapshots",
        lambda _game, powers, phase_index: {
            power: _snapshot(power, phase_type="R", phase_index=phase_index)
            for power in powers
        },
    )

    _run_phase(game, players, settings, phase_index=1)

    assert players["FRANCE"].message_calls == []
    assert players["ENGLAND"].message_calls == []
    assert players["FRANCE"].order_visible_bodies is None
    assert players["ENGLAND"].order_visible_bodies is None


class _FakePhaseGame:
    def __init__(self, *, phase_type: str) -> None:
        self.phase_type = phase_type
        self.result_history: dict[str, dict[str, list[str]]] = {"S1901R": {}}
        self.processed = False
        self.orders: dict[str, list[str]] = {}
        self.waits: dict[str, bool] = {}

    def get_power(self, power_name: str) -> object:
        _ = power_name
        return SimpleNamespace(is_eliminated=lambda: False)

    def get_current_phase(self) -> str:
        return "S1901R"

    def set_orders(self, power_name: str, orders: list[str]) -> None:
        self.orders[power_name] = orders

    def set_wait(self, power_name: str, wait: bool) -> None:  # noqa: FBT001
        self.waits[power_name] = wait

    def process(self) -> None:
        self.processed = True


class _FakePhasePlayer:
    def __init__(self, power_name: str) -> None:
        self.power_name = power_name
        self.message_calls: list[dict[str, object]] = []
        self.order_visible_bodies: list[str] | None = None

    def get_messages(
        self,
        snapshot: PowerPhaseSnapshot,
        *,
        message_window: int,
        visible_messages: tuple[DiplomacyMessage, ...],
        eligible_recipient_powers: list[str],
        start_sequence: int = 0,
    ) -> MessageValidationResult:
        _ = snapshot, eligible_recipient_powers, start_sequence
        self.message_calls.append(
            {
                "window": message_window,
                "visible_bodies": [message.body for message in visible_messages],
            }
        )
        return MessageValidationResult(accepted=(), dropped=())

    def get_orders(
        self,
        snapshot: PowerPhaseSnapshot,
        *,
        visible_messages: tuple[DiplomacyMessage, ...] | None = None,
    ) -> tuple[list[str], str]:
        _ = snapshot
        self.order_visible_bodies = (
            None
            if visible_messages is None
            else [message.body for message in visible_messages]
        )
        return [], f"{self.power_name} orders"


class _FakePairwisePlayer:
    def __init__(self, power_name: str) -> None:
        self.power_name = power_name
        self.prompts: list[str] = []

    def get_messages_with_prompt(
        self,
        snapshot: PowerPhaseSnapshot,
        *,
        prompt: str,
        message_window: int,
        eligible_recipient_powers: tuple[str, ...],
        start_sequence: int = 0,
    ) -> MessageValidationResult:
        _ = snapshot, start_sequence
        self.prompts.append(prompt)
        if "INITIAL DIPLOMATIC OUTBOX" in prompt:
            return self._initial_response(message_window)
        if "REVISE YOUR OUTBOX" in prompt:
            return self._revision_response(message_window, eligible_recipient_powers)
        if "PENDING PRIVATE REPLIES" in prompt:
            return self._reply_response(message_window, eligible_recipient_powers)
        return MessageValidationResult(accepted=(), dropped=())

    def _initial_response(self, message_window: int) -> MessageValidationResult:
        if self.power_name == "FRANCE":
            return MessageValidationResult(
                accepted=(
                    _message(
                        sequence=0,
                        message_window=message_window,
                        sender="FRANCE",
                        recipient="GERMANY",
                        body="France opens to Germany.",
                    ),
                    _message(
                        sequence=1,
                        message_window=message_window,
                        sender="FRANCE",
                        recipient="ENGLAND",
                        body="France opens to England.",
                    ),
                ),
                dropped=(),
            )
        if self.power_name == "GERMANY":
            time.sleep(0.03)
            return MessageValidationResult(
                accepted=(
                    _message(
                        sequence=0,
                        message_window=message_window,
                        sender="GERMANY",
                        recipient="FRANCE",
                        body="Germany initial stale draft.",
                    ),
                ),
                dropped=(),
            )
        time.sleep(0.06)
        return MessageValidationResult(accepted=(), dropped=())

    def _revision_response(
        self,
        message_window: int,
        eligible_recipient_powers: tuple[str, ...],
    ) -> MessageValidationResult:
        if self.power_name == "GERMANY" and "FRANCE" in eligible_recipient_powers:
            return MessageValidationResult(
                accepted=(
                    _message(
                        sequence=0,
                        message_window=message_window,
                        sender="GERMANY",
                        recipient="FRANCE",
                        body="Germany revises for France.",
                    ),
                ),
                dropped=(),
            )
        if self.power_name == "FRANCE" and "GERMANY" in eligible_recipient_powers:
            return MessageValidationResult(
                accepted=(
                    _message(
                        sequence=0,
                        message_window=message_window,
                        sender="FRANCE",
                        recipient="GERMANY",
                        body="France replies with England context.",
                    ),
                ),
                dropped=(),
            )
        return MessageValidationResult(accepted=(), dropped=())

    def _reply_response(
        self,
        message_window: int,
        eligible_recipient_powers: tuple[str, ...],
    ) -> MessageValidationResult:
        if self.power_name == "FRANCE" and "GERMANY" in eligible_recipient_powers:
            return MessageValidationResult(
                accepted=(
                    _message(
                        sequence=0,
                        message_window=message_window,
                        sender="FRANCE",
                        recipient="GERMANY",
                        body="France replies with England context.",
                    ),
                ),
                dropped=(),
            )
        return MessageValidationResult(accepted=(), dropped=())


class _LimitNoticePairwisePlayer:
    def __init__(self, power_name: str) -> None:
        self.power_name = power_name

    def get_messages_with_prompt(
        self,
        snapshot: PowerPhaseSnapshot,
        *,
        prompt: str,
        message_window: int,
        eligible_recipient_powers: tuple[str, ...],
        start_sequence: int = 0,
    ) -> MessageValidationResult:
        _ = snapshot, start_sequence
        if "INITIAL DIPLOMATIC OUTBOX" in prompt:
            return self._initial_response(message_window)
        if (
            self.power_name == "FRANCE"
            and (
                "PENDING PRIVATE REPLIES" in prompt
                or "REVISE YOUR OUTBOX" in prompt
            )
            and "GERMANY" in eligible_recipient_powers
        ):
            return MessageValidationResult(
                accepted=(
                    _message(
                        sequence=0,
                        message_window=message_window,
                        sender="FRANCE",
                        recipient="GERMANY",
                        body="France uses its one reply slot.",
                    ),
                ),
                dropped=(),
            )
        return MessageValidationResult(accepted=(), dropped=())

    def _initial_response(self, message_window: int) -> MessageValidationResult:
        if self.power_name == "FRANCE":
            time.sleep(0.05)
            return MessageValidationResult(accepted=(), dropped=())
        return MessageValidationResult(
            accepted=(
                _message(
                    sequence=0,
                    message_window=message_window,
                    sender=self.power_name,
                    recipient="FRANCE",
                    body=f"{self.power_name} asks France for a reply.",
                ),
            ),
            dropped=(),
        )


def _snapshot(
    power_name: str,
    *,
    phase_type: str,
    phase_index: int,
) -> PowerPhaseSnapshot:
    return PowerPhaseSnapshot(
        power_name=power_name,
        phase="S1901M" if phase_type == "M" else "S1901R",
        phase_type=phase_type,
        own_units=(),
        own_centers=(),
        all_units={"FRANCE": (), "ENGLAND": (), "GERMANY": ()},
        all_centers={"FRANCE": (), "ENGLAND": (), "GERMANY": ()},
        last_phase=None,
        last_phase_results={},
        orderable_locations=(),
        possible_orders={},
        phase_index=phase_index,
    )


def _message(
    *,
    sequence: int,
    message_window: int,
    sender: str,
    recipient: str,
    body: str,
) -> DiplomacyMessage:
    return DiplomacyMessage(
        sequence=sequence,
        phase="S1901M",
        phase_index=3,
        message_window=message_window,
        sender=sender,
        recipient=recipient,
        intent="coordinate",
        body=body,
    )


def _message_phase_artifact(
    *,
    accepted: tuple[DiplomacyMessage, ...] = (),
) -> MessagePhaseArtifact:
    return MessagePhaseArtifact(
        phase="S1901M",
        phase_index=0,
        phase_type="M",
        enabled_powers=("FRANCE", "ENGLAND"),
        windows=(
            MessageWindowArtifact(
                message_window=1,
                accepted=accepted,
                dropped=(),
            ),
        ),
    )
