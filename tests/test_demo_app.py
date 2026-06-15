from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import urlparse

import pytest
from diplomacy import Game
from diplomacy.utils.export import to_saved_game_format

from demo_app.events import DEMO_EVENT_SCHEMA, DemoEventError, load_demo_events
from demo_app.labels import demo_run_label
from demo_app.replay import list_demo_runs, load_demo_replay
from demo_app.server import create_server
from diplomacy_llm.config import PROJECT_ROOT


def test_list_demo_runs_only_returns_replayable_runs(tmp_path: Path) -> None:
    data_dir = tmp_path / "demo_data"
    _write_demo_run(data_dir, "sample_replay")
    (data_dir / "empty_run").mkdir()
    (data_dir / "bad name").mkdir()

    runs = list_demo_runs(data_dir)

    assert runs == [
        {
            "run_id": "sample_replay",
            "label": "Sample Replay",
            "metadata": {
                "name": "Sample Replay",
                "setup": "short_demo_EFGA_baseline",
                "map": "EFGA_11",
                "powers": ["ENGLAND", "FRANCE", "GERMANY"],
            },
            "has_events": True,
            "has_boards": False,
            "replay_ready": True,
        },
    ]


def test_load_demo_replay_reconstructs_svg_frames_from_game_jsonl(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "demo_data"
    _write_demo_run(data_dir, "sample_replay")

    replay = load_demo_replay("sample_replay", data_dir)

    assert replay["run_id"] == "sample_replay"
    assert replay["label"] == "Sample Replay"
    assert replay["frame_count"] == 2
    assert replay["has_events"] is True
    assert replay["replay_ready"] is True
    assert replay["event_schema"] == DEMO_EVENT_SCHEMA
    assert replay["event_count"] == 7
    frames = replay["frames"]
    assert isinstance(frames, list)
    assert frames[0]["phase"] == "S1901M"
    assert frames[0]["filename_stem"] == "1901_spring"
    assert [event["type"] for event in frames[0]["events"]] == [
        "run_started",
        "phase_started",
        "message_sent",
        "orders_submitted",
        "reasoning_available",
    ]
    assert str(frames[0]["svg"]).startswith("<?xml")
    assert frames[1]["is_final"] is True
    assert [event["type"] for event in frames[1]["events"]] == [
        "phase_resolved",
        "game_finished",
    ]


def test_failed_demo_run_without_game_jsonl_remains_inspectable(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "demo_data"
    run_dir = data_dir / "failed_live"
    run_dir.mkdir(parents=True)
    (run_dir / "metadata.json").write_text(
        json.dumps(
            {
                "name": "Failed Live",
                "status": "error",
                "run_mode": "live",
                "error": {
                    "type": "TimeoutError",
                    "message": "Provider timed out",
                },
            },
        ),
        encoding="utf-8",
    )
    (run_dir / "events.jsonl").write_text(
        json.dumps(
            {
                "type": "run_error",
                "phase": None,
                "phase_index": None,
                "status": "error",
                "error": "Provider timed out",
                "error_type": "TimeoutError",
            },
        )
        + "\n",
        encoding="utf-8",
    )

    runs = list_demo_runs(data_dir)
    replay = load_demo_replay("failed_live", data_dir)

    assert runs == [
        {
            "run_id": "failed_live",
            "label": "Failed Live",
            "metadata": {
                "name": "Failed Live",
                "status": "error",
                "run_mode": "live",
                "error": {
                    "type": "TimeoutError",
                    "message": "Provider timed out",
                },
            },
            "has_events": True,
            "has_boards": False,
            "replay_ready": False,
        },
    ]
    assert replay["frame_count"] == 0
    assert replay["frames"] == []
    assert replay["replay_ready"] is False
    assert replay["event_count"] == 1
    assert replay["events"][0]["type"] == "run_error"


def test_load_demo_events_normalizes_phase_timeline(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "message_sent",
                        "phase": "S1901M",
                        "phase_index": 0,
                        "sender": "FRANCE",
                        "recipient": "ENGLAND",
                        "intent": "coordinate",
                        "body": "Keep ENG quiet.",
                        "referenced_locations": ["ENG"],
                    },
                ),
                json.dumps(
                    {
                        "type": "orders_submitted",
                        "phase": "S1901M",
                        "phase_index": 0,
                        "power": "FRANCE",
                        "orders": ["F BRE H"],
                    },
                ),
                json.dumps(
                    {
                        "type": "phase_resolved",
                        "phase": "S1901M",
                        "phase_index": 0,
                        "scores": {"FRANCE": 3, "ENGLAND": 3},
                    },
                ),
            ],
        )
        + "\n",
        encoding="utf-8",
    )

    events = load_demo_events(events_path)

    assert events[0]["event_index"] == 0
    assert events[0]["sequence"] == 0
    assert events[0]["referenced_locations"] == ["ENG"]
    assert events[1]["orders"] == ["F BRE H"]
    assert events[2]["scores"] == {"FRANCE": 3, "ENGLAND": 3}


