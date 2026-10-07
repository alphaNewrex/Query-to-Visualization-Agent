"""The README says only what the repository can back: its commands, links, variables and counts exist."""

import importlib.util
import re
from pathlib import Path
from typing import get_args

import pytest

from ctviz import docgen
from ctviz.catalog.fields import CATALOG
from ctviz.contract.plan import ChartType
from ctviz.settings import Settings
from ctviz.viz.text import CHART_RATIONALE

MARKER = "<!-- owner:confirm -->"


@pytest.fixture(scope="module")
def readme(real_repository_root: Path) -> str:
    path = docgen.find_readme(real_repository_root)
    assert path is not None
    return path.read_text(encoding="utf-8")


def anchors(markdown: str) -> set[str]:
    """The anchors GitHub gives the headings of a Markdown text."""
    headings = re.findall(r"^#+ (.+?)\s*$", markdown, re.MULTILINE)
    return {
        re.sub(r"[^\w -]", "", heading.lower().replace("`", "")).replace(" ", "-") for heading in headings
    }


def test_every_make_target_the_readme_names_exists(real_repository_root: Path, readme: str) -> None:
    makefile = (real_repository_root / "Makefile").read_text(encoding="utf-8")
    phony = re.search(r"^\.PHONY:(.*)$", makefile, re.MULTILINE)
    assert phony is not None
    declared = set(phony[1].split())
    defined = set(re.findall(r"^([a-z-]+):", makefile, re.MULTILINE))
    named = set(re.findall(r"(?:`|^ *)make ([a-z-]+)", readme, re.MULTILINE))

    assert declared == defined
    assert named and named <= declared


def test_every_relative_link_and_every_path_in_code_spans_exists(
    real_repository_root: Path, readme: str
) -> None:
    links = re.findall(r"\]\(([^)\s]+)\)", readme)
    paths = [link.split("#")[0] for link in links if not link.startswith(("http", "#", "mailto"))]
    spans = re.findall(r"`((?:backend|frontend|docs)/[A-Za-z0-9_./-]+)`", readme)

    missing = [path for path in [*paths, *spans] if not (real_repository_root / path).exists()]

    assert missing == []


def test_every_anchor_the_readme_links_to_is_a_heading(real_repository_root: Path, readme: str) -> None:
    schema_page = (real_repository_root / "docs" / "SCHEMA.md").read_text(encoding="utf-8")
    own = set(re.findall(r"\]\(#([^)\s]+)\)", readme))
    schema = set(re.findall(r"\]\(docs/SCHEMA\.md#([^)\s]+)\)", readme))

    assert own <= anchors(readme)
    assert schema <= anchors(schema_page)


def test_every_ctviz_variable_the_readme_names_is_a_setting(readme: str) -> None:
    settings = {f"CTVIZ_{name.upper()}" for name in Settings.model_fields} | {"CTVIZ_ENV_FILE"}

    assert set(re.findall(r"CTVIZ_[A-Z_]+", readme)) <= settings


def test_the_counts_the_readme_states_are_the_counts_in_the_code(
    real_repository_root: Path, readme: str
) -> None:
    source = real_repository_root / "backend" / "src" / "ctviz"
    tests = real_repository_root / "backend" / "tests" / "unit"
    invariants = (source / "contract" / "invariants.py").read_text(encoding="utf-8")
    invariant_tests = (tests / "test_invariants.py").read_text(encoding="utf-8")
    plan_tests = (tests / "test_check_plan.py").read_text(encoding="utf-8")
    plan_rules = {int(n) for n in re.findall(r"def test_rule_(\d+)", plan_tests)}
    plan_rules |= {int(n) for n in re.findall(r"def test_rules_\d+_and_(\d+)", plan_tests)}
    plan_rules |= {int(n) for n in re.findall(r"def test_rules_(\d+)_and_\d+", plan_tests)}
    rules = {int(n) for n in re.findall(r"\b_rule_(\d+)", invariants)}
    rules |= {int(n) for n in re.findall(r"\b_rule_\d+_and_(\d+)", invariants)}
    rules |= {
        n for a, b in re.findall(r"\b_rule_(\d+)_to_(\d+)", invariants) for n in range(int(a), int(b) + 1)
    }
    tested = {int(n) for n in re.findall(r"def test_rule_(\d+)", invariant_tests)}

    assert plan_rules == set(range(1, max(plan_rules) + 1))
    assert rules == set(range(1, max(rules) + 1))
    assert set(re.findall(r"(\d+) dimensions", readme)) == {str(len(CATALOG))}
    assert set(re.findall(r"(\d+) plan rules", readme)) == {str(len(plan_rules))}
    assert set(re.findall(r"(\d+) invariants", readme)) == {str(len(rules))}
    assert set(re.findall(r"(\d+) of the (\d+) invariants", readme)) == {(str(len(tested)), str(len(rules)))}
    assert set(re.findall(r"table of (\d+) rules", readme)) == {str(len(CHART_RATIONALE))}
    assert len(get_args(ChartType)) == 7 and "seven visualization types" in readme


def test_the_owner_marker_stands_alone_and_a_request_to_rewrite_follows_it(readme: str) -> None:
    lines = readme.splitlines()

    assert lines.count(MARKER) == 1
    after = "\n".join(lines[lines.index(MARKER) + 1 : lines.index(MARKER) + 6])
    assert "in your own words" in after and "before submitting" in after


def test_the_required_headings_are_there(real_repository_root: Path, readme: str) -> None:
    path = real_repository_root / "backend" / "scripts" / "check_submission.py"
    spec = importlib.util.spec_from_file_location("check_submission", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.missing_headings(readme) == []


def test_no_secret_and_no_emoji_in_the_readme(readme: str) -> None:
    assert not re.search(r"sk-[A-Za-z0-9_-]{16,}", readme)
    assert not re.search("[\U0001f300-\U0001faff☀-➿]", readme)
