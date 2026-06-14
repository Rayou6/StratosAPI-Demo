import json
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any

import pytest

from diplomacy_llm import llm_player
from diplomacy_llm.config import MessagingSettings, Settings
from diplomacy_llm.llm_player import LLMPlayer, LLMProviderCriticalError
from diplomacy_llm.messaging import DiplomacyMessage
from diplomacy_llm.messaging.schemas import MESSAGES_RESPONSE_SCHEMA
from diplomacy_llm.metrics_collector import MetricsCollector
from diplomacy_llm.phase_snapshot import PowerPhaseSnapshot
from diplomacy_llm.prompt_templates import get_order_prompt_template
from diplomacy_llm.response_schemas import ORDERS_RESPONSE_SCHEMA
from diplomacy_llm.strategies import (
    StrategyContext,
    StrategyResolution,
    StrategyRuntimeContext,
)
from diplomacy_llm.strategies.protocols import (
    BaseStrategyProtocol,
    get_strategy_protocol,
)


class FakeChat:
    def __init__(self, responses: list[str], routed_model: str | None = None) -> None:
        self._responses = responses
        self._routed_model = routed_model
        self.calls: list[list[dict[str, str]]] = []
        self.response_formats: list[dict[str, Any]] = []
        self.request_options: list[dict[str, object]] = []

    def send(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        response_format: dict[str, Any],
        **request_options: object,
    ) -> Any:
        _ = max_tokens, response_format
        self.calls.append(messages)
        self.response_formats.append(response_format)
        self.request_options.append(dict(request_options))
        content = self._responses.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            model=self._routed_model or model,
            usage=SimpleNamespace(total_tokens=7, prompt_tokens_details=None),
        )


class FakeClient:
    def __init__(
        self,
        responses: list[str] | None = None,
        routed_model: str | None = None,
    ) -> None:
        self.chat = FakeChat(responses or [], routed_model=routed_model)


class RaisingChat:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def send(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        response_format: dict[str, Any],
        **request_options: object,
    ) -> Any:
        _ = model, messages, max_tokens, response_format, request_options
        raise self.error


class RaisingClient:
    def __init__(self, error: Exception) -> None:
        self.chat = RaisingChat(error)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        default_model="fake/model",
        power_models={"FRANCE": "fake/model"},
        map_name="configs/maps/EFG_9.map",
        max_years=1,
        win_score=9,
        max_tokens=256,
        max_retries=1,
        retry_delay=0,
    )


def make_player(
    tmp_path: Path,
    settings: Settings,
    responses: list[str] | None = None,
) -> LLMPlayer:
    return LLMPlayer(
        power_name="FRANCE",
        model="fake/model",
        client=FakeClient(responses),
        collector=MetricsCollector(run_id="test", metrics_dir=tmp_path),
        settings=settings,
    )


def test_get_orders_sends_only_configured_generation_controls(
    tmp_path: Path,
    settings: Settings,
) -> None:
    class CapturingChat:
        def __init__(self) -> None:
            self.kwargs: list[dict[str, Any]] = []

        def send(self, **kwargs: Any) -> Any:
            self.kwargs.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=response(["A PAR H"]))
                    )
                ],
                model=kwargs["model"],
                usage=SimpleNamespace(total_tokens=7, prompt_tokens_details=None),
            )

    chat = CapturingChat()
    client = SimpleNamespace(chat=chat)
    tuned_settings = settings.model_copy(update={"llm_seed": 1001})
    player = LLMPlayer(
        power_name="FRANCE",
        model="fake/model",
        client=client,
        collector=MetricsCollector(run_id="test", metrics_dir=tmp_path),
        settings=tuned_settings,
    )

    player.get_orders(make_snapshot())

    request_kwargs = chat.kwargs[0]
    assert request_kwargs["seed"] == 1001
    assert "temperature" not in request_kwargs
    assert "top_p" not in request_kwargs


def test_get_orders_sends_temperature_and_top_p_when_configured(
    tmp_path: Path,
    settings: Settings,
) -> None:
    client = FakeClient([response(["A PAR H"])])
    tuned_settings = settings.model_copy(
        update={
            "llm_seed": 1001,
            "temperature": 0.7,
            "top_p": 0.9,
        },
    )
    player = LLMPlayer(
        power_name="FRANCE",
        model="fake/model",
        client=client,
        collector=MetricsCollector(run_id="test", metrics_dir=tmp_path),
        settings=tuned_settings,
    )

    player.get_orders(make_snapshot())

    assert client.chat.request_options == [
        {
            "seed": 1001,
            "temperature": 0.7,
            "top_p": 0.9,
        },
    ]


