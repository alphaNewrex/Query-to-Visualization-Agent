"""What the service can do, rendered from the catalogue and the settings for `GET /v1/capabilities`."""

from collections.abc import Mapping
from typing import Final, get_args

from ctviz.catalog.fields import BoundDimension, FieldSpec
from ctviz.contract.plan import ChartType, NodeKind, NumericField
from ctviz.engine.lower import DEFAULT_TOP_N, MAX_LINKS, MAX_SERIES, MAX_TOP_N
from ctviz.settings import Settings

type Document = dict[str, object]

API_VERSION: Final = "v1"


def capabilities(catalog: Mapping[str, FieldSpec], settings: Settings) -> Document:
    return {
        "api_version": API_VERSION,
        "dimensions": [_dimension(spec) for spec in catalog.values()],
        "numeric_fields": list(get_args(NumericField)),
        "node_kinds": list(get_args(NodeKind)),
        "visualization_types": list(get_args(ChartType)),
        "limits": {
            "walk_cap": settings.walk_cap,
            "one_page_max": settings.one_page_max,
            "max_fanout_requests": settings.max_fanout_requests,
            "default_top_n": DEFAULT_TOP_N,
            "max_top_n": MAX_TOP_N,
            "max_series": MAX_SERIES,
            "max_links": MAX_LINKS,
            "request_deadline_s": settings.request_deadline_s,
        },
        "planner": {
            "is_available": settings.openai_api_key is not None,
            "model": settings.planner_model if settings.openai_api_key is not None else None,
        },
    }


def _dimension(spec: FieldSpec) -> Document:
    return {
        "key": spec.key,
        "title": spec.title,
        "kind": spec.kind,
        "is_exclusive": spec.is_exclusive,
        "buckets": _labels(spec),
    }


def _labels(spec: FieldSpec) -> list[str]:
    """The labels of a closed list; a date, whose periods depend on a window, and an open list have none."""
    if spec.buckets is None or spec.kind == "date":
        return []
    return [bucket.label for bucket in spec.buckets(BoundDimension(spec, None, "axis"), None)]
