from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from diplomacy_llm.messaging.models import (
    DiplomacyMessage,
    MessageValidationResult,
    eligible_recipients,
    validate_message_envelopes,
)
from diplomacy_llm.messaging.protocols.base import (
    BaseMessagingProtocol,
    MessagingProtocolIdentity,
    parse_json_object,
)
from diplomacy_llm.messaging.schemas import MESSAGES_RESPONSE_SCHEMA

if TYPE_CHECKING:
    from diplomacy_llm.config import Settings
    from diplomacy_llm.phase_snapshot import PowerPhaseSnapshot


class LatencyPairwisePrivateMessagingProtocol(BaseMessagingProtocol):
    """Private press with latency-ordered pairwise threads and global context."""

    @property
    def identity(self) -> MessagingProtocolIdentity:
        """Return stable protocol identity metadata."""
        return MessagingProtocolIdentity(
            name="latency_pairwise_private",
            version="v1",
        )

    @property
    def response_schema(self) -> dict[str, object]:
        """Return the structured-output schema used for message generation."""
        return MESSAGES_RESPONSE_SCHEMA

    def eligible_recipients(
        self,
        power: str,
        messaging_enabled_powers: Sequence[str],
    ) -> tuple[str, ...]:
        """Return private-message recipients, excluding the sender."""
        return eligible_recipients(power, messaging_enabled_powers)

    def render_prompt(
        self,
        snapshot: PowerPhaseSnapshot,
        *,
        message_window: int,
        eligible_recipients: tuple[str, ...],
        visible_messages: tuple[DiplomacyMessage, ...],
        settings: Settings,
    ) -> str:
        """
        Render an initial global outbox prompt.

        The latency-pairwise runtime normally calls the more specific render
        helpers below. This method keeps the base protocol contract usable.
        """
        _ = message_window, visible_messages
        return self.render_initial_outbox_prompt(
            snapshot,
            eligible_recipients=eligible_recipients,
            timeline_lines=(),
            settings=settings,
        )

    def render_initial_outbox_prompt(  # noqa: PLR0913
        self,
        snapshot: PowerPhaseSnapshot,
        *,
        eligible_recipients: tuple[str, ...],
        timeline_lines: tuple[str, ...],
        settings: Settings,
        sent_count: int | None = 0,
        thread_status_lines: tuple[str, ...] = (),
    ) -> str:
        """Render the first whole-phase diplomatic outbox prompt."""
        return _BASE_PAIRWISE_TEMPLATE.format(
            title=f"INITIAL DIPLOMATIC OUTBOX: {snapshot.phase}",
            task=(
                "Choose who you want to message at the start of this Movement "
                "messaging phase. Think about your whole diplomatic position, "
                "then send zero or more initial private messages."
            ),
            power=snapshot.power_name,
            phase=snapshot.phase,
            phase_label=_phase_label(snapshot.phase_type),
            board=_board_context(snapshot),
            timeline=_format_timeline(timeline_lines),
            thread_status=_format_thread_status(thread_status_lines),
            pending="  (none)",
            eligible=_format_recipients(eligible_recipients),
            stale="  (none)",
            max_messages=(
                settings.messaging.latency_pairwise_private.max_messages_per_response
            ),
            max_chars=settings.messaging.max_message_length_chars,
            max_turns=(
                settings.messaging.latency_pairwise_private.max_turns_per_conversation
            ),
            sent_budget=_format_sent_budget(sent_count, settings),
            extra_rules=(
                "- You may send at most one message to each listed recipient.\n"
                "- You may send no messages if silence is strategically best."
            ),
        )

    def render_revision_prompt(  # noqa: PLR0913
        self,
        snapshot: PowerPhaseSnapshot,
        *,
        eligible_recipients: tuple[str, ...],
        pending_recipients: tuple[str, ...],
        timeline_lines: tuple[str, ...],
        new_timeline_lines: tuple[str, ...],
        stale_messages: tuple[DiplomacyMessage, ...],
        settings: Settings,
        sent_count: int | None,
        thread_status_lines: tuple[str, ...] = (),
    ) -> str:
        """Render a prompt for revising a stale outbox or stale reply batch."""
        return _BASE_PAIRWISE_TEMPLATE.format(
            title=f"REVISE YOUR OUTBOX: {snapshot.phase}",
            task=(
                "Your previous response was drafted before new private messages "
                "arrived. Do not send the stale draft automatically. Reconsider "
                "your whole visible messaging context now."
            ),
            power=snapshot.power_name,
            phase=snapshot.phase,
            phase_label=_phase_label(snapshot.phase_type),
            board=_board_context(snapshot),
            timeline=_format_timeline(timeline_lines),
            thread_status=_format_thread_status(thread_status_lines),
            pending=_format_recipients(pending_recipients),
            eligible=_format_recipients(eligible_recipients),
            stale=_format_stale_messages(stale_messages, new_timeline_lines),
            max_messages=(
                settings.messaging.latency_pairwise_private.max_messages_per_response
            ),
            max_chars=settings.messaging.max_message_length_chars,
            max_turns=(
                settings.messaging.latency_pairwise_private.max_turns_per_conversation
            ),
            sent_budget=_format_sent_budget(sent_count, settings),
            extra_rules=(
                "- For each pending reply thread you omit, the runtime will close "
                "that thread as your decision not to respond.\n"
                "- You may only message listed recipients. Do not follow up in a "
                "thread where you are waiting for the other power to answer."
            ),
        )

    def render_grouped_reply_prompt(  # noqa: PLR0913
        self,
        snapshot: PowerPhaseSnapshot,
        *,
        pending_recipients: tuple[str, ...],
        timeline_lines: tuple[str, ...],
        settings: Settings,
        sent_count: int | None,
        thread_status_lines: tuple[str, ...] = (),
    ) -> str:
        """Render one grouped reply prompt for all currently pending threads."""
        return _BASE_PAIRWISE_TEMPLATE.format(
            title=f"PENDING PRIVATE REPLIES: {snapshot.phase}",
            task=(
                "Reply to the pending private threads listed below. Consider your "
                "global visible messaging context, but send at most one reply per "
                "listed thread."
            ),
            power=snapshot.power_name,
            phase=snapshot.phase,
            phase_label=_phase_label(snapshot.phase_type),
            board=_board_context(snapshot),
            timeline=_format_timeline(timeline_lines),
            thread_status=_format_thread_status(thread_status_lines),
            pending=_format_recipients(pending_recipients),
            eligible=_format_recipients(pending_recipients),
            stale="  (none)",
            max_messages=(
                settings.messaging.latency_pairwise_private.max_messages_per_response
            ),
            max_chars=settings.messaging.max_message_length_chars,
            max_turns=(
                settings.messaging.latency_pairwise_private.max_turns_per_conversation
            ),
            sent_budget=_format_sent_budget(sent_count, settings),
            extra_rules=(
                "- For each pending reply thread you omit, the runtime will close "
                "that thread as your decision not to respond.\n"
                "- You cannot start new conversations in this reply step."
            ),
        )

    def parse_response(self, raw: str) -> tuple[object | None, str | None]:
        """Parse a raw model response into the messages envelope list."""
        data, reason = parse_json_object(raw)
        if data is None:
            return None, reason

        messages = data.get("messages")
        if not isinstance(messages, list):
            return None, "messages_must_be_a_list"
        return messages, None

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
        """Validate private message envelopes for a pairwise decision batch."""
        return validate_message_envelopes(
            envelopes,
            sender=sender,
            phase=phase,
            phase_index=phase_index,
            message_window=message_window,
            eligible_recipient_powers=eligible_recipient_powers,
            max_messages=(
                settings.messaging.latency_pairwise_private.max_messages_per_response
            ),
            max_message_length_chars=settings.messaging.max_message_length_chars,
            start_sequence=start_sequence,
        )

    def response_shape_description(
        self,
        eligible_recipient_powers: Sequence[str],
    ) -> str:
        """Return a concise JSON response shape for healing prompts."""
        return (
            "a JSON object containing 'messages' (list of message objects). "
            "Each message object must contain 'recipient' (one of "
            f"{list(eligible_recipient_powers)}), 'intent', 'body', "
            "'referenced_locations' (list of strings), 'requested_orders' "
            "(list of strings), and 'offered_orders' (list of strings). "
            "Omit a pending recipient to decline that reply and close the thread"
        )


