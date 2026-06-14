import pytest

from diplomacy_llm.config import StrategyAssignmentSettings
from diplomacy_llm.strategies import (
    StrategyResolution,
    resolve_power_strategies,
    strategy_resolution_payload,
)


def test_resolver_assigns_default_strategy_when_model_has_no_rule() -> None:
    resolutions = resolve_power_strategies(
        {"ENGLAND": "model/a"},
        StrategyAssignmentSettings(default="baseline"),
    )

    assert resolutions == {
        "ENGLAND": StrategyResolution(
            strategy_name="baseline",
            strategy_version="v1",
            source="default",
            matched_model=None,
        ),
    }


def test_resolver_assigns_strategy_by_effective_model() -> None:
    resolutions = resolve_power_strategies(
        {
            "ENGLAND": "model/planner",
            "GERMANY": "model/deceptive",
            "FRANCE": "model/baseline",
        },
        StrategyAssignmentSettings(
            default="baseline",
            by_model={
                "model/deceptive": "baseline",
                "model/planner": "baseline",
            },
        ),
    )

    assert resolutions["ENGLAND"] == StrategyResolution(
        strategy_name="baseline",
        strategy_version="v1",
        source="by_model",
        matched_model="model/planner",
    )
    assert resolutions["GERMANY"] == StrategyResolution(
        strategy_name="baseline",
        strategy_version="v1",
        source="by_model",
        matched_model="model/deceptive",
    )
    assert resolutions["FRANCE"] == StrategyResolution(
        strategy_name="baseline",
        strategy_version="v1",
        source="default",
        matched_model=None,
    )


def test_resolver_assigns_strategy_by_power_before_model_rules() -> None:
    resolutions = resolve_power_strategies(
        {
            "ENGLAND": "model/planner",
            "FRANCE": "model/planner",
            "GERMANY": "model/baseline",
        },
        StrategyAssignmentSettings(
            default="baseline",
            by_power={"ENGLAND": "baseline"},
            by_model={"model/planner": "baseline"},
        ),
    )

    assert resolutions["ENGLAND"] == StrategyResolution(
        strategy_name="baseline",
        strategy_version="v1",
        source="by_power",
        matched_model=None,
    )
    assert resolutions["FRANCE"] == StrategyResolution(
        strategy_name="baseline",
        strategy_version="v1",
        source="by_model",
        matched_model="model/planner",
    )
    assert resolutions["GERMANY"] == StrategyResolution(
        strategy_name="baseline",
        strategy_version="v1",
        source="default",
        matched_model=None,
    )


def test_resolver_follows_model_after_shuffle() -> None:
    effective_power_models_after_shuffle = {
        "ENGLAND": "model/planner",
        "FRANCE": "model/baseline",
    }

    resolutions = resolve_power_strategies(
        effective_power_models_after_shuffle,
        StrategyAssignmentSettings(
            default="baseline",
            by_model={"model/planner": "baseline"},
        ),
    )

    assert resolutions["ENGLAND"].strategy_name == "baseline"
    assert resolutions["FRANCE"].strategy_name == "baseline"


def test_strategy_resolution_payload_is_json_safe() -> None:
    payload = strategy_resolution_payload(
        {
            "ENGLAND": StrategyResolution(
                strategy_name="baseline",
                strategy_version="v1",
                source="by_model",
                matched_model="model/planner",
            ),
            "FRANCE": StrategyResolution(
                strategy_name="baseline",
                strategy_version="v1",
                source="default",
                matched_model=None,
            ),
        },
    )

    assert payload == {
        "ENGLAND": {
            "name": "baseline",
            "version": "v1",
            "source": "by_model",
            "matched_model": "model/planner",
        },
        "FRANCE": {
            "name": "baseline",
            "version": "v1",
            "source": "default",
            "matched_model": None,
        },
    }


@pytest.mark.parametrize(
    "assignment",
    [
        StrategyAssignmentSettings(default="missing"),
        StrategyAssignmentSettings(
            default="baseline",
            by_power={"ENGLAND": "missing"},
        ),
        StrategyAssignmentSettings(
            default="baseline",
            by_model={"model/a": "missing"},
        ),
    ],
)
def test_resolver_rejects_unknown_strategy_names(
    assignment: StrategyAssignmentSettings,
) -> None:
    with pytest.raises(ValueError, match="Unknown strategy 'missing'"):
        resolve_power_strategies({"ENGLAND": "model/a"}, assignment)
