from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from demo_app.live import (
    OPENROUTER_NO_CREDITS_MESSAGE,
    DemoLiveRunError,
    DemoLiveRunManager,
    DemoLiveRunNotFoundError,
)
from demo_app.replay import DemoRunNotFoundError, list_demo_runs, load_demo_replay
from diplomacy_llm.config import InvalidArtifactNameError, SetupConfigNotFoundError
from diplomacy_llm.demo_paths import DEMO_DATA_DIR, DEMO_SETUPS_DIR

STATIC_DIR = Path(__file__).resolve().parent / "static"
MUSIC_DIR = Path(__file__).resolve().parents[1] / "music"
_MAX_REQUEST_BODY_BYTES = 32_000
_MUSIC_EXTENSIONS = {".mp3"}
_TRACK_PREFIX_RE = re.compile(r"^\s*(\d+)[.)\s_-]*(.*)$")
_OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"
_OPENROUTER_KEY_VALIDATION_TIMEOUT_SECONDS = 10


class OpenRouterKeyValidationError(ValueError):
    """Raised when a launch-time OpenRouter key cannot be verified."""


class OpenRouterCreditExhaustedError(OpenRouterKeyValidationError):
    """Raised when OpenRouter reports that no usable credits are available."""


class DemoThreadingHTTPServer(ThreadingHTTPServer):
    """HTTP server that reports request failures without full tracebacks."""

    def handle_error(self, _request: object, client_address: object) -> None:
        exc = sys.exc_info()[1]
        print(f"Demo Live server request error from {client_address}: {exc}")


