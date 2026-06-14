from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol


class LLMChatSender(Protocol):
    """Minimal chat API used by the order-generation layer."""

    def send(
        self,
        *,
        model: str,
        messages: Sequence[Mapping[str, str]],
        max_tokens: int,
        response_format: Mapping[str, Any],
        **request_options: Any,
    ) -> Any:
        """Send one chat request and return an SDK-compatible chat result."""


class LLMClient(Protocol):
    """Client shape required by LLMPlayer."""

    chat: LLMChatSender
