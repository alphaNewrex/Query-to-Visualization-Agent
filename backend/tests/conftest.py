"""Configuration shared by every test.

Each test runs with no OPENAI_*, CTVIZ_* or ALLOWED_MODELS variable in its environment, and with
the repository root replaced by an empty temporary folder. A developer's shell can therefore
neither leak into a test nor break one, and the real `.env` is never opened.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
import structlog

import ctviz.settings as settings_module

_REAL_REPOSITORY_ROOT = settings_module.REPOSITORY_ROOT
_CONFIGURATION_PREFIXES = ("OPENAI_", "CTVIZ_")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def real_repository_root() -> Path:
    """The root the package computed from its own location, before any test replaced it."""
    return _REAL_REPOSITORY_ROOT


@pytest.fixture(autouse=True)
def repository_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Isolate the configuration; return the empty folder that stands in for the repository root."""
    for name in list(os.environ):
        # pydantic-settings matches names without regard to case, so the cleaning does too.
        if name.upper().startswith(_CONFIGURATION_PREFIXES) or name.upper() == "ALLOWED_MODELS":
            monkeypatch.delenv(name)
    root = tmp_path / "repository"
    root.mkdir()
    monkeypatch.setattr(settings_module, "REPOSITORY_ROOT", root)
    return root


@pytest.fixture(autouse=True)
def default_logging() -> Iterator[None]:
    """`create_app` configures structlog for the whole process; undo it so test order cannot matter."""
    yield
    structlog.reset_defaults()
