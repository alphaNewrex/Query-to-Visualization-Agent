"""Check a submission archive before it is sent.

Run from backend/:  uv run python scripts/check_submission.py ../dist/submission.zip

`make zip` builds the archive with `git archive` and runs this. It fails (exit status 1) when the archive
holds a `.env` file (a template such as `.env.example` may ship), when the README lacks a required
heading, when fewer than five examples hold a valid `request.json` and `response.json`, or when a
lockfile is missing. It only warns while the README still holds the `<!-- owner:confirm -->` marker,
which says that its "How this was built" section is a draft, or a `<!-- owner:confirm: a note -->` that
marks a sentence for the owner to check.
"""

import argparse
import json
import re
import sys
import zipfile
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Final

from pydantic import TypeAdapter, ValidationError

from ctviz.contract.response import QueryResponse

# A heading counts when its text starts with one of these, in any case.
REQUIRED_HEADINGS: Final = (
    "How to run",
    "Request schema",
    "Response schema",
    "Example runs",
    "Key design decisions",
    "How correctness was validated",
    "Limitations",
    "How this was built",
)
OWNER_MARKER: Final = "<!-- owner:confirm -->"
# `<!-- owner:confirm: a note -->` marks one sentence that the owner is to check; the plain marker above
# says that the whole "How this was built" section is still a draft.
_OWNER_NOTE: Final = re.compile(r"<!--\s*owner:confirm:\s*(.+?)\s*-->")
MIN_EXAMPLES: Final = 5
LOCKFILES: Final = ("backend/uv.lock", "frontend/pnpm-lock.yaml")

_TEMPLATE_SUFFIXES: Final = frozenset({"example", "sample", "template"})
_HEADING: Final = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)
_EXAMPLE: Final = re.compile(r"^docs/examples/(?P<slug>[^/]+)/(?P<name>request|response)\.json$")


def _is_env_file(name: str) -> bool:
    """`.env` and `.env.local` hold secrets; `.env.example` and its like are templates and may ship."""
    return name == ".env" or (name.startswith(".env.") and name.rsplit(".", 1)[1] not in _TEMPLATE_SUFFIXES)


def missing_headings(readme: str) -> list[str]:
    """The required headings that no heading of `readme` starts with."""
    found = [match[1].strip().casefold() for match in _HEADING.finditer(readme)]
    return [
        wanted
        for wanted in REQUIRED_HEADINGS
        if not any(text.startswith(wanted.casefold()) for text in found)
    ]


def inspect(archive: zipfile.ZipFile) -> tuple[list[str], list[str], list[str]]:
    """The passed checks, the failures and the warnings, each as lines of text."""
    passed: list[str] = []
    failures: list[str] = []
    warnings: list[str] = []

    names = [name for name in archive.namelist() if not name.endswith("/")]
    tops = {PurePosixPath(name).parts[0] for name in names}
    if len(tops) != 1:
        failures.append(f"the archive must hold one top-level folder, it holds {sorted(tops)}")
        return passed, failures, warnings
    prefix = f"{tops.pop()}/"
    files = {name.removeprefix(prefix) for name in names}
    passed.append(f"one top-level folder, {prefix}")

    secrets = sorted(name for name in files if _is_env_file(PurePosixPath(name).name))
    (failures if secrets else passed).append(
        f"an environment file is inside: {secrets}" if secrets else "no .env file inside"
    )

    _check_readme(archive, prefix, files, passed, failures, warnings)
    _check_examples(archive, prefix, files, passed, failures)

    absent = [lock for lock in LOCKFILES if lock not in files]
    (failures if absent else passed).append(
        f"missing lockfiles: {absent}" if absent else "both lockfiles present"
    )
    return passed, failures, warnings


def _check_readme(
    archive: zipfile.ZipFile,
    prefix: str,
    files: set[str],
    passed: list[str],
    failures: list[str],
    warnings: list[str],
) -> None:
    readme = next(
        (name for name in sorted(files) if "/" not in name and name.casefold() == "readme.md"), None
    )
    if readme is None:
        failures.append("there is no README at the top of the archive")
        return
    text = archive.read(prefix + readme).decode("utf-8")
    missing = missing_headings(text)
    if missing:
        failures.append(f"the README lacks these headings: {missing}")
    else:
        passed.append(f"{readme} has its {len(REQUIRED_HEADINGS)} required headings")
    if OWNER_MARKER in text:
        warnings.append(
            f"{readme} still holds {OWNER_MARKER}: rewrite 'How this was built' and delete the line"
        )
    warnings.extend(f"{readme} holds a note for the owner: {note}" for note in _OWNER_NOTE.findall(text))


def _check_examples(
    archive: zipfile.ZipFile, prefix: str, files: set[str], passed: list[str], failures: list[str]
) -> None:
    parts: dict[str, set[str]] = {}
    for name in files:
        if match := _EXAMPLE.match(name):
            parts.setdefault(match["slug"], set()).add(match["name"])
    complete = sorted(slug for slug, found in parts.items() if found == {"request", "response"})
    problems = [problem for slug in complete if (problem := _first_problem(archive, prefix, slug))]
    if len(complete) < MIN_EXAMPLES:
        failures.append(
            f"{len(complete)} example folders have a request.json and a response.json; need {MIN_EXAMPLES}"
        )
    elif problems:
        failures.append(
            f"{len(problems)} of {len(complete)} examples do not validate against the response models "
            f"(first: {problems[0]}). If they were recorded before the contract changed, run `make examples`."
        )
    else:
        passed.append(f"{len(complete)} examples, each with a valid request.json and response.json")


def _first_problem(archive: zipfile.ZipFile, prefix: str, slug: str) -> str | None:
    """Why an example is not a request and its answer, or None when it is one."""
    try:
        json.loads(archive.read(f"{prefix}docs/examples/{slug}/request.json"))
        TypeAdapter(QueryResponse).validate_json(archive.read(f"{prefix}docs/examples/{slug}/response.json"))
    except ValidationError as error:
        first = error.errors()[0]
        return f"{slug} at {'.'.join(str(part) for part in first['loc'])}: {first['msg'].lower()}"
    except ValueError as error:
        return f"{slug}: {error}"
    return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("archive", type=Path, help="the zip file to check")
    arguments = parser.parse_args(argv)

    if not arguments.archive.is_file():
        print(f"error: {arguments.archive} does not exist", file=sys.stderr)
        return 2
    with zipfile.ZipFile(arguments.archive) as archive:
        passed, failures, warnings = inspect(archive)
    for line in passed:
        print(f"ok    {line}")
    for line in warnings:
        print(f"warn  {line}")
    for line in failures:
        print(f"FAIL  {line}")
    print(f"{arguments.archive}: {len(failures)} failed, {len(warnings)} warnings")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
