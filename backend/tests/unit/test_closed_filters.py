"""Every closed dimension is also a filter, and the intervention model is a dimension."""

from ctviz.catalog.entries import ENTRIES
from ctviz.contract.plan import FAMILY_DIMENSIONS, FAMILY_FIELDS
from ctviz.contract.request import QueryRequest
from ctviz.ctgov.params import Params, Scope
from ctviz.ctgov.study import parse_study
from ctviz.planning.prompt import build_instructions
from tests.unit.plan_samples import plan, request
from tests.unit.test_check_plan import check, evidence


def scope(**enum_filters: tuple[str, ...]) -> Scope:
    return Scope(id="s0", label=None, terms=(), enum_filters=enum_filters, date_range=None)


def test_every_filter_family_is_a_catalogue_dimension_that_a_filter_can_select() -> None:
    for family, key in FAMILY_DIMENSIONS.items():
        spec = ENTRIES[key]
        values = tuple(bucket.key for bucket in spec.buckets(None, None) if bucket.key != "MISSING")  # type: ignore[arg-type]
        assert scope(**{key: values[:1]}).params().pairs(), family
        assert FAMILY_FIELDS[family] in QueryRequest.model_fields


def test_a_filter_is_the_same_expression_as_the_bucket_of_its_value() -> None:
    cases = {
        "allocation": ("RANDOMIZED",),
        "masking": ("DOUBLE",),
        "intervention_model": ("CROSSOVER",),
        "has_results": ("true",),
        "sex": ("ALL",),
    }
    for key, values in cases.items():
        bucket = next(b for b in ENTRIES[key].buckets(None, None) if b.key == values[0])  # type: ignore[arg-type]
        assert scope(**{key: values}).params() == Params(advanced=bucket.expr)


def test_several_values_of_a_filter_are_any_of() -> None:
    advanced = scope(intervention_model=("PARALLEL", "CROSSOVER")).params().advanced

    assert advanced == "AREA[DesignInterventionModel](PARALLEL OR CROSSOVER)"


def test_the_intervention_model_is_read_from_the_design_info() -> None:
    def record(model: str | None) -> dict[str, object]:
        design: dict[str, object] = {"designInfo": {"interventionModel": model}} if model else {}
        return {
            "protocolSection": {
                "identificationModule": {"nctId": "NCT00000001"},
                "designModule": design,
            }
        }

    spec = ENTRIES["intervention_model"]
    dimension = type("D", (), {})()
    found = spec.extract(parse_study(record("FACTORIAL")), None, dimension)  # type: ignore[arg-type]
    missing = spec.extract(parse_study(record(None)), None, dimension)  # type: ignore[arg-type]

    assert [(v.key, v.label) for v in found] == [("FACTORIAL", "Factorial Assignment")]
    assert found[0].evidence[0].path == "protocolSection.designModule.designInfo.interventionModel"
    assert [v.key for v in missing] == ["MISSING"]


def test_the_request_fields_of_the_new_families_win_over_the_model() -> None:
    model = plan(
        filters={"allocations": ["NON_RANDOMIZED"], "evidence": [evidence("allocations", "non-randomised")]}
    )
    result = check(
        model, request("Non-randomised pembrolizumab studies", allocation=["RANDOMIZED"], masking=["DOUBLE"])
    )

    assert result.plan.filters.allocations == ["RANDOMIZED"]
    assert result.plan.filters.maskings == ["DOUBLE"]
    assert result.blocking == ()


def test_a_new_family_without_a_quoted_phrase_is_sent_back() -> None:
    result = check(plan(filters={"maskings": ["DOUBLE"]}), request("Pembrolizumab trials by phase"))

    assert [(i.code, i.path) for i in result.blocking] == [("ungrounded_filter", "/filters/maskings")]


def test_the_instructions_list_the_codes_of_the_new_families() -> None:
    instructions = build_instructions()

    assert "intervention_models: PARALLEL" in instructions
    assert "allocations: RANDOMIZED" in instructions
    assert "intervention_model: Intervention model (category)" in instructions
