"""Every sentence a user reads comes from this module: titles, messages, assumptions, warnings, questions.

Templates take facts that code computed (labels from the catalogue, counts from the data); nothing here
or anywhere else puts model-written text on the screen. Third-party text (a sponsor, a trial title) is
only ever interpolated, never interpreted.
"""

from collections.abc import Mapping, Sequence
from typing import Final

from ctviz.catalog import vocab
from ctviz.contract.response import ClarificationReason, Note, NumberFormat
from ctviz.ctgov.params import DateRange, Scope

# --- Vocabulary ---------------------------------------------------------------------------------------

DATE_VERBS: Final = {
    "start_date": "started",
    "primary_completion_date": "reaching primary completion",
    "completion_date": "completed",
    "first_posted_date": "first posted",
}
NUMBER_TITLES: Final = {"enrollment": "Enrollment", "duration_months": "Duration", "site_count": "Sites"}
NUMBER_UNITS: Final = {"enrollment": "participants", "duration_months": "months", "site_count": "sites"}
NUMBER_FORMATS: Final[dict[str, NumberFormat]] = {
    "enrollment": ",d",
    "duration_months": ".1f",
    "site_count": ",d",
}
KIND_PLURALS: Final = {
    "sponsor": "Sponsors",
    "drug": "Drugs",
    "condition": "Conditions",
    "country": "Countries",
}
KIND_TITLES: Final = {"drug": "Drug", "condition": "Condition", "sponsor": "Sponsor", "country": "Country"}
SORT_TITLES: Final = {
    "enrollment": "largest enrollment",
    "start_date": "start date",
    "completion_date": "completion date",
    "first_posted_date": "first posted date",
}
# The enum filters of a scope in the order a sentence lists them, with the title that names each.
FILTER_TITLES: Final = {
    "phase": "Phase",
    "overall_status": "Status",
    "study_type": "Study type",
    "sponsor_class": "Sponsor class",
    "intervention_type": "Intervention type",
}
DATE_PIECE_VERBS: Final = {
    "StartDate": "started",
    "PrimaryCompletionDate": "reached primary completion",
    "CompletionDate": "completed",
    "StudyFirstPostDate": "first posted",
}

CHART_RATIONALE: Final = {
    1: "Rule 1: two numbers per trial, so a scatter plot with one point per trial.",
    2: "Rule 2: a list of trials, so a table.",
    3: "Rule 3: one number for one scope, so a metric.",
    4: "Rule 4: one number for each of several scopes, so bars side by side.",
    5: "Rule 5: one date dimension, so a line over time.",
    6: "Rule 6: enrollment for one scope without a series, so a histogram.",
    7: "Rule 7: enrollment bins for several scopes or a series, so grouped bars over the bin labels.",
    8: "Rule 8: one ordinal category, so vertical bars in natural order.",
    9: "Rule 9: one nominal category, so horizontal bars sorted by value.",
    10: "Rule 10: the series may share trials, so the bars are grouped, never stacked.",
    11: "Rule 11: the series splits the trials exactly, so the bars are stacked.",
    12: "Rule 12: two different kinds of node, so a bipartite network.",
    13: "Rule 13: one kind of node on both sides, so a force-directed network.",
}
PREFERENCE_TIME_AS_BARS: Final = " Drawn as bars on the time axis, as asked."
PREFERENCE_HISTOGRAM_AS_BARS: Final = " Drawn as bars over the bin labels, as asked."
PREFERENCE_AS_TABLE: Final = " Returned as a table of its rows, as asked."

UNSUPPORTED_MESSAGES: Final = {
    "not_about_clinical_trials": "This service answers questions about clinical trials registered on "
    "ClinicalTrials.gov; this question is about something else.",
    "needs_data_not_in_registry": "ClinicalTrials.gov does not hold the data this question needs, so it "
    "cannot be answered from the registry.",
    "single_trial_lookup": "This asks about one trial. Open the trial on ClinicalTrials.gov, or ask how "
    "many trials match a description.",
    "analysis_not_supported": "This kind of analysis or grouping is not offered. Try counting trials by "
    "phase, status, sponsor, country, drug or condition, or over time.",
    "other": "This question cannot be answered from ClinicalTrials.gov registry data.",
}
CLARIFICATION_MISSING: Final = {
    "drug_name": "Which drug do you mean? Name it in the question or send `drug_name`.",
    "condition": "Which condition do you mean? Name it in the question or send `condition`.",
    "sponsor": "Which sponsor do you mean? Name it in the question or send `sponsor`.",
    "country": "Which country do you mean? Name it in the question or send `country`.",
    "compare": "What should be compared? Name the values as 'A vs B' or send `compare`.",
    "group_by": "How should the trials be grouped? Say 'by phase' or 'per year', or send `group_by`.",
}
CLARIFICATION_REASONS: Final = {
    "missing_entity": "Which drug, condition, sponsor or country do you mean? Name it in the question.",
    "unknown_value": "One of the values was not recognised. Check the spelling or pick one of the options.",
    "ambiguous_request": "The question can be read in more than one way. Say what to count and how "
    "to group it.",
    "could_not_interpret": "The question could not be turned into an analysis of clinical trials. "
    "Rephrase it, or send structured fields.",
    "too_broad": "That breakdown is too broad to draw exactly. Narrow the scope or use a coarser time unit.",
}