_BASE_PAIRWISE_TEMPLATE = """=== {title} ({phase_label}) ===

You are {power}. Use English for all message bodies and reasoning.

TASK:
{task}

BOARD CONTEXT:
{board}

GLOBAL MESSAGING TIMELINE THIS PHASE:
{timeline}

PAIRWISE THREAD STATUS:
{thread_status}

NEW OR STALE CONTEXT:
{stale}

PENDING REPLY THREADS:
{pending}

RECIPIENTS YOU MAY MESSAGE NOW:
{eligible}

LIMITS:
- At most {max_messages} message(s) in this decision.
- Each body must be at most {max_chars} characters.
- Conversation turn limit: {max_turns} delivered message(s) per pairwise thread.
{sent_budget}

RULES:
- Threads are private and pairwise.
- You may not send two messages in a row to the same power.
- If you already sent the latest message in a thread, wait for the other power.
{extra_rules}
- Keep messages strategically useful and concise.
"""


def _phase_label(phase_type: str) -> str:
    return {
        "M": "Movement Phase",
        "R": "Retreat Phase",
        "A": "Adjustment Phase",
    }.get(phase_type, phase_type)


def _board_context(snapshot: PowerPhaseSnapshot) -> str:
    lines = [
        f"  Your units: {list(snapshot.own_units)}",
        f"  Your supply centers: {list(snapshot.own_centers)} ({len(snapshot.own_centers)} SCs)",
        "  Other powers:",
    ]
    for power, units in snapshot.all_units.items():
        if power == snapshot.power_name:
            continue
        centers = snapshot.all_centers.get(power, ())
        lines.append(
            f"    {power}: units={list(units)} SCs={list(centers)} ({len(centers)})"
        )
    return "\n".join(lines)