def make_snapshot(  # noqa: PLR0913
    *,
    phase: str = "S1901M",
    phase_type: str = "M",
    own_units: tuple[str, ...] = ("A PAR",),
    own_centers: tuple[str, ...] = ("PAR",),
    orderable_locations: tuple[str, ...] = ("PAR",),
    possible_orders: dict[str, tuple[str, ...]] | None = None,
    adjustment_build_count: int = 0,
    adjustment_disband_count: int = 0,
) -> PowerPhaseSnapshot:
    possible = possible_orders or {"PAR": ("A PAR H", "A PAR - BUR")}
    return PowerPhaseSnapshot(
        power_name="FRANCE",
        phase=phase,
        phase_type=phase_type,
        own_units=own_units,
        own_centers=own_centers,
        all_units=MappingProxyType({"FRANCE": own_units}),
        all_centers=MappingProxyType({"FRANCE": own_centers}),
        last_phase=None,
        last_phase_results=MappingProxyType({}),
        orderable_locations=orderable_locations,
        possible_orders=MappingProxyType(possible),
        adjustment_build_count=adjustment_build_count,
        adjustment_disband_count=adjustment_disband_count,
    )


def response(orders: list[str], reasoning: str = "ok") -> str:
    return json.dumps({"orders": orders, "reasoning": reasoning})


def message_response(messages: list[dict[str, object]]) -> str:
    return json.dumps({"messages": messages})


class CustomStrategyProtocol(BaseStrategyProtocol):
    """Tiny test protocol proving LLMPlayer uses the strategy boundary."""

    name = "custom_strategy"
    version = "test"

    def build_context(self, snapshot: PowerPhaseSnapshot) -> StrategyContext | None:
        _ = snapshot
        return None

    def render_prompt_section(
        self,
        runtime_context: StrategyRuntimeContext,
    ) -> str | None:
        snapshot = runtime_context.snapshot
        return f"Custom strategy section for {snapshot.power_name} {snapshot.phase}"


@pytest.mark.parametrize(
    ("snapshot", "orders", "expected"),
    [
        (
            make_snapshot(),
            ["A PAR - BUR"],
            ["A PAR - BUR"],
        ),
        (
            make_snapshot(
                phase="S1901R",
                phase_type="R",
                own_units=("*A PAR",),
                orderable_locations=("PAR",),
                possible_orders={"PAR": ("A PAR R BUR", "A PAR D")},
            ),
            ["A PAR R BUR"],
            ["A PAR R BUR"],
        ),
        (
            make_snapshot(
                phase="W1901A",
                phase_type="A",
                own_units=(),
                orderable_locations=("PAR",),
                possible_orders={"PAR": ("A PAR B", "WAIVE")},
                adjustment_build_count=1,
            ),
            ["A PAR B"],
            ["A PAR B"],
        ),
    ],
)
def test_validate_orders_accepts_whitelist_orders_for_all_phase_types(
    tmp_path: Path,
    settings: Settings,
    snapshot: PowerPhaseSnapshot,
    orders: list[str],
    expected: list[str],
) -> None:
    player = make_player(tmp_path, settings)

    validated, invalid_count = player._validate_orders(snapshot, orders)

    assert validated == expected
    assert invalid_count == 0


def test_validate_orders_replaces_invalid_movement_order_with_legal_hold(
    tmp_path: Path,
    settings: Settings,
) -> None:
    player = make_player(tmp_path, settings)
    snapshot = make_snapshot()

    validated, invalid_count = player._validate_orders(
        snapshot,
        ["A PAR - PIC"],
    )

    assert validated == ["A PAR H"]
    assert invalid_count == 1


@pytest.mark.parametrize(
    ("snapshot", "expected"),
    [
        (
            make_snapshot(
                phase="S1901R",
                phase_type="R",
                own_units=("*A PAR",),
                orderable_locations=("PAR",),
                possible_orders={"PAR": ("A PAR R BUR", "A PAR D")},
            ),
            ["A PAR R BUR"],
        ),
        (
            make_snapshot(
                phase="W1901A",
                phase_type="A",
                own_units=("A MAR",),
                orderable_locations=("MAR",),
                possible_orders={"MAR": ("A MAR D",)},
                adjustment_disband_count=1,
            ),
            ["A MAR D"],
        ),
    ],
)
def test_validate_orders_fills_missing_retreat_and_adjustment_from_whitelist(
    tmp_path: Path,
    settings: Settings,
    snapshot: PowerPhaseSnapshot,
    expected: list[str],
) -> None:
    player = make_player(tmp_path, settings)

    validated, invalid_count = player._validate_orders(snapshot, [])

    assert validated == expected
    assert invalid_count == 0


