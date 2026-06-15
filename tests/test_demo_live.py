from __future__ import annotations

import json
import threading
import urllib.error
from http import HTTPStatus
from http.client import HTTPConnection
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse

import pytest
from diplomacy import Game

from demo_app import live as demo_live
from demo_app import server as demo_server
from demo_app.events import load_demo_events
from demo_app.live import DemoLiveRunError, DemoLiveRunManager
from demo_app.replay import list_demo_runs, load_demo_replay
from demo_app.server import create_server
from diplomacy_llm.config import PROJECT_ROOT
from diplomacy_llm.llm_player import LLMProviderCriticalError


def test_demo_live_run_writes_demo_data_without_benchmark_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setups_dir = _write_demo_setup(tmp_path)
    data_dir = tmp_path / "demo_data"
    monkeypatch.setattr(demo_live, "OpenRouter", _FakeOpenRouter)
    monkeypatch.setattr(demo_live, "_save_game_snapshot", _fake_save_game_snapshot)
    monkeypatch.setattr(demo_live, "run_game", _fake_successful_run_game)
    manager = DemoLiveRunManager(data_dir=data_dir, setups_dir=setups_dir)

    state = manager.start_run(
        setup_name="quick_demo",
        mode="live",
        openrouter_api_key="sk-or-live",
    )
    _join_run(state.thread)

    metadata = json.loads(state.paths.metadata_path.read_text(encoding="utf-8"))
    events = _read_jsonl(state.paths.events_path)

    assert metadata["status"] == "done"
    assert metadata["run_mode"] == "live"
    assert str(metadata["name"]).startswith("quick demo (live) (")
    assert metadata["setup_label"] == "quick demo"
    assert "sk-or-live" not in json.dumps(metadata)
    assert state.paths.run_dir.parent == data_dir
    assert state.paths.game_path.exists()
    assert [event["type"] for event in events] == [
        "run_started",
        "phase_started",
        "orders_submitted",
        "reasoning_available",
        "phase_resolved",
        "game_finished",
    ]
    assert not (tmp_path / "data" / "runs").exists()


def test_demo_live_requires_key_for_live_mode(tmp_path: Path) -> None:
    setups_dir = _write_demo_setup(tmp_path)
    manager = DemoLiveRunManager(data_dir=tmp_path / "demo_data", setups_dir=setups_dir)

    with pytest.raises(DemoLiveRunError, match="OpenRouter API key"):
        manager.start_run(
            setup_name="quick_demo",
            mode="live",
            openrouter_api_key="",
        )


def test_demo_live_rejects_removed_dry_run_mode(tmp_path: Path) -> None:
    setups_dir = _write_demo_setup(tmp_path)
    manager = DemoLiveRunManager(data_dir=tmp_path / "demo_data", setups_dir=setups_dir)

    with pytest.raises(DemoLiveRunError, match="must be 'live'"):
        manager.start_run(
            setup_name="quick_demo",
            mode="dry_run",
            openrouter_api_key="sk-or-demo",
        )