def _format_timeline(lines: tuple[str, ...]) -> str:
    if not lines:
        return "  (no private messaging events visible to you yet)"
    return "\n".join(f"  [{index}] {line}" for index, line in enumerate(lines, start=1))


def _format_recipients(recipients: tuple[str, ...]) -> str:
    if not recipients:
        return "  (none)"
    return "\n".join(f"  - {recipient}" for recipient in recipients)


def _format_thread_status(lines: tuple[str, ...]) -> str:
    if not lines:
        return "  (no pairwise thread state available yet)"
    return "\n".join(f"  - {line}" for line in lines)


def _format_stale_messages(
    stale_messages: tuple[DiplomacyMessage, ...],
    new_timeline_lines: tuple[str, ...],
) -> str:
    lines: list[str] = []
    if new_timeline_lines:
        lines.append("  New events that arrived while you were thinking:")
        lines.extend(f"    - {line}" for line in new_timeline_lines)
    if stale_messages:
        lines.append("  Your stale draft was not delivered:")
        lines.extend(
            f"    - to {message.recipient} [{message.intent}]: {message.body}"
            for message in stale_messages
        )
    return "\n".join(lines) if lines else "  (none)"


def _format_sent_budget(sent_count: int | None, settings: Settings) -> str:
    cap = settings.messaging.latency_pairwise_private.max_messages_sent_per_power
    if cap is None or sent_count is None:
        return "- No global per-power message cap is configured."
    remaining = max(0, cap - sent_count)
    return (
        f"- Global sent-message budget this phase: {sent_count} / {cap} used; "
        f"{remaining} remaining."
    )
