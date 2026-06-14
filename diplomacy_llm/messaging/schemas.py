from __future__ import annotations

from typing import Any

from diplomacy_llm.messaging.models import MESSAGE_INTENTS

_MESSAGE_STRING_ARRAY_PROPERTY: dict[str, Any] = {
    "type": "array",
    "items": {"type": "string"},
    "description": "Optional lightweight structured references. Use an empty list if none apply.",
}
_MESSAGES_PROPERTY: dict[str, Any] = {
    "type": "array",
    "description": (
        "Private messages to send in this messaging decision. "
        "Return an empty list to send no messages."
    ),
    "items": {
        "type": "object",
        "properties": {
            "recipient": {
                "type": "string",
                "description": "Target power name. Must be one eligible recipient from the prompt.",
            },
            "intent": {
                "type": "string",
                "enum": list(MESSAGE_INTENTS),
                "description": "Lightweight message intent label for later analysis.",
            },
            "body": {
                "type": "string",
                "description": "Private natural-language message body.",
            },
            "referenced_locations": _MESSAGE_STRING_ARRAY_PROPERTY,
            "requested_orders": _MESSAGE_STRING_ARRAY_PROPERTY,
            "offered_orders": _MESSAGE_STRING_ARRAY_PROPERTY,
        },
        "required": [
            "recipient",
            "intent",
            "body",
            "referenced_locations",
            "requested_orders",
            "offered_orders",
        ],
        "additionalProperties": False,
    },
}

MESSAGES_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "diplomacy_messages",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "messages": _MESSAGES_PROPERTY,
            },
            "required": ["messages"],
            "additionalProperties": False,
        },
    },
}
