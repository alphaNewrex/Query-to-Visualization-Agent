"""The drug normaliser on real intervention names."""

import pytest

from ctviz.catalog.drugs import DrugNormalizer, is_arm_label, is_noise, normalise, split_combination
from ctviz.catalog.fields import Value
from ctviz.ctgov.study import Intervention

from .catalog_samples import pembrolizumab


@pytest.mark.parametrize(
    ("raw", "normalised"),
    [
        ("Pembrolizumab (KEYTRUDA®)", "pembrolizumab"),
        ("Pembrolizumab 200 mg", "pembrolizumab"),
        ("Pembrolizumab 200 mg IV every 3 weeks", "pembrolizumab every 3 weeks"),
        ("Pembrolizumab + Cisplatin/Carboplatin + 5-FU", "pembrolizumab + cisplatin/carboplatin + 5-fu"),
        ("NS-065/NCNP-01", "ns-065/ncnp-01"),
        ("Eribulin Mesylate", "eribulin mesylate"),
        ("Arm B: RPTR-147:1 and Pembrolizumab", "rptr-147 1 and pembrolizumab"),
        ("Pembrolizumab and high dose interferon alfa-2b (HDI)", "pembrolizumab and interferon alfa-2b"),
        ("lenvatinib", "lenvatinib"),
        ("Dexamethasone 0.9% injection", "dexamethasone"),
        # A leading sign or configuration is part of the name: these are different compounds.
        ("(+)-Epicatechin", "pos-epicatechin"),
        ("(\u2212)-Epicatechin", "neg-epicatechin"),  # the Unicode minus sign
        ("(-)-epicatechin 100 mg", "neg-epicatechin"),
        ("(\u00b1)-Epicatechin", "rac-epicatechin"),
        ("(S)-Ketamine", "s-ketamine"),
        ("( R )-ketamine", "r-ketamine"),
        # A bracket that is not a descriptor is still an aside.
        ("Epicatechin (+)", "epicatechin"),
        ("(Keytruda) pembrolizumab", "pembrolizumab"),
    ],
)
def test_normalise(raw: str, normalised: str) -> None:
    assert normalise(raw) == normalised


@pytest.mark.parametrize(
    "raw",
    ["Placebo", "Placebo to pembrolizumab", "Saline", "Standard of care", "Chemotherapy", "Immunotherapy",
     "Carbon ion radiotherapy", "TPC combined with Tislelizumab", "Best supportive care"],
)  # fmt: skip
def test_noise_is_found_anywhere_in_the_raw_name(raw: str) -> None:
    assert is_noise(raw)


@pytest.mark.parametrize("raw", ["Pembrolizumab", "Cetuximab", "Eribulin Mesylate", "Platinum-Based Drug"])
def test_real_drugs_are_not_noise(raw: str) -> None:
    assert not is_noise(raw)


def test_a_combination_is_split_only_when_every_part_is_a_known_drug() -> None:
    known = frozenset({"pembrolizumab", "cisplatin", "carboplatin", "5-fu"})
    combination = "pembrolizumab + cisplatin/carboplatin + 5-fu"
    assert split_combination(combination, known) == ["pembrolizumab", "cisplatin", "carboplatin", "5-fu"]
    assert split_combination(combination, known - {"5-fu"}) == [combination]


def test_a_code_name_with_a_slash_stays_one_drug() -> None:
    assert split_combination("ns-065/ncnp-01", frozenset({"ns-065"})) == ["ns-065/ncnp-01"]


def test_or_is_never_a_separator() -> None:
    known = frozenset({"pembrolizumab", "nivolumab"})
    assert split_combination("pembrolizumab or nivolumab", known) == ["pembrolizumab or nivolumab"]


def _keys(values: list[Value]) -> list[str]:
    return [value.key for value in values]


@pytest.fixture(scope="module")
def normalizer() -> DrugNormalizer:
    return DrugNormalizer.fit(pembrolizumab())


def test_the_label_is_the_commonest_raw_spelling(normalizer: DrugNormalizer) -> None:
    assert normalizer.labels["pembrolizumab"] == "Pembrolizumab"
    assert "chemotherapy" not in normalizer.known


def test_a_combination_string_of_known_drugs_becomes_two_drugs(normalizer: DrugNormalizer) -> None:
    study = next(
        s
        for s in pembrolizumab()
        if any(i.name == "Arm B: RPTR-147:1 and Pembrolizumab" for i in s.interventions)
    )
    values = normalizer.values(study)
    assert {"rptr-147 1", "pembrolizumab"} <= set(_keys(values))
    pembro = next(v for v in values if v.key == "pembrolizumab")
    assert pembro.evidence[0].excerpt is not None
    assert study.value_at(pembro.evidence[0].path) == pembro.evidence[0].excerpt


