"""Configuration: the owner's three variables, optional `CTVIZ_` overrides, and where they come from.

The service runs with exactly the three variables of the root `.example.env`. Everything else
has a default and an optional `CTVIZ_`-prefixed override.
"""

import os
from pathlib import Path
from typing import Annotated, Final, Literal, Self

import structlog
from pydantic import Field, SecretStr, ValidationInfo, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    EnvSettingsSource,
    NoDecode,
    SettingsConfigDict,
)
from structlog.typing import FilteringBoundLogger

from ctviz.log import LogFormat

Effort = Literal["none", "minimal", "low", "medium", "high", "xhigh"]
Source = Literal["process_env", "file", "default"]

ENV_FILE_VARIABLE: Final = "CTVIZ_ENV_FILE"
# This file is <repository root>/backend/src/ctviz/settings.py.
REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[3]

# What the root `.example.env` holds where the key belongs.
_EXAMPLE_KEY_PLACEHOLDER: Final = "your_openai_api_key"
_EFFORTS_OF_EVERY_REASONING_FAMILY: Final = frozenset({"low", "medium", "high"})

_log: FilteringBoundLogger = structlog.get_logger()


class Settings(BaseSettings):
    """Everything configurable about the service; safe to build with no variable set at all."""

    model_config = SettingsConfigDict(env_prefix="CTVIZ_", extra="ignore", hide_input_in_errors=True)

    # The owner's three variables, read under exactly these names.
    openai_api_key: SecretStr | None = Field(None, validation_alias="OPENAI_API_KEY")
    openai_api_base: str = Field("https://api.openai.com/v1", validation_alias="OPENAI_API_BASE")
    allowed_models: Annotated[frozenset[str], NoDecode] = Field(
        frozenset(), validation_alias="ALLOWED_MODELS"
    )

    # Optional, read as CTVIZ_PLANNER_MODEL, CTVIZ_WALK_CAP, ...
    planner_model: str = "gpt-5.4-mini"
    planner_effort: Effort | None = "low"
    planner_fallback_model: str | None = "gpt-4.1-mini"
    planner_timeout_s: float = 20.0
    ctgov_base_url: str = "https://clinicaltrials.gov/api/v2"
    ctgov_concurrency: int = 4
    ctgov_burst: int = 10
    ctgov_rate_per_s: float = 5.0
    one_page_max: int = 1000
    walk_cap: int = 5000
    max_fanout_requests: int = 60
    low_match_threshold: int = 10
    request_deadline_s: float = 45.0
    cache_ttl_s: int = 900
    examples_dir: Path | None = None
    log_format: LogFormat = "console"

    @field_validator("*", mode="before")
    @classmethod
    def _blank_is_not_set(cls, value: object, info: ValidationInfo) -> object:
        """A variable left blank, as in `OPENAI_API_KEY=`, counts as not set: the field keeps its default.

        Taken as written, a blank key would count as a configured key, and a blank URL or
        directory would be used as it stands.
        """
        if isinstance(value, str) and not value.strip() and info.field_name is not None:
            return cls.model_fields[info.field_name].default
        return value

    @field_validator("openai_api_key", mode="before")
    @classmethod
    def _placeholder_is_no_key(cls, value: object) -> object:
        """The placeholder a reviewer has after `cp .example.env .env` is not a key.

        Taken for one, it would make the planner look available and turn every question into
        a 503 after the provider's 401.
        """
        return None if isinstance(value, str) and value.strip() == _EXAMPLE_KEY_PLACEHOLDER else value

    @field_validator("allowed_models", mode="before")
    @classmethod
    def _split(cls, value: object) -> object:
        """Split a comma-separated list; `docker run --env-file` keeps literal quotes, so drop them."""
        if not isinstance(value, str):
            return value
        return frozenset(part.strip() for part in value.strip().strip("\"'").split(",") if part.strip())

    @model_validator(mode="after")
    def _check_models(self) -> Self:
        """Reject a model or effort the key cannot use, naming the variable to change.

        Left unchecked, a wrong pair is an HTTP 400 on every request. The checks run only when
        a key is configured, and the messages name variables, never their values.
        """
        if self.openai_api_key is None:
            return self
        if self.allowed_models:
            configured = {
                "CTVIZ_PLANNER_MODEL": self.planner_model,
                "CTVIZ_PLANNER_FALLBACK_MODEL": self.planner_fallback_model,
            }
            for variable, model in configured.items():
                if model is not None and model not in self.allowed_models:
                    raise ValueError(
                        f"{variable} names a model that is not in ALLOWED_MODELS: "
                        f"change {variable} or add the model to ALLOWED_MODELS"
                    )
        accepted = accepted_efforts(self.planner_model)
        if accepted is not None and self.planner_effort is not None and self.planner_effort not in accepted:
            raise ValueError(
                "CTVIZ_PLANNER_EFFORT is not accepted by the model family of CTVIZ_PLANNER_MODEL: "
                f"change CTVIZ_PLANNER_EFFORT to one of {', '.join(sorted(accepted))}"
            )
        return self