def test_load_demo_events_rejects_malformed_jsonl(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_text('{"type": "unknown"}\n', encoding="utf-8")

    with pytest.raises(DemoEventError, match="unknown demo event type"):
        load_demo_events(events_path)


def test_demo_run_label_appends_created_at_stamp() -> None:
    metadata = {
        "name": "EFGA 11 short press (live)",
        "created_at": "2026-06-09T14:02:00",
    }

    assert (
        demo_run_label("demo_run", metadata)
        == "EFGA 11 short press (live) (09.06.2026 - 14:02)"
    )


def test_demo_run_label_does_not_duplicate_existing_stamp() -> None:
    metadata = {
        "name": "EFGA 11 short press (live) (09.06.2026 - 14:02)",
        "created_at": "2026-06-09T14:02:00",
    }

    assert (
        demo_run_label("demo_run", metadata)
        == "EFGA 11 short press (live) (09.06.2026 - 14:02)"
    )


def test_demo_run_label_removes_seconds_from_existing_stamp() -> None:
    metadata = {
        "name": "EFGA 11 short press (live) (09.06.2026 - 14:02:59)",
        "created_at": "2026-06-09T14:02:59",
    }

    assert (
        demo_run_label("demo_run", metadata)
        == "EFGA 11 short press (live) (09.06.2026 - 14:02)"
    )


def test_demo_run_label_cleans_generated_live_run_names() -> None:
    metadata = {
        "name": "short_demo_EFGA_baseline (live)",
        "setup": "short_demo_EFGA_baseline",
        "run_mode": "live",
        "created_at": "2026-06-09T14:02:00",
    }

    assert (
        demo_run_label("demo_run", metadata)
        == "short demo EFGA baseline (live) (09.06.2026 - 14:02)"
    )


def test_demo_server_serves_run_api_and_static_ui(tmp_path: Path) -> None:
    data_dir = tmp_path / "demo_data"
    _write_demo_run(data_dir, "sample_replay")
    server = create_server(port=0, data_dir=data_dir)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        runs = _read_json(f"{base_url}/api/runs")
        replay = _read_json(f"{base_url}/api/runs/sample_replay")
        html = _read_text(base_url, "/")
        styles = _read_text(base_url, "/styles.css")
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert runs["runs"][0]["run_id"] == "sample_replay"
    assert replay["frame_count"] == 2
    assert replay["event_count"] == 7
    assert "Demo Live Replay" in html
    assert 'class="mode-tabs"' in html
    assert 'data-mode="live"' in html
    assert 'data-mode="replay"' in html
    assert 'data-mode="dry_run"' not in html
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in styles
    assert 'data-detail-tab="stats"' in html
    assert "/app.js" in html


def test_demo_server_lists_and_serves_music_files(tmp_path: Path) -> None:
    data_dir = tmp_path / "demo_data"
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    (music_dir / "2. Second Track.mp3").write_bytes(b"second")
    (music_dir / "1. First Track.mp3").write_bytes(b"first")
    (music_dir / "notes.txt").write_text("ignore me", encoding="utf-8")
    server = create_server(port=0, data_dir=data_dir, music_dir=music_dir)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        music = _read_json(f"{base_url}/api/music")
        first_track = _read_bytes(base_url, "/music/1.%20First%20Track.mp3")
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert music["tracks"] == [
        {
            "filename": "1. First Track.mp3",
            "label": "First Track",
            "url": "/music/1.%20First%20Track.mp3",
        },
        {
            "filename": "2. Second Track.mp3",
            "label": "Second Track",
            "url": "/music/2.%20Second%20Track.mp3",
        },
    ]
    assert first_track == b"first"


def _read_text(base_url: str, path: str) -> str:
    parsed = urlparse(base_url)
    if parsed.hostname is None or parsed.port is None:
        msg = f"Expected a local HTTP URL with a port: {base_url}"
        raise ValueError(msg)
    connection = HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        assert response.status == 200
        return response.read().decode("utf-8")
    finally:
        connection.close()


def _read_bytes(base_url: str, path: str) -> bytes:
    parsed = urlparse(base_url)
    if parsed.hostname is None or parsed.port is None:
        msg = f"Expected a local HTTP URL with a port: {base_url}"
        raise ValueError(msg)
    connection = HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        assert response.status == 200
        return response.read()
    finally:
        connection.close()


def _read_json(url: str) -> dict[str, object]:
    parsed = urlparse(url)
    payload = json.loads(_read_text(f"{parsed.scheme}://{parsed.netloc}", parsed.path))
    assert isinstance(payload, dict)
    return payload


def _write_demo_run(data_dir: Path, run_id: str) -> Path:
    run_dir = data_dir / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "metadata.json").write_text(
        json.dumps(
            {
                "name": "Sample Replay",
                "setup": "short_demo_EFGA_baseline",
                "map": "EFGA_11",
                "powers": ["ENGLAND", "FRANCE", "GERMANY"],
            },
        ),
        encoding="utf-8",
    )
    (run_dir / "events.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "run_started",
                        "phase": "S1901M",
                        "phase_index": 0,
                        "status": "replay",
                    },
                ),
                json.dumps(
                    {
                        "type": "phase_started",
                        "phase": "S1901M",
                        "phase_index": 0,
                        "phase_type": "M",
                    },
                ),
                json.dumps(
                    {
                        "type": "message_sent",
                        "phase": "S1901M",
                        "phase_index": 0,
                        "phase_type": "M",
                        "sender": "FRANCE",
                        "recipient": "ENGLAND",
                        "intent": "coordinate",
                        "body": "Please hold London.",
                        "requested_orders": ["F LON H"],
                    },
                ),
                json.dumps(
                    {
                        "type": "orders_submitted",
                        "phase": "S1901M",
                        "phase_index": 0,
                        "phase_type": "M",
                        "power": "FRANCE",
                        "orders": ["F BRE H", "A MAR H", "A PAR H"],
                    },
                ),
                json.dumps(
                    {
                        "type": "reasoning_available",
                        "phase": "S1901M",
                        "phase_index": 0,
                        "phase_type": "M",
                        "power": "FRANCE",
                        "reasoning": "Hold home centers for this fixture.",
                    },
                ),
                json.dumps(
                    {
                        "type": "phase_resolved",
                        "phase": "F1901M",
                        "phase_index": 1,
                        "scores": {"ENGLAND": 3, "FRANCE": 3, "GERMANY": 3},
                    },
                ),
                json.dumps(
                    {
                        "type": "game_finished",
                        "phase": "F1901M",
                        "phase_index": 1,
                        "status": "completed_no_winner",
                    },
                ),
            ],
        )
        + "\n",
        encoding="utf-8",
    )
    saved_game = run_dir / "game.jsonl"
    game = Game(map_name=str(PROJECT_ROOT / "configs" / "maps" / "EFGA_11.map"))
    for power, units in game.get_units().items():
        game.set_orders(power, [f"{unit} H" for unit in units])
        game.set_wait(power, False)
    game.process()
    to_saved_game_format(game, output_path=str(saved_game), output_mode="a")
    return saved_game
