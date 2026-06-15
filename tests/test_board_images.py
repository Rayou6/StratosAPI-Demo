from pathlib import Path

from diplomacy import Game
from diplomacy.utils.export import to_saved_game_format

from diplomacy_llm.board_images import (
    iter_board_frames,
    phase_to_filename_stem,
    temporary_board_pngs,
)
from diplomacy_llm.config import PROJECT_ROOT


def test_phase_to_filename_stem_keeps_existing_svg_names() -> None:
    assert phase_to_filename_stem("S1901M") == "1901_spring"
    assert phase_to_filename_stem("F1901M") == "1901_fall"
    assert phase_to_filename_stem("W1901A") == "1901_winter"
    assert phase_to_filename_stem("S1902R") == "1902_spring_retreat"
    assert phase_to_filename_stem("final") == "final"


def test_iter_board_frames_replays_saved_jsonl(tmp_path: Path) -> None:
    saved_game = _write_one_phase_saved_game(tmp_path)

    frames = list(iter_board_frames(saved_game))

    assert len(frames) == 2
    assert frames[0].phase_index == 0
    assert frames[0].phase == "S1901M"
    assert frames[0].phase_type == "M"
    assert frames[0].filename_stem == "1901_spring"
    assert frames[0].is_final is False
    assert frames[0].svg.startswith("<?xml")
    assert frames[1].phase_index == 1
    assert frames[1].phase == "F1901M"
    assert frames[1].filename_stem == "final"
    assert frames[1].is_final is True


def test_temporary_board_pngs_removes_local_images(tmp_path: Path) -> None:
    saved_game = _write_one_phase_saved_game(tmp_path)

    with temporary_board_pngs(saved_game, output_width=320) as images:
        temp_root = images[0].path.parent.parent
        assert [image.filename for image in images] == ["1901_spring.png", "final.png"]
        assert images[0].metadata()["phase"] == "S1901M"
        assert all(image.path.read_bytes().startswith(b"\x89PNG") for image in images)

    assert not temp_root.exists()


def _write_one_phase_saved_game(tmp_path: Path) -> Path:
    saved_game = tmp_path / "game.jsonl"
    game = Game(map_name=str(PROJECT_ROOT / "configs" / "maps" / "EFGA_11.map"))
    for power, units in game.get_units().items():
        game.set_orders(power, [f"{unit} H" for unit in units])
        game.set_wait(power, False)
    game.process()
    to_saved_game_format(game, output_path=str(saved_game), output_mode="a")
    return saved_game
