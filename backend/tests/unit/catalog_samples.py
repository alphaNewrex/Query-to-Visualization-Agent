"""Real trimmed records shared by the catalogue tests.

`pembrolizumab_records.json` is a sample of 95 studies of `query.intr=pembrolizumab`, with the pieces the
catalogue projects and at most 12 sites each. It holds all eight phase arrays that occur in the registry
and trials with no phase, six partial start dates, five trials with no start date, and seven trials with
sites in several countries. `withheld.json` is one of the 983 withheld studies. `duchenne_records.json`
is all 499 trials of `query.cond=Duchenne muscular dystrophy`, cut down to the pieces that phases,
intervention types, sponsors and drugs read.
"""

import json
from functools import cache
from pathlib import Path
from typing import Any

from ctviz.catalog.fields import CATALOG, BoundDimension
from ctviz.contract.plan import TimeUnit
from ctviz.ctgov.study import Study, parse_study

FIXTURES = Path(__file__).parent.parent / "fixtures"


@cache
def pembrolizumab() -> tuple[Study, ...]:
    records: list[dict[str, Any]] = json.loads((FIXTURES / "pembrolizumab_records.json").read_text("utf-8"))
    return tuple(parse_study(record) for record in records)


def pembrolizumab_study(nct_id: str) -> Study:
    return next(study for study in pembrolizumab() if study.nct_id == nct_id)


@cache
def duchenne() -> tuple[Study, ...]:
    records: list[dict[str, Any]] = json.loads((FIXTURES / "duchenne_records.json").read_text("utf-8"))
    return tuple(parse_study(record) for record in records)


@cache
def withheld() -> Study:
    return parse_study(json.loads((FIXTURES / "withheld.json").read_text("utf-8")))


def dimension(key: str, unit: TimeUnit | None = None) -> BoundDimension:
    return BoundDimension(CATALOG[key], unit, "axis")
