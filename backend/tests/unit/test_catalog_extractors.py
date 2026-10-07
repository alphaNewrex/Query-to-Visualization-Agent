"""The catalogue entries and their extractors, on real trimmed records."""

from collections import Counter
from typing import get_args

import pytest

from ctviz.catalog.drugs import DRUG_TYPES
from ctviz.catalog.fields import CATALOG, Window
from ctviz.catalog.vocab import ENROLLMENT_BINS
from ctviz.contract.plan import DimensionKey, TimeUnit
from ctviz.ctgov.study import Study

from .catalog_samples import dimension, pembrolizumab, pembrolizumab_study, withheld


def values(key: str, study: Study, unit: TimeUnit | None = None) -> list[tuple[str, str]]:
    spec = CATALOG[key]
    context = spec.prepare(pembrolizumab()) if spec.prepare else None
    found = spec.extract(study, context, dimension(key, unit))
    return [(value.key, value.label) for value in found]


def test_the_catalogue_keys_are_the_plan_vocabulary_in_order() -> None:
    assert list(CATALOG) == list(get_args(DimensionKey))
    assert all(spec.key == key for key, spec in CATALOG.items())


def test_exclusive_dimensions_with_a_closed_list_say_where_a_missing_value_goes() -> None:
    for key in ("study_type", "sponsor_class", "sex", "allocation", "masking", "primary_purpose"):
        assert CATALOG[key].missing is not None
        assert CATALOG[key].missing.expr.endswith("MISSING")  # type: ignore[union-attr]
    assert CATALOG["overall_status"].missing is None
    assert CATALOG["phase"].missing is None  # "No phase listed" is one of the nine buckets


def test_a_not_provided_bucket_is_listed_with_the_others_so_that_a_fan_out_counts_it() -> None:
    for key in ("study_type", "sponsor_class", "sex", "allocation", "masking", "primary_purpose"):
        spec = CATALOG[key]
        assert spec.buckets is not None
        assert spec.buckets(dimension(key), None)[-1] == spec.missing
    assert [b.key for b in CATALOG["study_type"].buckets(dimension("study_type"), None)] == [  # type: ignore[misc]
        "INTERVENTIONAL",
        "OBSERVATIONAL",
        "EXPANDED_ACCESS",
        "MISSING",
    ]


@pytest.mark.parametrize(
    "key", [key for key, spec in CATALOG.items() if spec.kind != "date" and spec.buckets]
)
def test_every_value_of_a_closed_dimension_is_one_of_its_buckets(key: str) -> None:
    spec = CATALOG[key]
    buckets = {bucket.key for bucket in spec.buckets(dimension(key), None)}  # type: ignore[misc]
    for study in (*pembrolizumab(), withheld()):
        assert {value.key for value in spec.extract(study, None, dimension(key))} <= buckets


@pytest.mark.parametrize(
    "key", ["phase", "overall_status", "study_type", "sponsor_class", "sex", "allocation", "masking"]
)
def test_an_exclusive_closed_dimension_gives_every_trial_exactly_one_bucket(key: str) -> None:
    for study in (*pembrolizumab(), withheld()):
        assert len(CATALOG[key].extract(study, None, dimension(key))) == 1


# --- phase ----------------------------------------------------------------------------------------------


def test_the_nine_phase_buckets_partition_the_trials() -> None:
    tally = Counter(key for study in pembrolizumab() for key, _ in values("phase", study))
    assert sum(tally.values()) == len(pembrolizumab())
    assert tally.keys() <= {bucket.key for bucket in CATALOG["phase"].buckets(dimension("phase"), None)}  # type: ignore[misc]
    assert {
        "EARLY_PHASE1",
        "PHASE1",
        "PHASE1_PHASE2",
        "PHASE2",
        "PHASE2_PHASE3",
        "PHASE3",
        "PHASE4",
        "NA",
        "NONE",
    } == set(tally)


def test_a_trial_with_two_phases_is_one_combined_bucket() -> None:
    study = next(s for s in pembrolizumab() if s.phases == ("PHASE1", "PHASE2"))
    found = CATALOG["phase"].extract(study, None, dimension("phase"))
    assert [(v.key, v.label) for v in found] == [("PHASE1_PHASE2", "Phase 1/Phase 2")]
    assert [e.path for e in found[0].evidence] == [
        "protocolSection.designModule.phases[0]",
        "protocolSection.designModule.phases[1]",
    ]


