from __future__ import annotations

import json
import platform
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from diplomacy import Game

from diplomacy_llm.saved_games import resolve_map_path

DEFAULT_BOARD_IMAGE_WIDTH: int = 1200


@dataclass(frozen=True)
class BoardFrame:
    """One board state reconstructed from a saved game transcript."""

    phase_index: int
    phase: str
    phase_type: str
    filename_stem: str
    is_final: bool
    svg: str


@dataclass(frozen=True)
class BoardImage:
    """A rendered board image and the phase metadata needed to identify it."""

    phase_index: int
    phase: str
    phase_type: str
    filename_stem: str
    is_final: bool
    path: Path
    source_jsonl: Path

    @property
    def filename(self) -> str:
        return self.path.name

    def metadata(self) -> dict[str, object]:
        return {
            "phase_index": self.phase_index,
            "phase": self.phase,
            "phase_type": self.phase_type,
            "filename": self.filename,
            "is_final": self.is_final,
            "source_jsonl": str(self.source_jsonl),
        }


def load_saved_game(saved_game_path: Path) -> Mapping[str, object]:
    """Load the first saved-game JSON object from a JSONL transcript."""
    with saved_game_path.open(encoding="utf-8") as saved_game_file:
        first_line = saved_game_file.readline()
    if not first_line:
        msg = f"Saved game file is empty: {saved_game_path}"
        raise ValueError(msg)
    raw = json.loads(first_line)
    if not isinstance(raw, Mapping):
        msg = f"Saved game root must be a JSON object: {saved_game_path}"
        raise TypeError(msg)
    return raw


def phase_to_filename_stem(phase_name: str) -> str:
    """Return the stable board filename stem used for replay artifacts."""
    if phase_name == "final":
        return "final"
    season_map = {"S": "spring", "F": "fall", "W": "winter"}
    season = season_map.get(phase_name[0], "unknown")
    year = phase_name[1:5]
    suffix = "_retreat" if phase_name.endswith("R") else ""
    return f"{year}_{season}{suffix}"


def iter_board_frames(saved_game_path: Path) -> Iterator[BoardFrame]:
    """Yield SVG board states reconstructed from a saved game JSONL transcript."""
    raw = load_saved_game(saved_game_path)
    raw_map = raw.get("map")
    if not isinstance(raw_map, str):
        msg = f"Saved game is missing a string map field: {saved_game_path}"
        raise TypeError(msg)

    phases = raw.get("phases")
    if not isinstance(phases, list):
        msg = f"Saved game is missing a phases list: {saved_game_path}"
        raise TypeError(msg)

    game = Game(map_name=resolve_map_path(raw_map))

    for phase_index, phase in enumerate(phases):
        if not isinstance(phase, Mapping):
            msg = f"Saved game phase {phase_index} must be a JSON object"
            raise TypeError(msg)

        phase_name = _phase_name(phase)
        orders = _phase_orders(phase)
        if _is_terminal_orders(orders):
            yield BoardFrame(
                phase_index=phase_index,
                phase=phase_name,
                phase_type=_phase_type(phase_name),
                filename_stem="final",
                is_final=True,
                svg=game.render(),
            )
            break

        yield BoardFrame(
            phase_index=phase_index,
            phase=phase_name,
            phase_type=_phase_type(phase_name),
            filename_stem=phase_to_filename_stem(phase_name),
            is_final=False,
            svg=game.render(),
        )

        _process_phase_orders(game, orders)
    else:
        yield BoardFrame(
            phase_index=len(phases),
            phase="final",
            phase_type="",
            filename_stem="final",
            is_final=True,
            svg=game.render(),
        )


