import json
from pathlib import Path

from diplomacy import Game

from diplomacy_llm.config import PROJECT_ROOT
from diplomacy_llm.saved_games import (
    export_saved_game,
    normalize_saved_game_map_paths,
    portable_map_reference,
    resolve_map_path,
)

WINDOWS_PROJECT_MAP = (
    r"C:\Users\Rayan\Desktop\Prog Project\StratosAPI\configs\maps\EFGA_11.map"
)


def test_portable_map_reference_remaps_windows_project_map() -> None:
    assert portable_map_reference(WINDOWS_PROJECT_MAP) == "configs/maps/EFGA_11.map"


def test_resolve_map_path_remaps_windows_project_map() -> None:
    assert resolve_map_path(WINDOWS_PROJECT_MAP) == str(
        PROJECT_ROOT / "configs" / "maps" / "EFGA_11.map",
    )


def test_normalize_saved_game_map_paths_rewrites_nested_map_fields(
    tmp_path: Path,
) -> None:
    saved_game = tmp_path / "game.jsonl"
    saved_game.write_text(
        json.dumps(
            {
                "map": WINDOWS_PROJECT_MAP,
                "phases": [
                    {
                        "state": {
                            "map": WINDOWS_PROJECT_MAP,
                        },
                    },
                ],
            },
        )
        + "\n",
        encoding="utf-8",
    )

    assert normalize_saved_game_map_paths(saved_game) == 2

    payload = json.loads(saved_game.read_text(encoding="utf-8"))
    assert payload["map"] == "configs/maps/EFGA_11.map"
    assert payload["phases"][0]["state"]["map"] == "configs/maps/EFGA_11.map"


def test_export_saved_game_writes_project_relative_map(tmp_path: Path) -> None:
    saved_game = tmp_path / "game.jsonl"
    game = Game(map_name=str(PROJECT_ROOT / "configs" / "maps" / "EFGA_11.map"))
    for power, units in game.get_units().items():
        game.set_orders(power, [f"{unit} H" for unit in units])
        game.set_wait(power, False)
    game.process()

    export_saved_game(game, saved_game, output_mode="w")

    payload = json.loads(saved_game.read_text(encoding="utf-8"))
    assert payload["map"] == "configs/maps/EFGA_11.map"
    assert payload["phases"][0]["state"]["map"] == "configs/maps/EFGA_11.map"