def test_a_trial_with_no_phase_cites_the_absence() -> None:
    study = pembrolizumab_study("NCT04194190")
    [value] = CATALOG["phase"].extract(study, None, dimension("phase"))
    assert (value.key, value.label) == ("NONE", "No phase listed")
    assert value.evidence[0].excerpt is None
    assert study.value_at(value.evidence[0].path) is None


def test_an_unexpected_phase_array_goes_to_its_own_bucket() -> None:
    study = next(s for s in pembrolizumab() if s.phases == ("PHASE1", "PHASE2"))
    odd = Study(**{**study.__dict__, "phases": ("PHASE1", "PHASE4")})
    assert values("phase", odd) == [("OTHER_COMBINATION", "Other combination")]


# --- dates ----------------------------------------------------------------------------------------------


def test_a_partial_start_date_is_placed_by_its_first_day() -> None:
    study = pembrolizumab_study("NCT05566223")  # starts "2023-02", estimated
    assert values("start_date", study) == [("2023", "2023")]
    assert values("start_date", study, "quarter") == [("2023-Q1", "2023-Q1")]
    assert values("start_date", study, "month") == [("2023-02", "2023-02")]


def test_a_full_start_date_cites_its_text() -> None:
    study = pembrolizumab_study("NCT05008224")
    [value] = CATALOG["start_date"].extract(study, None, dimension("start_date", "quarter"))
    assert value.key == "2021-Q4"
    assert value.evidence[0].path == "protocolSection.statusModule.startDateStruct.date"
    assert value.evidence[0].excerpt == "2021-10-07" == study.value_at(value.evidence[0].path)


def test_a_trial_without_a_start_date_has_no_period() -> None:
    assert values("start_date", pembrolizumab_study("NCT04194190")) == []
    assert values("start_date", withheld()) == []


def test_the_other_date_fields_read_their_own_structs() -> None:
    study = pembrolizumab_study("NCT06358573")
    assert values("primary_completion_date", study, "year") == [("2028", "2028")]
    assert values("completion_date", study, "month") == [("2031-12", "2031-12")]
    assert values("first_posted_date", study, "year") == [("2024", "2024")]


def test_date_buckets_are_ranges_of_the_window() -> None:
    buckets = CATALOG["start_date"].buckets(
        dimension("start_date", "quarter"), Window("quarter", "2022-Q1", "2022-Q3")
    )  # type: ignore[misc]
    assert [(b.key, b.expr) for b in buckets] == [
        ("2022-Q1", "AREA[StartDate]RANGE[2022-01-01,2022-03-31]"),
        ("2022-Q2", "AREA[StartDate]RANGE[2022-04-01,2022-06-30]"),
        ("2022-Q3", "AREA[StartDate]RANGE[2022-07-01,2022-09-30]"),
    ]
    with pytest.raises(ValueError, match="window"):
        CATALOG["start_date"].buckets(dimension("start_date"), None)  # type: ignore[misc]


def test_dates_that_can_be_missing_have_a_presence_test_and_first_posted_does_not() -> None:
    assert CATALOG["start_date"].presence == "NOT (AREA[StartDate]MISSING)"
    assert CATALOG["first_posted_date"].presence is None
    assert CATALOG["first_posted_date"].pieces == ("StudyFirstPostDate",)


# --- countries ------------------------------------------------------------------------------------------


def test_a_study_with_many_sites_in_two_countries_counts_each_once() -> None:
    study = pembrolizumab_study("NCT06358573")
    assert [c.country for c in study.locations].count("Switzerland") > 1
    found = CATALOG["country"].extract(study, None, dimension("country"))
    assert [v.key for v in found] == ["France", "Switzerland"]
    assert found[1].evidence[0].path == "protocolSection.contactsLocationsModule.locations[1].country"


def test_a_study_with_sites_in_one_country_is_that_country_once() -> None:
    [value] = CATALOG["country"].extract(pembrolizumab_study("NCT05916261"), None, dimension("country"))
    assert value.key == "China"


def test_a_study_without_locations_has_no_country() -> None:
    assert values("country", pembrolizumab_study("NCT04194190")) == []
    assert values("country", withheld()) == []