def test_validate_build_adjustment_preserves_waive_and_quota(
    tmp_path: Path,
    settings: Settings,
) -> None:
    player = make_player(tmp_path, settings)
    snapshot = make_snapshot(
        phase="W1901A",
        phase_type="A",
        own_units=("A BUR",),
        own_centers=("BRE", "MAR"),
        orderable_locations=("BRE", "MAR"),
        possible_orders={
            "BRE": ("A BRE B", "F BRE B", "WAIVE"),
            "MAR": ("A MAR B", "F MAR B", "WAIVE"),
        },
        adjustment_build_count=1,
    )

    validated, invalid_count = player._validate_orders(
        snapshot,
        ["A MAR B", "WAIVE"],
    )

    assert validated == ["A MAR B"]
    assert invalid_count == 1


def test_validate_build_adjustment_fills_missing_waive(
    tmp_path: Path,
    settings: Settings,
) -> None:
    player = make_player(tmp_path, settings)
    snapshot = make_snapshot(
        phase="W1901A",
        phase_type="A",
        own_units=("A BUR",),
        own_centers=("BRE", "MAR", "PAR"),
        orderable_locations=("BRE", "MAR"),
        possible_orders={
            "BRE": ("A BRE B", "F BRE B", "WAIVE"),
            "MAR": ("A MAR B", "F MAR B", "WAIVE"),
        },
        adjustment_build_count=2,
    )

    validated, invalid_count = player._validate_orders(snapshot, ["A MAR B"])

    assert validated == ["A MAR B", "WAIVE"]
    assert invalid_count == 0


def test_validate_build_adjustment_allows_repeated_waive(
    tmp_path: Path,
    settings: Settings,
) -> None:
    player = make_player(tmp_path, settings)
    snapshot = make_snapshot(
        phase="W1901A",
        phase_type="A",
        own_units=("A BUR",),
        own_centers=("BRE", "MAR", "PAR"),
        orderable_locations=("BRE", "MAR"),
        possible_orders={
            "BRE": ("A BRE B", "F BRE B", "WAIVE"),
            "MAR": ("A MAR B", "F MAR B", "WAIVE"),
        },
        adjustment_build_count=2,
    )

    validated, invalid_count = player._validate_orders(
        snapshot,
        ["WAIVE", "WAIVE"],
    )

    assert validated == ["WAIVE", "WAIVE"]
    assert invalid_count == 0


def test_validate_disband_adjustment_keeps_only_required_disbands(
    tmp_path: Path,
    settings: Settings,
) -> None:
    player = make_player(tmp_path, settings)
    snapshot = make_snapshot(
        phase="W1901A",
        phase_type="A",
        own_units=("A BUR", "A PIC", "F MAO"),
        own_centers=("BRE", "MAR"),
        orderable_locations=("BUR", "MAO", "PIC"),
        possible_orders={
            "BUR": ("A BUR D",),
            "MAO": ("F MAO D",),
            "PIC": ("A PIC D",),
        },
        adjustment_disband_count=1,
    )

    validated, invalid_count = player._validate_orders(
        snapshot,
        ["A PIC D", "A BUR D", "F MAO D"],
    )

    assert validated == ["A PIC D"]
    assert invalid_count == 2


@pytest.mark.parametrize(
    ("base_location", "coastal_order"),
    [
        ("STP", "F STP/SC - BOT"),
        ("STP", "F STP/NC - BAR"),
        ("SPA", "F SPA/SC - MAO"),
        ("SPA", "F SPA/NC - GAS"),
    ],
)
def test_validate_orders_treats_coastal_orders_as_covering_base_location(
    tmp_path: Path,
    settings: Settings,
    base_location: str,
    coastal_order: str,
) -> None:
    player = make_player(tmp_path, settings)
    snapshot = make_snapshot(
        own_units=(coastal_order.rsplit(" - ", maxsplit=1)[0],),
        orderable_locations=(base_location,),
        possible_orders={base_location: (coastal_order,)},
    )

    validated, invalid_count = player._validate_orders(
        snapshot,
        [coastal_order],
    )

    assert validated == [coastal_order]
    assert invalid_count == 0


@pytest.mark.parametrize(
    ("base_location", "coastal_order"),
    [
        ("STP", "F STP/SC - BOT"),
        ("STP", "F STP/NC - BAR"),
        ("SPA", "F SPA/SC - MAO"),
        ("SPA", "F SPA/NC - GAS"),
    ],
)
def test_missing_orderable_locs_treats_coastal_orders_as_base_location(
    base_location: str,
    coastal_order: str,
) -> None:
    missing = LLMPlayer._missing_orderable_locs(
        [coastal_order],
        [base_location],
    )

    assert missing == []


