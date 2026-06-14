from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from diplomacy_llm.config import (
    PROJECT_ROOT,
    SETUPS_DIR,
    Settings,
    SetupConfigNotFoundError,
    load_settings_from_path,
    validate_safe_artifact_name,
)

DEMO_SETUPS_DIR = SETUPS_DIR
DEMO_DATA_DIR = PROJECT_ROOT / "demo_data"

DEMO_METADATA_FILENAME = "metadata.json"
DEMO_EVENTS_FILENAME = "events.jsonl"
DEMO_GAME_FILENAME = "game.jsonl"
DEMO_BOARDS_DIRNAME = "boards"


@dataclass(frozen=True)
class DemoRunPaths:
    """Canonical local paths for one isolated demo run."""

    run_id: str
    run_dir: Path
    metadata_path: Path
    events_path: Path
    game_path: Path
    boards_dir: Path


def available_demo_setup_names(setups_dir: Path = DEMO_SETUPS_DIR) -> list[str]:
    """Return demo setup names without inspecting benchmark setup directories."""
    return sorted(path.stem for path in setups_dir.glob("*.yaml"))


def demo_setup_path_for(name: str, setups_dir: Path = DEMO_SETUPS_DIR) -> Path:
    """Return the demo setup YAML path for a safe simple setup name."""
    safe_name = validate_safe_artifact_name(name, label="demo config name")
    return setups_dir / f"{safe_name}.yaml"


def load_demo_settings(name: str, setups_dir: Path = DEMO_SETUPS_DIR) -> Settings:
    """Load one demo-only setup from configs/demo_setups/."""
    path = demo_setup_path_for(name, setups_dir)
    if not path.exists():
        raise SetupConfigNotFoundError(
            name,
            path,
            available_demo_setup_names(setups_dir),
        )
    return load_settings_from_path(path)


def demo_run_paths(run_id: str, data_dir: Path = DEMO_DATA_DIR) -> DemoRunPaths:
    """Return the target file layout for one demo run under demo_data/<run_id>/."""
    safe_run_id = validate_safe_artifact_name(run_id, label="demo run_id")
    run_dir = data_dir / safe_run_id
    return DemoRunPaths(
        run_id=safe_run_id,
        run_dir=run_dir,
        metadata_path=run_dir / DEMO_METADATA_FILENAME,
        events_path=run_dir / DEMO_EVENTS_FILENAME,
        game_path=run_dir / DEMO_GAME_FILENAME,
        boards_dir=run_dir / DEMO_BOARDS_DIRNAME,
    )
