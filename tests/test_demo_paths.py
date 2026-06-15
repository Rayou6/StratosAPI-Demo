from pathlib import Path

import pytest

from diplomacy_llm.config import InvalidArtifactNameError, SetupConfigNotFoundError
from diplomacy_llm.demo_paths import (
    DEMO_DATA_DIR,
    DEMO_SETUPS_DIR,
    available_demo_setup_names,
    demo_run_paths,
    demo_setup_path_for,
    load_demo_settings,
)


def test_demo_setup_listing_only_reads_demo_setups() -> None:
    names = available_demo_setup_names()

    assert names == sorted(path.stem for path in DEMO_SETUPS_DIR.glob("*.yaml"))
    assert "short_demo_EFGA_baseline" in names
    assert names == [
        "demo_EFGA_aggressive",
        "demo_EFGA_baseline",
        "short_demo_EFGA_aggressive",
        "short_demo_EFGA_baseline",
    ]


def test_load_demo_settings_loads_short_baseline_setup() -> None:
    settings = load_demo_settings("short_demo_EFGA_baseline")

    assert settings.map_name == "configs/maps/EFGA_11.map"
    assert settings.max_years == 4
    assert settings.win_score == 11
    assert settings.shuffle_models is True
    assert settings.messaging_variant == "latency_pairwise_private"
    assert settings.messaging_enabled_powers == [
        "ENGLAND",
        "FRANCE",
        "GERMANY",
        "AUSTRIA",
    ]
    assert settings.messaging.latency_pairwise_private.max_messages_per_response == 3
    assert settings.messaging.latency_pairwise_private.max_turns_per_conversation == 4


def test_load_demo_settings_does_not_fall_back_to_benchmark_setups(
    tmp_path: Path,
) -> None:
    with pytest.raises(SetupConfigNotFoundError) as exc_info:
        load_demo_settings("missing_demo_setup", setups_dir=tmp_path)

    exc = exc_info.value
    assert exc.path == tmp_path / "missing_demo_setup.yaml"
    assert exc.available == []


def test_demo_path_helpers_reject_path_like_names() -> None:
    with pytest.raises(InvalidArtifactNameError):
        demo_setup_path_for("../demo_setups/short_demo_EFGA_baseline")

    with pytest.raises(InvalidArtifactNameError):
        demo_run_paths(r"..\data\runs")


def test_demo_run_paths_use_demo_data_layout(tmp_path: Path) -> None:
    data_dir = tmp_path / "demo_data"
    paths = demo_run_paths("demo_run_001", data_dir=data_dir)

    assert DEMO_DATA_DIR.name == "demo_data"
    assert paths.run_id == "demo_run_001"
    assert paths.run_dir == data_dir / "demo_run_001"
    assert paths.metadata_path == data_dir / "demo_run_001" / "metadata.json"
    assert paths.events_path == data_dir / "demo_run_001" / "events.jsonl"
    assert paths.game_path == data_dir / "demo_run_001" / "game.jsonl"
    assert paths.boards_dir == data_dir / "demo_run_001" / "boards"