def test_get_orders_retries_after_invalid_order(
    tmp_path: Path,
    settings: Settings,
) -> None:
    client = FakeClient(
        [
            response(["A PAR - PIC"], "bad"),
            response(["A PAR H"], "fixed"),
        ],
    )
    player = LLMPlayer(
        power_name="FRANCE",
        model="fake/model",
        client=client,
        collector=MetricsCollector(run_id="test", metrics_dir=tmp_path),
        settings=settings,
    )

    orders, reasoning = player.get_orders(make_snapshot())

    assert orders == ["A PAR H"]
    assert reasoning == "fixed"
    assert len(client.chat.calls) == 2
    assert "Invalid orders" in client.chat.calls[1][-1]["content"]


def test_strategy_prompt_keeps_whitelist_validation(
    tmp_path: Path,
    settings: Settings,
) -> None:
    client = FakeClient(
        [
            response(["A PAR - PIC"], "bad"),
            response(["A PAR H"], "fixed"),
        ],
    )
    player = LLMPlayer(
        power_name="FRANCE",
        model="fake/model",
        client=client,
        collector=MetricsCollector(run_id="test", metrics_dir=tmp_path),
        settings=settings,
        strategy=CustomStrategyProtocol(),
    )

    orders, reasoning = player.get_orders(make_snapshot())

    assert orders == ["A PAR H"]
    assert reasoning == "fixed"
    assert len(client.chat.calls) == 2
    first_prompt = client.chat.calls[0][1]["content"]
    assert (
        "STRATEGY METADATA:\nCustom strategy section for FRANCE S1901M"
        in first_prompt
    )
    assert (
        "VALID ORDERS FOR YOUR UNITS:\n  PAR: ['A PAR H', 'A PAR - BUR']"
        in first_prompt
    )
    assert (
        "Invalid orders (not in the provided list): ['A PAR - PIC']"
        in (client.chat.calls[1][-1]["content"])
    )


def test_get_messages_accepts_multiple_recipients_in_one_window(
    tmp_path: Path,
    settings: Settings,
) -> None:
    message_settings = settings.model_copy(
        update={"messaging": MessagingSettings(enabled=True)},
    )
    client = FakeClient(
        [
            message_response(
                [
                    {
                        "recipient": "ENGLAND",
                        "intent": "coordinate",
                        "body": "I can avoid ENG if Brest stays quiet.",
                        "referenced_locations": ["ENG", "BRE"],
                        "requested_orders": [],
                        "offered_orders": ["F BRE H"],
                    },
                    {
                        "recipient": "GERMANY",
                        "intent": "request_support",
                        "body": "Can we keep Burgundy calm this spring?",
                        "referenced_locations": ["BUR"],
                        "requested_orders": [],
                        "offered_orders": [],
                    },
                ],
            ),
        ],
    )
    player = LLMPlayer(
        power_name="FRANCE",
        model="fake/model",
        client=client,
        collector=MetricsCollector(run_id="test", metrics_dir=tmp_path),
        settings=message_settings,
    )

    result = player.get_messages(
        make_snapshot(),
        message_window=1,
        visible_messages=(),
        eligible_recipient_powers=["FRANCE", "ENGLAND", "GERMANY"],
        start_sequence=4,
    )

    assert result.dropped == ()
    assert [(message.sequence, message.recipient) for message in result.accepted] == [
        (4, "ENGLAND"),
        (5, "GERMANY"),
    ]
    assert [message.body for message in result.accepted] == [
        "I can avoid ENG if Brest stays quiet.",
        "Can we keep Burgundy calm this spring?",
    ]
    assert client.chat.response_formats == [MESSAGES_RESPONSE_SCHEMA]
    prompt = client.chat.calls[0][-1]["content"]
    assert "RECIPIENTS YOU MAY MESSAGE NOW:\n  - ENGLAND\n  - GERMANY" in prompt
    assert (
        "You may send at most one message to each listed recipient" in prompt
    )


def test_get_messages_records_llm_usage(
    tmp_path: Path,
    settings: Settings,
) -> None:
    message_settings = settings.model_copy(
        update={"messaging": MessagingSettings(enabled=True)},
    )
    client = FakeClient(
        [
            message_response(
                [
                    {
                        "recipient": "ENGLAND",
                        "intent": "coordinate",
                        "body": "I can avoid ENG if Brest stays quiet.",
                        "referenced_locations": ["ENG", "BRE"],
                        "requested_orders": [],
                        "offered_orders": ["F BRE H"],
                    },
                ],
            ),
        ],
    )
    collector = MetricsCollector(run_id="test", metrics_dir=tmp_path)
    player = LLMPlayer(
        power_name="FRANCE",
        model="fake/model",
        client=client,
        collector=collector,
        settings=message_settings,
    )

    player.get_messages(
        make_snapshot(),
        message_window=2,
        visible_messages=(),
        eligible_recipient_powers=["FRANCE", "ENGLAND"],
    )

    call = collector._llm_calls[0]
    assert call["call_kind"] == "messages"
    assert call["message_window"] == 2
    assert call["tokens_total"] == 7
    assert call["tokens_cached"] == 0
    assert call["is_fallback"] is False


