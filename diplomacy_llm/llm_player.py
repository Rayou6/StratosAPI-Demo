import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from openrouter.components.chatusage import PromptTokensDetails
from openrouter.errors import (
    BadRequestResponseError,
    PaymentRequiredResponseError,
    TooManyRequestsResponseError,
)

from diplomacy_llm.config import Settings
from diplomacy_llm.llm_client import LLMClient
from diplomacy_llm.messaging import (
    DiplomacyMessage,
    DroppedMessage,
    MessageValidationResult,
)
from diplomacy_llm.messaging.prompts import render_visible_messages_prompt
from diplomacy_llm.messaging.protocols import (
    BaseMessagingProtocol,
    get_messaging_protocol,
)
from diplomacy_llm.metrics_collector import LLMCallRecord, MetricsCollector
from diplomacy_llm.phase_snapshot import PowerPhaseSnapshot
from diplomacy_llm.prompt_templates import (
    OrderPromptTemplate,
    get_order_prompt_template,
)
from diplomacy_llm.response_schemas import ORDERS_RESPONSE_SCHEMA
from diplomacy_llm.strategies import (
    StrategyResolution,
    StrategyRuntimeContext,
)
from diplomacy_llm.strategies.protocols import (
    BaseStrategyProtocol,
    get_strategy_protocol,
)

logger: logging.Logger = logging.getLogger(__name__)

_MAX_HEALING_ATTEMPTS: int = 3
_MIN_ORDER_PARTS: int = 2  # diplomacy orders have at least "TYPE LOC [...]"
_CONTEXT_PROMPT_VARIANT: str = "orders_with_context"


class LLMProviderCriticalError(RuntimeError):
    """Raised when a provider error makes the current demo run unreliable."""

    def __init__(
        self,
        *,
        power_name: str,
        model: str,
        reason: str,
    ) -> None:
        self.power_name = power_name
        self.model = model
        self.reason = reason
        super().__init__(
            f"Non-recoverable LLM provider error for {power_name} "
            f"using {model}: {reason}",
        )


@dataclass(frozen=True)
class _MessageLLMUsage:
    message_window: int
    actual_model: str
    tokens_total: int
    tokens_cached: int
    response_latency_ms: float
    is_fallback: bool
    attempt_count: int
    no_response_count: int
    parse_failure_count: int
    validation_failure_count: int
    accepted_message_count: int
    dropped_message_count: int
    provider_retry_count: int
    rate_limit_count: int
    api_error_count: int


