import pytest

from diplomacy_llm.messaging import (
    DiplomacyMessage,
    build_message_threads,
    eligible_recipients,
    validate_message_envelopes,
    visible_messages_for_power,
)
from diplomacy_llm.messaging.protocols import (
    LatencyPairwisePrivateMessagingProtocol,
    available_messaging_protocol_names,
    get_messaging_protocol,
)


def test_eligible_recipients_excludes_sender_and_disabled_powers() -> None:
    assert eligible_recipients("FRANCE", ["ENGLAND", "FRANCE", "GERMANY"]) == (
        "ENGLAND",
        "GERMANY",
    )


def test_messaging_protocol_registry_exposes_latency_pairwise_private() -> None:
    assert available_messaging_protocol_names() == ("latency_pairwise_private",)

    protocol = get_messaging_protocol("none")

    assert isinstance(protocol, LatencyPairwisePrivateMessagingProtocol)
    assert protocol.identity.name == "latency_pairwise_private"
    assert protocol.identity.version == "v1"

    latency_protocol = get_messaging_protocol("latency_pairwise_private")

    assert isinstance(latency_protocol, LatencyPairwisePrivateMessagingProtocol)
    assert latency_protocol.identity.version == "v1"


def test_validate_message_envelopes_accepts_lightly_structured_free_text() -> None:
    result = validate_message_envelopes(
        [
            {
                "recipient": "england",
                "intent": "request_support",
                "body": "If you avoid ENG, I can keep Brest quiet.",
                "referenced_locations": ["ENG", "BRE"],
                "requested_orders": ["F LON - NTH"],
                "offered_orders": ["F BRE H"],
            },
            {
                "recipient": "GERMANY",
                "intent": "creative_diplomacy",
                "body": "Let's keep Burgundy calm while we watch England.",
            },
        ],
        sender="france",
        phase="S1901M",
        phase_index=0,
        message_window=1,
        eligible_recipient_powers=["ENGLAND", "GERMANY"],
        max_messages=6,
        max_message_length_chars=1200,
        start_sequence=10,
    )

    assert result.dropped == ()
    assert [message.to_dict() for message in result.accepted] == [
        {
            "sequence": 10,
            "phase": "S1901M",
            "phase_index": 0,
            "message_window": 1,
            "sender": "FRANCE",
            "recipient": "ENGLAND",
            "intent": "request_support",
            "body": "If you avoid ENG, I can keep Brest quiet.",
            "referenced_locations": ["ENG", "BRE"],
            "requested_orders": ["F LON - NTH"],
            "offered_orders": ["F BRE H"],
        },
        {
            "sequence": 11,
            "phase": "S1901M",
            "phase_index": 0,
            "message_window": 1,
            "sender": "FRANCE",
            "recipient": "GERMANY",
            "intent": "other",
            "body": "Let's keep Burgundy calm while we watch England.",
            "referenced_locations": [],
            "requested_orders": [],
            "offered_orders": [],
        },
    ]


def test_validate_message_envelopes_drops_invalid_messages_safely() -> None:
    result = validate_message_envelopes(
        [
            "not an object",
            {"recipient": "FRANCE", "intent": "warn", "body": "self"},
            {"recipient": "ITALY", "intent": "warn", "body": "unknown"},
            {"recipient": "ENGLAND", "intent": "warn", "body": ""},
            {"recipient": "ENGLAND", "intent": "warn", "body": "x" * 6},
            {"recipient": "ENGLAND", "intent": "warn", "body": "valid"},
            {"recipient": "ENGLAND", "intent": "warn", "body": "duplicate"},
            {"recipient": "GERMANY", "intent": "warn", "body": "over cap"},
        ],
        sender="FRANCE",
        phase="S1901M",
        phase_index=0,
        message_window=1,
        eligible_recipient_powers=["ENGLAND", "GERMANY"],
        max_messages=1,
        max_message_length_chars=5,
    )

    assert [message.body for message in result.accepted] == ["valid"]
    assert [drop.reason for drop in result.dropped] == [
        "message_must_be_an_object",
        "self_recipient",
        "ineligible_recipient",
        "empty_body",
        "body_too_long",
        "duplicate_recipient",
        "max_messages_exceeded",
    ]


def test_validate_message_envelopes_drops_non_list_response() -> None:
    result = validate_message_envelopes(
        {"recipient": "ENGLAND", "body": "hello"},
        sender="FRANCE",
        phase="S1901M",
        phase_index=0,
        message_window=1,
        eligible_recipient_powers=["ENGLAND"],
        max_messages=1,
        max_message_length_chars=1200,
    )

    assert result.accepted == ()
    assert [drop.reason for drop in result.dropped] == ["messages_must_be_a_list"]


def test_visible_messages_include_only_sent_or_received_messages() -> None:
    messages = [
        _message(0, 1, "FRANCE", "ENGLAND", "to england"),
        _message(1, 1, "ENGLAND", "FRANCE", "to france"),
        _message(2, 1, "ENGLAND", "GERMANY", "private elsewhere"),
    ]

    visible = visible_messages_for_power(messages, "FRANCE")

    assert [message.body for message in visible] == ["to england", "to france"]


def test_build_message_threads_keeps_counterparties_and_windows_separate() -> None:
    messages = [
        _message(0, 1, "FRANCE", "ENGLAND", "France window 1 to England"),
        _message(1, 1, "ENGLAND", "FRANCE", "England window 1 to France"),
        _message(2, 1, "GERMANY", "FRANCE", "Germany window 1 to France"),
        _message(3, 2, "FRANCE", "ENGLAND", "France window 2 to England"),
        _message(4, 2, "ENGLAND", "FRANCE", "England window 2 to France"),
    ]

    threads = build_message_threads(
        messages,
        power="FRANCE",
        powers_order=["ENGLAND", "FRANCE", "GERMANY"],
    )

    assert [thread.counterparty for thread in threads] == ["ENGLAND", "GERMANY"]
    england_thread = threads[0]
    germany_thread = threads[1]

    assert [window.message_window for window in england_thread.windows] == [1, 2]
    assert [message.body for message in england_thread.windows[0].sent] == [
        "France window 1 to England",
    ]
    assert [message.body for message in england_thread.windows[0].received] == [
        "England window 1 to France",
    ]
    assert [message.body for message in england_thread.windows[1].sent] == [
        "France window 2 to England",
    ]
    assert [message.body for message in england_thread.windows[1].received] == [
        "England window 2 to France",
    ]
    assert [window.message_window for window in germany_thread.windows] == [1]
    assert germany_thread.windows[0].sent == ()
    assert [message.body for message in germany_thread.windows[0].received] == [
        "Germany window 1 to France",
    ]


def test_diplomacy_message_rejects_invalid_direct_construction() -> None:
    with pytest.raises(ValueError, match="sender and recipient must differ"):
        _message(0, 1, "FRANCE", "FRANCE", "self")


def _message(
    sequence: int,
    message_window: int,
    sender: str,
    recipient: str,
    body: str,
) -> DiplomacyMessage:
    return DiplomacyMessage(
        sequence=sequence,
        phase="S1901M",
        phase_index=0,
        message_window=message_window,
        sender=sender,
        recipient=recipient,
        intent="coordinate",
        body=body,
    )
