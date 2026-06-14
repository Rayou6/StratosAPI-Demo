from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

_TRAILING_TIMESTAMP_RE = re.compile(
    r"\s*\(\d{2}\.\d{2}\.\d{4} - \d{2}:\d{2}(?::\d{2})?\)\s*$",
)


def demo_run_label(run_id: str, metadata: Mapping[str, Any]) -> str:
    """Return the human-facing label for a saved demo run."""
    label = _generated_run_base_label(metadata)
    if label is None:
        label_value = metadata.get("name") or metadata.get("title")
        label = str(label_value) if label_value else run_id
    stamp = demo_created_at_stamp(metadata.get("created_at"))
    if stamp:
        label = _strip_trailing_timestamp(label)
        return f"{label} ({stamp})"
    return label


def demo_run_name(setup_label: str, mode_label: str, created_at: str) -> str:
    """Return the persisted display name for a newly created demo run."""
    label = f"{setup_label} ({mode_label})"
    stamp = demo_created_at_stamp(created_at)
    return f"{label} ({stamp})" if stamp else label


def demo_setup_label(setup_name: str) -> str:
    """Return the compact setup label shown in the local demo UI."""
    label = setup_name.removeprefix("demo_").replace("_", " ").strip()
    return label or setup_name


def demo_mode_label(mode: str) -> str:
    """Return the compact run mode label shown in demo run names."""
    return mode


def demo_created_at_stamp(value: object) -> str | None:
    """Format an ISO timestamp as a compact local date/time label."""
    if not isinstance(value, str) or not value:
        return None
    try:
        created_at = datetime.fromisoformat(value)
    except ValueError:
        return None
    return created_at.astimezone().strftime("%d.%m.%Y - %H:%M")


def _strip_trailing_timestamp(label: str) -> str:
    return _TRAILING_TIMESTAMP_RE.sub("", label).strip()


def _generated_run_base_label(metadata: Mapping[str, Any]) -> str | None:
    run_mode = metadata.get("run_mode")
    if not isinstance(run_mode, str) or run_mode != "live":
        return None

    setup_label_value = metadata.get("setup_label")
    if isinstance(setup_label_value, str) and setup_label_value.strip():
        setup_label = setup_label_value
    else:
        setup_name = metadata.get("setup_name") or metadata.get("setup")
        if not isinstance(setup_name, str) or not setup_name.strip():
            return None
        setup_label = demo_setup_label(setup_name)

    return f"{setup_label} ({demo_mode_label(run_mode)})"
