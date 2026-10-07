"""A statistic of a numeric field: its name in a row, its channel, and its value for the numbers of a cell.

The model only chooses the statistic and the field from closed lists; every number here is computed from
the trial records the registry returned.
"""

import statistics
from collections.abc import Sequence

from ctviz.contract.response import NumberFormat, QuantitativeChannel
from ctviz.engine.lower import MeasureSpec
from ctviz.viz import text

# A median of whole numbers can end in .5, so a rounded value keeps one decimal; a sum is a whole number.
_DECIMALS = 1


def row_key(measure: MeasureSpec) -> str:
    """The row key of the value, e.g. `median_duration_months`."""
    return f"{measure.statistic}_{measure.field}"


def number_format(measure: MeasureSpec) -> NumberFormat:
    return ",d" if measure.statistic == "sum" else ".1f"


def channel(measure: MeasureSpec) -> QuantitativeChannel:
    return QuantitativeChannel(
        field=row_key(measure),
        type="quantitative",
        title=text.measure_label(measure.statistic, measure.field),
        unit=text.NUMBER_UNITS[measure.field],
        format=number_format(measure),
        scale="linear",
    )


def value_of(values: Sequence[float], measure: MeasureSpec) -> float | int | None:
    """The statistic of the values of a cell; None for a cell that holds no trial."""
    if not values:
        return None
    if measure.statistic == "sum":
        return round(sum(values))
    found = statistics.median(values) if measure.statistic == "median" else statistics.fmean(values)
    return round(found, _DECIMALS)