def test_get_messages_retries_after_invalid_recipient(
    tmp_path: Path,
    settings: Settings,
) -> None:
    message_settings = settings.model_copy(
        update={"messaging": MessagingSettings(enabled=True)},
    )
    client = FakeClient(
        [
            message_response(
                [
                    {
                        "recipient": "ITALY",
                        "intent": "warn",
                        "body": "Invalid recipient.",
                        "referenced_locations": [],
                        "requested_orders": [],
                        "offered_orders": [],
                    },
                ],
            ),
            message_response(
                [
                    {
                        "recipient": "ENGLAND",
                        "intent": "warn",
                        "body": "Valid recipient now.",
                        "referenced_locations": [],
                        "requested_orders": [],
                        "offered_orders": [],
                    },
                ],
            ),
        ],
    )
    player = LLMPlayer(
        power_name="FRANCE",
        model="fake/model",
        client=client,
        collector=MetricsCollector(run_id="test", metrics_dir=tmp_path),
        settings=message_settings,
    )

    result = player.get_messages(
        make_snapshot(),
        message_window=1,
        visible_messages=(),
        eligible_recipient_powers=["FRANCE", "ENGLAND"],
    )

    assert [message.recipient for message in result.accepted] == ["ENGLAND"]
    assert result.dropped == ()
    assert len(client.chat.calls) == 2
    assert "ineligible_recipient" in client.chat.calls[1][-1]["content"]


def test_get_messages_falls_back_to_no_messages_after_failed_healing(
    tmp_path: Path,
    settings: Settings,
) -> None:
    message_settings = settings.model_copy(
        update={"messaging": MessagingSettings(enabled=True)},
    )
    invalid = message_response(
        [
            {
                "recipient": "ITALY",
                "intent": "warn",
                "body": "Still invalid.",
                "referenced_locations": [],
                "requested_orders": [],
                "offered_orders": [],
            },
        ],
    )
    client = FakeClient([invalid, invalid, invalid])
    player = LLMPlayer(
        power_name="FRANCE",
        model="fake/model",
        client=client,
        collector=MetricsCollector(run_id="test", metrics_dir=tmp_path),
        settings=message_settings,
    )

    result = player.get_messages(
        make_snapshot(),
        message_window=1,
        visible_messages=(),
        eligible_recipient_powers=["FRANCE", "ENGLAND"],
    )

    assert result.accepted == ()
    assert [drop.reason for drop in result.dropped] == ["ineligible_recipient"]
    assert len(client.chat.calls) == 3


def test_get_messages_keeps_valid_messages_after_final_partial_failure(
    tmp_path: Path,
    settings: Settings,
) -> None:
    message_settings = settings.model_copy(
        update={
            "messaging": MessagingSettings(
                enabled=True,
                max_message_length_chars=10,
            ),
        },
    )
    partial = message_response(
        [
            {
                "recipient": "GERMANY",
                "intent": "coordinate",
                "body": "Valid",
                "referenced_locations": [],
                "requested_orders": [],
                "offered_orders": [],
            },
            {
                "recipient": "ENGLAND",
                "intent": "coordinate",
                "body": "This message is too long.",
                "referenced_locations": [],
                "requested_orders": [],
                "offered_orders": [],
            },
        ],
    )
    client = FakeClient([partial, partial, partial])
    player = LLMPlayer(
        power_name="FRANCE",
        model="fake/model",
        client=client,
        collector=MetricsCollector(run_id="test", metrics_dir=tmp_path),
        settings=message_settings,
    )

    result = player.get_messages(
        make_snapshot(),
        message_window=1,
        visible_messages=(),
        eligible_recipient_powers=["FRANCE", "ENGLAND", "GERMANY"],
    )

    assert [(message.recipient, message.body) for message in result.accepted] == [
        ("GERMANY", "Valid"),
    ]
    assert [drop.reason for drop in result.dropped] == ["body_too_long"]
    assert len(client.chat.calls) == 3


def test_get_messages_preserves_previous_partial_when_later_retry_is_unparseable(
    tmp_path: Path,
    settings: Settings,
) -> None:
    message_settings = settings.model_copy(
        update={
            "messaging": MessagingSettings(
                enabled=True,
                max_message_length_chars=10,
            ),
        },
    )
    partial = message_response(
        [
            {
                "recipient": "GERMANY",
                "intent": "coordinate",
                "body": "Valid",
                "referenced_locations": [],
                "requested_orders": [],
                "offered_orders": [],
            },
            {
                "recipient": "ENGLAND",
                "intent": "coordinate",
                "body": "This message is too long.",
                "referenced_locations": [],
                "requested_orders": [],
                "offered_orders": [],
            },
        ],
    )
    client = FakeClient([partial, "not json", "not json"])
    player = LLMPlayer(
        power_name="FRANCE",
        model="fake/model",
        client=client,
        collector=MetricsCollector(run_id="test", metrics_dir=tmp_path),
        settings=message_settings,
    )

    result = player.get_messages(
        make_snapshot(),
        message_window=1,
        visible_messages=(),
        eligible_recipient_powers=["FRANCE", "ENGLAND", "GERMANY"],
    )

    assert [(message.recipient, message.body) for message in result.accepted] == [
        ("GERMANY", "Valid"),
    ]
    assert [drop.reason for drop in result.dropped] == ["body_too_long"]
    assert len(client.chat.calls) == 3