class LLMPlayer:
    """
    Represents a single LLM-controlled power in a Diplomacy game.

    Each instance wraps one power + one model. It is responsible for:
    - Building the prompt from the current game state
    - Calling the OpenRouter API with structured output enforcement
    - Validating the returned orders against the game's valid order list
    - Falling back to legal orders gracefully on any failure

    One LLMPlayer instance is created per power at the start of each game
    and reused across all phases.
    """

    def __init__(  # noqa: PLR0913
        self,
        power_name: str,
        model: str,
        client: LLMClient,
        collector: MetricsCollector,
        settings: Settings,
        strategy: BaseStrategyProtocol | None = None,
        strategy_resolution: StrategyResolution | None = None,
        messaging_protocol: BaseMessagingProtocol | None = None,
    ) -> None:
        """
        Initialize the static system prompt once to optimize performance.

        Args:
            power_name: The Diplomacy power this player controls (e.g. 'FRANCE').
            model:      The OpenRouter model ID to use.
            client:     Shared LLM client instance.
            collector:  Shared MetricsCollector instance for live-run bookkeeping.
            settings:   Explicit game/run settings.
            strategy:   Optional strategy protocol. Defaults to baseline behavior.
            strategy_resolution: Strategy assignment provenance for metrics.
            messaging_protocol: Optional messaging protocol. Defaults to configured protocol.

        """
        self.power_name: str = power_name
        self.model: str = model
        self.client: LLMClient = client
        self.collector: MetricsCollector = collector
        self.settings: Settings = settings
        self.strategy: BaseStrategyProtocol = strategy or get_strategy_protocol(
            "baseline",
        )
        self.strategy_resolution: StrategyResolution = (
            strategy_resolution
            or StrategyResolution(
                strategy_name=self.strategy.identity.name,
                strategy_version=self.strategy.identity.version,
                source="default",
                matched_model=None,
            )
        )
        self.messaging_protocol: BaseMessagingProtocol = (
            messaging_protocol or get_messaging_protocol(settings.messaging_variant)
        )
        self._response_schema: dict[str, Any] = ORDERS_RESPONSE_SCHEMA
        self._prompt_template: OrderPromptTemplate = get_order_prompt_template(
            settings.prompt_variant,
        )

        # Pre-build the system prompt once — it never changes during the game
        self._system_prompt: str = self._prompt_template.render_system(
            power_name,
            settings,
        )

    def get_messages(
        self,
        snapshot: PowerPhaseSnapshot,
        *,
        message_window: int,
        visible_messages: Sequence[DiplomacyMessage],
        eligible_recipient_powers: Sequence[str],
        start_sequence: int = 0,
    ) -> MessageValidationResult:
        """
        Generate private messages for one simultaneous message window.

        Message failures fall back to sending no messages. The runtime never
        invents replacement press on behalf of the model.
        """
        recipients = self.messaging_protocol.eligible_recipients(
            self.power_name,
            eligible_recipient_powers,
        )
        if (
            snapshot.phase_type != "M"
            or not self.settings.messaging_enabled_for_power(self.power_name)
            or not recipients
            or self._message_batch_limit() <= 0
        ):
            return MessageValidationResult(accepted=(), dropped=())

        logger.info(
            "[%s] Generating messages for phase %s window %d",
            self.power_name,
            snapshot.phase,
            message_window,
        )
        prompt = self.messaging_protocol.render_prompt(
            snapshot,
            message_window=message_window,
            eligible_recipients=recipients,
            visible_messages=tuple(visible_messages),
            settings=self.settings,
        )
        return self.get_messages_with_prompt(
            snapshot,
            prompt=prompt,
            message_window=message_window,
            eligible_recipient_powers=recipients,
            start_sequence=start_sequence,
        )

    def get_messages_with_prompt(  # noqa: PLR0915
        self,
        snapshot: PowerPhaseSnapshot,
        *,
        prompt: str,
        message_window: int,
        eligible_recipient_powers: Sequence[str],
        start_sequence: int = 0,
    ) -> MessageValidationResult:
        """
        Generate private messages from a protocol-rendered prompt.

        This keeps retry, structured-output parsing, validation, and metrics in
        one place while allowing messaging runtimes to supply different prompts.
        """
        recipients = tuple(eligible_recipient_powers)
        if (
            snapshot.phase_type != "M"
            or not self.settings.messaging_enabled_for_power(self.power_name)
            or not recipients
            or self._message_batch_limit() <= 0
        ):
            return MessageValidationResult(accepted=(), dropped=())

        user_prompt = self._render_message_user_prompt(snapshot, prompt)
        messages: list[dict[str, str]] = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        start_time: float = time.monotonic()
        total_tokens: int = 0
        total_cached: int = 0
        actual_model: str = self.model
        last_result = MessageValidationResult(accepted=(), dropped=())
        last_partial_result: MessageValidationResult | None = None
        attempt_count = 0
        no_response_count = 0
        parse_failure_count = 0
        validation_failure_count = 0
        provider_retry_count = 0
        rate_limit_count = 0
        api_error_count = 0
        for attempt in range(_MAX_HEALING_ATTEMPTS):
            attempt_count = attempt + 1
            (
                raw,
                actual_model,
                tokens,
                cached,
                _,
                provider_retries,
                rate_limits,
                api_errors,
            ) = self._call_llm(
                messages,
                response_schema=self.messaging_protocol.response_schema,
            )
            total_tokens += tokens
            total_cached += cached
            provider_retry_count += provider_retries
            rate_limit_count += rate_limits
            api_error_count += api_errors
            if raw is None:
                no_response_count += 1
                last_result = MessageValidationResult(
                    accepted=(),
                    dropped=(
                        self._message_drop(
                            snapshot,
                            message_window,
                            "no_response",
                            {"value": None},
                        ),
                    ),
                )
                if attempt < _MAX_HEALING_ATTEMPTS - 1:
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "You did not provide a valid message response. "
                                f"Please respond with {self.messaging_protocol.response_shape_description(recipients)}."
                            ),
                        },
                    )
                    continue
                break

            envelopes, parse_reason = self.messaging_protocol.parse_response(raw)
            if envelopes is None:
                parse_failure_count += 1
                last_result = MessageValidationResult(
                    accepted=(),
                    dropped=(
                        self._message_drop(
                            snapshot,
                            message_window,
                            parse_reason or "invalid_message_response",
                            {"value": raw[:500]},
                        ),
                    ),
                )
                if attempt < _MAX_HEALING_ATTEMPTS - 1:
                    messages.extend(
                        [
                            {"role": "assistant", "content": raw},
                            {
                                "role": "user",
                                "content": (
                                    "Your message response could not be parsed. "
                                    f"Please respond with {self.messaging_protocol.response_shape_description(recipients)}."
                                ),
                            },
                        ],
                    )
                    continue
                break

            result = self.messaging_protocol.validate_envelopes(
                envelopes,
                sender=self.power_name,
                phase=snapshot.phase,
                phase_index=snapshot.phase_index,
                message_window=message_window,
                eligible_recipient_powers=recipients,
                settings=self.settings,
                start_sequence=start_sequence,
            )
            if result.dropped:
                validation_failure_count += 1
                last_result = result
                if result.accepted:
                    last_partial_result = result
                if attempt < _MAX_HEALING_ATTEMPTS - 1:
                    problems = sorted({drop.reason for drop in result.dropped})
                    messages.extend(
                        [
                            {"role": "assistant", "content": raw},
                            {
                                "role": "user",
                                "content": (
                                    "Your message response contained invalid envelope(s): "
                                    f"{problems}. Please answer again using only eligible "
                                    f"recipients {list(recipients)}, at most one message per "
                                    "recipient, non-empty bodies within the configured length, "
                                    "and the required JSON shape."
                                ),
                            },
                        ],
                    )
                    continue
                break

            self._record_message_llm_call(
                snapshot,
                _MessageLLMUsage(
                    message_window=message_window,
                    actual_model=actual_model,
                    tokens_total=total_tokens,
                    tokens_cached=total_cached,
                    response_latency_ms=(time.monotonic() - start_time) * 1000,
                    is_fallback=False,
                    attempt_count=attempt_count,
                    no_response_count=no_response_count,
                    parse_failure_count=parse_failure_count,
                    validation_failure_count=validation_failure_count,
                    accepted_message_count=len(result.accepted),
                    dropped_message_count=len(result.dropped),
                    provider_retry_count=provider_retry_count,
                    rate_limit_count=rate_limit_count,
                    api_error_count=api_error_count,
                ),
            )
            return result

        fallback_result = self._message_generation_fallback_result(
            last_result,
            last_partial_result,
        )
        self._record_message_llm_call(
            snapshot,
            _MessageLLMUsage(
                message_window=message_window,
                actual_model=actual_model,
                tokens_total=total_tokens,
                tokens_cached=total_cached,
                response_latency_ms=(time.monotonic() - start_time) * 1000,
                is_fallback=True,
                attempt_count=attempt_count,
                no_response_count=no_response_count,
                parse_failure_count=parse_failure_count,
                validation_failure_count=validation_failure_count,
                accepted_message_count=len(fallback_result.accepted),
                dropped_message_count=len(fallback_result.dropped),
                provider_retry_count=provider_retry_count,
                rate_limit_count=rate_limit_count,
                api_error_count=api_error_count,
            ),
        )
        logger.warning(
            "[%s] Message generation ended with %d accepted and %d dropped message(s) for phase %s window %d",
            self.power_name,
            len(fallback_result.accepted),
            len(fallback_result.dropped),
            snapshot.phase,
            message_window,
        )
        return fallback_result

    def _record_message_llm_call(
        self,
        snapshot: PowerPhaseSnapshot,
        usage: _MessageLLMUsage,
    ) -> None:
        """Record messaging LLM usage in the in-memory collector."""
        self.collector.log_llm_call(
            LLMCallRecord(
                call_kind="messages",
                message_window=usage.message_window,
                power=self.power_name,
                phase=snapshot.phase,
                phase_type=snapshot.phase_type,
                strategy_name=self.strategy_resolution.strategy_name,
                strategy_version=self.strategy_resolution.strategy_version,
                strategy_resolution_source=self.strategy_resolution.source,
                strategy_matched_model=self.strategy_resolution.matched_model,
                requested_model=self.model,
                actual_model=usage.actual_model,
                model_routing_mismatch=usage.actual_model != self.model,
                model=usage.actual_model,
                tokens_total=usage.tokens_total,
                tokens_cached=usage.tokens_cached,
                is_fallback=usage.is_fallback,
                attempt_count=usage.attempt_count,
                healing_attempt_count=max(0, usage.attempt_count - 1),
                no_response_count=usage.no_response_count,
                parse_failure_count=usage.parse_failure_count,
                validation_failure_count=usage.validation_failure_count,
                accepted_message_count=usage.accepted_message_count,
                dropped_message_count=usage.dropped_message_count,
                provider_retry_count=usage.provider_retry_count,
                rate_limit_count=usage.rate_limit_count,
                api_error_count=usage.api_error_count,
                invalid_order_count=0,
                reasoning_length=0,
                response_latency_ms=usage.response_latency_ms,
                move_count=0,
                hold_count=0,
                support_count=0,
            ),
        )

    def get_orders(  # noqa: PLR0915
        self,
        snapshot: PowerPhaseSnapshot,
        *,
        visible_messages: Sequence[DiplomacyMessage] | None = None,
    ) -> tuple[list[str], str]:
        """
        Main method — called once per phase for this power.

        Builds the user prompt from a phase snapshot, calls the LLM,
        validates the returned orders, and returns a clean list ready for
        game.set_orders(). Falls back to legal orders on any failure.

        Args:
            snapshot: Immutable phase context extracted from the live Game object.
            visible_messages: Current-phase private messages visible to this power,
                or None when messaging context is unavailable for this phase.

        Returns:
            A tuple of (orders, reasoning): validated order strings and the LLM's
            strategic reasoning (empty string on fallback).

        """
        phase: str = snapshot.phase
        logger.info("[%s] Generating orders for phase %s", self.power_name, phase)

        # Early exit if nothing to order this phase (e.g. power has no units left)
        orderable: tuple[str, ...] = snapshot.orderable_locations
        if not orderable:
            logger.debug(
                "[%s] No orderable units this phase, skipping LLM call",
                self.power_name,
            )
            return [], ""

        phase_type: str = snapshot.phase_type

        messages: list[dict[str, str]] = [
            {"role": "system", "content": self._system_prompt},
            {
                "role": "user",
                "content": self._render_user_prompt(
                    snapshot,
                    visible_messages=visible_messages,
                ),
            },
        ]

        valid_orders_set: set[str] = {
            order
            for loc in orderable
            for order in snapshot.possible_orders.get(loc, ())
        }

        start_time: float = time.monotonic()
        total_tokens: int = 0
        total_cached: int = 0
        actual_model: str = self.model
        attempt_count = 0
        no_response_count = 0
        parse_failure_count = 0
        validation_failure_count = 0
        provider_retry_count = 0
        rate_limit_count = 0
        api_error_count = 0

        for attempt in range(_MAX_HEALING_ATTEMPTS):
            attempt_count = attempt + 1
            (
                raw,
                actual_model,
                tokens,
                cached,
                _,
                provider_retries,
                rate_limits,
                api_errors,
            ) = self._call_llm(messages)
            total_tokens += tokens
            total_cached += cached
            provider_retry_count += provider_retries
            rate_limit_count += rate_limits
            api_error_count += api_errors

            # Case 1: no response (API failure or reasoning model with empty content)
            if raw is None:
                no_response_count += 1
                logger.warning(
                    "[%s] No response (attempt %d/%d)",
                    self.power_name,
                    attempt + 1,
                    _MAX_HEALING_ATTEMPTS,
                )
                if attempt < _MAX_HEALING_ATTEMPTS - 1:
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "You did not provide a valid response. "
                                f"Please respond with {self._response_shape_description()}."
                            ),
                        },
                    )
                    continue
                break

            # Case 2: parse failure
            orders, reasoning = self._parse_response(raw)
            if orders is None:
                parse_failure_count += 1
                logger.warning(
                    "[%s] Parse failure (attempt %d/%d)",
                    self.power_name,
                    attempt + 1,
                    _MAX_HEALING_ATTEMPTS,
                )
                if attempt < _MAX_HEALING_ATTEMPTS - 1:
                    messages.extend(
                        [
                            {"role": "assistant", "content": raw},
                            {
                                "role": "user",
                                "content": (
                                    "Your response could not be parsed as valid JSON. "
                                    f"Please respond with {self._response_shape_description()}."
                                ),
                            },
                        ],
                    )
                    continue
                break

            # Case 3: invalid count, invalid whitelist item, or missing coverage.
            problems = self._validation_problems(
                snapshot,
                orders,
                valid_orders_set,
            )

            if problems:
                validation_failure_count += 1

            if problems and attempt < _MAX_HEALING_ATTEMPTS - 1:
                parts = problems
                logger.warning(
                    "[%s] Bad orders — %s (attempt %d/%d)",
                    self.power_name,
                    "; ".join(parts),
                    attempt + 1,
                    _MAX_HEALING_ATTEMPTS,
                )
                messages.extend(
                    [
                        {"role": "assistant", "content": raw},
                        {
                            "role": "user",
                            "content": (
                                ". ".join(parts)
                                + ". Please answer again using only exact orders "
                                "from the valid list and following the phase-specific "
                                "order count requirement."
                            ),
                        },
                    ],
                )
                continue

            # Success (or last attempt — gap-fill handles remaining issues)
            latency_ms = (time.monotonic() - start_time) * 1000
            validated, invalid_count = self._validate_orders(
                snapshot,
                orders,
            )
            move_count, hold_count, support_count = self._count_order_types(validated)
            self.collector.log_llm_call(
                LLMCallRecord(
                    call_kind="orders",
                    message_window=None,
                    power=self.power_name,
                    phase=phase,
                    phase_type=phase_type,
                    strategy_name=self.strategy_resolution.strategy_name,
                    strategy_version=self.strategy_resolution.strategy_version,
                    strategy_resolution_source=self.strategy_resolution.source,
                    strategy_matched_model=self.strategy_resolution.matched_model,
                    requested_model=self.model,
                    actual_model=actual_model,
                    model=actual_model,
                    model_routing_mismatch=actual_model != self.model,
                    tokens_total=total_tokens,
                    tokens_cached=total_cached,
                    is_fallback=False,
                    attempt_count=attempt_count,
                    healing_attempt_count=max(0, attempt_count - 1),
                    no_response_count=no_response_count,
                    parse_failure_count=parse_failure_count,
                    validation_failure_count=validation_failure_count,
                    accepted_message_count=0,
                    dropped_message_count=0,
                    provider_retry_count=provider_retry_count,
                    rate_limit_count=rate_limit_count,
                    api_error_count=api_error_count,
                    invalid_order_count=invalid_count,
                    reasoning_length=len(reasoning),
                    response_latency_ms=latency_ms,
                    move_count=move_count,
                    hold_count=hold_count,
                    support_count=support_count,
                ),
            )
            logger.debug("[%s] Orders: %s", self.power_name, validated)
            return validated, reasoning

        # All healing attempts exhausted → HOLD
        latency_ms = (time.monotonic() - start_time) * 1000
        logger.warning(
            "[%s] All %d attempts failed — using legal fallback orders",
            self.power_name,
            _MAX_HEALING_ATTEMPTS,
        )
        self.collector.log_llm_call(
            LLMCallRecord(
                call_kind="orders",
                message_window=None,
                power=self.power_name,
                phase=phase,
                phase_type=phase_type,
                strategy_name=self.strategy_resolution.strategy_name,
                strategy_version=self.strategy_resolution.strategy_version,
                strategy_resolution_source=self.strategy_resolution.source,
                strategy_matched_model=self.strategy_resolution.matched_model,
                requested_model=self.model,
                actual_model=actual_model,
                model=actual_model,
                model_routing_mismatch=actual_model != self.model,
                tokens_total=total_tokens,
                tokens_cached=total_cached,
                is_fallback=True,
                attempt_count=attempt_count,
                healing_attempt_count=max(0, attempt_count - 1),
                no_response_count=no_response_count,
                parse_failure_count=parse_failure_count,
                validation_failure_count=validation_failure_count,
                accepted_message_count=0,
                dropped_message_count=0,
                provider_retry_count=provider_retry_count,
                rate_limit_count=rate_limit_count,
                api_error_count=api_error_count,
                invalid_order_count=0,
                reasoning_length=0,
                response_latency_ms=latency_ms,
                move_count=0,
                hold_count=0,
                support_count=0,
            ),
        )
        return self._legal_fallback_orders(snapshot), ""

    def _render_user_prompt(
        self,
        snapshot: PowerPhaseSnapshot,
        *,
        visible_messages: Sequence[DiplomacyMessage] | None = None,
    ) -> str:
        """
        Render the phase prompt, adding strategy context only when one exists.

        Baseline strategies return no context, so the configured baseline prompt
        remains exactly the same as before the strategy layer.
        """
        strategy = self._render_strategy_prompt(snapshot)
        messages = self._render_visible_messages_prompt(snapshot, visible_messages)
        if strategy is None and messages is None:
            return self._prompt_template.render_user(snapshot)

        prompt_template = self._prompt_template
        if prompt_template.name == "baseline_orders":
            prompt_template = get_order_prompt_template(_CONTEXT_PROMPT_VARIANT)
        return prompt_template.render_user(
            snapshot,
            strategy=strategy,
            messages=messages,
        )

    def _render_message_user_prompt(
        self,
        snapshot: PowerPhaseSnapshot,
        prompt: str,
    ) -> str:
        """Add active strategy context to protocol-rendered messaging prompts."""
        strategy = self._render_strategy_prompt(snapshot)
        if strategy is None:
            return prompt
        return (
            "CONTEXT SECTIONS:\n"
            "- Strategy metadata is the active doctrine for this power. "
            "Apply it to this messaging decision.\n\n"
            "STRATEGY METADATA:\n"
            f"{strategy}\n\n"
            "MESSAGING TASK:\n"
            f"{prompt}"
        )

    def _render_strategy_prompt(self, snapshot: PowerPhaseSnapshot) -> str | None:
        """Return this power's active strategy prompt block, if configured."""
        return self.strategy.render_prompt_section(
            StrategyRuntimeContext(snapshot=snapshot),
        )

    def _render_visible_messages_prompt(
        self,
        snapshot: PowerPhaseSnapshot,
        visible_messages: Sequence[DiplomacyMessage] | None,
    ) -> str | None:
        """Return current-phase private messages for the order prompt, if provided."""
        if visible_messages is None:
            return None
        return render_visible_messages_prompt(
            tuple(visible_messages),
            power=self.power_name,
            powers_order=tuple(snapshot.all_units),
        )

    def _call_llm(
        self,
        messages: list[dict[str, str]],
        *,
        response_schema: dict[str, Any] | None = None,
    ) -> tuple[str | None, str, int, int, float, int, int, int]:
        """
        Call the OpenRouter API with retry logic and error handling.

        Handles rate limits (exponential backoff), bad requests, and payment
        errors. Also detects reasoning models — if content is not a string,
        the model is incompatible with our pipeline and we return None.

        Args:
            messages: The full [system, user] message list for this call.
            response_schema: Optional structured-output schema override.

        Returns:
            A tuple of content, model, token counts, latency, and lightweight
            provider reliability counters. content is None if all retries failed.

        """
        schema = self._response_schema if response_schema is None else response_schema
        start: float = time.monotonic()
        provider_retry_count = 0
        rate_limit_count = 0
        for attempt in range(self.settings.max_retries):
            try:
                res: Any = self.client.chat.send(
                    **self._llm_request_kwargs(messages, schema),
                )

            except TooManyRequestsResponseError:
                provider_retry_count += 1
                rate_limit_count += 1
                # Exponential backoff — critical for free models (50 req/day, 20 req/min)
                wait: float = self.settings.retry_delay * (2**attempt)
                logger.warning(
                    "[%s] Rate limited. Waiting %.1fs (attempt %d/%d)",
                    self.power_name,
                    wait,
                    attempt + 1,
                    self.settings.max_retries,
                )
                time.sleep(wait)

            except BadRequestResponseError as exc:
                # Usually means response_format not supported by this model
                logger.exception(
                    "[%s] Bad request — '%s' may not support response_format with strict=True",
                    self.power_name,
                    self.model,
                )
                raise LLMProviderCriticalError(
                    power_name=self.power_name,
                    model=self.model,
                    reason="bad_request_or_strict_response_format_unsupported",
                ) from exc

            except PaymentRequiredResponseError as exc:
                logger.exception(
                    "[%s] Insufficient OpenRouter credits — aborting",
                    self.power_name,
                )
                raise LLMProviderCriticalError(
                    power_name=self.power_name,
                    model=self.model,
                    reason="payment_required_or_insufficient_credits",
                ) from exc

            except Exception:
                logger.exception(
                    "[%s] Unexpected error on attempt %d",
                    self.power_name,
                    attempt + 1,
                )
                return (
                    None,
                    self.model,
                    0,
                    0,
                    (time.monotonic() - start) * 1000,
                    provider_retry_count,
                    rate_limit_count,
                    1,
                )

            else:
                content = res.choices[0].message.content

                usage = res.usage
                tokens_total: int = (
                    usage.total_tokens
                    if usage is not None and usage.total_tokens is not None
                    else 0
                )
                details = usage.prompt_tokens_details if usage is not None else None
                tokens_cached: int = (
                    details.cached_tokens
                    if isinstance(details, PromptTokensDetails)
                    and details.cached_tokens is not None
                    else 0
                )

                if not isinstance(content, str):
                    logger.warning(
                        "[%s] Empty content from '%s' — will retry with context",
                        self.power_name,
                        res.model,
                    )
                    return (
                        None,
                        res.model,
                        tokens_total,
                        tokens_cached,
                        (time.monotonic() - start) * 1000,
                        provider_retry_count,
                        rate_limit_count,
                        0,
                    )

                return (
                    content,
                    res.model,
                    tokens_total,
                    tokens_cached,
                    (time.monotonic() - start) * 1000,
                    provider_retry_count,
                    rate_limit_count,
                    0,
                )

        logger.error(
            "[%s] All %d attempts failed",
            self.power_name,
            self.settings.max_retries,
        )
        return (
            None,
            self.model,
            0,
            0,
            (time.monotonic() - start) * 1000,
            provider_retry_count,
            rate_limit_count,
            0,
        )

    def _llm_request_kwargs(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        """Build the OpenRouter chat request, omitting unset generation controls."""
        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.settings.max_tokens,
            "response_format": schema,
        }
        if self.settings.llm_seed is not None:
            request_kwargs["seed"] = self.settings.llm_seed
        if self.settings.temperature is not None:
            request_kwargs["temperature"] = self.settings.temperature
        if self.settings.top_p is not None:
            request_kwargs["top_p"] = self.settings.top_p
        return request_kwargs

    def _parse_response(
        self,
        raw: str,
    ) -> tuple[list[str] | None, str]:
        """
        Parse the raw JSON string returned by the LLM.

        The schema guarantees 'orders' and 'reasoning' are present if the
        model respected the response_format. We still validate here as a
        safety net in case the model ignored the schema.

        Args:
            raw: The raw content string from the LLM response.

        Returns:
            A tuple of (orders, reasoning). orders is None if parsing failed.

        """
        try:
            data_raw: Any = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning(
                "[%s] JSON parse error (%s) — raw[:100]: %s",
                self.power_name,
                exc.msg,
                raw[:100],
            )
            return None, ""

        if not isinstance(data_raw, dict):
            logger.warning(
                "[%s] JSON response is not an object (got %s)",
                self.power_name,
                type(data_raw).__name__,
            )
            return None, ""

        data: dict[str, Any] = data_raw
        orders_raw: Any = data.get("orders")
        if not isinstance(orders_raw, list):
            logger.warning(
                "[%s] 'orders' field is missing or not a list (got %s)",
                self.power_name,
                type(orders_raw).__name__,
            )
            return None, ""

        reasoning_raw: Any = data.get("reasoning", "")
        if isinstance(reasoning_raw, str):
            reasoning: str = reasoning_raw
        elif reasoning_raw is None:
            reasoning = ""
        else:
            reasoning = str(reasoning_raw)

        return [str(o) for o in orders_raw], reasoning

    def _response_shape_description(self) -> str:
        """Return a concise JSON response shape for healing prompts."""
        return (
            "a JSON object containing 'orders' (list of strings) and "
            "'reasoning' (string)"
        )

    def _message_drop(
        self,
        snapshot: PowerPhaseSnapshot,
        message_window: int,
        reason: str,
        raw: dict[str, object],
    ) -> DroppedMessage:
        """Build one dropped-message audit record for non-envelope failures."""
        return DroppedMessage(
            sender=self.power_name,
            phase=snapshot.phase,
            phase_index=snapshot.phase_index,
            message_window=message_window,
            reason=reason,
            raw=raw,
        )

    @staticmethod
    def _message_generation_fallback_result(
        last_result: MessageValidationResult,
        last_partial_result: MessageValidationResult | None,
    ) -> MessageValidationResult:
        """Prefer the latest partially valid message response over a later hard failure."""
        if last_result.accepted or last_partial_result is None:
            return last_result
        return last_partial_result

    def _validation_problems(
        self,
        snapshot: PowerPhaseSnapshot,
        orders: list[str],
        valid_orders: set[str],
    ) -> list[str]:
        """Return human-readable problems for the healing retry prompt."""
        invalid = [order for order in orders if order not in valid_orders]
        problems: list[str] = []
        if invalid:
            problems.append(f"Invalid orders (not in the provided list): {invalid}")

        if snapshot.phase_type == "A":
            problems.extend(self._adjustment_validation_problems(snapshot, orders))
            return problems

        duplicate_locations = self._duplicate_order_locations(orders)
        if duplicate_locations:
            problems.append(
                f"Multiple orders submitted for locations: {duplicate_locations}",
            )

        missing = self._missing_orderable_locs(orders, snapshot.orderable_locations)
        if missing:
            problems.append(f"Missing orders for locations: {missing}")
        return problems

    def _adjustment_validation_problems(
        self,
        snapshot: PowerPhaseSnapshot,
        orders: list[str],
    ) -> list[str]:
        """Return Adjustment-specific quota problems for the healing loop."""
        build_count = snapshot.adjustment_build_count
        disband_count = snapshot.adjustment_disband_count
        valid_orders = {
            order
            for loc in snapshot.orderable_locations
            for order in snapshot.possible_orders.get(loc, ())
        }
        valid_submitted = [order for order in orders if order in valid_orders]
        problems: list[str] = []

        if build_count > 0:
            build_orders = [
                order
                for order in valid_submitted
                if order != "WAIVE" and order.endswith(" B")
            ]
            waive_count = sum(1 for order in valid_submitted if order == "WAIVE")
            duplicate_sites = self._duplicate_adjustment_sites(build_orders)
            if duplicate_sites:
                problems.append(
                    f"Multiple builds submitted for home centers: {duplicate_sites}",
                )
            if len(build_orders) > build_count:
                problems.append(
                    f"Too many builds: choose at most {build_count} build order(s)",
                )
            if len(build_orders) + waive_count < build_count:
                problems.append(
                    f"Missing adjustment decisions: return {build_count} build/WAIVE decision(s)",
                )
            if len(build_orders) + waive_count > build_count:
                problems.append(
                    f"Too many adjustment decisions: return exactly {build_count}",
                )
            return problems

        if disband_count > 0:
            disband_orders = [
                order for order in valid_submitted if order.endswith(" D")
            ]
            duplicate_units = self._duplicate_adjustment_sites(disband_orders)
            if duplicate_units:
                problems.append(
                    f"Multiple disbands submitted for units: {duplicate_units}",
                )
            if len(disband_orders) < disband_count:
                problems.append(
                    f"Missing disbands: choose exactly {disband_count} unit(s)",
                )
            if len(disband_orders) > disband_count:
                problems.append(
                    f"Too many disbands: choose exactly {disband_count} unit(s)",
                )
        return problems

    def _message_batch_limit(self) -> int:
        """Return the active protocol's maximum messages for one model response."""
        return self.settings.messaging.latency_pairwise_private.max_messages_per_response

    def _validate_orders(
        self,
        snapshot: PowerPhaseSnapshot,
        orders: list[str],
    ) -> tuple[list[str], int]:
        """
        Validate LLM-generated orders and resolve one final order per orderable location.

        Builds context from the snapshot, guards against duplicates, then delegates
        per-location resolution to _resolve_order_for_loc.

        Args:
            snapshot: Immutable phase context extracted from the live Game object.
            orders:       Raw order list from the LLM.

        Returns:
            A tuple of (validated_orders, invalid_order_count).

        """
        orderable: tuple[str, ...] = snapshot.orderable_locations
        valid_orders: set[str] = {
            order
            for loc in orderable
            for order in snapshot.possible_orders.get(loc, ())
        }

        if snapshot.phase_type == "A":
            return self._validate_adjustment_orders(snapshot, orders, valid_orders)

        if len(orders) != len(set(orders)):
            logger.warning(
                "[%s] Duplicate orders detected — using legal fallback orders",
                self.power_name,
            )
            return self._legal_fallback_orders(snapshot), 0

        # Diplomacy orders have at least "TYPE LOC [...]" — 2 tokens minimum
        min_parts: int = _MIN_ORDER_PARTS
        invalid_count: int = 0

        # Index valid LLM orders by location (e.g. 'PAR' → 'A PAR - BUR')
        llm_by_loc: dict[str, str] = {}
        for order in orders:
            parts = order.strip().split()
            if len(parts) >= min_parts:
                if order in valid_orders:
                    llm_by_loc[parts[1]] = order
                    if "/" in parts[1]:
                        llm_by_loc[parts[1].split("/")[0]] = order
                else:
                    logger.warning(
                        "[%s] Invalid order '%s' — will substitute",
                        self.power_name,
                        order,
                    )
                    invalid_count += 1

        # Map location → clean unit string for HOLD fallback in Movement/Retreat phases
        unit_at_loc: dict[str, str] = {}
        for unit in snapshot.own_units:
            clean: str = unit.lstrip("*")
            parts: list[str] = clean.split()
            if len(parts) >= min_parts:
                unit_at_loc[parts[1]] = clean

        validated = [
            order
            for loc in orderable
            if (
                order := self._resolve_order_for_loc(
                    loc,
                    llm_by_loc,
                    unit_at_loc,
                    valid_orders,
                    snapshot.possible_orders.get(loc, ()),
                )
            )
            is not None
        ]
        return validated, invalid_count

    def _validate_adjustment_orders(
        self,
        snapshot: PowerPhaseSnapshot,
        orders: list[str],
        valid_orders: set[str],
    ) -> tuple[list[str], int]:
        """Validate Winter build/disband orders against the phase quota."""
        if snapshot.adjustment_build_count > 0:
            return self._validate_build_orders(snapshot, orders, valid_orders)
        if snapshot.adjustment_disband_count > 0:
            return self._validate_disband_orders(snapshot, orders, valid_orders)
        return [], sum(1 for order in orders if order not in valid_orders)

    def _validate_build_orders(
        self,
        snapshot: PowerPhaseSnapshot,
        orders: list[str],
        valid_orders: set[str],
    ) -> tuple[list[str], int]:
        """Keep legal build choices up to the build quota and fill unused slots."""
        required = snapshot.adjustment_build_count
        selected: list[str] = []
        used_sites: set[str] = set()
        invalid_count = 0
        waive_count_submitted = 0

        for order in orders:
            if order == "WAIVE" and order in valid_orders:
                waive_count_submitted += 1
                continue

            if order not in valid_orders or not order.endswith(" B"):
                invalid_count += 1
                continue

            site = self._order_location_key(order)
            if site is None or site in used_sites or len(selected) >= required:
                invalid_count += 1
                continue

            selected.append(order)
            used_sites.add(site)

        extra_decisions = max(0, len(selected) + waive_count_submitted - required)
        invalid_count += extra_decisions
        waive_count = max(0, required - len(selected))
        selected.extend(["WAIVE"] * waive_count)
        return selected, invalid_count

    def _validate_disband_orders(
        self,
        snapshot: PowerPhaseSnapshot,
        orders: list[str],
        valid_orders: set[str],
    ) -> tuple[list[str], int]:
        """Keep exactly the required number of legal disbands."""
        required = snapshot.adjustment_disband_count
        selected: list[str] = []
        used_units: set[str] = set()
        invalid_count = 0

        for order in orders:
            if order not in valid_orders or not order.endswith(" D"):
                invalid_count += 1
                continue

            unit_key = self._order_location_key(order)
            if unit_key is None or unit_key in used_units or len(selected) >= required:
                invalid_count += 1
                continue

            selected.append(order)
            used_units.add(unit_key)

        if len(selected) < required:
            for loc in snapshot.orderable_locations:
                for order in snapshot.possible_orders.get(loc, ()):
                    unit_key = self._order_location_key(order)
                    if (
                        order.endswith(" D")
                        and unit_key is not None
                        and unit_key not in used_units
                    ):
                        selected.append(order)
                        used_units.add(unit_key)
                        break
                if len(selected) >= required:
                    break

        return selected, invalid_count

    def _resolve_order_for_loc(
        self,
        loc: str,
        llm_by_loc: dict[str, str],
        unit_at_loc: dict[str, str],
        valid_orders: set[str],
        loc_orders: tuple[str, ...],
    ) -> str | None:
        """
        Pick the best available order for a single orderable location.

        Priority: (1) LLM order if valid, (2) HOLD if the unit is there and HOLD is legal,
        (3) first available order from the game whitelist, (4) None (location skipped).

        Args:
            loc:          The 3-letter location code to resolve (e.g. 'PAR', 'BUD').
            llm_by_loc:   Valid LLM orders indexed by location.
            unit_at_loc:  Existing units indexed by location (for HOLD attempts).
            valid_orders: Full set of legal orders for this power this phase.
            loc_orders:   Ordered list of all legal orders for this specific location.

        Returns:
            The chosen order string, or None if no legal order exists.

        """
        if loc in llm_by_loc:
            return llm_by_loc[loc]

        unit: str | None = unit_at_loc.get(loc)
        hold: str | None = f"{unit} H" if unit else None

        if hold and hold in valid_orders:
            logger.warning(
                "[%s] Missing order for %s — substituting HOLD",
                self.power_name,
                unit,
            )
            return hold

        if loc_orders:
            logger.warning(
                "[%s] No LLM order for %s — using first available: %s",
                self.power_name,
                loc,
                loc_orders[0],
            )
            return loc_orders[0]

        logger.warning(
            "[%s] No valid orders found for %s — skipping",
            self.power_name,
            loc,
        )
        return None

    @staticmethod
    def _duplicate_order_locations(orders: list[str]) -> list[str]:
        """Return non-WAIVE locations that appear in more than one order."""
        seen: set[str] = set()
        duplicates: set[str] = set()
        for order in orders:
            if order == "WAIVE":
                continue
            loc = LLMPlayer._order_location_key(order)
            if loc is None:
                continue
            if loc in seen:
                duplicates.add(loc)
            seen.add(loc)
        return sorted(duplicates)

    @staticmethod
    def _duplicate_adjustment_sites(orders: list[str]) -> list[str]:
        """Return duplicate base locations in build/disband adjustment orders."""
        seen: set[str] = set()
        duplicates: set[str] = set()
        for order in orders:
            loc = LLMPlayer._order_location_key(order)
            if loc is None:
                continue
            if loc in seen:
                duplicates.add(loc)
            seen.add(loc)
        return sorted(duplicates)

    @staticmethod
    def _order_location_key(order: str) -> str | None:
        """Return the base province key for an order, ignoring split coasts."""
        parts = order.strip().split()
        if len(parts) < _MIN_ORDER_PARTS:
            return None
        return parts[1].split("/")[0]

    @staticmethod
    def _missing_orderable_locs(
        orders: list[str],
        orderable: Sequence[str],
    ) -> list[str]:
        """
        Return orderable locations not covered by any of the LLM's orders.

        Handles coastal variants: 'STP/SC' is treated as covering base location 'STP'.
        """
        covered: set[str] = set()
        for o in orders:
            parts = o.strip().split()
            if len(parts) >= _MIN_ORDER_PARTS:
                loc = parts[1]
                covered.add(loc)
                if "/" in loc:
                    covered.add(loc.split("/")[0])
        return [loc for loc in orderable if loc not in covered]

    @staticmethod
    def _count_order_types(orders: list[str]) -> tuple[int, int, int]:
        """Count move, hold, and support orders. Meaningful for Movement phases only."""
        move: int = 0
        hold: int = 0
        support: int = 0
        for order in orders:
            parts = order.split()
            if len(parts) < 3:
                continue
            action = parts[2]
            if action == "-":
                move += 1
            elif action == "H":
                hold += 1
            elif action == "S":
                support += 1
        return move, hold, support

    def _legal_fallback_orders(
        self,
        snapshot: PowerPhaseSnapshot,
    ) -> list[str]:
        """
        Generate safe legal fallback orders for this power.

        Used as a fallback when the LLM call fails entirely or returns
        an unparseable response. Ensures the game can always proceed
        even if one player's LLM is unavailable.

        Args:
            snapshot: Immutable phase context extracted from the live Game object.

        Returns:
            A list with one legal fallback order per orderable location.

        """
        orderable: tuple[str, ...] = snapshot.orderable_locations
        if snapshot.phase_type == "A":
            valid_orders = {
                order
                for loc in orderable
                for order in snapshot.possible_orders.get(loc, ())
            }
            fallback, _ = self._validate_adjustment_orders(
                snapshot,
                [],
                valid_orders,
            )
            return fallback

        # Map location → clean unit string for HOLD attempt in Movement/Retreat
        unit_at_loc: dict[str, str] = {}
        for unit in snapshot.own_units:
            clean: str = unit.lstrip("*")
            parts = clean.split()
            if len(parts) >= _MIN_ORDER_PARTS:
                unit_at_loc[parts[1]] = clean

        orders: list[str] = []
        for loc in orderable:
            unit = unit_at_loc.get(loc)
            hold: str | None = f"{unit} H" if unit else None
            loc_orders: tuple[str, ...] = snapshot.possible_orders.get(loc, ())

            if hold and hold in loc_orders:
                orders.append(hold)
            elif loc_orders:
                # HOLD not valid (Retreat/Adjustment) — force first available order
                orders.append(loc_orders[0])

        return orders
