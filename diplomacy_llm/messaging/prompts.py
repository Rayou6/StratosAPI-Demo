from __future__ import annotations

from diplomacy_llm.messaging.models import (
    DiplomacyMessage,
    build_message_threads,
)


def render_visible_messages_prompt(
    messages: tuple[DiplomacyMessage, ...],
    *,
    power: str,
    powers_order: tuple[str, ...],
) -> str:
    """Render current-phase private messages visible to one power."""
    return _format_visible_messages(messages, power=power, powers_order=powers_order)


def _format_visible_messages(
    messages: tuple[DiplomacyMessage, ...],
    *,
    power: str,
    powers_order: tuple[str, ...],
) -> str:
    """Render visible private messages grouped by counterparty and window."""
    threads = build_message_threads(messages, power=power, powers_order=powers_order)
    if not threads:
        return "  (no delivered private messages visible to you yet)"

    lines: list[str] = []
    for thread in threads:
        lines.append(f"Thread with {thread.counterparty}:")
        for window in thread.windows:
            lines.append(f"  Message Window {window.message_window}:")
            if window.sent:
                for message in window.sent:
                    lines.append(f"    Sent [{message.intent}]: {message.body}")
            else:
                lines.append("    Sent: (none)")
            if window.received:
                for message in window.received:
                    lines.append(
                        f"    Received [{message.intent}]: {message.body}",
                    )
            else:
                lines.append("    Received: (none)")
    return "\n".join(lines)

