# StratosAPI Demo

## Introduction

StratosAPI Demo is a reduced and runnable version of my broader
[StratosAPI](https://github.com/Rayou6/StratosAPI) project. I created this
shorter repository for the course submission to make the code easier to read and
review, while also letting me continue the research project separately without
changing the demo version used for grading.
This difference is explained again below and in
[docs/research-context.md](docs/research-context.md).

I made this version for the course **Skills: Programming with Advanced Computer
Languages** at the University of St. Gallen, taught by Professor **Silic
Mario**.

The goal of this repository is to keep only the parts needed for the demo:
replaying saved AI Diplomacy games, starting a live model-vs-model run, and
inspecting the map, phases, orders, private messages, scores, and game progress
in a local dashboard.

Saved replays work without any API key. An OpenRouter API key is only needed if
you launch a new live run.

### Motivation

I chose this project because I wanted to study how LLMs behave in a social
strategy environment, where decisions are not only about finding a good move but
also about communicating, cooperating, bluffing, and sometimes betraying other
players. Diplomacy is useful for this because the game makes social behavior
visible: messages, promises, alliances, and final orders can all be compared.

The initial inspiration came from
[this video about Claude playing Catan](https://www.youtube.com/watch?v=BER3EhUIyz0).
From there, the idea grew into a project that can also support research-style
benchmarking: different LLMs can be placed in the same type of environment and
their behavior can be compared, since models clearly do not all play, negotiate,
or react in the same way.

## Installation

These instructions assume that Python 3.13, Git, `pip`, and `venv` are already
installed on your machine.

I recommend using [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
because it is faster and the project already includes the files needed for it
(`pyproject.toml` and `uv.lock`). The classic `pip` + `venv` setup also works.

For live runs, an optional `OPENROUTER_API_KEY` is provided privately on the
course website. No public API key is included in this repository. A personal
OpenRouter API key can also be used. Saved replays do not need any key.

### macOS / UNIX

#### Option A: with uv

```bash
git clone https://github.com/Rayou6/StratosAPI-Demo.git
cd StratosAPI-Demo

uv sync
uv run python -m demo_app.server
```

#### Option B: with pip and venv

```bash
git clone https://github.com/Rayou6/StratosAPI-Demo.git
cd StratosAPI-Demo

python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python -m demo_app.server
```

If your Python 3.13 command is named differently, use that command instead of
`python3.13`.

### Windows

#### Option A: with uv

```powershell
git clone https://github.com/Rayou6/StratosAPI-Demo.git
cd StratosAPI-Demo

uv sync
uv run python -m demo_app.server
```

#### Option B: with pip and venv

```powershell
git clone https://github.com/Rayou6/StratosAPI-Demo.git
cd StratosAPI-Demo

py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
python -m demo_app.server
```

If `py -3.13` is not available on your Windows setup, use the command that
points to your Python 3.13 installation.

The `pip install -e .` command installs the dependencies declared in
`pyproject.toml`, so a separate `requirements.txt` file is not needed here.

## Run The Demo

After installation, start the local demo server:

```bash
python -m demo_app.server
```

Then open:

```text
http://127.0.0.1:8765
```

If port `8765` is already in use, choose another one:

```bash
python -m demo_app.server --port 8766
```

If you installed the project with `uv`, you can also run the same commands by
adding `uv run` at the beginning:

```bash
uv run python -m demo_app.server
```

## Using The Dashboard

A short [YouTube tutorial](https://www.youtube.com/) is available and is
strongly recommended for understanding the dashboard quickly. The adapted game
rules are documented in [docs/game-rules.md](docs/game-rules.md).

To replay an existing run, open the `Replay` tab, choose a historic run, and
click `Open replay`.

To start a new live run, open the `Live` tab, choose the demo setup, enter an
OpenRouter API key, and click `Launch`.

Once a run is open, the dashboard lets you move through the phases and inspect
the map, orders, messages, scores, reasoning, and live events.

## Demo Run Limits

The demo setups intentionally use 4 countries instead of the 7 countries from
the full Diplomacy board, and live games stop after 12 in-game years. This is a
design choice to keep OpenRouter calls and costs under control, since live runs
use my personal budget.

Because of this, some demo runs can end without a winner. It is still possible
to use another OpenRouter key, create a custom setup with more countries, and
remove the year limit to run a longer game that only stops when a winner is
found.

## Additional Documentation

More project notes are available here:

- [Architecture notes](docs/architecture.md)
- [Research context](docs/research-context.md)

## Credits And Notes

This project uses the Python
[`diplomacy`](https://github.com/diplomacy/diplomacy) package for the Diplomacy
game engine.

Music used in the local demo:

- `Broken Accord`: based on **I Promised You** from *Arcane* and **Fallout**
  from *Arcane*.
- `Fellowship`: based on music from *The Lord of the Rings: The Fellowship of
  the Ring*.
- `Velvet Tribunal`: based on **The Courtroom's Magician** from *Professor
  Layton vs. Phoenix Wright*.
- `Crimson Front`: based on **To Ashes and Blood** from *Arcane*, by Woodkid.

This project originally started only for the course **Skills: Programming with
Advanced Computer Languages**. The idea of extending it toward a broader
research project came during the development process. It was not used for any
other course or occasion before this one.

I used Codex as a programming assistant to speed up implementation, debugging,
and cleanup. The project idea, architecture, research direction, and final
decisions are my own work.
