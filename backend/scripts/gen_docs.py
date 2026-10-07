"""Regenerate the JSON Schema files under docs/schema/ from the Pydantic models.

Run from backend/:  uv run python scripts/gen_docs.py [--check]

With --check nothing is written: the files are regenerated in memory and the exit status is 1
when any committed file is missing or differs, so a stale schema fails the build.
"""

import argparse
import sys
from collections.abc import Sequence

from ctviz.docgen import SCHEMA_DIRECTORY, stale_schema_files, write_schema_files


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--check", action="store_true", help="fail if a committed file is out of date")
    arguments = parser.parse_args(argv)

    if arguments.check:
        stale = stale_schema_files()
        for name in stale:
            print(f"stale: {SCHEMA_DIRECTORY / name}", file=sys.stderr)
        return 1 if stale else 0

    for path in write_schema_files():
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
