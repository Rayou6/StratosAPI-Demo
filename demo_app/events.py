from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

DEMO_EVENT_SCHEMA = "stratosapi-demo-events-v1"

DEMO_EVENT_TYPES: tuple[str, ...] = (
    "run_started",
    "phase_started",
    "message_sent",
    "orders_submitted",
    "reasoning_available",
    "phase_resolved",
    "year_summary",
    "game_finished",
    "run_error",
)

_STRING_FIELDS = (
    "phase",
    "phase_type",
    "power",
    "sender",
    "recipient",
    "intent",
    "body",
    "message",
    "reasoning",
    "status",
    "winner",
    "error",
    "system_event",
    "system_reason",
)
_STRING_LIST_FIELDS = (
    "orders",
    "referenced_locations",
    "requested_orders",
    "offered_orders",
    "results",
)


class DemoEventError(ValueError):
    """Raised when demo timeline data is malformed."""


def load_demo_events(events_path: Path) -> list[dict[str, object]]:
    """Read a demo events.jsonl file into normalized JSON-safe events."""
    if not events_path.exists():
        return []

    events: list[dict[str, object]] = []
    with events_path.open(encoding="utf-8") as event_file:
        for line_number, raw_line in enumerate(event_file, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                message = _error_message(events_path, line_number, f"invalid JSON: {exc.msg}")
                raise DemoEventError(message) from exc
            if not isinstance(raw, Mapping):
                message = _error_message(
                    events_path,
                    line_number,
                    "event must be a JSON object",
                )
                raise DemoEventError(message)
            events.append(
                _normalize_event(
                    raw,
                    event_index=len(events),
                    source=str(events_path),
                    line_number=line_number,
                ),
            )
    return events


def attach_events_to_frames(
    frames: Sequence[Mapping[str, object]],
    events: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Return frame payloads with phase-scoped events attached."""
    return [
        {
            **dict(frame),
            "events": [
                dict(event) for event in events if _event_matches_frame(event, frame)
            ],
        }
        for frame in frames
    ]


def _normalize_event(
    raw: Mapping[object, object],
    *,
    event_index: int,
    source: str,
    line_number: int,
) -> dict[str, object]:
    event_type = raw.get("type")
    if not isinstance(event_type, str) or not event_type.strip():
        message = _error_message(source, line_number, "event type must be a string")
        raise DemoEventError(message)

    event_type = event_type.strip()
    if event_type not in DEMO_EVENT_TYPES:
        message = _error_message(
            source,
            line_number,
            f"unknown demo event type: {event_type}",
        )
        raise DemoEventError(message)

    event = {str(key): value for key, value in raw.items()}
    event["type"] = event_type
    event["event_index"] = event_index
    event["sequence"] = _non_negative_int_or_default(
        raw.get("sequence"),
        default=event_index,
        field="sequence",
        source=source,
        line_number=line_number,
    )
    event["phase_index"] = _optional_non_negative_int(
        raw.get("phase_index"),
        field="phase_index",
        source=source,
        line_number=line_number,
    )

    for field in _STRING_FIELDS:
        event[field] = _optional_string(
            raw.get(field),
            field=field,
            source=source,
            line_number=line_number,
        )
    for field in _STRING_LIST_FIELDS:
        event[field] = _string_list(raw.get(field))

    scores = raw.get("scores")
    if scores is not None:
        event["scores"] = _scores(scores, source=source, line_number=line_number)

    return event


def _event_matches_frame(
    event: Mapping[str, object],
    frame: Mapping[str, object],
) -> bool:
    event_phase_index = event.get("phase_index")
    frame_phase_index = frame.get("phase_index")
    if (
        event_phase_index is not None
        and frame_phase_index is not None
        and event_phase_index == frame_phase_index
    ):
        return True

    event_phase = event.get("phase")
    frame_phase = frame.get("phase")
    return event_phase is not None and event_phase == frame_phase


def _optional_string(
    value: object,
    *,
    field: str,
    source: str,
    line_number: int,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        message = _error_message(source, line_number, f"{field} must be a string")
        raise DemoEventError(message)
    return value


def _optional_non_negative_int(
    value: object,
    *,
    field: str,
    source: str,
    line_number: int,
) -> int | None:
    if value is None:
        return None
    return _non_negative_int(
        value,
        field=field,
        source=source,
        line_number=line_number,
    )


def _non_negative_int_or_default(
    value: object,
    *,
    default: int,
    field: str,
    source: str,
    line_number: int,
) -> int:
    if value is None:
        return default
    return _non_negative_int(
        value,
        field=field,
        source=source,
        line_number=line_number,
    )


def _non_negative_int(
    value: object,
    *,
    field: str,
    source: str,
    line_number: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        message = _error_message(
            source,
            line_number,
            f"{field} must be a non-negative integer",
        )
        raise DemoEventError(message)
    return value


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    return [str(item) for item in value]


def _scores(
    value: object,
    *,
    source: str,
    line_number: int,
) -> dict[str, int]:
    if not isinstance(value, Mapping):
        message = _error_message(source, line_number, "scores must be an object")
        raise DemoEventError(message)

    scores: dict[str, int] = {}
    for power, score in value.items():
        if isinstance(score, bool) or not isinstance(score, int):
            message = _error_message(
                source,
                line_number,
                "scores values must be integers",
            )
            raise DemoEventError(message)
        scores[str(power)] = score
    return scores


def _error_message(source: object, line_number: int, detail: str) -> str:
    return f"{source}:{line_number}: {detail}"
