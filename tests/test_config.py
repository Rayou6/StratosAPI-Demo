import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from diplomacy_llm.config import (
    SETUPS_DIR,
    InvalidArtifactNameError,
    SetupConfigNotFoundError,
    available_setup_names,
    load_settings,
    load_settings_from_path,
    setup_path_for,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_CONFIG_NAME = "short_demo_EFGA_baseline"
DEMO_SETUP_NAMES = [
    "demo_EFGA_aggressive",
    "demo_EFGA_baseline",
    "short_demo_EFGA_aggressive",
    "short_demo_EFGA_baseline",
]


def test_config_import_isolated_process_without_config_arg() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.argv = ['worker']; import diplomacy_llm.config; print('ok')",
        ],
        capture_output=True,
        check=True,
        cwd=PROJECT_ROOT,
        text=True,
    )

    assert result.stdout.strip() == "ok"


def test_load_settings_loads_current_setup() -> None:
    settings = load_settings(DEMO_CONFIG_NAME)

    assert settings.map_name == "configs/maps/EFGA_11.map"
    assert settings.max_years == 4
    assert settings.win_score == 11
    assert settings.shuffle_models is True
    assert settings.model_assignment_seed is None
    assert settings.llm_seed == 1001
    assert settings.temperature is None
    assert settings.top_p is None
    assert settings.prompt_variant == "baseline_orders"
    assert settings.prompt_version == "neutral-v1"
    assert settings.power_models == {
        "ENGLAND": "mistralai/mistral-nemo",
        "FRANCE": "meta-llama/llama-3.1-8b-instruct",
        "GERMANY": "qwen/qwen3-235b-a22b-2507",
        "AUSTRIA": "deepseek/deepseek-v4-flash",
    }
    assert settings.powers == ["ENGLAND", "FRANCE", "GERMANY", "AUSTRIA"]


def test_load_settings_loads_demo_messaging_setup() -> None:
    settings = load_settings(DEMO_CONFIG_NAME)

    assert settings.messaging_variant == "latency_pairwise_private"
    assert settings.messaging.enabled is True
    assert settings.messaging_enabled_powers == [
        "ENGLAND",
        "FRANCE",
        "GERMANY",
        "AUSTRIA",
    ]
    assert settings.messaging.latency_pairwise_private.max_messages_per_response == 3
    assert settings.messaging.latency_pairwise_private.max_turns_per_conversation == 4
    assert (
        settings.messaging.latency_pairwise_private.max_messages_sent_per_power
        is None
    )


def test_load_settings_does_not_fall_back_to_removed_historical_setups() -> None:
    removed_name = "same_EFGA_11"

    with pytest.raises(SetupConfigNotFoundError) as exc_info:
        load_settings(removed_name)

    exc = exc_info.value
    assert exc.path == SETUPS_DIR / f"{removed_name}.yaml"
    assert exc.available == DEMO_SETUP_NAMES


def test_default_setup_listing_only_shows_demo_setups() -> None:
    current_names = sorted(path.stem for path in SETUPS_DIR.glob("*.yaml"))

    assert current_names == DEMO_SETUP_NAMES
    assert available_setup_names() == current_names
    assert setup_path_for(DEMO_CONFIG_NAME) == SETUPS_DIR / f"{DEMO_CONFIG_NAME}.yaml"