def test_get_messages_skips_when_not_movement_or_messaging_disabled(
    tmp_path: Path,
    settings: Settings,
) -> None:
    client = FakeClient([message_response([])])
    player = LLMPlayer(
        power_name="FRANCE",
        model="fake/model",
        client=client,
        collector=MetricsCollector(run_id="test", metrics_dir=tmp_path),
        settings=settings,
    )

    disabled = player.get_messages(
        make_snapshot(),
        message_window=1,
        visible_messages=(),
        eligible_recipient_powers=["FRANCE", "ENGLAND"],
    )

    message_settings = settings.model_copy(
        update={"messaging": MessagingSettings(enabled=True)},
    )
    movement_only_player = LLMPlayer(
        power_name="FRANCE",
        model="fake/model",
        client=client,
        collector=MetricsCollector(run_id="test", metrics_dir=tmp_path),
        settings=message_settings,
    )
    retreat = movement_only_player.get_messages(
        make_snapshot(phase="S1901R", phase_type="R"),
        message_window=1,
        visible_messages=(),
        eligible_recipient_powers=["FRANCE", "ENGLAND"],
    )

    assert disabled.accepted == ()
    assert disabled.dropped == ()
    assert retreat.accepted == ()
    assert retreat.dropped == ()
    assert client.chat.calls == []


def test_get_messages_prompt_includes_only_visible_prior_messages(
    tmp_path: Path,
    settings: Settings,
) -> None:
    message_settings = settings.model_copy(
        update={"messaging": MessagingSettings(enabled=True)},
    )
    client = FakeClient([message_response([])])
    player = LLMPlayer(
        power_name="FRANCE",
        model="fake/model",
        client=client,
        collector=MetricsCollector(run_id="test", metrics_dir=tmp_path),
        settings=message_settings,
    )

    player.get_messages(
        make_snapshot(),
        message_window=2,
        visible_messages=(
            DiplomacyMessage(
                sequence=0,
                phase="S1901M",
                phase_index=0,
                message_window=1,
                sender="ENGLAND",
                recipient="FRANCE",
                intent="coordinate",
                body="Visible to France.",
            ),
            DiplomacyMessage(
                sequence=1,
                phase="S1901M",
                phase_index=0,
                message_window=1,
                sender="ENGLAND",
                recipient="GERMANY",
                intent="warn",
                body="Hidden from France.",
            ),
        ),
        eligible_recipient_powers=["FRANCE", "ENGLAND", "GERMANY"],
    )

    prompt = client.chat.calls[0][-1]["content"]
    assert "GLOBAL MESSAGING TIMELINE THIS PHASE:" in prompt
    assert "no private messaging events visible to you yet" in prompt
    assert "Hidden from France." not in prompt


def test_get_messages_prompt_includes_active_strategy_context(
    tmp_path: Path,
    settings: Settings,
) -> None:
    message_settings = settings.model_copy(
        update={"messaging": MessagingSettings(enabled=True)},
    )
    client = FakeClient([message_response([])])
    player = LLMPlayer(
        power_name="FRANCE",
        model="fake/model",
        client=client,
        collector=MetricsCollector(run_id="test", metrics_dir=tmp_path),
        settings=message_settings,
        strategy=CustomStrategyProtocol(),
    )

    result = player.get_messages(
        make_snapshot(),
        message_window=1,
        visible_messages=(),
        eligible_recipient_powers=["FRANCE", "ENGLAND", "GERMANY"],
    )

    assert result.accepted == ()
    prompt = client.chat.calls[0][-1]["content"]
    assert "CONTEXT SECTIONS:" in prompt
    assert "Apply it to this messaging decision." in prompt
    assert "STRATEGY METADATA:\nCustom strategy section for FRANCE S1901M" in prompt
    assert "MESSAGING TASK:\n=== INITIAL DIPLOMATIC OUTBOX:" in prompt
    assert prompt.index("STRATEGY METADATA:") < prompt.index("MESSAGING TASK:")


