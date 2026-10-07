"""Parsing study records: trimmed real records, and the three ways a projection says "no value".

The fixtures are real records from ClinicalTrials.gov with the long text, results and most locations cut
away. `projected_records.json` holds records exactly as a `fields` projection returned them.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from ctviz.ctgov.study import Intervention, Location, StudyDate, parse_study

FIXTURES = Path(__file__).parent.parent / "fixtures"


def load(name: str) -> dict[str, Any]:
    record: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return record


def projected(label: str) -> dict[str, Any]:
    record: dict[str, Any] = load("projected_records.json")[label]
    return record


# --- real records ---------------------------------------------------------------------------------------


def test_an_industry_phase_3_trial_without_locations() -> None:
    study = parse_study(load("industry_phase3_no_locations.json"))

    assert (study.nct_id, study.overall_status, study.study_type) == (
        "NCT02578680",
        "COMPLETED",
        "INTERVENTIONAL",
    )
    assert study.phases == ("PHASE3",)
    assert study.start_date == StudyDate("2016-01-15", "ACTUAL")
    assert (study.enrollment_count, study.enrollment_type) == (616, "ACTUAL")
    assert (study.lead_sponsor_name, study.lead_sponsor_class) == ("Merck Sharp & Dohme LLC", "INDUSTRY")
    assert study.locations == ()
    assert [(item.index, item.name) for item in study.interventions][:3] == [
        (0, "Pembrolizumab 200 mg"),
        (1, "Cisplatin"),
        (2, "Carboplatin"),
    ]
    assert study.interventions[7].type == "DRUG"
    assert study.intervention_mesh_terms[0] == "pembrolizumab"
    assert (study.sex, study.std_ages) == ("ALL", ("ADULT", "OLDER_ADULT"))
    assert (study.allocation, study.masking, study.primary_purpose) == (
        "RANDOMIZED",
        "QUADRUPLE",
        "TREATMENT",
    )
    assert study.has_results is True


def test_an_observational_study_has_no_phases() -> None:
    study = parse_study(load("observational_no_phases.json"))

    assert (study.study_type, study.phases) == ("OBSERVATIONAL", ())
    assert study.locations == (Location(0, "China"), Location(1, "China"))
    assert (study.allocation, study.masking, study.primary_purpose) == (None, None, None)
    assert study.has_results is False


def test_a_record_from_1985_has_months_and_no_date_types() -> None:
    study = parse_study(load("old_record_1985.json"))

    assert study.start_date == StudyDate("1985-06", None)
    assert study.primary_completion_date is None
    assert study.completion_date == StudyDate("2004-07", "ACTUAL")
    assert study.first_post_date == StudyDate("2005-09-21", "ESTIMATED")
    assert (study.enrollment_count, study.enrollment_type) == (120, None)


def test_a_two_phase_trial_with_brand_names_in_other_names() -> None:
    study = parse_study(load("two_phase_14_arms.json"))

    assert study.phases == ("PHASE1", "PHASE2")
    assert [item.name for item in study.interventions] == [
        "Pembrolizumab",
        "Dabrafenib",
        "Trametinib",
        "Placebo",
    ]
    pembrolizumab = study.interventions[0]
    assert pembrolizumab.other_names == ("MK-3475", "KEYTRUDA®")
    assert len(pembrolizumab.arm_group_labels) == 13
    assert len(study.interventions[2].arm_group_labels) == 14  # trametinib is in all 14 arms but one
    assert study.conditions == ("Melanoma", "Solid Tumors")


def test_a_withheld_record_keeps_what_it_has_and_nothing_else() -> None:
    study = parse_study(load("withheld.json"))

    assert (study.nct_id, study.overall_status) == ("NCT01660113", "WITHHELD")
    assert study.lead_sponsor_name == "[Redacted]"
    assert study.lead_sponsor_class is None
    assert (study.study_type, study.phases, study.conditions) == (None, (), ())
    assert (study.interventions, study.locations, study.start_date) == ((), (), None)
    assert study.first_post_date == StudyDate("2012-08-08", "ESTIMATED")


# --- the three forms of "no value" ---------------------------------------------------------------------


def test_an_empty_module_is_no_value() -> None:
    study = parse_study(projected("design_module_empty"))

    assert study.phases == ()
    assert study.start_date == StudyDate("2022-04-23", None)


@pytest.mark.parametrize("label", ["locations_module_empty", "locations_module_absent"])
def test_an_empty_or_absent_module_holds_no_locations(label: str) -> None:
    assert parse_study(projected(label)).locations == ()


def test_a_list_holding_only_an_empty_object_is_no_value() -> None:
    study = parse_study(projected("location_item_empty"))

    assert study.raw["protocolSection"]["contactsLocationsModule"]["locations"] == [{}]
    assert study.locations == ()
    assert [item.name for item in study.interventions] == ["bevacizumab", "paclitaxel"]


def test_an_empty_item_is_left_out_of_a_list_and_the_others_keep_their_index() -> None:
    study = parse_study(projected("intervention_item_empty"))

    assert study.interventions == (
        Intervention(0, None, None, ("MK-3475", "KEYTRUDA®"), ()),
        Intervention(1, None, None, ("TAFINLAR®",), ()),
        Intervention(2, None, None, ("MEKINIST®",), ()),
    )

    middle = parse_study(
        {
            "protocolSection": {
                "identificationModule": {"nctId": "NCT00000001"},
                "armsInterventionsModule": {"interventions": [{"name": "A"}, {}, {"name": "C"}]},
            }
        }
    )
    assert [(item.index, item.name) for item in middle.interventions] == [(0, "A"), (2, "C")]
    assert middle.value_at("protocolSection.armsInterventionsModule.interventions[2].name") == "C"


def test_a_record_with_only_an_id_has_no_other_value() -> None:
    study = parse_study({"protocolSection": {"identificationModule": {"nctId": "NCT00000002"}}})

    assert study.nct_id == "NCT00000002"
    assert (study.brief_title, study.overall_status, study.start_date, study.has_results) == (None,) * 4
    assert (study.phases, study.conditions, study.interventions, study.locations, study.std_ages) == ((),) * 5


def test_values_of_the_wrong_shape_are_no_value() -> None:
    study = parse_study(
        {
            "protocolSection": {
                "identificationModule": {"nctId": "NCT00000003"},
                "statusModule": "unexpected",
                "designModule": {"phases": "PHASE2", "enrollmentInfo": {"count": True}},
                "conditionsModule": {"conditions": ["Asthma", {}, None, 3]},
            }
        }
    )

    assert (study.overall_status, study.phases, study.enrollment_count) == (None, (), None)
    assert study.conditions == ("Asthma",)


@pytest.mark.parametrize(
    "record",
    [{}, {"protocolSection": {}}, {"protocolSection": {"identificationModule": {}}}],
)
def test_a_record_without_an_nct_id_is_refused(record: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="no NCT ID"):
        parse_study(record)


# --- paths -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("protocolSection.statusModule.overallStatus", "COMPLETED"),
        ("protocolSection.designModule.phases[0]", "PHASE3"),
        ("protocolSection.designModule.phases", ["PHASE3"]),
        ("protocolSection.designModule.enrollmentInfo.count", 616),
        ("protocolSection.armsInterventionsModule.interventions[1].name", "Cisplatin"),
        ("protocolSection.armsInterventionsModule.interventions[1].armGroupLabels[0]", "Control"),
        ("hasResults", True),
        ("protocolSection.designModule.phases[1]", None),
        ("protocolSection.contactsLocationsModule.locations[0].country", None),
        ("protocolSection.designModule.studyType.unknown", None),
        ("protocolSection.designModule.enrollmentInfo[0]", None),
    ],
)
def test_a_path_resolves_in_the_apis_own_notation(path: str, expected: object) -> None:
    study = parse_study(load("industry_phase3_no_locations.json"))

    assert study.value_at(path) == expected


@pytest.mark.parametrize("path", ["", ".hasResults", "a..b", "a[x]", "a[0", "a b", "a.b[-1]"])
def test_a_malformed_path_is_refused(path: str) -> None:
    study = parse_study(load("industry_phase3_no_locations.json"))

    with pytest.raises(ValueError, match="Not a JSON path"):
        study.value_at(path)


def test_the_path_a_citation_quotes_resolves_in_a_projected_record() -> None:
    study = parse_study(projected("intervention_item_empty"))

    assert (
        study.value_at("protocolSection.armsInterventionsModule.interventions[1].otherNames[0]")
        == "TAFINLAR®"
    )
    assert study.value_at("protocolSection.armsInterventionsModule.interventions[3].otherNames[0]") is None


def test_a_study_is_hashable_and_compares_by_value() -> None:
    first, second = parse_study(load("withheld.json")), parse_study(load("withheld.json"))

    assert first == second
    assert len({first, second}) == 1
