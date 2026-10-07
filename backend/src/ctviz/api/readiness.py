"""`GET /readyz`: whether this process can do its job, reported check by check.

Ready means the registry answers, because without it no question can be answered. The planner's model list
is information only: a key that does not list a configured model, or a provider that does not answer in
time, never makes the service unready, since everything except a model-written plan still works.
"""

from typing import Final, Literal

import anyio
import structlog
from pydantic import BaseModel, ConfigDict, Field
from structlog.typing import FilteringBoundLogger

from ctviz.errors import AppError
from ctviz.pipeline import Deps

MODELS_TIMEOUT_S: Final = 2.0
REGISTRY_TIMEOUT_S: Final = 5.0

_log: FilteringBoundLogger = structlog.get_logger()


class _Report(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConfigurationReadiness(_Report):
    is_loaded: bool = Field(description="The settings were read and passed their checks at start-up.")


class RegistryReadiness(_Report):
    is_reachable: bool = Field(
        description="`GET /version` of ClinicalTrials.gov answered. It is cached for five minutes."
    )
    api_version: str | None
    data_timestamp: str | None = Field(
        description="As the registry wrote it; every answer is cached under it."
    )
    reason: str | None = Field(
        description="The error code of the failure when the registry is not reachable."
    )


class PlannerReadiness(_Report):
    is_available: bool = Field(description="A model is configured to write plans.")
    models: list[str] = Field(description="The configured models: the planner, then its fallback.")
    models_check: Literal["ok", "missing", "unavailable", "skipped"] = Field(
        description="`ok`: the key lists every configured model (it may list more). `missing`: it lists "
        "some but not all. `unavailable`: the list could not be read in two seconds. `skipped`: no model "
        "is configured. None of them makes the service unready."
    )
    missing_models: list[str] = Field(description="The configured models the key does not list.")


class Readiness(_Report):
    status: Literal["ready", "not_ready"] = Field(
        description="`not_ready` only when the registry is unreachable."
    )
    configuration: ConfigurationReadiness
    registry: RegistryReadiness
    planner: PlannerReadiness


async def check_readiness(deps: Deps) -> Readiness:
    registry = await _registry(deps)
    return Readiness(
        status="ready" if registry.is_reachable else "not_ready",
        configuration=ConfigurationReadiness(is_loaded=True),
        registry=registry,
        planner=await _planner(deps),
    )


async def _registry(deps: Deps) -> RegistryReadiness:
    try:
        with anyio.fail_after(REGISTRY_TIMEOUT_S):
            version = await deps.ctgov.version()
    except (AppError, TimeoutError) as error:
        reason = error.code.value if isinstance(error, AppError) else "upstream_timeout"
        return RegistryReadiness(is_reachable=False, api_version=None, data_timestamp=None, reason=reason)
    return RegistryReadiness(
        is_reachable=True, api_version=version.api_version, data_timestamp=version.data_timestamp, reason=None
    )


async def _planner(deps: Deps) -> PlannerReadiness:
    configured = [deps.settings.planner_model, deps.settings.planner_fallback_model]
    models = list(dict.fromkeys(model for model in configured if model is not None))
    try:
        with anyio.fail_after(MODELS_TIMEOUT_S):
            listed = await deps.plans.listed_models(timeout_s=MODELS_TIMEOUT_S)
    except Exception as error:  # the list is information only: whatever goes wrong, readiness is answered
        _log.warning("readiness_model_list_failed", reason=type(error).__name__)
        return PlannerReadiness(
            is_available=True, models=models, models_check="unavailable", missing_models=[]
        )
    if listed is None:
        return PlannerReadiness(is_available=False, models=[], models_check="skipped", missing_models=[])
    missing = [model for model in models if model not in listed]
    return PlannerReadiness(
        is_available=True, models=models, models_check="missing" if missing else "ok", missing_models=missing
    )
