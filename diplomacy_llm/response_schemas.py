"""Structured response schemas for LLM order calls."""

from typing import Any

_ORDERS_PROPERTY: dict[str, Any] = {
    "type": "array",
    "items": {"type": "string"},
    "description": (
        "List of orders or adjustment decisions. The phase prompt "
        "defines how many entries to return. Each entry must be "
        "taken exactly from the valid orders list provided."
    ),
}
_REASONING_PROPERTY: dict[str, Any] = {
    "type": "string",
    "description": (
        "Brief explanation of your strategic thinking this phase (1-3 sentences)."
    ),
}


ORDERS_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "diplomacy_orders",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "orders": _ORDERS_PROPERTY,
                "reasoning": _REASONING_PROPERTY,
            },
            "required": ["orders", "reasoning"],
            "additionalProperties": False,
        },
    },
}

def orders_response_schema_for() -> dict[str, Any]:
    """Return the structured output schema for one order-generation call."""
    return ORDERS_RESPONSE_SCHEMA
