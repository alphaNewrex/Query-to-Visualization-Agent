"""Condition labels and the commonest-spelling rule they share with drug names."""

from dataclasses import replace

from ctviz.catalog.conditions import ConditionLabels, fold
from ctviz.catalog.fields import CATALOG, BoundDimension
from ctviz.catalog.spellings import commonest_spellings
from ctviz.ctgov.study import Study

from .catalog_samples import pembrolizumab


def _with_conditions(*spellings: str) -> list[Study]:
    return [replace(pembrolizumab()[0], conditions=(raw,)) for raw in spellings]


def test_fold_collapses_case_and_white_space_and_nothing_else() -> None:
    assert fold("  Non-Small   Cell Lung CANCER ") == "non-small cell lung cancer"
    assert fold("   ") == ""


def test_the_label_is_the_commonest_raw_spelling() -> None:
    studies = _with_conditions("Breast Cancer", "breast cancer", "Breast cancer", "Breast cancer")
    assert ConditionLabels.fit(studies).labels == {"breast cancer": "Breast cancer"}


def test_a_tie_goes_to_the_first_spelling_in_alphabetical_order_whatever_the_order_sent() -> None:
    spellings = ["Lung Cancer", "lung cancer"]
    assert commonest_spellings(("lung cancer", raw) for raw in spellings) == {"lung cancer": "Lung Cancer"}
    assert commonest_spellings(("lung cancer", raw) for raw in reversed(spellings)) == {
        "lung cancer": "Lung Cancer"
    }


def test_a_value_is_labelled_by_the_fitted_spelling_and_cites_its_own() -> None:
    spec = CATALOG["condition"]
    studies = _with_conditions("Breast cancer", "Breast cancer", "BREAST CANCER")
    context = spec.prepare(studies) if spec.prepare else None
    [value] = spec.extract(studies[2], context, BoundDimension(spec, None, "axis"))
    assert (value.key, value.label) == ("breast cancer", "Breast cancer")
    assert value.evidence[0].excerpt == "BREAST CANCER"


def test_without_a_fitted_set_a_condition_keeps_its_own_spelling() -> None:
    spec = CATALOG["condition"]
    [study] = _with_conditions("BREAST CANCER")
    [value] = spec.extract(study, None, BoundDimension(spec, None, "axis"))
    assert value.label == "BREAST CANCER"
