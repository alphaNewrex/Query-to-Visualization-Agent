"""Settings: the owner's three variables, env-file discovery, start-up checks and the source log.

Every value here is a placeholder. The real `.env` is never opened: `conftest.py` points the
repository root at an empty temporary folder and clears the configuration variables.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError
from structlog.testing import capture_logs

from ctviz.settings import (
    ENV_FILE_VARIABLE,
    Settings,
    accepted_efforts,
    find_env_file,
    load_settings,
    log_configuration_sources,
    variable_sources,
)

PLACEHOLDER_KEY = "sk-placeholder-0123456789-abcdefghijklmnopqrstuvwxyz-9876543210"
WITH_KEY = {"OPENAI_API_KEY": PLACEHOLDER_KEY}
TWO_MODELS = frozenset({"gpt-4.1-mini", "gpt-5.4-mini"})


def export(monkeypatch: pytest.MonkeyPatch, **variables: str) -> None:
    """Put variables into the process environment, as a shell would."""
    for name, value in variables.items():
        monkeypatch.setenv(name, value)


def write_env(path: Path, *lines: str) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def key_of(settings: Settings) -> str | None:
    return None if settings.openai_api_key is None else settings.openai_api_key.get_secret_value()


@pytest.fixture
def root_env_file(repository_root: Path) -> Path:
    """A root `.env` laid out like the owner's example, holding placeholder values."""
    return write_env(
        repository_root / ".env",
        f"OPENAI_API_KEY={PLACEHOLDER_KEY}",
        "OPENAI_API_BASE=https://gateway.example/v1",
        "",
        'ALLOWED_MODELS="gpt-4.1-mini,gpt-5.4-mini"',
    )


# --- the three owner variables and the optional overrides -------------------------------------


def test_the_service_is_configured_with_nothing_set() -> None:
    settings = Settings()

    assert settings.openai_api_key is None
    assert settings.openai_api_base == "https://api.openai.com/v1"
    assert settings.allowed_models == frozenset()
    assert (settings.planner_model, settings.planner_effort) == ("gpt-5.4-mini", "low")
    assert settings.planner_fallback_model == "gpt-4.1-mini"
    assert settings.request_deadline_s == 45.0


