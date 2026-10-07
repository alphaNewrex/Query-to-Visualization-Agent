"""`scripts/check_submission.py` fails on what must not ship and warns of what the owner must still do."""

import importlib.util
import json
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.unit.contract_samples import time_series_response

TOP = "query-to-visualization-agent/"
DROPPED = ("docs/examples/04", "docs/examples/05")
HEADINGS = "\n".join(f"## {heading}" for heading in (
    "How to run", "Request schema", "Response schema", "Example runs",
    "Key design decisions and tradeoffs", "How correctness was validated",
    "Limitations and what I would improve", "How this was built",
))  # fmt: skip


@pytest.fixture(scope="module")
def script(real_repository_root: Path) -> ModuleType:
    path = real_repository_root / "backend" / "scripts" / "check_submission.py"
    spec = importlib.util.spec_from_file_location("check_submission", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def good_files() -> dict[str, str]:
    files = {
        "readme.md": f"# Title\n\n{HEADINGS}\n",
        "backend/uv.lock": "lock",
        "frontend/pnpm-lock.yaml": "lock",
        ".example.env": "OPENAI_API_KEY=your_openai_api_key",
    }
    for number in range(1, 6):
        folder = f"docs/examples/{number:02d}-example"
        files[f"{folder}/request.json"] = json.dumps({"query": "How many trials?"})
        files[f"{folder}/response.json"] = json.dumps(time_series_response())
    return files


def build(path: Path, files: dict[str, str], top: str = TOP) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in files.items():
            archive.writestr(f"{top}{name}", text)
    return path


def run(script: ModuleType, path: Path) -> tuple[list[str], list[str], list[str]]:
    with zipfile.ZipFile(path) as archive:
        passed, failures, warnings = script.inspect(archive)
    return passed, failures, warnings


def test_a_sound_archive_passes(script: ModuleType, tmp_path: Path) -> None:
    passed, failures, warnings = run(script, build(tmp_path / "a.zip", good_files()))

    assert failures == [] and warnings == []
    assert len(passed) == 5


def test_an_env_file_anywhere_fails_but_the_example_env_does_not(script: ModuleType, tmp_path: Path) -> None:
    files = {**good_files(), "backend/.env": "OPENAI_API_KEY=secret", "frontend/.env.local": "x"}

    _, failures, _ = run(script, build(tmp_path / "a.zip", files))

    assert len(failures) == 1
    assert "backend/.env" in failures[0] and "frontend/.env.local" in failures[0]
    assert ".example.env" not in failures[0]


def test_env_templates_may_ship_but_other_env_files_may_not(script: ModuleType) -> None:
    assert not any(
        script._is_env_file(name) for name in (".example.env", ".env.example", ".env.sample", "env.py")
    )
    assert all(script._is_env_file(name) for name in (".env", ".env.local", ".env.production"))


def test_a_missing_heading_fails_and_names_it(script: ModuleType, tmp_path: Path) -> None:
    files = {
        **good_files(),
        "readme.md": good_files()["readme.md"].replace("## Limitations and what I would improve", ""),
    }

    _, failures, _ = run(script, build(tmp_path / "a.zip", files))

    assert failures == ["the README lacks these headings: ['Limitations']"]


def test_a_heading_matches_by_its_first_words_in_any_case(script: ModuleType) -> None:
    assert script.missing_headings("### HOW TO RUN and more\n## request schema\n") == [
        heading for heading in script.REQUIRED_HEADINGS if heading not in ("How to run", "Request schema")
    ]


def test_a_readme_may_be_named_in_any_case_but_must_exist(script: ModuleType, tmp_path: Path) -> None:
    renamed = {("README.md" if name == "readme.md" else name): text for name, text in good_files().items()}
    without = {name: text for name, text in good_files().items() if name != "readme.md"}

    assert run(script, build(tmp_path / "a.zip", renamed))[1] == []
    assert run(script, build(tmp_path / "b.zip", without))[1] == [
        "there is no README at the top of the archive"
    ]


def test_fewer_than_five_complete_examples_fail(script: ModuleType, tmp_path: Path) -> None:
    files = {name: text for name, text in good_files().items() if not name.startswith(DROPPED)}
    del files["docs/examples/03-example/request.json"]

    _, failures, _ = run(script, build(tmp_path / "a.zip", files))

    assert failures == ["2 example folders have a request.json and a response.json; need 5"]


def test_an_example_that_is_not_a_valid_response_fails(script: ModuleType, tmp_path: Path) -> None:
    broken: dict[str, Any] = time_series_response()
    del broken["meta"]
    files = {**good_files(), "docs/examples/02-example/response.json": json.dumps(broken)}

    _, failures, _ = run(script, build(tmp_path / "a.zip", files))

    assert len(failures) == 1
    assert failures[0].startswith(
        "1 of 5 examples do not validate against the response models (first: 02-example"
    )
    assert "run `make examples`" in failures[0]


def test_a_missing_lockfile_fails(script: ModuleType, tmp_path: Path) -> None:
    files = {name: text for name, text in good_files().items() if name != "frontend/pnpm-lock.yaml"}

    _, failures, _ = run(script, build(tmp_path / "a.zip", files))

    assert failures == ["missing lockfiles: ['frontend/pnpm-lock.yaml']"]


def test_the_owner_marker_is_a_warning_and_not_a_failure(script: ModuleType, tmp_path: Path) -> None:
    files = {**good_files(), "readme.md": good_files()["readme.md"] + "\n<!-- owner:confirm -->\n"}

    _, failures, warnings = run(script, build(tmp_path / "a.zip", files))

    assert failures == []
    assert len(warnings) == 1 and "<!-- owner:confirm -->" in warnings[0]


def test_a_named_owner_marker_is_reported_with_its_note(script: ModuleType, tmp_path: Path) -> None:
    readme = good_files()["readme.md"] + "\ntext <!-- owner:confirm: the caches -->\n"

    _, failures, warnings = run(script, build(tmp_path / "a.zip", {**good_files(), "readme.md": readme}))

    assert failures == []
    assert len(warnings) == 1 and "the caches" in warnings[0]


def test_two_top_level_folders_fail_at_once(script: ModuleType, tmp_path: Path) -> None:
    path = build(tmp_path / "a.zip", good_files())
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("stray.txt", "x")

    _, failures, _ = run(script, path)

    assert len(failures) == 1 and "one top-level folder" in failures[0]


def test_the_command_line_reports_and_sets_its_status(
    script: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    good = build(tmp_path / "good.zip", good_files())
    bad = build(tmp_path / "bad.zip", {**good_files(), ".env": "secret"})

    assert script.main([str(good)]) == 0
    assert script.main([str(bad)]) == 1
    assert script.main([str(tmp_path / "missing.zip")]) == 2
    output = capsys.readouterr()
    assert "ok    no .env file inside" in output.out and "FAIL  an environment file is inside" in output.out