def test_a_country_bucket_is_a_quoted_phrase_and_only_for_registry_names() -> None:
    bucket_for = CATALOG["country"].bucket_for
    assert bucket_for is not None
    found = bucket_for("United States")
    assert found is not None
    assert found.expr == 'AREA[LocationCountry]"United States"'
    assert bucket_for("Atlantis") is None


# --- closed dimensions, sponsors, conditions, enrollment ----------------------------------------------


def test_a_withheld_study_falls_in_the_not_provided_buckets_and_has_no_sponsor() -> None:
    study = withheld()
    assert values("sponsor", study) == []
    assert values("study_type", study) == [("MISSING", "Not provided")]
    assert values("sponsor_class", study) == [("MISSING", "Not provided")]
    assert values("masking", study) == [("MISSING", "Not provided")]
    assert values("phase", study) == [("NONE", "No phase listed")]
    assert values("overall_status", study) == [("WITHHELD", "Withheld")]
    assert values("intervention_type", study) == []
    assert values("condition", study) == []
    assert values("enrollment", study) == []


def test_single_valued_dimensions_on_a_real_record() -> None:
    study = pembrolizumab_study("NCT05008224")
    assert values("overall_status", study) == [("COMPLETED", "Completed")]
    assert values("study_type", study) == [("INTERVENTIONAL", "Interventional")]
    assert values("sponsor_class", study)[0][0] in {"INDUSTRY", "OTHER", "NIH", "NETWORK"}
    assert values("sponsor", study) == [(study.lead_sponsor_name, study.lead_sponsor_name)]


def test_has_results_reads_the_top_level_flag() -> None:
    tally = Counter(key for study in pembrolizumab() for key, _ in values("has_results", study))
    assert set(tally) <= {"true", "false"}
    assert sum(tally.values()) == len(pembrolizumab())


def test_intervention_types_are_distinct_per_trial() -> None:
    for study in pembrolizumab():
        keys = [key for key, _ in values("intervention_type", study)]
        assert keys == list(dict.fromkeys(i.type for i in study.interventions if i.type))


def test_conditions_are_case_folded_and_keep_the_raw_label() -> None:
    study = next(s for s in pembrolizumab() if s.conditions)
    found = CATALOG["condition"].extract(study, None, dimension("condition"))
    assert found[0].key == " ".join(study.conditions[0].casefold().split())
    assert found[0].label == study.conditions[0]


def test_enrollment_falls_in_the_bin_that_holds_it() -> None:
    for study in pembrolizumab():
        found = values("enrollment", study)
        if study.enrollment_count is None:
            assert found == []
        else:
            [(key, _)] = found
            assert key == next(b.label for b in ENROLLMENT_BINS if b.holds(study.enrollment_count))


def test_a_walk_over_drugs_reads_only_trials_with_a_drug_type_intervention() -> None:
    expected = "AREA[InterventionType](DRUG OR BIOLOGICAL OR GENETIC OR COMBINATION_PRODUCT)"
    assert CATALOG["drug"].presence == expected
    for study in (*pembrolizumab(), withheld()):
        if not any(item.type in DRUG_TYPES for item in study.interventions):
            assert values("drug", study) == []


def test_the_drug_dimension_needs_its_fitted_context() -> None:
    study = pembrolizumab_study("NCT05916261")
    with pytest.raises(TypeError, match="DrugNormalizer"):
        CATALOG["drug"].extract(study, None, dimension("drug"))
    assert values("drug", study) == [
        ("personalized neoantigen tumor vaccine", "Personalized neoantigen tumor vaccine")
    ]


# --- evidence -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("key", list(CATALOG))
def test_every_value_cites_a_path_that_holds_its_excerpt(key: str) -> None:
    """The citation of a value must be checkable against the record it came from."""
    spec = CATALOG[key]
    context = spec.prepare(pembrolizumab()) if spec.prepare else None
    for study in (*pembrolizumab(), withheld()):
        for value in spec.extract(study, context, dimension(key, "month")):
            assert value.evidence
            for evidence in value.evidence:
                found = study.value_at(evidence.path)
                if evidence.excerpt is None:
                    assert found is None
                else:
                    assert str(found).lower() == evidence.excerpt.lower()
