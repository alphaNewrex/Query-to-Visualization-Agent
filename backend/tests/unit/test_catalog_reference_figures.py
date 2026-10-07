"""The reference figures of plan 4.8 that the catalogue produces, on the 499 Duchenne trials.

They were first computed by the plan's prototype over a live walk and are reproduced here from the
catalogue's own extractors, before alias merging. A change to a phase bucket, to the drug normaliser or
to the sponsor rule shows up as a different figure.
"""

import itertools
from collections import Counter

from ctviz.catalog.fields import CATALOG, BoundDimension

from .catalog_samples import duchenne

PHASE_ORDER = (
    "EARLY_PHASE1",
    "PHASE1",
    "PHASE1_PHASE2",
    "PHASE2",
    "PHASE2_PHASE3",
    "PHASE3",
    "PHASE4",
    "NA",
    "NONE",
)


def test_the_phase_buckets_partition_the_trials_as_a_walk_counts_them() -> None:
    spec = CATALOG["phase"]
    tally = Counter(
        value.key
        for study in duchenne()
        for value in spec.extract(study, None, BoundDimension(spec, None, "axis"))
    )
    assert [tally[key] for key in PHASE_ORDER] == [10, 49, 47, 88, 11, 50, 11, 91, 142]
    assert sum(tally.values()) == len(duchenne()) == 499


def test_intervention_types_count_each_type_once_per_trial() -> None:
    spec = CATALOG["intervention_type"]
    found = [spec.extract(study, None, BoundDimension(spec, None, "axis")) for study in duchenne()]
    tally = Counter(value.key for values in found for value in values)
    assert tally.most_common(5) == [
        ("DRUG", 225),
        ("OTHER", 93),
        ("BIOLOGICAL", 32),
        ("DEVICE", 31),
        ("GENETIC", 28),
    ]
    assert sum(1 for values in found if not values) == 78


def test_the_sponsor_and_drug_network_before_alias_merging() -> None:
    drug, sponsor = CATALOG["drug"], CATALOG["sponsor"]
    assert drug.prepare is not None
    context = drug.prepare(duchenne())
    links: Counter[tuple[str, str]] = Counter()
    sponsors, drugs, contributing = set(), set(), 0
    for study in duchenne():
        found_drugs = drug.extract(study, context, BoundDimension(drug, None, "node"))
        found_sponsors = sponsor.extract(study, None, BoundDimension(sponsor, None, "node"))
        if found_drugs and found_sponsors:
            contributing += 1
            sponsors.update(value.key for value in found_sponsors)
            drugs.update(value.key for value in found_drugs)
            links.update((s.key, d.key) for s, d in itertools.product(found_sponsors, found_drugs))
    assert (len(sponsors), len(drugs), len(links), contributing) == (114, 187, 224, 279)
    assert links.most_common(3) == [
        (("PTC Therapeutics", "ataluren"), 13),
        (("Sarepta Therapeutics, Inc.", "delandistrogene moxeparvovec"), 9),
        (("Sarepta Therapeutics, Inc.", "eteplirsen"), 7),
    ]


def test_the_trials_a_drug_walk_reads_are_the_ones_with_a_drug_type_intervention() -> None:
    """The presence expression selects 281 of the 499 trials on the registry (measured); a walk agrees."""
    types = {"DRUG", "BIOLOGICAL", "GENETIC", "COMBINATION_PRODUCT"}
    assert sum(1 for study in duchenne() if any(item.type in types for item in study.interventions)) == 281