def render_board_pngs(
    saved_game_path: Path,
    output_dir: Path,
    *,
    output_width: int = DEFAULT_BOARD_IMAGE_WIDTH,
) -> list[BoardImage]:
    """Render compact PNG board images from a saved game JSONL transcript."""
    output_dir.mkdir(parents=True, exist_ok=True)
    source_jsonl = saved_game_path.resolve()
    images: list[BoardImage] = []
    cairosvg = _import_cairosvg()

    for frame in iter_board_frames(saved_game_path):
        output_path = output_dir / f"{frame.filename_stem}.png"
        cairosvg.svg2png(
            bytestring=frame.svg.encode("utf-8"),
            write_to=str(output_path),
            output_width=output_width,
        )
        images.append(
            BoardImage(
                phase_index=frame.phase_index,
                phase=frame.phase,
                phase_type=frame.phase_type,
                filename_stem=frame.filename_stem,
                is_final=frame.is_final,
                path=output_path,
                source_jsonl=source_jsonl,
            ),
        )
    return images


def _import_cairosvg() -> object:
    """Import CairoSVG after installing a Homebrew Cairo lookup fallback."""
    _install_homebrew_cairo_lookup_fallback()
    import cairosvg  # noqa: PLC0415

    return cairosvg


def _install_homebrew_cairo_lookup_fallback() -> None:
    """
    Help cairocffi find Cairo installed by Homebrew on macOS.

    `cairocffi` asks `ctypes.util.find_library` for cairo before trying bare
    filenames. In the Codex macOS environment that lookup can return None even
    when Homebrew has installed `/opt/homebrew/lib/libcairo.2.dylib`.
    """
    if platform.system() != "Darwin":
        return

    import ctypes.util  # noqa: PLC0415

    original_find_library = ctypes.util.find_library
    if original_find_library("cairo"):
        return

    candidate = next(
        (
            path
            for path in (
                Path("/opt/homebrew/lib/libcairo.2.dylib"),
                Path("/opt/homebrew/opt/cairo/lib/libcairo.2.dylib"),
                Path("/usr/local/lib/libcairo.2.dylib"),
                Path("/usr/local/opt/cairo/lib/libcairo.2.dylib"),
            )
            if path.exists()
        ),
        None,
    )
    if candidate is None:
        return

    cairo_names = {
        "cairo",
        "cairo-2",
        "libcairo",
        "libcairo-2",
        "libcairo.2.dylib",
        "libcairo.so.2",
    }

    def find_library(name: str) -> str | None:
        if name in cairo_names:
            return str(candidate)
        return original_find_library(name)

    ctypes.util.find_library = find_library


@contextmanager
def temporary_board_pngs(
    saved_game_path: Path,
    *,
    parent_dir: Path | None = None,
    output_width: int = DEFAULT_BOARD_IMAGE_WIDTH,
) -> Iterator[list[BoardImage]]:
    """Render board PNGs in a temporary directory that is removed on exit."""
    with TemporaryDirectory(dir=parent_dir) as tmp_dir:
        output_dir = Path(tmp_dir) / "board_images"
        yield render_board_pngs(
            saved_game_path,
            output_dir,
            output_width=output_width,
        )


def _phase_name(phase: Mapping[object, object]) -> str:
    value = phase.get("name")
    if not isinstance(value, str):
        msg = "Saved game phase is missing a string name"
        raise TypeError(msg)
    return value


def _phase_type(phase_name: str) -> str:
    return phase_name[-1:] if phase_name.endswith(("M", "R", "A")) else ""


def _phase_orders(
    phase: Mapping[object, object],
) -> Mapping[object, object] | None:
    orders = phase.get("orders")
    if orders is None:
        return None
    if isinstance(orders, Mapping):
        return orders
    msg = "Saved game phase orders must be an object or null"
    raise ValueError(msg)


def _is_terminal_orders(orders: Mapping[object, object] | None) -> bool:
    return (
        orders is None or not orders or all(value is None for value in orders.values())
    )


def _process_phase_orders(
    game: Game,
    orders: Mapping[object, object] | None,
) -> None:
    if orders is None:
        return
    for power, power_orders in orders.items():
        power_name = str(power)
        if power_orders:
            game.set_orders(power_name, list(power_orders))
        game.set_wait(power_name, False)
    game.process()
