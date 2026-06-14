from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from demo_app.events import (
    DEMO_EVENT_SCHEMA,
    attach_events_to_frames,
    load_demo_events,
)
from demo_app.labels import demo_run_label
from diplomacy_llm.board_images import BoardFrame, iter_board_frames
from diplomacy_llm.config import InvalidArtifactNameError
from diplomacy_llm.demo_paths import DEMO_DATA_DIR, DemoRunPaths, demo_run_paths


class DemoRunNotFoundError(FileNotFoundError):
    """Raised when a requested demo run is missing or incomplete."""


def list_demo_runs(data_dir: Path = DEMO_DATA_DIR) -> list[dict[str, object]]:
    """List demo runs that contain replay or failure-inspection artifacts."""
    if not data_dir.exists():
        return []

    runs: list[dict[str, object]] = []
    for run_dir in sorted(path for path in data_dir.iterdir() if path.is_dir()):
        try:
            paths = demo_run_paths(run_dir.name, data_dir=data_dir)
        except InvalidArtifactNameError:
            continue
        replay_ready = paths.game_path.exists()
        has_inspectable_artifact = (
            replay_ready or paths.events_path.exists() or paths.metadata_path.exists()
        )
        if not has_inspectable_artifact:
            continue
        metadata = _read_metadata(paths.metadata_path)
        runs.append(
            {
                "run_id": paths.run_id,
                "label": _run_label(paths.run_id, metadata),
                "metadata": metadata,
                "has_events": paths.events_path.exists(),
                "has_boards": paths.boards_dir.exists(),
                "replay_ready": replay_ready,
            },
        )
    return runs


def load_demo_replay(
    run_id: str,
    data_dir: Path = DEMO_DATA_DIR,
) -> dict[str, object]:
    """Load a demo run and reconstruct board frames when game.jsonl exists."""
    paths = demo_run_paths(run_id, data_dir=data_dir)
    _require_run_dir(paths)
    metadata = _read_metadata(paths.metadata_path)
    events = load_demo_events(paths.events_path)
    replay_ready = paths.game_path.exists()
    frames = (
        attach_events_to_frames(
            [_frame_payload(frame) for frame in iter_board_frames(paths.game_path)],
            events,
        )
        if replay_ready
        else []
    )
    return {
        "run_id": paths.run_id,
        "label": _run_label(paths.run_id, metadata),
        "metadata": metadata,
        "frames": frames,
        "frame_count": len(frames),
        "has_events": paths.events_path.exists(),
        "replay_ready": replay_ready,
        "event_schema": DEMO_EVENT_SCHEMA,
        "event_count": len(events),
        "events": events,
        "events_path": str(paths.events_path),
    }


def _require_run_dir(paths: DemoRunPaths) -> None:
    if not paths.run_dir.exists():
        msg = f"Demo run not found: {paths.run_id}"
        raise DemoRunNotFoundError(msg)


def _read_metadata(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        return {}
    return dict(data)


def _run_label(run_id: str, metadata: Mapping[str, Any]) -> str:
    return demo_run_label(run_id, metadata)


def _frame_payload(frame: BoardFrame) -> dict[str, object]:
    return {
        "phase_index": frame.phase_index,
        "phase": frame.phase,
        "phase_type": frame.phase_type,
        "filename_stem": frame.filename_stem,
        "is_final": frame.is_final,
        "svg": frame.svg,
    }