def test_a_combination_with_an_unknown_part_is_kept_whole(normalizer: DrugNormalizer) -> None:
    study = next(
        s
        for s in pembrolizumab()
        if any(i.name == "Pembrolizumab and high dose interferon alfa-2b (HDI)" for i in s.interventions)
    )
    kept_whole = next(v for v in normalizer.values(study) if v.key == "pembrolizumab and interferon alfa-2b")
    assert kept_whole.label == "Pembrolizumab and high dose interferon alfa-2b (HDI)"


def test_noise_and_non_drug_interventions_give_no_drug(normalizer: DrugNormalizer) -> None:
    study = next(s for s in pembrolizumab() if any(i.name == "Chemotherapy" for i in s.interventions))
    keys = _keys(normalizer.values(study))
    assert "chemotherapy" not in keys
    assert "biopsy" not in {key for s in pembrolizumab() for key in _keys(normalizer.values(s))}


def test_each_drug_appears_once_per_trial_with_its_arms(normalizer: DrugNormalizer) -> None:
    for study in pembrolizumab():
        keys = _keys(normalizer.values(study))
        assert len(keys) == len(set(keys))
    study = next(
        s
        for s in pembrolizumab()
        if sum(i.name == "Pembrolizumab" for i in s.interventions) and s.interventions[0].arm_group_labels
    )
    pembro = next(v for v in normalizer.values(study) if v.key == "pembrolizumab")
    assert pembro.groups


def _intervention(name: str, kind: str, labels: tuple[str, ...]) -> Intervention:
    return Intervention(index=0, type=kind, name=name, other_names=(), arm_group_labels=labels)


def test_an_intervention_named_as_its_arm_group_is_a_label_and_not_a_drug() -> None:
    assert is_arm_label(_intervention("LSG-Alpha group", "COMBINATION_PRODUCT", ("LSG-Alpha group",)))
    assert is_arm_label(_intervention("Gamma cohort", "DRUG", ("gamma cohort",)))


def test_a_drug_whose_arm_is_named_after_it_is_kept() -> None:
    assert not is_arm_label(_intervention("Pembrolizumab", "DRUG", ("Pembrolizumab",)))
    assert not is_arm_label(_intervention("Pembrolizumab", "DRUG", ("Pembrolizumab group",)))
    assert not is_arm_label(_intervention("Beta group therapy", "DRUG", ("Control arm",)))
    assert not is_arm_label(
        _intervention("Alpha (extension of cohort 1)", "BIOLOGICAL", ("alpha (extension of cohort 1)",))
    )


def test_the_parts_of_one_combination_string_pair_by_arm_even_when_the_record_lists_no_arm_label() -> None:
    from ctviz.catalog.fields import CATALOG, BoundDimension
    from ctviz.engine.aggregate import cells

    from .engine_memory import record

    studies = [
        record(1, names=["alpha"], arms={"alpha": []}),
        record(2, names=["beta"], arms={"beta": []}),
        record(3, names=["alpha + beta"], arms={"alpha + beta": []}),  # no arm label at all
        record(4, names=["alpha + beta"], arms={"alpha + beta": ["Arm 1"]}),
        record(5, names=["alpha", "beta"], types=("DRUG", "DRUG"), arms={"alpha": ["A"], "beta": ["B"]}),
    ]
    fitted = DrugNormalizer.fit(studies)
    dims = (BoundDimension(CATALOG["drug"], None, "node"),) * 2

    pairs = {
        study.nct_id: [(a.key, b.key) for a, b in cells([fitted.values(study)], dims, "same_arm")]
        for study in studies[2:]
    }

    assert pairs == {
        "NCT00000003": [("alpha", "beta")],  # one string: given together
        "NCT00000004": [("alpha", "beta")],
        "NCT00000005": [],  # two interventions in two different arms: not given together
    }


def test_the_two_forms_of_one_molecule_are_two_drugs_and_the_commonest_spelling_labels_each() -> None:
    from .engine_memory import record

    studies = [
        record(1, names=["(+)-epicatechin"]),
        record(2, names=["(+)-Epicatechin"]),
        record(3, names=["(\u2212)-epicatechin"]),
        record(4, names=["(-)-epicatechin"]),
        record(5, names=["epicatechin"]),
    ]
    fitted = DrugNormalizer.fit(studies)

    keys = {study.nct_id: [v.key for v in fitted.values(study)] for study in studies}

    assert keys == {
        "NCT00000001": ["pos-epicatechin"],
        "NCT00000002": ["pos-epicatechin"],
        "NCT00000003": ["neg-epicatechin"],
        "NCT00000004": ["neg-epicatechin"],
        "NCT00000005": ["epicatechin"],
    }
    assert fitted.labels["pos-epicatechin"] == "(+)-Epicatechin" or fitted.labels[
        "pos-epicatechin"
    ].startswith("(+)")