def count(number: int) -> str:
    return f"{number:,}"


def share(fraction: float) -> str:
    return f"{fraction:.1%}"


# --- Titles and subtitles -----------------------------------------------------------------------------


def scope_suffix(labels: Sequence[str | None]) -> str:
    named = [label for label in labels if label]
    return f": {' vs '.join(named)}" if named else ""


def scope_name(scope: Scope) -> str | None:
    """How a title names a scope: its compared value, else the words of the entities that define it."""
    if scope.label is not None:
        return scope.label
    return ", ".join(term.text for term in scope.terms) or None


def _filter_labels(key: str) -> Mapping[str, str]:
    return {
        "phase": vocab.PHASE_LABELS,
        "overall_status": vocab.overall_status().labels,
        "study_type": vocab.study_type().labels,
        "sponsor_class": vocab.sponsor_class().labels,
        "intervention_type": vocab.intervention_type().labels,
    }[key]


def date_range_phrase(date_range: DateRange) -> str:
    """`started 2020 or later`: the years of a range, whichever of its ends is set."""
    verb = DATE_PIECE_VERBS[date_range.piece]
    first = date_range.first_day[:4] if date_range.first_day else None
    last = date_range.last_day[:4] if date_range.last_day else None
    if first and last:
        return f"{verb} in {first}" if first == last else f"{verb} {first} to {last}"
    return f"{verb} {first} or later" if first else f"{verb} {last} or earlier"


def scope_filters(scope: Scope, *, skip_date_piece: str | None = None) -> list[str]:
    """What else limits a scope, as short phrases: shared entities, enum filters and a date range.

    A date range on the piece that a time axis already shows is left out, because the axis says it.
    """
    shared = scope.terms[:-1] if scope.label is not None else ()
    phrases = [", ".join(term.text for term in shared)] if shared else []
    for key, title in FILTER_TITLES.items():
        tokens = scope.enum_filters.get(key, ())
        if not tokens:
            continue
        names = " or ".join(_filter_labels(key).get(token, token) for token in tokens)
        phrases.append(names if key == "phase" else f"{title}: {names}")
    date_range = scope.date_range
    if date_range is not None and date_range.piece != skip_date_piece:
        phrases.append(date_range_phrase(date_range))
    return phrases


def count_title(date_key: str | None) -> str:
    return f"Trials {DATE_VERBS[date_key]}" if date_key in DATE_VERBS else "Trials"


def time_series_title(date_key: str, unit: str, labels: Sequence[str | None]) -> str:
    return f"{count_title(date_key)} per {unit}{scope_suffix(labels)}"


def bar_title(x_title: str, series_title: str | None, labels: Sequence[str | None]) -> str:
    by = x_title.lower() if series_title is None else f"{x_title.lower()} and {series_title.lower()}"
    return f"Trials by {by}{scope_suffix(labels)}"


def compared_totals_title(labels: Sequence[str | None]) -> str:
    return f"Number of trials{scope_suffix(labels)}"


def histogram_title(title: str, labels: Sequence[str | None]) -> str:
    return f"Trials by {title.lower()} size{scope_suffix(labels)}"


def scatter_title(x_title: str, y_title: str, labels: Sequence[str | None]) -> str:
    return f"{y_title} against {x_title.lower()}{scope_suffix(labels)}"


def table_title(sort_by: str, order: str, labels: Sequence[str | None]) -> str:
    if sort_by == "enrollment":
        ordering = (
            "Trials with the largest enrollment" if order == "desc" else "Trials with the smallest enrollment"
        )
    else:
        ordering = f"Trials by {SORT_TITLES[sort_by]}, {'latest' if order == 'desc' else 'earliest'} first"
    return f"{ordering}{scope_suffix(labels)}"


def network_title(source: str, target: str, labels: Sequence[str | None]) -> str:
    if source == target:
        return f"{KIND_PLURALS[source]} that occur together in trials{scope_suffix(labels)}"
    return (
        f"{KIND_PLURALS[source]} and {KIND_PLURALS[target].lower()} that occur together{scope_suffix(labels)}"
    )


