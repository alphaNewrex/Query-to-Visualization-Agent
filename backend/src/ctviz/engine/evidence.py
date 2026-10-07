"""What a request must project to answer a plan, and which trials make the best citations.

A projection keeps a page small: the registry returns only the pieces a plan reads. The citation rule is
the one of section 4.13: a trial that names a scope term comes first, then the most recently first-posted,
then the highest NCT ID.
"""

from typing import Final

from ctviz.catalog.fields import CATALOG, BoundDimension, Evidence, FieldSpec
from ctviz.ctgov.params import Scope
from ctviz.ctgov.study import Study
from ctviz.engine.lower import EnginePlan, ListRows, PointRows

BASE_FIELDS: Final = ("NCTId", "BriefTitle", "StudyFirstPostDate")
BRIEF_TITLE_PATH: Final = "protocolSection.identificationModule.briefTitle"
# Where each kind of scope term occurs in a record, as projection pieces: what the citation rule reads.
_TERM_PIECES: Final = {
    "drug": ("InterventionName", "InterventionOtherName"),
    "condition": ("Condition",),
    "sponsor": ("LeadSponsorName",),
    "country": ("LocationCountry",),
    "term": ("BriefTitle",),
}
_NUMERIC_PIECES: Final = {
    "enrollment": ("EnrollmentCount", "EnrollmentType"),
    "duration_months": ("StartDate", "CompletionDate"),
    "site_count": ("LocationCountry",),
}
# The trial list's columns, and the piece each sortable field is sorted by.
LIST_PIECES: Final = (
    "BriefTitle",
    "Phase",
    "OverallStatus",
    "StartDate",
    "EnrollmentCount",
    "LeadSponsorName",
)
SORT_PIECES: Final = {
    "enrollment": "EnrollmentCount",
    "start_date": "StartDate",
    "completion_date": "CompletionDate",
    "first_posted_date": "StudyFirstPostDate",
}
_NAMED_KINDS: Final = frozenset({"drug", "condition", "sponsor"})


def projection(plan: EnginePlan, scope: Scope) -> tuple[str, ...]:
    """The pieces a walk or a sample of this scope must project, `NCTId` first."""
    pieces = {*BASE_FIELDS}
    for term in scope.terms:
        pieces.update(_TERM_PIECES[term.kind])
    for dimension in plan.dimensions:
        pieces.update(dimension.spec.pieces)
    match plan.rows:
        case PointRows(x=x, y=y, color=color):
            pieces.update(_NUMERIC_PIECES[x], _NUMERIC_PIECES[y])
            if color is not None:
                pieces.update(color.spec.pieces)
        case ListRows(sort_by=sort_by):
            pieces.update(LIST_PIECES, (SORT_PIECES[sort_by],))
        case None:
            if not plan.dimensions:
                for spec in scope_evidence_specs(scope):
                    pieces.update(spec.pieces)
    return ("NCTId", *sorted(pieces - {"NCTId"}))


def scope_evidence_specs(scope: Scope) -> list[FieldSpec]:
    """The fields the scope filters on that the catalogue can quote: what a metric's citations show."""
    specs = [CATALOG[key] for key in scope.enum_filters if key in CATALOG]
    if scope.date_range is not None:
        specs.extend(
            spec
            for spec in CATALOG.values()
            if spec.kind == "date" and spec.pieces[0] == scope.date_range.piece
        )
    return specs


def scope_evidence(study: Study, scope: Scope) -> tuple[Evidence, ...]:
    """Why a trial is in a scope, quoted from the fields the scope filters on; the title when it has none."""
    evidence = [
        item
        for spec in scope_evidence_specs(scope)
        for value in spec.extract(
            study, None, BoundDimension(spec, "year" if spec.kind == "date" else None, "axis")
        )
        for item in value.evidence
    ]
    return tuple(evidence) or (Evidence(BRIEF_TITLE_PATH, study.brief_title),)


def citation_rank(study: Study, scope: Scope) -> tuple[bool, str, str]:
    """Written so that a larger rank is a better citation."""
    names = [
        *(intervention.name or "" for intervention in study.interventions),
        *(other for intervention in study.interventions for other in intervention.other_names),
        *study.conditions,
        study.lead_sponsor_name or "",
    ]
    haystack = " ".join(names).casefold()
    is_named = any(term.text.casefold() in haystack for term in scope.terms if term.kind in _NAMED_KINDS)
    first_posted = study.first_post_date.date if study.first_post_date is not None else ""
    return (is_named, first_posted, study.nct_id)
