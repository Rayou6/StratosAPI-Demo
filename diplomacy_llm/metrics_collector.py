from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, NotRequired, TypedDict


class LLMCallRecord(TypedDict):
    call_kind: Literal["orders", "messages"]
    message_window: int | None
    power: str
    phase: str
    phase_type: str
    strategy_name: str
    strategy_version: str
    strategy_resolution_source: str
    strategy_matched_model: str | None
    requested_model: str
    actual_model: str
    model_routing_mismatch: bool
    model: str
    tokens_total: int
    tokens_cached: int
    is_fallback: bool
    attempt_count: NotRequired[int]
    healing_attempt_count: NotRequired[int]
    no_response_count: NotRequired[int]
    parse_failure_count: NotRequired[int]
    validation_failure_count: NotRequired[int]
    accepted_message_count: NotRequired[int]
    dropped_message_count: NotRequired[int]
    provider_retry_count: NotRequired[int]
    rate_limit_count: NotRequired[int]
    api_error_count: NotRequired[int]
    invalid_order_count: int
    reasoning_length: int
    response_latency_ms: float
    move_count: int
    hold_count: int
    support_count: int


class _YearSummary(TypedDict):
    year: int
    calendar_year: int
    sc_counts: dict[str, int]
    sc_delta: dict[str, int]
    duration_s: float


class _GameEnd(TypedDict):
    winner: str | None
    final_sc_counts: dict[str, int]
    total_phases: int
    total_duration_s: float
    dislodge_event_count: int


class MetricsCollector:
    """Collect the small process-local metrics surface used by live demo runs."""

    def __init__(
        self,
        run_id: str,
        metrics_dir: Path,
    ) -> None:
        self._run_id = run_id
        self._metrics_dir = metrics_dir
        self._llm_calls: list[LLMCallRecord] = []
        self._year_summaries: list[_YearSummary] = []
        self._game_end: _GameEnd | None = None
        self._phase_count: int = 0
        self._created_at_utc: str = datetime.now(UTC).isoformat()
        self._game_start_time: float = time.monotonic()
        self._year_start_time: float = time.monotonic()
        self._prev_sc_counts: dict[str, int] = {}
        self._dislodge_event_count: int = 0

    def record_game_start(
        self, initial_sc_counts: dict[str, int], calendar_year: int
    ) -> None:
        """Store initial supply-center counts as the local baseline."""
        self._prev_sc_counts = initial_sc_counts.copy()
        self._year_summaries.append(
            {
                "year": 0,
                "calendar_year": calendar_year,
                "sc_counts": initial_sc_counts.copy(),
                "sc_delta": {p: 0 for p in initial_sc_counts},
                "duration_s": 0.0,
            },
        )

    def record_phase(self) -> None:
        """Increment the processed phase counter."""
        self._phase_count += 1

    def record_retreat_phase(self) -> None:
        """Count a retreat phase occurrence."""
        self._dislodge_event_count += 1

    def log_llm_call(self, event: LLMCallRecord) -> None:
        """Record one LLM call for live-run fallback and audit decisions."""
        self._llm_calls.append(event)

    def latest_order_call(self, *, power: str, phase: str) -> LLMCallRecord | None:
        """Return the latest recorded order-generation call for a power and phase."""
        for call in reversed(self._llm_calls):
            if (
                call["call_kind"] == "orders"
                and call["power"] == power
                and call["phase"] == phase
            ):
                return call
        return None

    def log_year_summary(
        self, year: int, calendar_year: int, sc_counts: dict[str, int]
    ) -> None:
        """Record supply-center counts and deltas after a completed year."""
        sc_delta = {p: sc_counts[p] - self._prev_sc_counts.get(p, 0) for p in sc_counts}
        duration_s = time.monotonic() - self._year_start_time
        self._year_summaries.append(
            {
                "year": year,
                "calendar_year": calendar_year,
                "sc_counts": sc_counts,
                "sc_delta": sc_delta,
                "duration_s": duration_s,
            },
        )
        self._prev_sc_counts = sc_counts.copy()
        self._year_start_time = time.monotonic()

    def log_game_end(self, winner: str | None, sc_counts: dict[str, int]) -> None:
        """Record the final game result in collector state."""
        self._game_end = {
            "winner": winner,
            "final_sc_counts": sc_counts,
            "total_phases": self._phase_count,
            "total_duration_s": time.monotonic() - self._game_start_time,
            "dislodge_event_count": self._dislodge_event_count,
        }