def date_axis_title(date_title: str, unit: str) -> str:
    return f"{date_title.removesuffix(' date')} {unit}"


def window_phrase(date_title: str, unit: str, first: str, last: str) -> str:
    return f"{date_axis_title(date_title, unit)}s {first} to {last}"


def trials_phrase(parts: Sequence[tuple[str | None, int]], noun: str = "trials") -> str:
    if len(parts) == 1 and parts[0][0] is None:
        return f"{count(parts[0][1])} {noun}"
    return ", ".join(f"{label}: {count(n)}" for label, n in parts) + f" {noun}"


def subtitle(*parts: str | None, data_date: str) -> str:
    return " · ".join([*(p for p in parts if p), f"ClinicalTrials.gov, data as of {data_date}"])


def subset_phrase(size: int, first_posted_from: str, first_posted_to: str) -> str:
    return (
        f"limited to the {count(size)} most recently first-posted trials "
        f"({first_posted_from} to {first_posted_to})"
    )


def node_size_title() -> str:
    return "Trials (all analysed trials of this node)"


def edge_weight_title() -> str:
    return "Trials that include both"


# --- One-sentence messages ----------------------------------------------------------------------------


def time_series_message(date_key: str, last: str, last_count: int, peak: str, peak_count: int) -> str:
    verb = DATE_VERBS[date_key]
    if last == peak:
        return f"{count(last_count)} trials {verb} in {last}, the most of any period shown."
    return f"{count(last_count)} trials {verb} in {last}; the peak was {count(peak_count)} in {peak}."


def series_peak_message(date_key: str, series: str, period: str, peak_count: int) -> str:
    return f"{series} peaked at {count(peak_count)} trials {DATE_VERBS[date_key]} in {period}."


def largest_category_message(label: str, trials: int, analyzed: int) -> str:
    fraction = trials / analyzed if analyzed else 0.0
    return f"{label} is the largest group: {count(trials)} of {count(analyzed)} trials ({share(fraction)})."


def largest_in_series_message(label: str, series: str, trials: int) -> str:
    return f"The largest group is {label} for {series}, with {count(trials)} trials."


def compared_totals_message(label: str, trials: int) -> str:
    return f"{label} has the most trials: {count(trials)}."


def metric_message(trials: int, labels: Sequence[str | None], filters: Sequence[str] = ()) -> str:
    named = [label for label in labels if label]
    detail = "; ".join([*([" vs ".join(named)] if named else []), *filters])
    verb = "trial matches" if trials == 1 else "trials match"
    return f"{count(trials)} {verb}: {detail}." if detail else f"{count(trials)} {verb}."


def histogram_message(label: str, trials: int) -> str:
    return f"The most common size range is {label}, with {count(trials)} trials."


def scatter_message(points: int, x_title: str, y_title: str) -> str:
    return f"{count(points)} trials plotted, {y_title.lower()} against {x_title.lower()}."


def table_message(rows: int, total: int, sort_by: str) -> str:
    return f"{count(rows)} of {count(total)} trials, ordered by {SORT_TITLES[sort_by]}."


def network_message(nodes: int, links: int, a: str, b: str, trials: int) -> str:
    return (
        f"{count(nodes)} nodes and {count(links)} links; the strongest link joins {a} and {b} "
        f"in {count(trials)} trials."
    )


def interpretation_summary(
    analysis: str,
    groups: Sequence[str],
    labels: Sequence[str | None],
    unit: str | None,
    filters: Sequence[str] = (),
) -> str:
    named = scope_suffix(labels)[2:] or "all trials"
    scope = f"{named} ({'; '.join(filters)})" if filters else named
    match analysis:
        case "total":
            return f"Counting trials for {scope}."
        case "relate":
            return f"Plotting one point per trial for {scope}."
        case "trial_list":
            return f"Listing trials for {scope}."
        case "network":
            return f"Counting trials in which {' and '.join(groups)} occur together, for {scope}."
        case _:
            by = ", then ".join(groups)
            return f"Counting trials by {by}{f' (per {unit})' if unit else ''} for {scope}."


# --- Assumptions, warnings and notes ------------------------------------------------------------------


def partial_period(period: str, data_date: str) -> Note:
    return Note(code="partial_period", message=f"{period} is incomplete: data as of {data_date}.")


def chart_preference_ignored(preference: str, chosen: str) -> Note:
    return Note(
        code="chart_preference_ignored",
        message=f"A {preference.replace('_', ' ')} does not fit this data, "
        f"so a {chosen.replace('_', ' ')} is shown.",
    )


