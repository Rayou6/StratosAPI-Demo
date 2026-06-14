from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, cast

MessageIntent = Literal[
    "propose",
    "request_support",
    "offer_support",
    "coordinate",
    "accept",
    "reject",
    "warn",
    "share_intent",
    "other",
]

MESSAGE_INTENTS: tuple[MessageIntent, ...] = (
    "propose",
    "request_support",
    "offer_support",
    "coordinate",
    "accept",
    "reject",
    "warn",
    "share_intent",
    "other",
)
DEFAULT_MESSAGE_INTENT: MessageIntent = "other"


@dataclass(frozen=True)
class DiplomacyMessage:
    """One accepted private message between two powers."""

    sequence: int
    phase: str
    phase_index: int
    message_window: int
    sender: str
    recipient: str
    intent: MessageIntent
    body: str
    referenced_locations: tuple[str, ...] = field(default_factory=tuple)
    requested_orders: tuple[str, ...] = field(default_factory=tuple)
    offered_orders: tuple[str, ...] = field(default_factory=tuple)
    system_event: str | None = None
    system_reason: str | None = None

    def __post_init__(self) -> None:
        """Validate and normalize stable message metadata."""
        if self.sequence < 0:
            msg = "message sequence must be non-negative"
            raise ValueError(msg)
        if self.phase_index < 0:
            msg = "message phase_index must be non-negative"
            raise ValueError(msg)
        if self.message_window < 1:
            msg = "message_window must be at least 1"
            raise ValueError(msg)
        if not self.phase:
            msg = "message phase must be non-empty"
            raise ValueError(msg)

        sender = _normalize_power(self.sender)
        recipient = _normalize_power(self.recipient)
        if not sender or not recipient:
            msg = "message sender and recipient must be non-empty"
            raise ValueError(msg)
        if sender == recipient:
            msg = "message sender and recipient must differ"
            raise ValueError(msg)
        if self.intent not in MESSAGE_INTENTS:
            msg = f"unknown message intent: {self.intent}"
            raise ValueError(msg)

        body = self.body.strip()
        if not body:
            msg = "message body must be non-empty"
            raise ValueError(msg)

        object.__setattr__(self, "sender", sender)
        object.__setattr__(self, "recipient", recipient)
        object.__setattr__(self, "body", body)
        object.__setattr__(self, "system_event", _optional_string(self.system_event))
        object.__setattr__(self, "system_reason", _optional_string(self.system_reason))
        object.__setattr__(
            self,
            "referenced_locations",
            _string_tuple(self.referenced_locations),
        )
        object.__setattr__(
            self,
            "requested_orders",
            _string_tuple(self.requested_orders),
        )
        object.__setattr__(self, "offered_orders", _string_tuple(self.offered_orders))

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe representation for message artifacts."""
        payload: dict[str, object] = {
            "sequence": self.sequence,
            "phase": self.phase,
            "phase_index": self.phase_index,
            "message_window": self.message_window,
            "sender": self.sender,
            "recipient": self.recipient,
            "intent": self.intent,
            "body": self.body,
            "referenced_locations": list(self.referenced_locations),
            "requested_orders": list(self.requested_orders),
            "offered_orders": list(self.offered_orders),
        }
        if self.system_event is not None:
            payload["system_event"] = self.system_event
        if self.system_reason is not None:
            payload["system_reason"] = self.system_reason
        return payload


@dataclass(frozen=True)
class DroppedMessage:
    """Rejected message envelope with enough context to audit safe drops."""

    sender: str
    phase: str
    phase_index: int
    message_window: int
    reason: str
    raw: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalize metadata and freeze raw details."""
        object.__setattr__(self, "sender", _normalize_power(self.sender))
        object.__setattr__(self, "raw", MappingProxyType(dict(self.raw)))

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe representation for message validation artifacts."""
        return {
            "sender": self.sender,
            "phase": self.phase,
            "phase_index": self.phase_index,
            "message_window": self.message_window,
            "reason": self.reason,
            "raw": dict(self.raw),
        }


@dataclass(frozen=True)
class MessageValidationResult:
    """Accepted and dropped messages from one sender in one message window."""

    accepted: tuple[DiplomacyMessage, ...]
    dropped: tuple[DroppedMessage, ...]


@dataclass(frozen=True)
class MessageWindowThread:
    """Visible messages with one counterparty in one simultaneous window."""

    message_window: int
    sent: tuple[DiplomacyMessage, ...]
    received: tuple[DiplomacyMessage, ...]


@dataclass(frozen=True)
class MessageThread:
    """Visible private message thread for one power and one counterparty."""

    counterparty: str
    windows: tuple[MessageWindowThread, ...]


def eligible_recipients(
    power: str, messaging_enabled_powers: Sequence[str]
) -> tuple[str, ...]:
    """Return message recipients enabled for private press, excluding the sender."""
    sender = _normalize_power(power)
    return tuple(
        candidate
        for candidate in (
            _normalize_power(power_name) for power_name in messaging_enabled_powers
        )
        if candidate and candidate != sender
    )


def validate_message_envelopes(  # noqa: C901, PLR0913
    envelopes: object,
    *,
    sender: str,
    phase: str,
    phase_index: int,
    message_window: int,
    eligible_recipient_powers: Sequence[str],
    max_messages: int,
    max_message_length_chars: int,
    start_sequence: int = 0,
) -> MessageValidationResult:
    """
    Validate structured message envelopes without blocking the game.

    Invalid envelopes are dropped with audit reasons. Content remains free-form
    inside the bounded body string.
    """
    if not isinstance(envelopes, Sequence) or isinstance(envelopes, str | bytes):
        return MessageValidationResult(
            accepted=(),
            dropped=(
                _drop(
                    sender=sender,
                    phase=phase,
                    phase_index=phase_index,
                    message_window=message_window,
                    reason="messages_must_be_a_list",
                    raw={"value": repr(envelopes)},
                ),
            ),
        )

    eligible = {_normalize_power(power) for power in eligible_recipient_powers}
    accepted: list[DiplomacyMessage] = []
    dropped: list[DroppedMessage] = []
    seen_recipients: set[str] = set()
    normalized_sender = _normalize_power(sender)

    for raw_envelope in envelopes:
        if not isinstance(raw_envelope, Mapping):
            dropped.append(
                _drop(
                    sender=sender,
                    phase=phase,
                    phase_index=phase_index,
                    message_window=message_window,
                    reason="message_must_be_an_object",
                    raw={"value": repr(raw_envelope)},
                ),
            )
            continue

        raw = {str(key): value for key, value in raw_envelope.items()}
        recipient = _normalize_power(raw.get("recipient"))
        body = raw.get("body")

        if not recipient:
            dropped.append(
                _drop_context(
                    raw, sender, phase, phase_index, message_window, "missing_recipient"
                ),
            )
            continue
        if recipient == normalized_sender:
            dropped.append(
                _drop_context(
                    raw, sender, phase, phase_index, message_window, "self_recipient"
                ),
            )
            continue
        if recipient not in eligible:
            dropped.append(
                _drop_context(
                    raw,
                    sender,
                    phase,
                    phase_index,
                    message_window,
                    "ineligible_recipient",
                ),
            )
            continue
        if recipient in seen_recipients:
            dropped.append(
                _drop_context(
                    raw,
                    sender,
                    phase,
                    phase_index,
                    message_window,
                    "duplicate_recipient",
                ),
            )
            continue
        if len(accepted) >= max_messages:
            dropped.append(
                _drop_context(
                    raw,
                    sender,
                    phase,
                    phase_index,
                    message_window,
                    "max_messages_exceeded",
                ),
            )
            continue
        if not isinstance(body, str) or not body.strip():
            dropped.append(
                _drop_context(
                    raw, sender, phase, phase_index, message_window, "empty_body"
                ),
            )
            continue
        if len(body.strip()) > max_message_length_chars:
            dropped.append(
                _drop_context(
                    raw,
                    sender,
                    phase,
                    phase_index,
                    message_window,
                    "body_too_long",
                ),
            )
            continue

        accepted.append(
            DiplomacyMessage(
                sequence=start_sequence + len(accepted),
                phase=phase,
                phase_index=phase_index,
                message_window=message_window,
                sender=normalized_sender,
                recipient=recipient,
                intent=_normalize_intent(raw.get("intent")),
                body=body,
                referenced_locations=_string_tuple(raw.get("referenced_locations")),
                requested_orders=_string_tuple(raw.get("requested_orders")),
                offered_orders=_string_tuple(raw.get("offered_orders")),
            ),
        )
        seen_recipients.add(recipient)

    return MessageValidationResult(accepted=tuple(accepted), dropped=tuple(dropped))


def visible_messages_for_power(
    messages: Sequence[DiplomacyMessage],
    power: str,
) -> tuple[DiplomacyMessage, ...]:
    """Return only messages sent or received by one power."""
    normalized_power = _normalize_power(power)
    return tuple(
        message
        for message in messages
        if normalized_power in {message.sender, message.recipient}
    )


def build_message_threads(
    messages: Sequence[DiplomacyMessage],
    *,
    power: str,
    powers_order: Sequence[str] | None = None,
) -> tuple[MessageThread, ...]:
    """Group visible messages by counterparty and simultaneous message window."""
    normalized_power = _normalize_power(power)
    visible = visible_messages_for_power(messages, normalized_power)
    order = {
        _normalize_power(power_name): index
        for index, power_name in enumerate(powers_order or ())
    }
    counterparties = sorted(
        {
            message.recipient if message.sender == normalized_power else message.sender
            for message in visible
        },
        key=lambda name: (order.get(name, len(order)), name),
    )

    threads: list[MessageThread] = []
    for counterparty in counterparties:
        counterparty_messages = [
            message
            for message in visible
            if counterparty in {message.sender, message.recipient}
        ]
        windows: list[MessageWindowThread] = []
        for message_window in sorted(
            {message.message_window for message in counterparty_messages},
        ):
            window_messages = [
                message
                for message in counterparty_messages
                if message.message_window == message_window
            ]
            windows.append(
                MessageWindowThread(
                    message_window=message_window,
                    sent=tuple(
                        message
                        for message in window_messages
                        if message.sender == normalized_power
                    ),
                    received=tuple(
                        message
                        for message in window_messages
                        if message.recipient == normalized_power
                    ),
                ),
            )
        threads.append(MessageThread(counterparty=counterparty, windows=tuple(windows)))
    return tuple(threads)


def _normalize_power(value: object) -> str:
    return str(value).strip().upper() if value is not None else ""


def _normalize_intent(value: object) -> MessageIntent:
    if not isinstance(value, str):
        return DEFAULT_MESSAGE_INTENT
    normalized = value.strip().lower()
    if normalized in MESSAGE_INTENTS:
        return cast("MessageIntent", normalized)
    return DEFAULT_MESSAGE_INTENT


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _drop_context(  # noqa: PLR0913
    raw: Mapping[str, object],
    sender: str,
    phase: str,
    phase_index: int,
    message_window: int,
    reason: str,
) -> DroppedMessage:
    return _drop(
        sender=sender,
        phase=phase,
        phase_index=phase_index,
        message_window=message_window,
        reason=reason,
        raw=raw,
    )


def _drop(  # noqa: PLR0913
    *,
    sender: str,
    phase: str,
    phase_index: int,
    message_window: int,
    reason: str,
    raw: Mapping[str, object],
) -> DroppedMessage:
    return DroppedMessage(
        sender=sender,
        phase=phase,
        phase_index=phase_index,
        message_window=message_window,
        reason=reason,
        raw=raw,
    )