def test_load_settings_from_explicit_path(tmp_path: Path) -> None:
    setup_path = tmp_path / "custom.yaml"
    setup_path.write_text(
        """default_model: "test/model"
power_models:
  ENGLAND: ~
  FRANCE: custom/model
map_name: "configs/maps/EFGA_11.map"
max_years: 2
win_score: 9
prompt_variant: baseline_orders
prompt_version: prompt-v2
model_assignment_seed: 12345
llm_seed: 6789
temperature: 0.7
top_p: 0.9
strategy_variant: cautious_opening
strategy_version: strategy-v1
strategy_assignment:
  default: baseline
  by_power:
    GERMANY: baseline
  by_model:
    custom/model: baseline
messaging_variant: latency_pairwise_private
messaging_version: messaging-v1
messaging:
  enabled: true
  power_overrides:
    FRANCE: false
  latency_pairwise_private:
    max_messages_per_response: 3
    max_turns_per_conversation: 2
  max_message_length_chars: 900
feature_flags:
  context_prompt: true
""",
        encoding="utf-8",
    )

    settings = load_settings_from_path(setup_path)

    assert settings.power_models == {
        "ENGLAND": "test/model",
        "FRANCE": "custom/model",
    }
    assert settings.max_years == 2
    assert settings.prompt_variant == "baseline_orders"
    assert settings.prompt_version == "prompt-v2"
    assert settings.model_assignment_seed == 12345
    assert settings.llm_seed == 6789
    assert settings.temperature == 0.7
    assert settings.top_p == 0.9
    assert settings.strategy_variant == "cautious_opening"
    assert settings.strategy_version == "strategy-v1"
    assert settings.strategy_assignment.default == "baseline"
    assert settings.strategy_assignment.by_power == {
        "GERMANY": "baseline",
    }
    assert settings.strategy_assignment.by_model == {
        "custom/model": "baseline",
    }
    assert settings.messaging_variant == "latency_pairwise_private"
    assert settings.messaging_version == "messaging-v1"
    assert settings.messaging.enabled is True
    assert settings.messaging.power_overrides == {"FRANCE": False}
    assert settings.messaging.latency_pairwise_private.max_messages_per_response == 3
    assert settings.messaging.latency_pairwise_private.max_turns_per_conversation == 2
    assert settings.messaging.max_message_length_chars == 900
    assert settings.messaging_enabled_for_power("ENGLAND") is True
    assert settings.messaging_enabled_for_power("FRANCE") is False
    assert settings.messaging_enabled_powers == ["ENGLAND"]
    assert settings.feature_flags == {
        "context_prompt": True,
    }


def test_missing_setup_reports_available_names(tmp_path: Path) -> None:
    (tmp_path / "available.yaml").write_text("default_model: test/model\n")

    with pytest.raises(SetupConfigNotFoundError) as exc_info:
        load_settings("missing", setups_dir=tmp_path)

    exc = exc_info.value
    assert exc.name == "missing"
    assert exc.path == tmp_path / "missing.yaml"
    assert exc.available == ["available"]
    assert "Config 'missing' not found" in str(exc)
    assert "Available: ['available']" in str(exc)


def test_setup_path_helpers_use_explicit_directory(tmp_path: Path) -> None:
    (tmp_path / "beta.yaml").write_text("default_model: test/model\n")
    (tmp_path / "alpha.yaml").write_text("default_model: test/model\n")

    assert setup_path_for("alpha", setups_dir=tmp_path) == tmp_path / "alpha.yaml"
    assert available_setup_names(tmp_path) == ["alpha", "beta"]


@pytest.mark.parametrize(
    "unsafe_name",
    [
        "../demo_setups/short_demo_EFGA_baseline",
        "nested/setup",
        r"nested\setup",
        "..",
        "bad name",
    ],
)
def test_setup_path_rejects_path_like_config_names(unsafe_name: str) -> None:
    with pytest.raises(InvalidArtifactNameError):
        setup_path_for(unsafe_name)


def test_load_settings_rejects_invalid_game_bounds(tmp_path: Path) -> None:
    setup_path = tmp_path / "invalid.yaml"
    setup_path.write_text(
        """default_model: "test/model"
power_models:
  ENGLAND: "test/model"
map_name: "configs/maps/EFGA_11.map"
max_years: 0
win_score: 99
total_scs: 34
""",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_settings_from_path(setup_path)


def test_load_settings_rejects_missing_explicit_map_path(tmp_path: Path) -> None:
    setup_path = tmp_path / "missing-map.yaml"
    setup_path.write_text(
        """default_model: "test/model"
power_models:
  ENGLAND: "test/model"
map_name: "configs/maps/missing.map"
max_years: 1
win_score: 9
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Configured map file does not exist"):
        load_settings_from_path(setup_path)


def test_load_settings_accepts_windows_project_map_path(tmp_path: Path) -> None:
    setup_path = tmp_path / "windows-map.yaml"
    setup_path.write_text(
        r"""default_model: "test/model"
power_models:
  ENGLAND: "test/model"
map_name: 'C:\Users\Rayan\Desktop\Prog Project\StratosAPI\configs\maps\EFGA_11.map'
max_years: 1
win_score: 9
""",
        encoding="utf-8",
    )

    settings = load_settings_from_path(setup_path)

    assert (
        settings.map_name
        == r"C:\Users\Rayan\Desktop\Prog Project\StratosAPI\configs\maps\EFGA_11.map"
    )
