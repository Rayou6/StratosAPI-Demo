# StratosAPI Demo README Plan

This file temporarily records the planned structure for the final public README.
It is a draft outline only: the detailed documentation files, video link, final
credits, and final installation verification still need to be completed later.

## Planned README Structure

### 1. Short Project Introduction

Introduce StratosAPI Demo as a compact, runnable demo version of the broader
StratosAPI project.

Include:

- A short explanation of what the demo shows.
- A link to the main StratosAPI repository.
- A clear note that this repository is a cleaned demo package, not a random
  copy of unrelated code.

### 2. What This Demo Shows

Keep this section short and concrete.

Main demo features:

- Replay saved AI Diplomacy games.
- Start a live model-vs-model run.
- Inspect orders, private messages, scores, phases, and game progression in the
  dashboard.

Saved replays should work without any API key.
Live runs should require an OpenRouter API key only when the user actually
launches a live run.

### 3. Requirements

List the minimal requirements early.

Planned requirements:

- Python 3.13 or the project-supported Python version.
- Optional but recommended: `uv`.
- Fallback: classic `pip` and `venv`.
- Optional for live runs only: `OPENROUTER_API_KEY`.

### 4. Installation

Provide two installation paths.

Recommended path with `uv`:

```bash
uv sync
uv run python -m demo_app.server
```

Classic fallback path with `pip` on macOS/Linux:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python -m demo_app.server
```

Classic fallback path with `pip` on Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
python -m demo_app.server
```

The final README should explain that `pip install -e .` reads the dependencies
from `pyproject.toml`, so a separate `requirements.txt` is not required unless
we later decide to add one for convenience.

### 5. Run The Demo

Explain the launch command and the local URL.

```bash
python -m demo_app.server
```

Then open:

```text
http://127.0.0.1:8765
```

Mention the alternate port option:

```bash
python -m demo_app.server --port 8766
```

### 6. Using The Dashboard

Keep the dashboard instructions short and practical.

Include:

- How to select and replay a saved run.
- How to start a live run.
- What the main dashboard panels show.
- A link to the short unlisted YouTube tutorial video.
- A link to `docs/game-rules.md`.

The tutorial video should stay short, ideally under five minutes.

### 7. Additional Documentation

Create a `docs/` folder later and link to these files from the README:

- `docs/game-rules.md`
  - Clear and concise explanation of the adapted Diplomacy rules used by the
    demo.
- `docs/architecture.md`
  - Short architecture overview for reading the project.
  - Explain the main folders and important files.
  - Explain the main algorithmic/runtime decisions without becoming a long
    report.
- `docs/research-context.md`
  - Link again to the main StratosAPI repository.
  - Explain the original research goal: benchmarking LLM behavior in Diplomacy.
  - Summarize the most relevant early results.
  - Describe possible future extensions.

### 8. Demo Data Policy

The final demo should only keep useful replay data.

Planned policy:

- No dry-run replay data.
- No old replay clutter.
- Keep only the four most recent selected live runs, from
  `0906_102358` onward.

Current target kept runs:

- `demo_live_demo_EFGA_11_short_press_0906_102358`
- `demo_live_demo_EFGA_11_short_press_0906_112314`
- `demo_live_demo_EFGA_11_short_press_0906_112845`
- `demo_live_demo_EFGA_11_short_press_0906_123909`

### 9. Troubleshooting

Keep this short.

Useful entries:

- `python` vs `python3`.
- Port `8765` already in use.
- `pip` too old: run `python -m pip install --upgrade pip`.
- Windows virtual environment activation.
- Saved replays do not require an API key.
- Live runs require `OPENROUTER_API_KEY`.

### 10. Credits And Notes

Include:

- Credit for the Python `diplomacy` package.
- Music credits.
- A short personal ownership disclaimer.
- A short Codex assistance note.

Suggested Codex note:

```text
I used Codex as a programming assistant to speed up implementation, debugging,
and cleanup. The project idea, architecture, research direction, and final
decisions are my own work.
```

## Final README Validation Checklist

Before publishing the GitHub repository:

- Clone the repository into a fresh folder.
- Test the `pip` installation path without using `uv`.
- Launch the demo server.
- Confirm the dashboard opens.
- Confirm saved replays are visible and replay correctly.
- Confirm no API key is required for replay.
- Confirm live runs only require an API key when launched.
- Test the GitHub "Download ZIP" flow if possible.