def recent_subset(size: int, matched: int) -> Note:
    return Note(
        code="recent_subset",
        message=f"{count(matched)} trials match, more than can be read exactly; only the {count(size)} most "
        "recently first-posted were counted.",
    )


def counts_not_reconciled(label: str | None, expected: int, counted: int) -> Note:
    scope = f" for {label}" if label else ""
    return Note(
        code="counts_not_reconciled",
        message=f"The registry reported {count(expected)} matching trials{scope} but {count(counted)} were "
        f"counted ({count(abs(expected - counted))} apart); the counted number is used.",
    )


def insufficient_cooccurrence() -> Note:
    return Note(
        code="insufficient_cooccurrence",
        message="Fewer than three pairs of items occur together in more than one trial.",
    )


def names_partly_normalised() -> Note:
    return Note(
        code="names_partly_normalised",
        message="Drug names are free text and only partly normalised: one compound under a code name and a "
        "generic name can appear as two entries.",
    )


def free_text_categories() -> Note:
    return Note(
        code="free_text_categories",
        message="Conditions are free text, so different spellings of one condition can appear as separate "
        "categories.",
    )


PHASE_FILTER_NOTE: Final = (
    "A phase filter keeps the trials that list that phase, so Phase 2 also includes Phase 1/Phase 2 and "
    "Phase 2/Phase 3 trials."
)
NUMBER_NOTES: Final = {
    "enrollment": "Enrollment counts, estimated and actual, are both used.",
    "duration_months": "Duration runs from the start date to the completion date, a month counting as its "
    "first day.",
    "site_count": "Sites are the locations listed in each record.",
}


def no_end_period(last: str) -> str:
    return f"No end year was given, so the axis stops at {last}, the period of the data."


def default_window(first: str) -> str:
    return f"No start year was given, so the axis starts at {first}; earlier trials are counted as before it."


def overlap_note(shared: int, labels: Sequence[str]) -> str:
    return f"{count(shared)} trials involve both {' and '.join(labels)} and appear in both groups."


def link_note(source: str, target: str, pairing: str) -> str:
    if source != target:
        pair = f"A {KIND_TITLES[source].lower()} and a {KIND_TITLES[target].lower()}"
        who = " The sponsor of a trial is its lead sponsor." if "sponsor" in (source, target) else ""
        return f"{pair} are linked when one trial lists both.{who}"
    kind = KIND_PLURALS[source].lower()
    if pairing == "same_arm":
        return (
            f"Two {kind} are linked when they share an arm label in a trial. An arm that offers a choice of "
            "agents lists both, so some links are alternatives."
        )
    return f"Two {kind} are linked when one trial lists both."


OUTSIDE_SUBSET_REASON: Final = "outside_recent_subset"
OUTSIDE_SUBSET_MESSAGE: Final = "Not read: older than the most recent trials that were counted."


def subset_assumption(size: int) -> str:
    return f"Counted the {count(size)} most recently first-posted trials of a larger set."


def citation_selection(strategies: Sequence[str]) -> str:
    if not strategies or set(strategies) == {"none"}:
        return "no trials are cited"
    if set(strategies) <= {"count_fan_out", "sample_then_recount", "sorted_page"}:
        return "most relevant trials by ClinicalTrials.gov's own ranking"
    return "trials naming the searched entity first, then most recently first-posted, then by NCT ID"


# --- Outcomes without a chart -------------------------------------------------------------------------


def clarification_question(reason: ClarificationReason, missing_fields: Sequence[str]) -> str:
    for name in missing_fields:
        if name in CLARIFICATION_MISSING:
            return CLARIFICATION_MISSING[name]
    return CLARIFICATION_REASONS[reason]


def unsupported_message(category: str) -> str:
    return UNSUPPORTED_MESSAGES.get(category, UNSUPPORTED_MESSAGES["other"])


def no_data_message(labels: Sequence[str | None], matched: Mapping[str, int] | None = None) -> str:
    what = scope_suffix(labels)[2:] or "the question"
    detail = "" if not matched else " (" + ", ".join(f"{k}: {count(v)}" for k, v in matched.items()) + ")"
    return f"No trials were found for {what}{detail}."


def insufficient_cooccurrence_message(labels: Sequence[str | None]) -> str:
    what = scope_suffix(labels)[2:] or "this scope"
    return f"Too few items occur together in more than one trial for {what} to draw a network."


# --- Follow-ups ---------------------------------------------------------------------------------------


SHOW_ALL_YEARS: Final = "Show all years"


def show_more(top_n: int) -> str:
    return f"Show the top {top_n}"


def sponsor_option(name: str) -> str:
    return f"Only trials led by {name}"
