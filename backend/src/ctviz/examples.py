"""The recorded runs under `docs/examples`: a folder each, holding a request, a plan and a response."""

import json
import re
from pathlib import Path
from typing import Final

from ctviz.settings import REPOSITORY_ROOT, Settings

_SLUG: Final = re.compile(r"^\d{2}-[a-z0-9-]+$")
_PARTS: Final = ("request", "plan", "response")

type Document = dict[str, object]


def examples_directory(settings: Settings) -> Path:
    return settings.examples_dir or REPOSITORY_ROOT / "docs" / "examples"


def slugs(directory: Path) -> list[str]:
    """The slugs of the examples that are complete, in order."""
    if not directory.is_dir():
        return []
    return sorted(
        path.name
        for path in directory.iterdir()
        if _SLUG.match(path.name) and all((path / f"{part}.json").is_file() for part in _PARTS)
    )


def load_example(directory: Path, slug: str) -> Document | None:
    """One example, or None for an unknown slug, so that no path is ever built from the input."""
    if slug not in slugs(directory):
        return None
    return {"slug": slug, **{part: _read(directory / slug / f"{part}.json") for part in _PARTS}}


def summaries(directory: Path) -> list[Document]:
    """What the gallery needs to list each example, without the responses."""
    listing: list[Document] = []
    for slug in slugs(directory):
        request = _read(directory / slug / "request.json")
        response = _read(directory / slug / "response.json")
        visualization = response.get("visualization")
        listing.append(
            {
                "slug": slug,
                "query": request.get("query"),
                "kind": response.get("kind"),
                "visualization_type": visualization.get("type") if isinstance(visualization, dict) else None,
            }
        )
    return listing


def _read(path: Path) -> Document:
    document: Document = json.loads(path.read_text(encoding="utf-8"))
    return document