def test_get_messages_with_custom_prompt_includes_active_strategy_context(
    tmp_path: Path,
    settings: Settings,
) -> None:
    message_settings = settings.model_copy(
        update={
            "messaging_variant": "latency_pairwise_private",
            "messaging": MessagingSettings(enabled=True),
        },
    )
    client = FakeClient([message_response([])])
    player = LLMPlayer(
        power_name="FRANCE",
        model="fake/model",
        client=client,
        collector=MetricsCollector(run_id="test", metrics_dir=tmp_path),
        settings=message_settings,
        strategy=CustomStrategyProtocol(),
    )

    result = player.get_messages_with_prompt(
        make_snapshot(),
        prompt="CUSTOM LATENCY PROMPT",
        message_window=4,
        eligible_recipient_powers=["ENGLAND"],
    )

    assert result.accepted == ()
    prompt = client.chat.calls[0][-1]["content"]
    assert "STRATEGY METADATA:\nCustom strategy section for FRANCE S1901M" in prompt
    assert "MESSAGING TASK:\nCUSTOM LATENCY PROMPT" in prompt


def test_get_orders_prompt_includes_visible_current_phase_messages(
    tmp_path: Path,
    settings: Settings,
) -> None:
    client = FakeClient(
        [
            response(
                ["A PAR H"],
                reasoning="Messages make holding Paris the safest option.",
            ),
        ],
    )
    player = LLMPlayer(
        power_name="FRANCE",
        model="fake/model",
        client=client,
        collector=MetricsCollector(run_id="test", metrics_dir=tmp_path),
        settings=settings,
    )

    orders, _ = player.get_orders(
        make_snapshot(),
        visible_messages=(
            DiplomacyMessage(
                sequence=0,
                phase="S1901M",
                phase_index=0,
                message_window=1,
                sender="ENGLAND",
                recipient="FRANCE",
                intent="coordinate",
                body="I will keep ENG quiet if you hold Brest.",
            ),
            DiplomacyMessage(
                sequence=1,
                phase="S1901M",
                phase_index=0,
                message_window=1,
                sender="ENGLAND",
                recipient="GERMANY",
                intent="warn",
                body="Hidden from France.",
            ),
        ),
    )

    prompt = client.chat.calls[0][-1]["content"]
    assert orders == ["A PAR H"]
    assert "MESSAGES:" in prompt
    assert "Thread with ENGLAND:" in prompt
    assert "Received [coordinate]: I will keep ENG quiet if you hold Brest." in prompt
    assert "Hidden from France." not in prompt


def test_baseline_strategy_prompt_matches_current_baseline_prompt(
    tmp_path: Path,
    settings: Settings,
) -> None:
    snapshot = make_snapshot()
    player = LLMPlayer(
        power_name="FRANCE",
        model="fake/model",
        client=FakeClient(),
        collector=MetricsCollector(run_id="test", metrics_dir=tmp_path),
        settings=settings,
        strategy=get_strategy_protocol("baseline"),
    )

    assert player._render_user_prompt(snapshot) == get_order_prompt_template(
        "baseline_orders",
    ).render_user(snapshot)


def test_custom_strategy_protocol_can_override_prompt_section(
    tmp_path: Path,
    settings: Settings,
) -> None:
    player = LLMPlayer(
        power_name="FRANCE",
        model="fake/model",
        client=FakeClient(),
        collector=MetricsCollector(run_id="test", metrics_dir=tmp_path),
        settings=settings,
        strategy=CustomStrategyProtocol(),
    )

    prompt = player._render_user_prompt(make_snapshot())

    assert "STRATEGY METADATA:\nCustom strategy section for FRANCE S1901M" in prompt
    assert "Doctrine:" not in prompt


def test_get_orders_records_requested_and_actual_model(
    tmp_path: Path,
    settings: Settings,
) -> None:
    collector = MetricsCollector(run_id="test", metrics_dir=tmp_path)
    player = LLMPlayer(
        power_name="FRANCE",
        model="fake/requested-model",
        client=FakeClient(
            [response(["A PAR H"], "ok")],
            routed_model="fake/routed-model",
        ),
        collector=collector,
        settings=settings,
    )

    orders, reasoning = player.get_orders(make_snapshot())
    [call] = collector._llm_calls

    assert orders == ["A PAR H"]
    assert reasoning == "ok"
    assert call["requested_model"] == "fake/requested-model"
    assert call["actual_model"] == "fake/routed-model"
    assert call["model_routing_mismatch"] is True
    assert call["model"] == "fake/routed-model"
    assert call["strategy_name"] == "baseline"
    assert call["strategy_version"] == "v1"
    assert call["strategy_resolution_source"] == "default"
    assert call["strategy_matched_model"] is None


