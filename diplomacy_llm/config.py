import re
from pathlib import Path, PureWindowsPath
from typing import Self

import yaml
from pydantic import AliasChoices, BaseModel, Field, computed_field, model_validator

# Paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SETUPS_DIR = PROJECT_ROOT / "configs" / "demo_setups"

_SAFE_ARTIFACT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_MODEL_ASSIGNMENT_SEED_LIMIT = 2**32


class SetupConfigNotFoundError(FileNotFoundError):
    """Raised when a named setup YAML file cannot be found."""

    def __init__(self, name: str, path: Path, available: list[str]) -> None:
        self.name = name
        self.path = path
        self.available = available
        super().__init__(
            f"Config '{name}' not found at {path}\nAvailable: {available}",
        )


class InvalidArtifactNameError(ValueError):
    """Raised when a user-controlled artifact/config name is unsafe."""


class StrategyAssignmentSettings(BaseModel):
    """YAML settings for assigning strategies after model assignment is resolved."""

    model_config = {"frozen": True}

    default: str = "baseline"
    by_power: dict[str, str] = Field(default_factory=dict)
    by_model: dict[str, str] = Field(default_factory=dict)


class LatencyPairwisePrivateSettings(BaseModel):
    """YAML settings for the latency-ordered pairwise messaging protocol."""

    model_config = {"frozen": True}

    max_messages_per_response: int = Field(
        default=6,
        ge=0,
        validation_alias=AliasChoices(
            "max_messages_per_response",
            "max_messages_per_decision",
        ),
    )
    max_turns_per_conversation: int = Field(default=4, ge=1)
    max_messages_sent_per_power: int | None = Field(default=None, ge=1)


class MessagingSettings(BaseModel):
    """YAML settings for per-game private messaging."""

    model_config = {"frozen": True}

    enabled: bool = False
    power_overrides: dict[str, bool] = Field(default_factory=dict)
    max_message_length_chars: int = Field(default=1200, ge=1)
    latency_pairwise_private: LatencyPairwisePrivateSettings = Field(
        default_factory=LatencyPairwisePrivateSettings,
    )

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_protocol_fields(cls, data: dict) -> dict:
        """Move legacy flat protocol fields into protocol-specific settings."""
        if not isinstance(data, dict):
            return data

        migrated = dict(data)
        pairwise = dict(migrated.get("latency_pairwise_private") or {})

        pairwise_aliases = {
            "max_messages_per_response": "max_messages_per_response",
            "max_messages_per_decision": "max_messages_per_response",
            "max_turns_per_conversation": "max_turns_per_conversation",
            "max_messages_sent_per_power": "max_messages_sent_per_power",
        }
        for legacy_name, nested_name in pairwise_aliases.items():
            if legacy_name in migrated and nested_name not in pairwise:
                pairwise[nested_name] = migrated.pop(legacy_name)

        if pairwise:
            migrated["latency_pairwise_private"] = pairwise
        return migrated

    def enabled_for_power(self, power: str) -> bool:
        """Return whether messaging is enabled for one power after overrides."""
        return self.power_overrides.get(power, self.enabled)


class Settings(BaseModel):
    """Run settings loaded from one explicit YAML setup file."""

    model_config = {"frozen": True, "extra": "forbid"}

    # Models
    default_model: str = Field(min_length=1)
    power_models: dict[str, str] = Field(min_length=1)

    # Game
    map_name: str = Field(min_length=1)
    max_years: int = Field(ge=1)
    win_score: int = Field(ge=1)
    total_scs: int = Field(default=34, ge=1)

    # LLM call
    max_tokens: int = Field(default=4096, ge=1)
    max_retries: int = Field(default=3, ge=1)
    retry_delay: float = Field(default=2.0, ge=0)
    llm_seed: int | None = Field(default=None, ge=0, lt=_MODEL_ASSIGNMENT_SEED_LIMIT)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    shuffle_models: bool = False
    model_assignment_seed: int | None = Field(
        default=None,
        ge=0,
        lt=_MODEL_ASSIGNMENT_SEED_LIMIT,
    )
    prompt_variant: str = "baseline_orders"
    prompt_version: str = "neutral-v1"

    # Future experiment variants
    strategy_variant: str = "none"
    strategy_version: str = "none"
    strategy_assignment: StrategyAssignmentSettings = Field(
        default_factory=StrategyAssignmentSettings,
    )
    messaging_variant: str = "none"
    messaging_version: str = "none"
    messaging: MessagingSettings = Field(default_factory=MessagingSettings)
    feature_flags: dict[str, bool] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def fill_null_models(cls, data: dict) -> dict:
        """Replace null per-power model values with default_model."""
        default = data.get("default_model", "")
        pm = data.get("power_models", {})
        data["power_models"] = {p: (m or default) for p, m in pm.items()}
        return data

    @model_validator(mode="after")
    def validate_run_settings(self) -> Self:
        """Validate cross-field invariants that keep setup files executable."""
        if not self.default_model.strip():
            msg = "default_model must be a non-empty model id"
            raise ValueError(msg)
        if not self.map_name.strip():
            msg = "map_name must be non-empty"
            raise ValueError(msg)
        if self.win_score > self.total_scs:
            msg = "win_score must be less than or equal to total_scs"
            raise ValueError(msg)
        for power, model in self.power_models.items():
            if not str(power).strip():
                msg = "power_models contains an empty power name"
                raise ValueError(msg)
            if not str(model).strip():
                msg = f"power_models[{power!r}] must be a non-empty model id"
                raise ValueError(msg)
        return self

    @computed_field
    @property
    def powers(self) -> list[str]:
        """Return powers in YAML declaration order."""
        return list(self.power_models.keys())

    def messaging_enabled_for_power(self, power: str) -> bool:
        """Return whether one power may participate in private messaging."""
        return self.messaging.enabled_for_power(power)

    @computed_field
    @property
    def messaging_enabled_powers(self) -> list[str]:
        """Return powers with messaging enabled in YAML declaration order."""
        return [
            power for power in self.powers if self.messaging_enabled_for_power(power)
        ]


