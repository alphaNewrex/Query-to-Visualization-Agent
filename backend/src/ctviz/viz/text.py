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
STATISTIC_WORDS: Final = {"median": "Median", "mean": "Mean", "sum": "Total"}
MEASURE_PHRASES: Final = {
    "enrollment": "enrollment (participants)",
    "duration_months": "duration (months)",
    "site_count": "number of sites per trial",
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
    "sex": "Sex",
    "age_group": "Age group",
    "allocation": "Allocation",
    "masking": "Masking",
    "primary_purpose": "Primary purpose",
    "has_results": "Results",
    "intervention_model": "Intervention model",
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


def n_trials(number: int) -> str:
    """`1 trial`, `0 trials`, `12 trials`."""
    return f"{count(number)} trial" if number == 1 else f"{count(number)} trials"


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
        "sex": vocab.sex().labels,
        "age_group": vocab.age_group().labels,
        "allocation": vocab.allocation().labels,
        "masking": vocab.masking().labels,
        "primary_purpose": vocab.primary_purpose().labels,
        "has_results": vocab.has_results().labels,
        "intervention_model": vocab.intervention_model().labels,
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
    if scope.excluded:
        phrases.append("excluding " + ", ".join(term.text for term in scope.excluded))
    if scope.excluded_statuses:
        labels = vocab.overall_status().labels
        phrases.append(
            "Status: not " + " or ".join(labels.get(token, token) for token in scope.excluded_statuses)
        )
    return phrases


def measure_label(statistic: str, of: str) -> str:
    """`Median duration (months)`: the statistic and the field, with the unit in the words."""
    phrase = MEASURE_PHRASES[of]
    if statistic == "sum":
        phrase = phrase.replace(" per trial", "")
    return f"{STATISTIC_WORDS[statistic]} {phrase}"


def measure_value(value: float | int | None, statistic: str) -> str:
    if value is None:
        return "no value"
    return f"{value:,.0f}" if statistic == "sum" else f"{value:,.1f}"


def measure_bar_title(
    label: str, x_title: str, series_title: str | None, labels: Sequence[str | None]
) -> str:
    by = x_title.lower() if series_title is None else f"{x_title.lower()} and {series_title.lower()}"
    return f"{label} by {by}{scope_suffix(labels)}"


def measure_time_title(
    label: str, date_title: str, unit: str, labels: Sequence[str | None], split: str | None = None
) -> str:
    by = f", by {split.lower()}" if split else ""
    return f"{label} by {date_axis_title(date_title, unit)}{by}{scope_suffix(labels)}"


def measure_compared_title(label: str, labels: Sequence[str | None]) -> str:
    return f"{label}{scope_suffix(labels)}"


def count_title(date_key: str | None) -> str:
    return f"Trials {DATE_VERBS[date_key]}" if date_key in DATE_VERBS else "Trials"


def time_series_title(
    date_key: str, unit: str, labels: Sequence[str | None], split: str | None = None
) -> str:
    by = f", by {split.lower()}" if split else ""
    return f"{count_title(date_key)} per {unit}{by}{scope_suffix(labels)}"


def split_series_title(compare_kind: str | None, split_title: str) -> str:
    """The legend title of a comparison that is also split: one line or bar per pair."""
    return f"{KIND_TITLES.get(compare_kind or '', 'Group')} and {split_title.lower()}"


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
        ordering = f"Trials with the {sort_phrase(sort_by, order)}"
    else:
        ordering = f"Trials by {sort_phrase(sort_by, order)}"
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
        return f"{count(parts[0][1])} {noun.replace('trials', 'trial') if parts[0][1] == 1 else noun}"
    return ", ".join(f"{label}: {count(n)}" for label, n in parts) + f" {noun}"


def listed_phrase(listed: Sequence[tuple[str | None, int, int]]) -> str:
    """`5 of 30 trials`, or `France: 3 of 30, Spain: 3 of 10 trials` for compared groups."""
    if len(listed) == 1 and listed[0][0] is None:
        shown, total = listed[0][1:]
        return f"{count(shown)} of {n_trials(total)}" if shown < total else n_trials(total)
    return (
        ", ".join(f"{label}: {count(shown)} of {count(total)}" for label, shown, total in listed) + " trials"
    )


def subtitle(*parts: str | None, data_date: str) -> str:
    return " · ".join([*(p for p in parts if p), f"ClinicalTrials.gov, data as of {data_date}"])


def node_size_title() -> str:
    return "Trials (all analysed trials of this node)"


def edge_weight_title() -> str:
    return "Trials that include both"


# --- One-sentence messages ----------------------------------------------------------------------------


def joined(names: Sequence[str], *, limit: int = 4) -> str:
    """`A`, `A and B`, `A, B and C`; a long list is cut to its first three and a count of the rest."""
    if len(names) > limit:
        return f"{', '.join(names[:3])} and {len(names) - 3} more"
    if len(names) <= 2:
        return " and ".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


def time_series_message(
    date_key: str, last: str, last_count: int, peaks: Sequence[str], peak_count: int
) -> str:
    """The latest period against the peak; periods that share the peak are all named, not the first."""
    verb = DATE_VERBS[date_key]
    if len(peaks) == 1:
        if last == peaks[0]:
            return f"{n_trials(last_count)} {verb} in {last}, the most of any period shown."
        return f"{n_trials(last_count)} {verb} in {last}; the peak was {count(peak_count)} in {peaks[0]}."
    if last in peaks:
        others = [period for period in peaks if period != last]
        return (
            f"{n_trials(last_count)} {verb} in {last}, tied with {joined(others)} "
            "for the most of any period shown."
        )
    return (
        f"{n_trials(last_count)} {verb} in {last}; the peak was {count(peak_count)}, "
        f"tied between {joined(peaks)}."
    )


def coverage_note(date_key: str, settled: Sequence[str], drawn: int, window_last: str, beyond: int) -> str:
    """The periods a headline is about, when others are drawn or trials fall after the axis.

    Nothing is added where the headline covers every period drawn and no trial lies beyond them.
    """
    parts = []
    if len(settled) < drawn and settled:
        parts.append(f"counted over the periods that have ended, {settled[0]} to {settled[-1]}")
    if beyond:
        verb = "is" if beyond == 1 else "are"
        parts.append(f"{n_trials(beyond)} {DATE_VERBS[date_key]} after {window_last} {verb} not shown")
    return f" ({'; '.join(parts)})" if parts else ""


def series_peak_message(date_key: str, peaks: Sequence[tuple[str, str]], peak_count: int) -> str:
    """`peaks` are (series, period) pairs that reach the highest count."""
    verb = DATE_VERBS[date_key]
    if len(peaks) == 1:
        series, period = peaks[0]
        return f"{series} peaked at {n_trials(peak_count)} {verb} in {period}."
    where = joined([f"{series} in {period}" for series, period in peaks])
    return f"{where} are tied at the peak of {n_trials(peak_count)} {verb}."


def largest_category_message(labels: Sequence[str], trials: int, analyzed: int) -> str:
    fraction = trials / analyzed if analyzed else 0.0
    if len(labels) > 1:
        return (
            f"{joined(labels)} are tied as the largest groups: {count(trials)} of {n_trials(analyzed)} "
            f"each ({share(fraction)})."
        )
    return f"{labels[0]} is the largest group: {count(trials)} of {n_trials(analyzed)} ({share(fraction)})."


def largest_in_series_message(leaders: Sequence[tuple[str, str]], trials: int) -> str:
    """`leaders` are (category, series) pairs that reach the highest count."""
    if len(leaders) > 1:
        where = joined([f"{label} for {series}" for label, series in leaders])
        return f"The largest groups are tied at {n_trials(trials)} each: {where}."
    label, series = leaders[0]
    return f"The largest group is {label} for {series}, with {n_trials(trials)}."


def compared_totals_message(labels: Sequence[str], trials: int) -> str:
    if len(labels) > 1:
        return f"{joined(labels)} are tied with the most trials: {count(trials)} each."
    return f"{labels[0]} has the most trials: {count(trials)}."


def metric_message(trials: int, labels: Sequence[str | None], filters: Sequence[str] = ()) -> str:
    named = [label for label in labels if label]
    detail = "; ".join([*([" vs ".join(named)] if named else []), *filters])
    verb = "trial matches" if trials == 1 else "trials match"
    return f"{count(trials)} {verb}: {detail}." if detail else f"{count(trials)} {verb}."


def measure_top_message(
    label: str, wheres: Sequence[str], value: str, trials: Sequence[int], *, is_overlapping: bool = False
) -> str:
    """The highest value of a statistic; marks that share it are all named, with the trials of each."""
    detail = "; each trial counts in full in every group it is in" if is_overlapping else ""
    if len(wheres) > 1:
        measured = ", ".join(count(n) for n in trials[:3]) + (" ..." if len(trials) > 3 else "")
        return (
            f"{joined(wheres)} are tied for the highest {label.lower()}: {value} "
            f"(trials measured: {measured}{detail})."
        )
    return f"{wheres[0]} has the highest {label.lower()}: {value} ({n_trials(trials[0])} measured{detail})."


def measure_metric_message(label: str, value: str, trials: int, labels: Sequence[str | None]) -> str:
    named = [name for name in labels if name]
    scope = f" for {' vs '.join(named)}" if named else ""
    return f"{label}{scope}: {value}, over {n_trials(trials)}."


def histogram_message(labels: Sequence[str], trials: int) -> str:
    if len(labels) > 1:
        return f"The most common size ranges are tied: {joined(labels)}, with {n_trials(trials)} each."
    return f"The most common size range is {labels[0]}, with {n_trials(trials)}."


def scatter_message(points: int, x_title: str, y_title: str) -> str:
    return f"{n_trials(points)} plotted, {y_title.lower()} against {x_title.lower()}."


def sort_phrase(sort_by: str, order: str) -> str:
    """How a list is ordered, in the direction it runs: `largest enrollment`, `start date, latest first`."""
    if sort_by == "enrollment":
        return "largest enrollment" if order == "desc" else "smallest enrollment"
    return f"{SORT_TITLES[sort_by]}, {'latest' if order == 'desc' else 'earliest'} first"


def table_message(listed: Sequence[tuple[str | None, int, int]], sort_by: str, order: str) -> str:
    """`listed` is (group, rows shown, trials that match) per scope; the list shows the first rows of each."""
    return f"{listed_phrase(listed)}, ordered by {sort_phrase(sort_by, order)}."


def network_message(nodes: int, links: int, strongest: Sequence[tuple[str, str]], trials: int) -> str:
    """`strongest` are the links that reach the highest weight, as (a, b) pairs."""
    head = f"{count(nodes)} nodes and {count(links)} links; "
    if len(strongest) > 1:
        pairs = joined([f"{a} with {b}" for a, b in strongest])
        return f"{head}{len(strongest)} links tie as the strongest, each in {n_trials(trials)}: {pairs}."
    a, b = strongest[0]
    return f"{head}the strongest link joins {a} and {b} in {n_trials(trials)}."


def interpretation_summary(
    analysis: str,
    groups: Sequence[str],
    labels: Sequence[str | None],
    unit: str | None,
    filters: Sequence[str] = (),
    measure: str | None = None,
) -> str:
    named = scope_suffix(labels)[2:] or "all trials"
    scope = f"{named} ({'; '.join(filters)})" if filters else named
    if measure is not None and analysis in ("aggregate", "total"):
        by = f" by {', then '.join(groups)}{f' (per {unit})' if unit else ''}" if groups else ""
        return f"Computing the {measure.lower()}{by} for {scope}."
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


def state_sites_note(countries: Sequence[str], *, is_open: bool) -> str:
    """Which sites a state grouping used: those in the countries the question names, else every site."""
    if countries and not is_open:
        return (
            f"States are those of sites in {' and '.join(countries)}, the "
            f"{'country' if len(countries) == 1 else 'countries'} the question names; sites elsewhere are "
            "not grouped, and a trial with no state at such a site is left out."
        )
    return (
        "The question names no country, so the sites of every country were grouped by state; a state name "
        "that two countries share is one group."
    )


STATE_SHARE_NOTE: Final = (
    "'Share of trials' is a share of the trials analysed (those with a state at a counted site). "
    "'Share of the bars' total' is a share of the sum of the bars drawn, which is more than the number of "
    "trials when a trial is in several states."
)


def partial_period(period: str, data_date: str) -> Note:
    return Note(code="partial_period", message=f"{period} is incomplete: data as of {data_date}.")


def future_periods(periods: Sequence[str], data_date: str) -> Note:
    """Periods after the data date are drawn: what a count there means, and does not."""
    where = periods[0] if len(periods) == 1 else f"{periods[0]} to {periods[-1]}"
    return Note(
        code="future_periods",
        message=f"{where} {'is' if len(periods) == 1 else 'are'} after the data date ({data_date}). Trials "
        "counted there have planned (estimated) dates, and a zero there says only that none is dated "
        "there yet.",
    )


def chart_preference_ignored(preference: str, chosen: str) -> Note:
    return Note(
        code="chart_preference_ignored",
        message=f"A {preference.replace('_', ' ')} does not fit this data, "
        f"so a {chosen.replace('_', ' ')} is shown.",
    )


def counts_not_reconciled(label: str | None, expected: int, counted: int) -> Note:
    scope = f" for {label}" if label else ""
    return Note(
        code="counts_not_reconciled",
        message=f"The registry reported {count(expected)} matching trials{scope} but {count(counted)} were "
        f"counted ({count(abs(expected - counted))} apart); the counted number is used.",
    )


def carried_over_assumption(scope: Sequence[str]) -> tuple[str, ...]:
    """The one sentence a follow-up adds when it kept scope of the previous question."""
    if not scope:
        return ()
    return (f"Kept from your previous question: {'; '.join(scope)}.",)


def upstream_throttled() -> Note:
    return Note(
        code="upstream_throttled",
        message="ClinicalTrials.gov limited the request rate recently, so this answer was fetched more "
        "slowly and, where it could, by reading whole pages of trials instead of counting groups one by one.",
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
# What a plotted number is, for a scatter plot: a trial without a value of a plotted field is left out.
NUMBER_NOTES: Final = {
    "enrollment": "Enrollment is the count in each record, actual or estimated; a trial with no enrollment "
    "count is left out.",
    "duration_months": "Duration runs from the start date to the completion date, whether each is actual or "
    "estimated, in calendar months with the days of the two dates as parts of a month; a date given only as "
    "a month counts as its first day, and a trial without both dates or one that completes before it starts "
    "is left out.",
    "site_count": "Sites are the locations listed in each record; a trial that lists no site is left out.",
}


# What a statistic is the statistic of, and which trials it leaves out.
MEASURE_NOTES: Final = {
    "enrollment": "Enrollment is the count in each record, actual or estimated; a trial with no enrollment "
    "count is left out, and a trial that reports zero (a withdrawn trial) is kept.",
    "duration_months": "Duration runs from the start date to the completion date, whether each is actual or "
    "estimated, in calendar months with the days of the two dates as parts of a month; a date given only as "
    "a month counts as its first day, and a trial without both dates or one that completes before it starts "
    "is left out.",
    "site_count": "Sites are the locations listed in each record; a trial that lists no site is left out. "
    "Within a country or a state, the sites counted are those in it.",
}


def measure_basis_note(field: str, basis: Mapping[str, int]) -> str | None:
    """How the values of a statistic are stated in the records: how many are planned and how many actual."""

    def split(prefix: str) -> str:
        parts = [
            f"{basis[key]:,} {word}"
            for word, key in (
                ("actual", f"{prefix}_actual"),
                ("estimated (planned)", f"{prefix}_estimated"),
                ("not stated as either", f"{prefix}_unstated"),
            )
            if basis.get(key)
        ]
        return ", ".join(parts)

    if field == "enrollment":
        found = split("enrollment")
        zeros = basis.get("enrollment_zero", 0)
        if not found:
            return None
        kept = f"; {zeros:,} of them are zero and kept" if zeros else ""
        return f"Enrollment counts used: {found}{kept}."
    if field == "duration_months":
        starts, ends = split("start"), split("end")
        if not starts and not ends:
            return None
        return f"Durations used: start dates {starts}; completion dates {ends}."
    return None


def overlapping_total_note(field: str, group_title: str) -> Note:
    """A sum of a number that belongs to the trial, drawn per group of a field that overlaps."""
    return Note(
        code="sum_over_overlapping_groups",
        message=f"Each trial's {MEASURE_PHRASES[field].replace(' per trial', '')} counts in full in every "
        f"{group_title.lower()} group it belongs to, so these totals overlap: they do not add up to the "
        f"grand total, and they are not the {NUMBER_TITLES[field].lower()} within each group.",
    )


def statistic_note(statistic: str, label: str) -> str:
    how = {
        "median": "The median is the middle value of the trials measured",
        "mean": "The mean is the sum of the values divided by the number of trials measured",
        "sum": "The total adds up the values of the trials measured",
    }[statistic]
    return (
        f"{label}: {how}. The number is computed here from the trial records that were read, not by the "
        "registry."
    )


def no_end_period(last: str) -> str:
    return f"No end year was given, so the axis stops at {last}, the period of the data."


def default_window(first: str) -> str:
    return f"No start year was given, so the axis starts at {first}; earlier trials are counted as before it."


def overlap_note(shared: int, labels: Sequence[str]) -> str:
    verb = "involves" if shared == 1 else "involve"
    return f"{n_trials(shared)} {verb} both {' and '.join(labels)} and appear in both groups."


def overlap_not_computed(groups: int) -> str:
    return (
        f"With {groups} compared groups the trials that belong to more than one are not counted; a trial "
        "can be in several groups, so the bars can add up to more than the trials that match."
    )


def compared_top_n_note() -> str:
    return (
        "With compared groups the categories shown are the largest by the trials of all groups together, "
        "the same for every group, so a trial in two groups counts twice in the ranking. 'Other (k more)' "
        "sums, in each group, the categories left out of the chart, and k is the number of categories left "
        "out of all groups together."
    )


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


SOURCE_URL_REASONS: Final = {
    "statistic": "A statistic is computed here from the trial records that were read; no search returns "
    "exactly the trials it was computed from, so its rows have no source_url.",
    "network": "A network's nodes and links count the drugs, conditions or sponsors read from the records; "
    "no search returns exactly the trials of a node or a link, so they have no source_url.",
    "rest": "The 'Other' row sums several values; no single search returns exactly its trials, so it has "
    "no source_url.",
    "state": "A state is searched by its name, which the registry matches more loosely than the spellings "
    "are grouped here; a state has a source_url only when the registry's own count for it equals the bar.",
    "other": "A row has no source_url where no search returns exactly its trials.",
}


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