@pytest.mark.parametrize(
    ("error_attr", "reason"),
    [
        (
            "BadRequestResponseError",
            "bad_request_or_strict_response_format_unsupported",
        ),
        (
            "PaymentRequiredResponseError",
            "payment_required_or_insufficient_credits",
        ),
    ],
)
def test_get_orders_aborts_on_non_recoverable_provider_errors(
    tmp_path: Path,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    error_attr: str,
    reason: str,
) -> None:
    class ProviderError(Exception):
        pass

    monkeypatch.setattr(llm_player, error_attr, ProviderError)
    player = LLMPlayer(
        power_name="FRANCE",
        model="fake/model",
        client=RaisingClient(ProviderError("provider rejected request")),
        collector=MetricsCollector(run_id="test", metrics_dir=tmp_path),
        settings=settings,
    )

    with pytest.raises(LLMProviderCriticalError, match=reason) as exc_info:
        player.get_orders(make_snapshot())

    assert exc_info.value.power_name == "FRANCE"
    assert exc_info.value.model == "fake/model"
    assert exc_info.value.reason == reason


def test_get_orders_uses_orders_response_schema(
    tmp_path: Path,
    settings: Settings,
) -> None:
    client = FakeClient([response(["A PAR H"], "ok")])
    player = LLMPlayer(
        power_name="FRANCE",
        model="fake/model",
        client=client,
        collector=MetricsCollector(run_id="test", metrics_dir=tmp_path),
        settings=settings,
    )

    player.get_orders(make_snapshot())

    assert client.chat.response_formats == [ORDERS_RESPONSE_SCHEMA]


def test_get_orders_records_strategy_resolution_metadata(
    tmp_path: Path,
    settings: Settings,
) -> None:
    collector = MetricsCollector(run_id="test", metrics_dir=tmp_path)
    player = LLMPlayer(
        power_name="FRANCE",
        model="model/planner",
        client=FakeClient([response(["A PAR H"], "ok")]),
        collector=collector,
        settings=settings,
        strategy=get_strategy_protocol("baseline"),
        strategy_resolution=StrategyResolution(
            strategy_name="baseline",
            strategy_version="v1",
            source="by_model",
            matched_model="model/baseline",
        ),
    )

    player.get_orders(make_snapshot())
    [call] = collector._llm_calls

    assert call["strategy_name"] == "baseline"
    assert call["strategy_version"] == "v1"
    assert call["strategy_resolution_source"] == "by_model"
    assert call["strategy_matched_model"] == "model/baseline"


def test_llm_player_rejects_unknown_prompt_variant(
    tmp_path: Path,
    settings: Settings,
) -> None:
    variant_settings = settings.model_copy(
        update={"prompt_variant": "missing_variant"},
    )

    with pytest.raises(ValueError, match="Unknown prompt_variant 'missing_variant'"):
        make_player(tmp_path, variant_settings)


def test_get_orders_retries_after_missing_order(
    tmp_path: Path,
    settings: Settings,
) -> None:
    client = FakeClient(
        [
            response(["A PAR H"], "partial"),
            response(["A PAR H", "A MAR H"], "complete"),
        ],
    )
    player = LLMPlayer(
        power_name="FRANCE",
        model="fake/model",
        client=client,
        collector=MetricsCollector(run_id="test", metrics_dir=tmp_path),
        settings=settings,
    )
    snapshot = make_snapshot(
        own_units=("A PAR", "A MAR"),
        own_centers=("PAR", "MAR"),
        orderable_locations=("PAR", "MAR"),
        possible_orders={
            "PAR": ("A PAR H",),
            "MAR": ("A MAR H",),
        },
    )

    orders, reasoning = player.get_orders(snapshot)

    assert orders == ["A PAR H", "A MAR H"]
    assert reasoning == "complete"
    assert len(client.chat.calls) == 2
    assert (
        "Missing orders for locations: ['MAR']" in client.chat.calls[1][-1]["content"]
    )


def test_get_orders_retries_after_too_many_adjustment_disbands(
    tmp_path: Path,
    settings: Settings,
) -> None:
    client = FakeClient(
        [
            response(["A BUR D", "A PIC D"], "too many"),
            response(["A PIC D"], "fixed"),
        ],
    )
    player = LLMPlayer(
        power_name="FRANCE",
        model="fake/model",
        client=client,
        collector=MetricsCollector(run_id="test", metrics_dir=tmp_path),
        settings=settings,
    )
    snapshot = make_snapshot(
        phase="W1901A",
        phase_type="A",
        own_units=("A BUR", "A PIC"),
        own_centers=("BRE",),
        orderable_locations=("BUR", "PIC"),
        possible_orders={
            "BUR": ("A BUR D",),
            "PIC": ("A PIC D",),
        },
        adjustment_disband_count=1,
    )

    orders, reasoning = player.get_orders(snapshot)

    assert orders == ["A PIC D"]
    assert reasoning == "fixed"
    assert len(client.chat.calls) == 2
    assert "Too many disbands" in client.chat.calls[1][-1]["content"]
