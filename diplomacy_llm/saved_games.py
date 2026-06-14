from __future__ import annotations

import json
from pathlib import Path, PureWindowsPath
from typing import Any

from diplomacy import Game
from diplomacy.utils.export import to_saved_game_format

from diplomacy_llm.config import PROJECT_ROOT

PROJECT_MAPS_DIR = PROJECT_ROOT / "configs" / "maps"


def export_saved_game(
    game: Game,
    output_path: Path,
    *,
    output_mode: str = "a",
) -> None:
    """Export a saved game transcript with portable project map references."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    to_saved_game_format(game, output_path=str(output_path), output_mode=output_mode)
    normalize_saved_game_map_paths(output_path)


def normalize_saved_game_map_paths(saved_game_path: Path) -> int:
    """Rewrite saved-game map fields to project-relative paths when possible."""
    if not saved_game_path.exists():
        return 0

    lines = saved_game_path.read_text(encoding="utf-8").splitlines()
    changed_count = 0
    output_lines: list[str] = []

    for line in lines:
        if not line.strip():
            output_lines.append(line)
            continue
        payload = json.loads(line)
        normalized, line_changes = _normalize_map_values(payload)
        changed_count += line_changes
        output_lines.append(
            json.dumps(normalized, ensure_ascii=False, separators=(",", ":")),
        )

    if changed_count:
        saved_game_path.write_text("\n".join(output_lines) + "\n", encoding="utf-8")

    return changed_count


def portable_map_reference(raw_map: str) -> str:
    """
    Return a stable saved-game map reference for local project maps.

    The Diplomacy exporter serializes the exact path used to create the Game.
    That can be a Windows or macOS absolute path. For maps that exist under this
    repository, store `configs/maps/<name>.map` so transcripts remain portable.
    """
    existing_path = _existing_local_path(raw_map)
    if existing_path is not None:
        project_relative = _project_relative(existing_path)
        if project_relative is not None:
            return project_relative
        return str(existing_path)

    filename = _path_name_for_any_platform(raw_map)
    if filename:
        project_map = PROJECT_MAPS_DIR / filename
        if project_map.exists():
            return project_map.relative_to(PROJECT_ROOT).as_posix()

    return raw_map


def resolve_map_path(raw_map: str) -> str:
    """Resolve a saved-game map reference on the current machine."""
    portable_map = portable_map_reference(raw_map)
    path = Path(portable_map)

    if path.is_absolute() and path.exists():
        return str(path)

    if not path.is_absolute():
        project_path = PROJECT_ROOT / path
        if project_path.exists():
            return str(project_path)

    filename = _path_name_for_any_platform(raw_map)
    if filename:
        project_map = PROJECT_MAPS_DIR / filename
        if project_map.exists():
            return str(project_map)

    if not _looks_like_path(raw_map):
        return raw_map

    msg = f"Map file not found: tried '{PROJECT_MAPS_DIR / filename}' and '{raw_map}'"
    raise FileNotFoundError(msg)


def _normalize_map_values(value: Any) -> tuple[Any, int]:
    if isinstance(value, dict):
        changes = 0
        for key, item in list(value.items()):
            if key == "map" and isinstance(item, str):
                normalized = portable_map_reference(item)
                if normalized != item:
                    value[key] = normalized
                    changes += 1
                continue
            normalized_item, item_changes = _normalize_map_values(item)
            value[key] = normalized_item
            changes += item_changes
        return value, changes

    if isinstance(value, list):
        changes = 0
        for index, item in enumerate(value):
            normalized_item, item_changes = _normalize_map_values(item)
            value[index] = normalized_item
            changes += item_changes
        return value, changes

    return value, 0


def _existing_local_path(raw_map: str) -> Path | None:
    path = Path(raw_map)
    if path.is_absolute() and path.exists():
        return path

    if not path.is_absolute():
        project_path = PROJECT_ROOT / path
        if project_path.exists():
            return project_path

    return None


def _project_relative(path: Path) -> str | None:
    try:
        return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return None


def _path_name_for_any_platform(raw_path: str) -> str:
    windows_name = PureWindowsPath(raw_path).name
    if windows_name:
        return windows_name
    return Path(raw_path).name


def _looks_like_path(raw_map: str) -> bool:
    return (
        Path(raw_map).is_absolute()
        or PureWindowsPath(raw_map).is_absolute()
        or "/" in raw_map
        or "\\" in raw_map
    )