def test_the_caches_have_sizes_that_can_be_changed_but_not_emptied(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings()
    assert (settings.plan_cache_size, settings.response_cache_size) == (512, 256)

    export(monkeypatch, CTVIZ_PLAN_CACHE_SIZE="8", CTVIZ_RESPONSE_CACHE_SIZE="4")
    resized = Settings()
    assert (resized.plan_cache_size, resized.response_cache_size) == (8, 4)

    export(monkeypatch, CTVIZ_RESPONSE_CACHE_SIZE="0")
    with pytest.raises(ValidationError):
        Settings()


def test_owner_variables_are_read_unprefixed_and_the_rest_only_with_the_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    export(
        monkeypatch,
        OPENAI_API_KEY=PLACEHOLDER_KEY,
        OPENAI_API_BASE="https://gateway.example/v1",
        ALLOWED_MODELS="gpt-4.1-mini,gpt-5.4-mini",
        CTVIZ_OPENAI_API_BASE="https://prefixed.example/v1",
        CTVIZ_WALK_CAP="1234",
        WALK_CAP="9999",
    )

    settings = Settings()

    assert key_of(settings) == PLACEHOLDER_KEY
    assert PLACEHOLDER_KEY not in repr(settings)
    assert settings.openai_api_base == "https://gateway.example/v1"
    assert settings.allowed_models == TWO_MODELS
    assert settings.walk_cap == 1234


def test_the_sdk_variable_openai_base_url_does_not_move_the_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    export(monkeypatch, OPENAI_BASE_URL="https://elsewhere.example/v1")

    assert Settings().openai_api_base == "https://api.openai.com/v1"


@pytest.mark.parametrize("not_a_key", ["", "   ", "your_openai_api_key"])
def test_a_blank_key_and_the_placeholder_of_the_example_file_mean_no_key(
    not_a_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    export(monkeypatch, OPENAI_API_KEY=not_a_key)

    assert Settings().openai_api_key is None


def test_a_variable_left_blank_counts_as_not_set(repository_root: Path) -> None:
    write_env(
        repository_root / ".env",
        "OPENAI_API_KEY=",
        "OPENAI_API_BASE=",
        "ALLOWED_MODELS=",
        "CTVIZ_PLANNER_FALLBACK_MODEL=",
        "CTVIZ_PLANNER_EFFORT=",
        "CTVIZ_WALK_CAP=",
        "CTVIZ_EXAMPLES_DIR=",
    )

    assert load_settings().model_dump() == Settings().model_dump()


def test_a_variable_blanked_in_the_shell_hides_its_value_in_the_env_file(
    root_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    export(monkeypatch, OPENAI_API_KEY="", OPENAI_API_BASE="")

    settings = load_settings()

    assert settings.openai_api_key is None
    assert settings.openai_api_base == "https://api.openai.com/v1"


@pytest.mark.parametrize(
    "raw",
    [
        "gpt-4.1-mini,gpt-5.4-mini",
        '"gpt-4.1-mini,gpt-5.4-mini"',  # `docker run --env-file` passes the quotes through
        "'gpt-4.1-mini,gpt-5.4-mini'",
        " gpt-4.1-mini , gpt-5.4-mini ,",
    ],
)
def test_allowed_models_is_a_comma_separated_list_quoted_or_not(
    raw: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    export(monkeypatch, ALLOWED_MODELS=raw)

    assert Settings().allowed_models == TWO_MODELS


def test_the_owner_example_file_configures_the_service_without_a_key(real_repository_root: Path) -> None:
    # What a reviewer has after `cp .example.env .env`, before the key is filled in.
    settings = Settings(_env_file=real_repository_root / ".example.env")

    assert settings.openai_api_key is None
    assert settings.openai_api_base == "https://api.openai.com/v1"
    assert len(settings.allowed_models) == 12
    assert {settings.planner_model, settings.planner_fallback_model} <= settings.allowed_models


# --- finding the env file ----------------------------------------------------------------------


def test_the_repository_root_is_computed_from_the_package_location(real_repository_root: Path) -> None:
    assert (real_repository_root / "backend" / "src" / "ctviz" / "settings.py").is_file()


@pytest.mark.parametrize("started_from", ["the repository root", "backend", "an unrelated directory"])
def test_the_root_env_file_is_found_wherever_the_service_is_started(
    started_from: str,
    repository_root: Path,
    root_env_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    working_directory = {
        "the repository root": repository_root,
        "backend": repository_root / "backend",
        "an unrelated directory": tmp_path / "elsewhere",
    }[started_from]
    working_directory.mkdir(exist_ok=True)
    if working_directory != repository_root:
        # A lookup relative to the working directory would pick this file up instead.
        write_env(working_directory / ".env", "OPENAI_API_BASE=https://decoy.example/v1")
    monkeypatch.chdir(working_directory)

    settings = load_settings()

    assert find_env_file() == root_env_file
    assert key_of(settings) == PLACEHOLDER_KEY
    assert settings.openai_api_base == "https://gateway.example/v1"
    assert settings.allowed_models == TWO_MODELS


def test_ctviz_env_file_is_preferred_to_the_root_file(
    root_env_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    override = write_env(tmp_path / "override.env", "OPENAI_API_BASE=https://override.example/v1")
    monkeypatch.setenv(ENV_FILE_VARIABLE, str(override))

    assert find_env_file() == override
    assert load_settings().openai_api_base == "https://override.example/v1"


@pytest.mark.parametrize("not_a_file", ["missing.env", "a-directory"])
def test_a_ctviz_env_file_that_is_not_a_file_falls_back_to_the_root_file(
    not_a_file: str, root_env_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a-directory").mkdir()
    monkeypatch.setenv(ENV_FILE_VARIABLE, str(tmp_path / not_a_file))

    assert find_env_file() == root_env_file


def test_no_env_file_at_all_still_gives_settings() -> None:
    # On a developer's machine the real root holds a `.env`; finding none proves it is out of reach.
    assert find_env_file() is None
    assert load_settings().openai_api_key is None


def test_the_process_environment_beats_the_env_file(
    root_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    export(monkeypatch, OPENAI_API_BASE="https://from-the-shell.example/v1")

    settings = load_settings()

    assert settings.openai_api_base == "https://from-the-shell.example/v1"
    assert key_of(settings) == PLACEHOLDER_KEY


def test_other_variables_in_the_env_file_are_ignored(repository_root: Path) -> None:
    write_env(
        repository_root / ".env", "DATABASE_URL=postgres://example", "WALK_CAP=9", "CTVIZ_WALK_CAP=1234"
    )

    assert load_settings().walk_cap == 1234


# --- start-up checks ---------------------------------------------------------------------------

NOT_ALLOWED = "names a model that is not in ALLOWED_MODELS"
EFFORT_REJECTED = "CTVIZ_PLANNER_EFFORT is not accepted by the model family of CTVIZ_PLANNER_MODEL"


@pytest.mark.parametrize(
    ("shell", "complaint", "configured_value"),
    [
        ({"ALLOWED_MODELS": "gpt-4.1-mini,gpt-5.4"}, f"CTVIZ_PLANNER_MODEL {NOT_ALLOWED}", "gpt-5.4-mini"),
        (
            {"ALLOWED_MODELS": "gpt-5.4-mini,gpt-5.4"},
            f"CTVIZ_PLANNER_FALLBACK_MODEL {NOT_ALLOWED}",
            "gpt-4.1-mini",
        ),
        ({"CTVIZ_PLANNER_MODEL": "gpt-5", "CTVIZ_PLANNER_EFFORT": "xhigh"}, EFFORT_REJECTED, "xhigh"),
        ({"CTVIZ_PLANNER_MODEL": "gpt-5.1", "CTVIZ_PLANNER_EFFORT": "minimal"}, EFFORT_REJECTED, "gpt-5.1"),
    ],
)
def test_a_model_or_effort_the_key_cannot_use_fails_naming_the_variable_not_the_value(
    shell: dict[str, str], complaint: str, configured_value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    export(monkeypatch, **WITH_KEY, **shell)

    with pytest.raises(ValidationError) as failure:
        Settings()

    assert complaint in str(failure.value)
    assert configured_value not in str(failure.value)


@pytest.mark.parametrize(
    "shell",
    [
        pytest.param(
            {
                "ALLOWED_MODELS": "another-model",
                "CTVIZ_PLANNER_MODEL": "gpt-5",
                "CTVIZ_PLANNER_EFFORT": "xhigh",
            },
            id="without a key nothing is checked",
        ),
        pytest.param({**WITH_KEY, "CTVIZ_PLANNER_MODEL": "a-gateway-model"}, id="no allow-list"),
        pytest.param(
            {**WITH_KEY, "ALLOWED_MODELS": "", "CTVIZ_PLANNER_MODEL": "a-gateway-model"},
            id="empty allow-list",
        ),
        pytest.param(
            {**WITH_KEY, "CTVIZ_PLANNER_MODEL": "gpt-5.4", "CTVIZ_PLANNER_EFFORT": "xhigh"},
            id="an effort the family accepts",
        ),
        pytest.param(
            {**WITH_KEY, "CTVIZ_PLANNER_MODEL": "gpt-4.1", "CTVIZ_PLANNER_EFFORT": "xhigh"},
            id="a family that takes no effort is sent none",
        ),
    ],
)
def test_a_usable_configuration_passes_the_start_up_checks(
    shell: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    export(monkeypatch, **shell)

    assert Settings().planner_model == shell["CTVIZ_PLANNER_MODEL"]


def test_no_fallback_model_and_no_effort_need_no_check(monkeypatch: pytest.MonkeyPatch) -> None:
    export(monkeypatch, **WITH_KEY, ALLOWED_MODELS="gpt-5.4-mini")

    settings = Settings(planner_fallback_model=None, planner_effort=None)

    assert (settings.planner_fallback_model, settings.planner_effort) == (None, None)


GPT_5 = {"minimal", "low", "medium", "high"}
GPT_5_1 = {"none", "low", "medium", "high"}
GPT_5_2_AND_5_4 = {"none", "low", "medium", "high", "xhigh"}


@pytest.mark.parametrize(
    ("model", "efforts"),
    [
        ("gpt-5", GPT_5),
        ("gpt-5-mini", GPT_5),
        ("gpt-5.1", GPT_5_1),
        ("gpt-5.2", GPT_5_2_AND_5_4),
        ("gpt-5.4", GPT_5_2_AND_5_4),
        ("gpt-5.4-mini", GPT_5_2_AND_5_4),
        ("gpt-5.4-nano", GPT_5_2_AND_5_4),
        # These families take no effort and none is sent, so there is nothing to check.
        ("gpt-4o-mini", None),
        ("gpt-4o-2024-08-06", None),
        ("gpt-4.1", None),
        ("gpt-4.1-mini", None),
        ("gpt-4.1-nano", None),
        ("a-gateway-model", None),
    ],
)
def test_accepted_efforts_follow_the_measured_model_families(model: str, efforts: set[str] | None) -> None:
    assert accepted_efforts(model) == efforts


@pytest.mark.parametrize(
    ("variable", "bad_value", "named"),
    [
        ("CTVIZ_WALK_CAP", "not-a-number", "walk_cap"),  # a field error
        ("ALLOWED_MODELS", "only-this-model", "CTVIZ_PLANNER_MODEL"),  # the whole-model check
    ],
)
def test_a_validation_error_shows_neither_the_key_nor_the_rejected_input(
    variable: str, bad_value: str, named: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    export(monkeypatch, **WITH_KEY, **{variable: bad_value})

    with pytest.raises(ValidationError) as failure:
        Settings()

    for text in (str(failure.value), repr(failure.value)):
        assert named in text
        assert bad_value not in text
        # Without hide_input_in_errors Pydantic prints the head and the tail of the raw input.
        assert PLACEHOLDER_KEY[:10] not in text
        assert PLACEHOLDER_KEY[-10:] not in text


# --- where each value came from ----------------------------------------------------------------


def test_each_owner_variable_reports_its_source(
    repository_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = write_env(
        repository_root / ".env",
        f"OPENAI_API_KEY={PLACEHOLDER_KEY}",
        "OPENAI_API_BASE=https://gateway.example/v1",
    )
    # pydantic-settings matches names without regard to case, and so must the report.
    export(monkeypatch, openai_api_key="sk-placeholder-from-the-shell")

    assert variable_sources(env_file) == {
        "OPENAI_API_KEY": "process_env",
        "OPENAI_API_BASE": "file",
        "ALLOWED_MODELS": "default",
    }
    assert key_of(load_settings()) == "sk-placeholder-from-the-shell"


def test_the_start_up_log_names_sources_and_never_values(
    root_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    export(monkeypatch, OPENAI_API_BASE="https://from-the-shell.example/v1", OPENAI_ORG_ID="org-placeholder")

    with capture_logs() as entries:
        log_configuration_sources(load_settings())

    by_event = {entry["event"]: entry for entry in entries}
    sources = {e["variable"]: e["source"] for e in entries if e["event"] == "configuration_source"}
    assert sources == {"OPENAI_API_KEY": "file", "OPENAI_API_BASE": "process_env", "ALLOWED_MODELS": "file"}
    assert by_event["env_file"]["path"] == str(root_env_file)
    assert by_event["openai_sdk_environment"]["openai_org_id_is_set"] is True
    assert by_event["openai_sdk_environment"]["openai_project_id_is_set"] is False
    assert "planner_not_configured" not in by_event
    logged = repr(entries)
    for value in (PLACEHOLDER_KEY, "from-the-shell.example", "org-placeholder", "gpt-4.1-mini"):
        assert value not in logged


def test_the_start_up_log_warns_when_there_is_no_key() -> None:
    with capture_logs() as entries:
        log_configuration_sources(load_settings())

    by_event = {entry["event"]: entry for entry in entries}
    assert by_event["env_file"]["path"] is None
    assert by_event["planner_not_configured"]["log_level"] == "warning"
    assert by_event["planner_not_configured"]["variable"] == "OPENAI_API_KEY"
