# Architecture

This document gives a high-level view of how the demo is structured. It is meant
to explain the main design choices and how the important parts of the code work
together, without documenting every function in detail.

## Global Architecture

The project is organized around three main layers:

- `configs/`: demo setup files and custom Diplomacy maps.
- `diplomacy_llm/`: the core game runtime, model decision logic, messaging
  system, and replay utilities.
- `demo_app/`: the local server and browser dashboard used to launch, replay,
  and inspect games.

The main flow is:

```text
setup config
  -> game runtime
  -> LLM decisions and private messages
  -> saved run artifacts
  -> dashboard replay or live view
```

This separation keeps the actual game logic independent from the local demo UI.
The dashboard displays and controls runs, but the core decisions and game state
are handled in the runtime layer.

## Core Game Runtime

The main game logic lives in `diplomacy_llm/`. This package contains the code
that loads a run setup, creates the Diplomacy game, moves through phases, asks
models for decisions, and records the result.

`config.py` defines the typed setup schema. It uses Pydantic models to validate
the configuration before a run starts. This avoids many runtime mistakes, such
as missing models, invalid map paths, or inconsistent game settings.

`demo_paths.py` keeps the demo file layout explicit. Demo setups are read from
`configs/demo_setups/`, and run outputs are written under `demo_data/`. This is
important because the demo should be easy to inspect and should not mix its
outputs with unrelated experiment files.

`game_runner.py` is the central runtime module. It controls the phase loop of
the Diplomacy game: movement phases, possible retreat phases, winter
adjustments, yearly summaries, scores, and game completion. It is also where
orders and private messaging are coordinated before the game engine resolves a
phase.

`phase_snapshot.py` extracts a clean, immutable view of the game state before
model calls. This is a deliberate design choice: the Diplomacy `Game` object is
mutable, so model workers receive plain snapshot data instead of sharing the
live game object directly.

## LLM Decision System

Each country is controlled by an `LLMPlayer` from `llm_player.py`. The player is
responsible for turning the current game state into a prompt, calling the model,
parsing the response, and returning legal orders.

The decision pipeline is:

```text
game snapshot
  -> prompt
  -> model response
  -> structured parsing
  -> legal-order validation
  -> submitted orders
```

Model output is treated as untrusted. Even if a model answers in the expected
format, the orders still have to be checked against the legal order list
provided by the Diplomacy engine. If the model gives malformed JSON, illegal
orders, or no usable answer, the code retries with a correction prompt. If it
still fails, a legal fallback is used so the game can continue.

This makes the run more robust. The system should not collapse only because one
model made a formatting mistake or selected an invalid order.

## Private Messaging / Press System

Diplomacy is not only about choosing moves. A large part of the game comes from
private communication between countries, also called press.

The messaging layer is separated from the order layer. Message data structures
live in `diplomacy_llm/messaging/models.py`, while the active private messaging
protocol is implemented under `diplomacy_llm/messaging/protocols/`.

The demo uses bounded private conversations before movement decisions. This
keeps the social part of the game visible while still limiting runtime cost and
complexity. Messages can be accepted, rejected, or dropped during validation,
which makes the communication system auditable instead of relying blindly on
model output.

This design makes communication a first-class part of the architecture. It is
important for the project because the goal is not only to see which moves LLMs
choose, but also how they negotiate, cooperate, bluff, and react to each other.

## Artifacts And Replay Design

Each demo run is stored as a small set of files under `demo_data/<run_id>/`:

```text
metadata.json
events.jsonl
game.jsonl
```

`metadata.json` stores general information about the run, such as the setup,
models, map, status, and final result.

`events.jsonl` stores the timeline of what happened during the run. Each line is
one JSON event, such as a message being sent, orders being submitted, a phase
being resolved, or the game ending.

`game.jsonl` stores the saved Diplomacy game. It is used to reconstruct board
states for replay.

This file-based design is simple on purpose. A database would add complexity
without much benefit for this demo. JSON and JSONL artifacts are easy to read,
debug, commit as examples, and reload later.

Replay support is handled by `demo_app/replay.py`, `demo_app/events.py`,
`diplomacy_llm/saved_games.py`, and `diplomacy_llm/board_images.py`. Together,
these modules load run metadata, normalize event logs, keep map references
portable, and rebuild board frames from the saved game.

## Demo Application Layer

`demo_app/` connects the runtime to the browser dashboard. It is intentionally
lightweight: the server is a local Python HTTP server, not a large web
framework.

The main responsibilities are:

- serve the static dashboard files
- list available demo setups
- list saved demo runs
- start a live run
- stream live events to the browser
- load replay data for completed or failed runs

`server.py` defines the local API routes and static file serving. `live.py`
manages active live runs in the current server process. `replay.py` loads saved
run artifacts. The frontend files in `demo_app/static/` provide the visual
dashboard for inspection.

This layer is mainly an interaction layer. The important game and model logic
stays in `diplomacy_llm/`.

## Key Design Choices

Several choices were made to keep the project reliable and readable:

- **Strict configuration**: setup files are validated before a run starts.
- **Isolated demo data**: demo runs use a predictable `demo_data/` layout.
- **Immutable snapshots**: model calls receive safe copies of game state.
- **Structured model output**: model answers are parsed and validated before
  they affect the game.
- **Legal fallbacks**: the game can continue even if a model fails.
- **Observer-style events**: the runtime can report live progress without being
  tied directly to the UI.
- **Simple artifacts**: JSON and JSONL files make runs inspectable and
  replayable.
- **Secret handling**: OpenRouter keys are used only at launch time and are not
  written to metadata, event logs, or saved games.

## Summary

The architecture separates configuration, game execution, model decisions,
persisted artifacts, and dashboard interaction. This makes the demo easier to
run, inspect, and explain, while still keeping the important parts of the
LLM-controlled Diplomacy system visible in the code.
