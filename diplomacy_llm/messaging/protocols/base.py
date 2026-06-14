from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from diplomacy_llm.config import Settings
    from diplomacy_llm.messaging.models import DiplomacyMessage, MessageValidationResult
    from diplomacy_llm.phase_snapshot import PowerPhaseSnapshot


@dataclass(frozen=True)
class MessagingProtocolIdentity:
    """Stable identity for one messaging protocol implementation."""

    name: str
    version: str


class BaseMessagingProtocol(ABC):
    """Base class for prompt/schema/validation owned by one messaging protocol."""

    @property
    @abstractmethod
    def identity(self) -> MessagingProtocolIdentity:
        """Return stable protocol identity metadata."""

    @property
    @abstractmethod
    def response_schema(self) -> dict[str, Any]:
        """Return the structured-output schema used for message generation."""

    @abstractmethod
    def eligible_recipients(
        self,
        power: str,
        messaging_enabled_powers: Sequence[str],
    ) -> tuple[str, ...]:
        """Return recipients visible to this sender under protocol rules."""

    @abstractmethod
    def render_prompt(
        self,
        snapshot: PowerPhaseSnapshot,
        *,
        message_window: int,
        eligible_recipients: tuple[str, ...],
        visible_messages: tuple[DiplomacyMessage, ...],
        settings: Settings,
    ) -> str:
        """Render one message-generation prompt."""

    @abstractmethod
    def parse_response(self, raw: str) -> tuple[object | None, str | None]:
        """Parse a raw model response into protocol-specific envelopes."""

    @abstractmethod
    def validate_envelopes(  # noqa: PLR0913
        self,
        envelopes: object,
        *,
        sender: str,
        phase: str,
        phase_index: int,
        message_window: int,
        eligible_recipient_powers: Sequence[str],
        settings: Settings,
        start_sequence: int = 0,
    ) -> MessageValidationResult:
        """Validate protocol envelopes into accepted/dropped messages."""

    @abstractmethod
    def response_shape_description(
        self,
        eligible_recipient_powers: Sequence[str],
    ) -> str:
        """Return a concise response-shape description for healing prompts."""


def parse_json_object(raw: str) -> tuple[Mapping[str, Any] | None, str | None]:
    """Parse a raw JSON object for protocol implementations."""
    try:
        data_raw: Any = json.loads(raw)
    except json.JSONDecodeError:
        return None, "json_parse_error"

    if not isinstance(data_raw, Mapping):
        return None, "response_must_be_an_object"

    return data_raw, None
