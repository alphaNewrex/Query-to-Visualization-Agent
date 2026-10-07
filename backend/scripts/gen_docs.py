"""Regenerate the documents that are written from the code.

Run from backend/:  uv run python scripts/gen_docs.py [--check]

Writes docs/schema/*.json (the JSON Schemas and the OpenAPI document), docs/SCHEMA.md, docs/examples/README.md
and the generated blocks of the README (the text between its `<!-- gen:NAME:start -->` and `end` lines).
With --check nothing is written: the documents are regenerated in memory and the exit status is 1 when
any committed one is missing or differs, so a stale document fails the build.
"""

import argparse
import sys
from collections.abc import Sequence

from ctviz.docgen import DocumentError, stale_documents, write_documents
from ctviz.settings import REPOSITORY_ROOT


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--check", action="store_true", help="fail if a committed document is out of date")
    arguments = parser.parse_args(argv)

    try:
        if arguments.check:
            stale = stale_documents()
            for path in stale:
                print(f"stale: {path.relative_to(REPOSITORY_ROOT)}", file=sys.stderr)
            if stale:
                print("run `make docs` to regenerate", file=sys.stderr)
            return 1 if stale else 0

        for path in write_documents():
            print(f"wrote {path.relative_to(REPOSITORY_ROOT)}")
    except DocumentError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
