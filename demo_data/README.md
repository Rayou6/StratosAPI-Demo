# Demo Data

Demo Live runs are stored here, one subdirectory per run id:

```text
demo_data/<run_id>/
  metadata.json
  events.jsonl
  game.jsonl
  boards/        # optional cached board images
```

`events.jsonl` uses one JSON object per line. The current schema is
`stratosapi-demo-events-v1`; each event has a required `type` plus optional
`phase`, `phase_index`, and ordered `sequence` fields. Supported event types:

```text
run_started
phase_started
message_sent
orders_submitted
reasoning_available
phase_resolved
year_summary
game_finished
run_error
```

Phase-specific events should include `phase_index` when possible so the replay
UI can synchronize messages, orders, and reasoning with the selected board
frame.

Committed replay folders are curated live-run examples for the local Demo Live
viewer.

This folder is intentionally separate from removed benchmark output. Demo code
should write only to the selected demo data directory.

The OpenRouter API key for a live demo is provided at launch time by the UI and
must stay in the running server process only. Do not save it in this folder,
logs, metadata, or configuration files.