@pytest.mark.parametrize(
    ("openrouter_error", "expected_error"),
    [
        (
            urllib.error.HTTPError(
                demo_server._OPENROUTER_KEY_URL,
                HTTPStatus.UNAUTHORIZED,
                "Unauthorized",
                hdrs=None,
                fp=None,
            ),
            "OpenRouter API key was rejected",
        ),
        (
            urllib.error.URLError("timed out"),
            "Could not reach OpenRouter to verify the API key",
        ),
    ],
)
def test_demo_server_rejects_key_validation_failures_without_run_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    openrouter_error: urllib.error.URLError,
    expected_error: str,
) -> None:
    setups_dir = _write_demo_setup(tmp_path)
    data_dir = tmp_path / "demo_data"

    def fake_urlopen(*args: Any, **kwargs: Any) -> object:
        _ = args, kwargs
        raise openrouter_error

    monkeypatch.setattr(demo_server.urllib.request, "urlopen", fake_urlopen)
    manager = DemoLiveRunManager(data_dir=data_dir, setups_dir=setups_dir)
    server = create_server(
        port=0,
        data_dir=data_dir,
        setups_dir=setups_dir,
        live_manager=manager,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        status, launch = _post_json_response(
            base_url,
            "/api/live-runs",
            {
                "setup": "quick_demo",
                "mode": "live",
                "openrouter_api_key": "sk-or-invalid",
            },
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert status == 400
    assert launch["error"] == expected_error
    assert not data_dir.exists()


def test_demo_server_payment_required_validation_blocks_live_without_run_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setups_dir = _write_demo_setup(tmp_path)
    data_dir = tmp_path / "demo_data"

    def fake_urlopen(*args: Any, **kwargs: Any) -> object:
        _ = args, kwargs
        raise urllib.error.HTTPError(
            demo_server._OPENROUTER_KEY_URL,
            HTTPStatus.PAYMENT_REQUIRED,
            "Payment Required",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr(demo_server.urllib.request, "urlopen", fake_urlopen)
    manager = DemoLiveRunManager(data_dir=data_dir, setups_dir=setups_dir)
    server = create_server(
        port=0,
        data_dir=data_dir,
        setups_dir=setups_dir,
        live_manager=manager,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        status, launch = _post_json_response(
            base_url,
            "/api/live-runs",
            {
                "setup": "quick_demo",
                "mode": "live",
                "openrouter_api_key": "sk-or-no-credit",
            },
        )
        runs = _read_json(base_url, "/api/runs")
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert status == 400
    assert "No OpenRouter credits" in launch["error"]
    assert runs["live_provider"]["status"] == "credit_exhausted"
    assert runs["runs"] == []
    assert not data_dir.exists()


def test_demo_live_redacts_key_from_live_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setups_dir = _write_demo_setup(tmp_path)
    data_dir = tmp_path / "demo_data"
    captured: dict[str, str] = {}
    secret = "sk-or-demo-secret"

    class FakeOpenRouter:
        def __init__(self, *, api_key: str) -> None:
            captured["api_key"] = api_key

    def fake_failing_run_game(*args: Any, **kwargs: Any) -> object:
        _ = args, kwargs
        raise RuntimeError(f"provider rejected {secret}")

    monkeypatch.setattr(demo_live, "OpenRouter", FakeOpenRouter)
    monkeypatch.setattr(demo_live, "run_game", fake_failing_run_game)
    manager = DemoLiveRunManager(data_dir=data_dir, setups_dir=setups_dir)

    state = manager.start_run(
        setup_name="quick_demo",
        mode="live",
        openrouter_api_key=secret,
    )
    _join_run(state.thread)

    metadata_text = state.paths.metadata_path.read_text(encoding="utf-8")
    events_text = state.paths.events_path.read_text(encoding="utf-8")
    assert captured["api_key"] == secret
    assert secret not in metadata_text
    assert secret not in events_text
    assert "[redacted]" in metadata_text
    assert "[redacted]" in events_text


def test_demo_live_provider_error_keeps_partial_replay_inspectable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setups_dir = _write_demo_setup(tmp_path)
    data_dir = tmp_path / "demo_data"
    secret = "sk-or-timeout-secret"

    class FakeOpenRouter:
        def __init__(self, *, api_key: str) -> None:
            assert api_key == secret

    def fake_partial_failing_run_game(  # noqa: PLR0913
        client: object,
        *,
        collector: object,
        settings: object,
        power_models: dict[str, str],
        power_strategies: object,
        orders_path: Path | None,
        messages_path: Path | None,
        observer: object,
    ) -> object:
        _ = (
            client,
            collector,
            power_strategies,
            orders_path,
            messages_path,
        )
        game = Game(map_name=str(PROJECT_ROOT / "configs" / "maps" / "EFGA_11.map"))
        observer.on_run_started(
            game=game,
            settings=settings,
            power_models=power_models,
            power_strategies=None,
            initial_sc_counts={"ENGLAND": 3, "FRANCE": 3, "GERMANY": 3},
            calendar_year=1901,
        )
        observer.on_phase_started(
            game=game,
            phase="S1901M",
            phase_index=0,
            phase_type="M",
        )
        raise TimeoutError(f"provider timed out for {secret}")

    monkeypatch.setattr(demo_live, "OpenRouter", FakeOpenRouter)
    monkeypatch.setattr(demo_live, "run_game", fake_partial_failing_run_game)
    manager = DemoLiveRunManager(data_dir=data_dir, setups_dir=setups_dir)

    state = manager.start_run(
        setup_name="quick_demo",
        mode="live",
        openrouter_api_key=secret,
    )
    _join_run(state.thread)

    metadata_text = state.paths.metadata_path.read_text(encoding="utf-8")
    events_text = state.paths.events_path.read_text(encoding="utf-8")
    metadata = json.loads(metadata_text)
    events = load_demo_events(state.paths.events_path)
    replay = load_demo_replay(state.run_id, data_dir)
    runs = list_demo_runs(data_dir)

    assert metadata["status"] == "error"
    assert metadata["error"]["type"] == "TimeoutError"
    assert metadata["error"]["message"] == "provider timed out for [redacted]"
    assert [event["type"] for event in events] == [
        "run_started",
        "phase_started",
        "run_error",
    ]
    assert events[-1]["error"] == "provider timed out for [redacted]"
    assert state.paths.game_path.exists()
    assert replay["replay_ready"] is True
    assert replay["frame_count"] >= 1
    assert replay["event_count"] == len(events)
    assert any(
        run["run_id"] == state.run_id and run["replay_ready"] is True
        for run in runs
    )
    assert secret not in metadata_text
    assert secret not in events_text
    assert not (tmp_path / "data" / "runs").exists()


def test_demo_live_no_credits_error_stops_run_and_blocks_live_launches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setups_dir = _write_demo_setup(tmp_path)
    data_dir = tmp_path / "demo_data"

    class FakeOpenRouter:
        def __init__(self, *, api_key: str) -> None:
            assert api_key == "sk-or-no-credit"

    def fake_no_credit_run_game(*args: Any, **kwargs: Any) -> object:
        _ = args, kwargs
        raise LLMProviderCriticalError(
            power_name="FRANCE",
            model="model/default",
            reason=demo_live.OPENROUTER_NO_CREDITS_REASON,
        )

    monkeypatch.setattr(demo_live, "OpenRouter", FakeOpenRouter)
    monkeypatch.setattr(demo_live, "run_game", fake_no_credit_run_game)
    manager = DemoLiveRunManager(data_dir=data_dir, setups_dir=setups_dir)

    state = manager.start_run(
        setup_name="quick_demo",
        mode="live",
        openrouter_api_key="sk-or-no-credit",
    )
    _join_run(state.thread)

    metadata = json.loads(state.paths.metadata_path.read_text(encoding="utf-8"))
    events = _read_jsonl(state.paths.events_path)
    provider = manager.live_provider_snapshot()

    assert metadata["status"] == "error"
    assert metadata["error"]["type"] == demo_live.OPENROUTER_NO_CREDITS_ERROR_TYPE
    assert "No OpenRouter credits" in metadata["error"]["message"]
    assert events[-1]["type"] == "run_error"
    assert events[-1]["error_type"] == demo_live.OPENROUTER_NO_CREDITS_ERROR_TYPE
    assert provider["status"] == "credit_exhausted"
    assert provider["error_type"] == demo_live.OPENROUTER_NO_CREDITS_ERROR_TYPE

    with pytest.raises(DemoLiveRunError, match="No OpenRouter credits"):
        manager.start_run(
            setup_name="quick_demo",
            mode="live",
            openrouter_api_key="sk-or-new-key",
        )

    def fail_validate_openrouter_api_key(api_key: str | None) -> None:
        _ = api_key
        pytest.fail("Credit-exhausted live launches must not revalidate the key")

    monkeypatch.setattr(
        demo_server,
        "validate_openrouter_api_key",
        fail_validate_openrouter_api_key,
    )
    server = create_server(
        port=0,
        data_dir=data_dir,
        setups_dir=setups_dir,
        live_manager=manager,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        runs = _read_json(base_url, "/api/runs")
        blocked_status, blocked_launch = _post_json_response(
            base_url,
            "/api/live-runs",
            {
                "setup": "quick_demo",
                "mode": "live",
                "openrouter_api_key": "sk-or-new-key",
            },
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert runs["live_provider"]["status"] == "credit_exhausted"
    assert blocked_status == 400
    assert "No OpenRouter credits" in blocked_launch["error"]


def test_demo_server_lists_setups_and_starts_live_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setups_dir = _write_demo_setup(tmp_path)
    data_dir = tmp_path / "demo_data"
    monkeypatch.setattr(demo_live, "OpenRouter", _FakeOpenRouter)
    monkeypatch.setattr(demo_server, "validate_openrouter_api_key", lambda _key: None)
    monkeypatch.setattr(demo_live, "_save_game_snapshot", _fake_save_game_snapshot)
    monkeypatch.setattr(demo_live, "run_game", _fake_successful_run_game)
    manager = DemoLiveRunManager(data_dir=data_dir, setups_dir=setups_dir)
    server = create_server(
        port=0,
        data_dir=data_dir,
        setups_dir=setups_dir,
        live_manager=manager,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        setups = _read_json(base_url, "/api/demo-setups")
        launch = _post_json(
            base_url,
            "/api/live-runs",
            {
                "setup": "quick_demo",
                "mode": "live",
                "openrouter_api_key": "sk-or-live",
            },
        )
        run_id = launch["run"]["run_id"]
        status = _read_json(base_url, f"/api/live-runs/{run_id}")
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    _join_run(manager.get_run(run_id).thread)
    assert setups["setups"] == [{"name": "quick_demo", "label": "quick demo"}]
    assert launch["stream_url"] == f"/api/live-runs/{run_id}/events"
    assert launch["run"]["mode"] == "live"
    assert status["run"]["run_id"] == run_id


def test_demo_server_rejects_second_launch_while_run_is_active(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setups_dir = _write_demo_setup(tmp_path)
    data_dir = tmp_path / "demo_data"
    started = threading.Event()
    release = threading.Event()

    def fake_blocking_run_game(*args: Any, **kwargs: Any) -> object:
        _ = args, kwargs
        started.set()
        assert release.wait(timeout=5)
        return _FakeGame()

    monkeypatch.setattr(demo_live, "OpenRouter", _FakeOpenRouter)
    monkeypatch.setattr(demo_server, "validate_openrouter_api_key", lambda _key: None)
    monkeypatch.setattr(demo_live, "_save_game_snapshot", _fake_save_game_snapshot)
    monkeypatch.setattr(demo_live, "run_game", fake_blocking_run_game)
    manager = DemoLiveRunManager(data_dir=data_dir, setups_dir=setups_dir)
    server = create_server(
        port=0,
        data_dir=data_dir,
        setups_dir=setups_dir,
        live_manager=manager,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        first_status, first_launch = _post_json_response(
            base_url,
            "/api/live-runs",
            {
                "setup": "quick_demo",
                "mode": "live",
                "openrouter_api_key": "sk-or-live",
            },
        )
        run_id = str(first_launch["run"]["run_id"])
        assert started.wait(timeout=5)

        runs = _read_json(base_url, "/api/runs")
        second_status, second_launch = _post_json_response(
            base_url,
            "/api/live-runs",
            {
                "setup": "quick_demo",
                "mode": "live",
                "openrouter_api_key": "sk-or-live-2",
            },
        )
    finally:
        release.set()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    _join_run(manager.get_run(run_id).thread)
    assert first_status == 201
    assert runs["active_run"]["run_id"] == run_id
    assert any(
        run["run_id"] == run_id and run["is_active_live"] is True
        for run in runs["runs"]
    )
    assert second_status == 400
    assert "already in progress" in second_launch["error"]
    assert "quick demo (live)" in second_launch["error"]
    assert run_id not in second_launch["error"]


def _fake_successful_run_game(  # noqa: PLR0913
    client: object,
    *,
    collector: object,
    settings: object,
    power_models: dict[str, str],
    power_strategies: object,
    orders_path: Path | None,
    messages_path: Path | None,
    observer: object,
) -> object:
    assert isinstance(client, _FakeOpenRouter)
    assert orders_path is None
    assert messages_path is None
    assert power_models == {
        "ENGLAND": "model/default",
        "FRANCE": "model/default",
        "GERMANY": "model/default",
    }
    _ = collector, power_strategies
    game = _FakeGame()
    observer.on_run_started(
        game=game,
        settings=settings,
        power_models=power_models,
        power_strategies=None,
        initial_sc_counts={"ENGLAND": 3, "FRANCE": 3, "GERMANY": 3},
        calendar_year=1901,
    )
    observer.on_phase_started(
        game=game,
        phase="S1901M",
        phase_index=0,
        phase_type="M",
    )
    observer.on_orders_submitted(
        phase="S1901M",
        phase_index=0,
        phase_type="M",
        power="ENGLAND",
        orders=[],
        reasoning="",
    )
    observer.on_orders_submitted(
        phase="S1901M",
        phase_index=0,
        phase_type="M",
        power="FRANCE",
        orders=["A PAR H"],
        reasoning="Hold Paris.",
    )
    game.phase = "F1901M"
    observer.on_phase_resolved(
        game=game,
        phase="S1901M",
        phase_index=0,
        phase_type="M",
        next_phase="F1901M",
        next_phase_index=1,
        result_history={},
        sc_counts={"ENGLAND": 3, "FRANCE": 3, "GERMANY": 3},
    )
    observer.on_game_finished(
        game=game,
        phase="F1901M",
        phase_index=1,
        winner=None,
        sc_counts={"ENGLAND": 3, "FRANCE": 3, "GERMANY": 3},
    )
    return game


class _FakeOpenRouter:
    def __init__(self, *, api_key: str) -> None:
        self.api_key = api_key


class _FakeGame(SimpleNamespace):
    def __init__(self) -> None:
        super().__init__(phase="S1901M")

    def get_current_phase(self) -> str:
        return str(self.phase)


def _fake_save_game_snapshot(game: _FakeGame, game_path: Path) -> None:
    game_path.parent.mkdir(parents=True, exist_ok=True)
    game_path.write_text(
        json.dumps({"phase": game.get_current_phase()}) + "\n",
        encoding="utf-8",
    )


def _write_demo_setup(tmp_path: Path) -> Path:
    setups_dir = tmp_path / "configs" / "demo_setups"
    setups_dir.mkdir(parents=True)
    (setups_dir / "quick_demo.yaml").write_text(
        """default_model: "model/default"
power_models:
  ENGLAND: "model/default"
  FRANCE: "model/default"
  GERMANY: "model/default"
map_name: "configs/maps/EFGA_11.map"
max_years: 1
win_score: 9
shuffle_models: false
messaging:
  enabled: false
""",
        encoding="utf-8",
    )
    return setups_dir


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _join_run(thread: threading.Thread | None) -> None:
    assert thread is not None
    thread.join(timeout=5)
    assert not thread.is_alive()


def _read_json(base_url: str, path: str) -> dict[str, Any]:
    parsed = urlparse(base_url)
    assert parsed.hostname is not None
    assert parsed.port is not None
    connection = HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        assert response.status == 200
        payload = json.loads(response.read().decode("utf-8"))
        assert isinstance(payload, dict)
        return payload
    finally:
        connection.close()


def _post_json(base_url: str, path: str, payload: dict[str, object]) -> dict[str, Any]:
    status, response_payload = _post_json_response(base_url, path, payload)
    assert status == 201
    return response_payload


def _post_json_response(
    base_url: str,
    path: str,
    payload: dict[str, object],
) -> tuple[int, dict[str, Any]]:
    parsed = urlparse(base_url)
    assert parsed.hostname is not None
    assert parsed.port is not None
    connection = HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    try:
        body = json.dumps(payload)
        connection.request(
            "POST",
            path,
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        response_payload = json.loads(response.read().decode("utf-8"))
        assert isinstance(response_payload, dict)
        return response.status, response_payload
    finally:
        connection.close()