class DemoRequestHandler(SimpleHTTPRequestHandler):
    """HTTP handler for the local Demo Live app."""

    data_dir: Path = DEMO_DATA_DIR
    static_dir: Path = STATIC_DIR
    music_dir: Path = MUSIC_DIR
    live_manager: DemoLiveRunManager

    def do_GET(self) -> None:
        """Serve API responses and static UI files."""
        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        if route == "/api/music":
            self._send_json({"tracks": _list_music_tracks(self.music_dir)})
        elif route.startswith("/music/"):
            self._serve_music(route.removeprefix("/music/"))
        elif route == "/api/runs":
            self._handle_runs()
        elif route == "/api/demo-setups":
            self._send_json({"setups": self.live_manager.list_setups()})
        elif route.startswith("/api/live-runs/") and route.endswith("/events"):
            self._handle_live_events(route, parsed.query)
        elif route.startswith("/api/live-runs/"):
            self._handle_live_status(route.removeprefix("/api/live-runs/"))
        elif route.startswith("/api/runs/"):
            self._handle_run(route.removeprefix("/api/runs/"))
        else:
            self._serve_static(parsed.path)

    def do_POST(self) -> None:
        """Handle local demo launch requests."""
        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        if route == "/api/live-runs":
            self._handle_start_live_run()
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, message_format: str, *args: Any) -> None:
        """Keep the demo server quiet unless the caller adds logging."""
        _ = message_format, args

    def _handle_start_live_run(self) -> None:
        try:
            payload = self._read_json_body()
            mode = str(payload.get("mode") or "live")
            _raise_if_active_run(self.live_manager.active_run_listing())
            openrouter_api_key = _optional_string(payload.get("openrouter_api_key"))
            self.live_manager.raise_if_live_provider_unavailable()
            validate_openrouter_api_key(openrouter_api_key)
            state = self.live_manager.start_run(
                setup_name=str(payload.get("setup") or payload.get("setup_name") or ""),
                mode=mode,
                openrouter_api_key=openrouter_api_key,
            )
        except (
            DemoLiveRunError,
            OpenRouterKeyValidationError,
            InvalidArtifactNameError,
            SetupConfigNotFoundError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ) as exc:
            if isinstance(exc, OpenRouterCreditExhaustedError):
                self.live_manager.mark_live_provider_credit_exhausted()
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

        self._send_json(
            {
                "run": state.snapshot(),
                "stream_url": f"/api/live-runs/{state.run_id}/events",
            },
            status=HTTPStatus.CREATED,
        )

    def _handle_runs(self) -> None:
        runs = list_demo_runs(self.data_dir)
        active_run = self.live_manager.active_run_listing()
        if active_run is not None:
            _mark_active_run(runs, active_run)
        self._send_json(
            {
                "runs": runs,
                "active_run": active_run,
                "live_provider": self.live_manager.live_provider_snapshot(),
            },
        )

    def _handle_run(self, raw_run_id: str) -> None:
        run_id = unquote(raw_run_id)
        try:
            self._send_json(load_demo_replay(run_id, self.data_dir))
        except (DemoRunNotFoundError, InvalidArtifactNameError) as exc:
            self._send_json(
                {"error": str(exc)},
                status=HTTPStatus.NOT_FOUND,
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._send_json(
                {"error": f"Could not load demo run: {exc}"},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def _handle_live_status(self, raw_run_id: str) -> None:
        run_id = unquote(raw_run_id)
        try:
            self._send_json({"run": self.live_manager.get_run(run_id).snapshot()})
        except (DemoLiveRunNotFoundError, InvalidArtifactNameError) as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.NOT_FOUND)

    def _handle_live_events(self, route: str, query: str) -> None:
        raw_run_id = route.removeprefix("/api/live-runs/").removesuffix("/events")
        run_id = unquote(raw_run_id.rstrip("/"))
        try:
            state = self.live_manager.get_run(run_id)
        except (DemoLiveRunNotFoundError, InvalidArtifactNameError) as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.NOT_FOUND)
            return

        params = parse_qs(query)
        after = _int_or_default(params.get("after", ["-1"])[0], default=-1)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            for event in state.iter_events(after_sequence=after):
                body = f"data: {json.dumps(event, separators=(',', ':'))}\n\n"
                self.wfile.write(body.encode("utf-8"))
                self.wfile.flush()
        except OSError:
            return

    def _serve_static(self, raw_path: str) -> None:
        relative = "index.html" if raw_path in {"", "/"} else unquote(raw_path[1:])
        if not _safe_static_path(relative):
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        path = self.static_dir / relative
        if path.is_dir():
            path = path / "index.html"
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_music(self, raw_filename: str) -> None:
        filename = unquote(raw_filename)
        if not _safe_music_path(filename):
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        path = self.music_dir / filename
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        content_type = mimetypes.guess_type(path.name)[0] or "audio/mpeg"
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> Mapping[str, object]:
        raw_length = self.headers.get("Content-Length", "0")
        length = int(raw_length)
        if length > _MAX_REQUEST_BODY_BYTES:
            msg = "Request body is too large"
            raise ValueError(msg)
        if length <= 0:
            return {}
        body = self.rfile.read(length)
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, Mapping):
            msg = "Request body must be a JSON object"
            raise TypeError(msg)
        return payload

    def _send_json(
        self,
        payload: Mapping[str, object],
        *,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def create_server(  # noqa: PLR0913
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    data_dir: Path = DEMO_DATA_DIR,
    setups_dir: Path = DEMO_SETUPS_DIR,
    static_dir: Path = STATIC_DIR,
    music_dir: Path = MUSIC_DIR,
    live_manager: DemoLiveRunManager | None = None,
) -> ThreadingHTTPServer:
    """Create a configured local demo HTTP server."""
    _quiet_engine_logs()

    class ConfiguredDemoRequestHandler(DemoRequestHandler):
        pass

    ConfiguredDemoRequestHandler.data_dir = data_dir
    ConfiguredDemoRequestHandler.static_dir = static_dir
    ConfiguredDemoRequestHandler.music_dir = music_dir
    ConfiguredDemoRequestHandler.live_manager = live_manager or DemoLiveRunManager(
        data_dir=data_dir,
        setups_dir=setups_dir,
    )
    return DemoThreadingHTTPServer((host, port), ConfiguredDemoRequestHandler)


def _safe_static_path(relative: str) -> bool:
    path = Path(relative)
    return not path.is_absolute() and ".." not in path.parts


def _safe_music_path(filename: str) -> bool:
    path = Path(filename)
    return (
        not path.is_absolute()
        and len(path.parts) == 1
        and not path.name.startswith(".")
        and path.suffix.lower() in _MUSIC_EXTENSIONS
    )


def _list_music_tracks(music_dir: Path) -> list[dict[str, str]]:
    if not music_dir.exists() or not music_dir.is_dir():
        return []

    tracks = [
        path
        for path in music_dir.iterdir()
        if path.is_file() and path.suffix.lower() in _MUSIC_EXTENSIONS
    ]
    return [
        {
            "filename": path.name,
            "label": _music_track_label(path),
            "url": f"/music/{quote(path.name)}",
        }
        for path in sorted(tracks, key=_music_track_sort_key)
    ]


def _mark_active_run(
    runs: list[dict[str, object]],
    active_run: dict[str, object],
) -> None:
    active_run_id = active_run["run_id"]
    for run in runs:
        if run.get("run_id") == active_run_id:
            run.update(
                {
                    "is_active_live": True,
                    "live_status": active_run.get("live_status"),
                    "replay_ready": active_run.get("replay_ready"),
                },
            )
            return
    runs.append(active_run)


def _raise_if_active_run(active_run: dict[str, object] | None) -> None:
    if active_run is None:
        return
    label = str(active_run.get("label") or active_run.get("run_id") or "current run")
    msg = (
        f"A demo run is already in progress: {label}. "
        "Open the Replay tab and select the active run to return to its live view, "
        "or wait for it to finish."
    )
    raise DemoLiveRunError(msg)


def _music_track_label(path: Path) -> str:
    stem = path.stem
    match = _TRACK_PREFIX_RE.match(stem)
    if not match:
        return stem
    label = match.group(2).strip()
    return label or stem


def _music_track_sort_key(path: Path) -> tuple[int, str]:
    match = _TRACK_PREFIX_RE.match(path.stem)
    if not match:
        return (10_000, path.name.lower())
    return (int(match.group(1)), path.name.lower())


def validate_openrouter_api_key(api_key: str | None) -> None:
    key = (api_key or "").strip()
    if not key:
        msg = "OpenRouter API key is required for live demo runs"
        raise OpenRouterKeyValidationError(msg)

    request = urllib.request.Request(  # noqa: S310
        _OPENROUTER_KEY_URL,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(  # noqa: S310
            request,
            timeout=_OPENROUTER_KEY_VALIDATION_TIMEOUT_SECONDS,
        ) as response:
            if response.status != HTTPStatus.OK:
                msg = "OpenRouter API key could not be verified"
                raise OpenRouterKeyValidationError(msg)
    except urllib.error.HTTPError as exc:
        if exc.code in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}:
            msg = "OpenRouter API key was rejected"
        elif exc.code == HTTPStatus.PAYMENT_REQUIRED:
            msg = OPENROUTER_NO_CREDITS_MESSAGE
            raise OpenRouterCreditExhaustedError(msg) from exc
        else:
            msg = f"OpenRouter key verification failed with HTTP {exc.code}"
        raise OpenRouterKeyValidationError(msg) from exc
    except urllib.error.URLError as exc:
        msg = "Could not reach OpenRouter to verify the API key"
        raise OpenRouterKeyValidationError(msg) from exc


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _int_or_default(value: str, *, default: int) -> int:
    try:
        return int(value)
    except ValueError:
        return default


def _quiet_engine_logs() -> None:
    logging.getLogger("diplomacy_llm").setLevel(logging.ERROR)
    logging.getLogger("diplomacy_llm.llm_player").setLevel(logging.CRITICAL)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the local Demo Live replay app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=DEMO_DATA_DIR)
    parser.add_argument("--setups-dir", type=Path, default=DEMO_SETUPS_DIR)
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    server = create_server(
        host=args.host,
        port=args.port,
        data_dir=args.data_dir,
        setups_dir=args.setups_dir,
    )
    url = f"http://{args.host}:{args.port}"
    print(f"Demo Live replay app: {url}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
