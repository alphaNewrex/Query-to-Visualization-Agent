"""Drug names: free text turned into comparable drug identities, with the intervention they came from.

Intervention names are free text, a quarter of them combinations in one string. `DrugNormalizer.fit`
learns which names stand alone in one result set; `values` then gives each trial's drugs. Learned
aliases (brand and code names read from `otherNames`) are not merged: one compound under a code name
and a generic name stays two drugs when nothing links them, which the `names_partly_normalised`
warning says.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Self

from ctviz.catalog.fields import Evidence, Value
from ctviz.catalog.spellings import commonest_spellings
from ctviz.ctgov.study import Intervention, Study

DRUG_TYPES: Final = ("DRUG", "BIOLOGICAL", "GENETIC", "COMBINATION_PRODUCT")
# Tested anywhere in the raw name, before normalising: a full-match test would keep names such as
# "Placebo to pembrolizumab", and a test on the normalised name gives different totals.
NOISE_RE: Final = re.compile(
    r"\b(placebos?|saline|sham|vehicle|dummy|standard[ -]of[ -]care|soc|best supportive care|supportive care|"
    r"usual care|no intervention|no treatment|observation|observational|control|comparator|chemotherapy|"
    r"chemoradiotherapy|chemoradiation|radiotherapy|radiation|surgery|immunotherapy|investigator'?s choice|"
    r"physician'?s choice|tpc|standard treatment|standard therapy|background therapy|rescue medication|"
    r"antiviral prophylaxis)\b",
    re.IGNORECASE,
)
_UNIT: Final = r"(?:mg|mcg|μg|µg|ug|g|kg|ml|l|iu|units?|mmol|meq|gy|cgy)"
_PER: Final = r"(?:kg|m2|m\^2|m²|ml|l|day|d|dose|week|wk|h|hr|hour|" + _UNIT + ")"
DOSE_RE: Final = re.compile(
    rf"\b\d+(?:[.,]\d+)?(?:\s*-\s*\d+(?:[.,]\d+)?)?\s*{_UNIT}\b(?:\s*/\s*\d*\s*{_PER}\b)*|\b\d+(?:[.,]\d+)?\s*%",
    re.IGNORECASE,
)
FORM_RE: Final = re.compile(
    r"\b(injections?|injectable|infusions?|tablets?|capsules?|oral|orally|intravenous|iv|i\.v\.|solution|"
    r"suspension|subcutaneous|sc|for injection|high dose|low dose|dose level \d+|dose|daily|weekly|"
    r"monotherapy|single agent|phase \d[ab]?|arm [a-z0-9]+:?|cohort [a-z0-9]+:?|part [a-z0-9]+:?)\b",
    re.IGNORECASE,
)
SPLIT_RE: Final = re.compile(
    r"\s*(?:\+|/|&|,|\band\b|\bplus\b|\bwith\b|\bcombined with\b|\bin combination with\b)\s*"
)
# "or" is a separator for this test only: a name containing it is never split, but is not stand-alone.
HAS_SEPARATOR: Final = re.compile(r"\+|/|&|,|\band\b|\bplus\b|\bwith\b|\bor\b")


# A design word: an intervention named like this is the arm it belongs to, not a compound.
ARM_WORD_RE: Final = re.compile(r"\b(?:groups?|arms?|cohorts?)\b", re.IGNORECASE)


def is_noise(raw_name: str) -> bool:
    return NOISE_RE.search(raw_name) is not None


def is_arm_label(item: Intervention) -> bool:
    """The intervention is named as the arm it is given in ("... group"): a label, not a drug.

    Sponsors sometimes register an arm as its own intervention, with the arm's label as the name. Both
    conditions must hold, so a drug whose arm is merely named after it is kept.
    """
    name = (item.name or "").strip().casefold()
    labels = {label.strip().casefold() for label in item.arm_group_labels}
    # The words in brackets are asides ("ACE-031 (extension of cohort 1)") and say nothing about the name.
    return ARM_WORD_RE.search(normalise(name)) is not None and name in labels


def normalise(name: str) -> str:
    """Lower-case, without registered marks, asides in brackets, doses and form or route words."""
    text = name.lower().replace("®", "").replace("™", "")
    text = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", text)
    text = FORM_RE.sub(" ", DOSE_RE.sub(" ", text))
    text = re.sub(r"[^a-z0-9+/&,\- ]", " ", text)
    return re.sub(r"\s+", " ", text).strip(" -+/&,")


def split_combination(normalised: str, known: frozenset[str]) -> list[str]:
    """The parts of a combination, only when every part is already a known stand-alone name.

    An unconditional split would turn the single drug `ns-065/ncnp-01` into a combination.
    """
    if not HAS_SEPARATOR.search(normalised):
        return [normalised]
    parts = [part.strip(" -") for part in SPLIT_RE.split(normalised) if part.strip(" -")]
    return parts if len(parts) > 1 and all(part in known for part in parts) else [normalised]


def _drug_interventions(study: Study) -> list[tuple[Intervention, str]]:
    """The interventions that can be drugs, with their raw names, noise and arm labels left out."""
    return [
        (item, item.name)
        for item in study.interventions
        if item.type in DRUG_TYPES and item.name and not is_noise(item.name) and not is_arm_label(item)
    ]


@dataclass(frozen=True)
class DrugNormalizer:
    """Fitted to one result set: the stand-alone names, and the commonest raw spelling of each key."""

    known: frozenset[str]
    labels: Mapping[str, str]

    @classmethod
    def fit(cls, studies: Sequence[Study]) -> Self:
        raw_names = (raw for study in studies for _, raw in _drug_interventions(study))
        labels = commonest_spellings((key, raw) for raw in raw_names if (key := normalise(raw)))
        stand_alone = {key for key in labels if not HAS_SEPARATOR.search(key)}
        mesh_terms = {term.lower() for study in studies for term in study.intervention_mesh_terms}
        return cls(known=frozenset(stand_alone | mesh_terms), labels=labels)

    def values(self, study: Study) -> list[Value]:
        """Each normalised drug of a trial once, with the arms it was given in and where its name was read."""
        found: dict[str, Value] = {}
        for item, raw in _drug_interventions(study):
            key = normalise(raw)
            if not key:
                continue
            evidence = Evidence(
                f"protocolSection.armsInterventionsModule.interventions[{item.index}].name", raw
            )
            for part in split_combination(key, self.known):
                earlier = found.get(part)
                groups = frozenset(item.arm_group_labels) | (earlier.groups if earlier else frozenset())
                found[part] = Value(
                    key=part,
                    label=self.labels.get(part, part),
                    evidence=earlier.evidence if earlier else (evidence,),
                    groups=groups,
                )
        return list(found.values())
