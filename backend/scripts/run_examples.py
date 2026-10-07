"""Record the example runs: each request goes through the real service and its answer is written down.

Run from backend/:  uv run python scripts/run_examples.py [slug ...]

Needs the key and the network. For each example it writes `docs/examples/NN-slug/` with `request.json`,
`plan.json` (the canonical plan the service used) and `response.json`, exactly as the service produced
them. A run that is not a visualization, or that fails validation, is reported and not written, except
the clarification example, which is meant to be one.
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

import httpx2
from pydantic import TypeAdapter

from ctviz.api.app import create_app
from ctviz.contract.response import QueryResponse
from ctviz.settings import REPOSITORY_ROOT

DIRECTORY: Final = REPOSITORY_ROOT / "docs" / "examples"

# Slug, request body, and the kind of answer the example is there to show.
EXAMPLES: Final[tuple[tuple[str, dict[str, Any], str], ...]] = (
    (
        "01-assignment-request",
        {
            "query": "How has the number of trials for this drug changed over time?",
            "drug_name": "Pembrolizumab",
        },
        "visualization",
    ),
    (
        "02-compare-phases",
        {"query": "Compare phases for trials involving pembrolizumab vs nivolumab."},
        "visualization",
    ),
    (
        "03-recruiting-by-country",
        {"query": "Which countries have the most recruiting trials for lung cancer?"},
        "visualization",
    ),
    (
        "04-sponsor-drug-network",
        {"query": "Show a network of sponsors and drugs for Duchenne muscular dystrophy trials."},
        "visualization",
    ),
    (
        "05-drug-cooccurrence",
        {"query": "Which drugs frequently co-occur in combination studies with pembrolizumab?"},
        "visualization",
    ),
    (
        "06-enrollment-histogram",
        {"query": "What is the distribution of enrollment sizes for pembrolizumab trials?"},
        "visualization",
    ),
    (
        "07-enrollment-vs-duration",
        {"query": "Plot enrollment against duration for completed Duchenne muscular dystrophy trials."},
        "visualization",
    ),
    ("08-largest-trials", {"query": "List the 10 largest lung cancer trials."}, "visualization"),
    (
        "09-recruiting-count",
        {"query": "How many recruiting trials are there for Duchenne muscular dystrophy?"},
        "visualization",
    ),
    (
        "10-no-drug-named",
        {"query": "How has the number of trials for this drug changed over time?"},
        "clarification",
    ),
)


async def record(slugs: Sequence[str]) -> int:
    app = create_app()
    chosen = [example for example in EXAMPLES if not slugs or example[0] in slugs]
    failures = 0
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://service", timeout=120) as client:
            for slug, request, expected_kind in chosen:
                problem = await _record_one(client, slug, request, expected_kind)
                print(f"{'FAILED' if problem else 'wrote '} {slug}" + (f": {problem}" if problem else ""))
                failures += problem is not None
    return failures


async def _record_one(
    client: httpx2.AsyncClient, slug: str, request: dict[str, Any], kind: str
) -> str | None:
    reply = await client.post("/v1/query", json=request)
    if reply.status_code != 200:
        return f"HTTP {reply.status_code}: {reply.text[:200]}"
    body = reply.json()
    TypeAdapter(QueryResponse).validate_python(body)
    if body["kind"] != kind:
        return f"expected a {kind}, the service answered {body['kind']}: {body['message']}"
    _write(DIRECTORY / slug, request, body)
    return None


def _write(directory: Path, request: dict[str, Any], response: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, document in (
        ("request", request),
        ("plan", response["meta"]["plan"]),
        ("response", response),
    ):
        (directory / f"{name}.json").write_text(
            json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("slugs", nargs="*", help="only these examples (default: all)")
    return 1 if asyncio.run(record(parser.parse_args(argv).slugs)) else 0


if __name__ == "__main__":
    sys.exit(main())