# The owner's variables are the fields read under a name of their own, without the prefix.
OWNER_VARIABLES: Final = tuple(
    field.validation_alias
    for field in Settings.model_fields.values()
    if isinstance(field.validation_alias, str)
)


def accepted_efforts(model: str) -> frozenset[str] | None:
    """The reasoning efforts a model's family accepts, as measured against the OpenAI API.

    None means there is nothing to check: the gpt-4o and gpt-4.1 families take no effort,
    so none is sent to them, and an unknown family is left for the provider to judge.
    """
    if model in ("gpt-5", "gpt-5-mini"):
        return _EFFORTS_OF_EVERY_REASONING_FAMILY | {"minimal"}
    if model.startswith("gpt-5.1"):
        return _EFFORTS_OF_EVERY_REASONING_FAMILY | {"none"}
    if model.startswith(("gpt-5.2", "gpt-5.4")):
        return _EFFORTS_OF_EVERY_REASONING_FAMILY | {"none", "xhigh"}
    return None


def find_env_file() -> Path | None:
    """The env file to load: `$CTVIZ_ENV_FILE` if it exists, else `<repository root>/.env`, else none.

    The root is computed from this file's location. A relative `.env` would resolve against the
    working directory, so a service started from `backend/` would silently miss the root file.
    """
    override = os.environ.get(ENV_FILE_VARIABLE)
    candidates = [Path(override)] if override else []
    candidates.append(REPOSITORY_ROOT / ".env")
    return next((path for path in candidates if path.is_file()), None)


def load_settings() -> Settings:
    """Settings from the process environment and the discovered env file; the environment wins."""
    return Settings(_env_file=find_env_file())


def variable_sources(env_file: Path | None) -> dict[str, Source]:
    """Where each of the owner's variables gets its value from.

    Asks the two pydantic-settings sources themselves, so the answer follows the same
    precedence, parsing and case rules as the load.
    """
    from_process_env = EnvSettingsSource(Settings)()
    from_file = DotEnvSettingsSource(Settings, env_file=env_file)()

    def source_of(variable: str) -> Source:
        if variable in from_process_env:
            return "process_env"
        return "file" if variable in from_file else "default"

    return {variable: source_of(variable) for variable in OWNER_VARIABLES}


def log_configuration_sources(settings: Settings) -> None:
    """Log, at start-up, where the configuration came from, and warn when no key is configured.

    Names and sources are logged, never values. The process environment beats the env file
    without a word from the libraries, so a key exported in a shell silently replaces the
    project's key and with it the usable models.
    """
    env_file = find_env_file()
    _log.info("env_file", path=None if env_file is None else str(env_file))
    for variable, source in variable_sources(env_file).items():
        _log.info("configuration_source", variable=variable, source=source)
    # The OpenAI SDK reads these two from the process environment on its own.
    _log.info(
        "openai_sdk_environment",
        openai_org_id_is_set=bool(os.environ.get("OPENAI_ORG_ID")),
        openai_project_id_is_set=bool(os.environ.get("OPENAI_PROJECT_ID")),
    )
    if settings.openai_api_key is None:
        _log.warning(
            "planner_not_configured",
            variable="OPENAI_API_KEY",
            reason="absent, blank or still the placeholder of .example.env",
            effect="no model writes plans; a request that needs one is answered 503 planner_unavailable",
        )