def available_setup_names(setups_dir: Path = SETUPS_DIR) -> list[str]:
    """Return setup names available in the configured setups directory."""
    return sorted(p.stem for p in setups_dir.glob("*.yaml"))


def validate_safe_artifact_name(name: str, *, label: str = "name") -> str:
    """Validate user-controlled names before using them in local artifact paths."""
    if not isinstance(name, str) or not name:
        msg = f"{label} must be a non-empty string"
        raise InvalidArtifactNameError(msg)
    if Path(name).is_absolute() or "/" in name or "\\" in name:
        msg = f"{label} must be a simple filename, not a path: {name!r}"
        raise InvalidArtifactNameError(msg)
    if name in {".", ".."} or ".." in Path(name).parts:
        msg = f"{label} must not contain parent-directory traversal: {name!r}"
        raise InvalidArtifactNameError(msg)
    if not _SAFE_ARTIFACT_NAME_RE.fullmatch(name):
        msg = (
            f"{label} contains unsupported characters: {name!r}. "
            "Use letters, numbers, underscore, dash, or dot."
        )
        raise InvalidArtifactNameError(msg)
    return name


def setup_path_for(name: str, setups_dir: Path = SETUPS_DIR) -> Path:
    """Return the YAML path for a demo setup name."""
    name = validate_safe_artifact_name(name, label="config name")
    return setups_dir / f"{name}.yaml"


def load_settings_from_path(path: Path) -> Settings:
    """Load settings from an explicit YAML file path."""
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    settings = Settings(**data)
    _validate_configured_map_path(settings.map_name)
    return settings


def load_settings(name: str, setups_dir: Path = SETUPS_DIR) -> Settings:
    """Load an explicit YAML setup by name."""
    path = setup_path_for(name, setups_dir)
    if not path.exists():
        raise SetupConfigNotFoundError(
            name,
            path,
            _available_setup_names_for_error(setups_dir),
        )
    return load_settings_from_path(path)


def _available_setup_names_for_error(setups_dir: Path) -> list[str]:
    return available_setup_names(setups_dir)


def _validate_configured_map_path(map_name: str) -> None:
    """Fail early when a setup references a missing project map file."""
    if not _looks_like_map_path(map_name):
        return

    if _configured_map_path_exists(map_name):
        return

    msg = f"Configured map file does not exist: {map_name}"
    raise ValueError(msg)


def _configured_map_path_exists(map_name: str) -> bool:
    path = Path(map_name)
    if path.is_absolute() and path.exists():
        return True

    if not path.is_absolute() and (PROJECT_ROOT / path).exists():
        return True

    filename = _path_name_for_any_platform(map_name)
    return bool(filename and (PROJECT_ROOT / "configs" / "maps" / filename).exists())


def _path_name_for_any_platform(raw_path: str) -> str:
    windows_name = PureWindowsPath(raw_path).name
    if windows_name:
        return windows_name
    return Path(raw_path).name


def _looks_like_map_path(map_name: str) -> bool:
    path = Path(map_name)
    return (
        path.is_absolute()
        or PureWindowsPath(map_name).is_absolute()
        or "/" in map_name
        or "\\" in map_name
    )
