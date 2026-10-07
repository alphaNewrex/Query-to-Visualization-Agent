"""Ranking and headlines: what a top-N ranks by, what a superlative names, and what a tie says."""

from typing import Any

import pytest

from ctviz.catalog.fields import Window

from .engine_memory import Run, answer, dim, plan, public, record, rows_of

pytestmark = pytest.mark.anyio

SPONSORS = public(
    {
        "kind": "aggregate",
        "dimension": "sponsor",
        "series": None,
        "time_unit": None,
        "top_n": 2,
        "statistic": None,
        "of": None,
    }
)


def sponsors(counts: dict[str, int], **kwargs: Any) -> list[Any]:
    studies, number = [], 0
    for name, trials in counts.items():
        for _ in range(trials):
            number += 1
            studies.append(record(number, sponsor=name, **kwargs))
    return studies


async def bars(counts: dict[str, int], *, top_n: int = 2) -> Run:
    p = plan(dim("sponsor"), pub=SPONSORS, top_n=top_n)
    return await answer(p, sponsors(counts))


async def test_the_rest_of_a_top_n_is_never_named_the_largest_group() -> None:
    run = await bars({"Zed": 3, "Abe": 2, **{f"Small{n}": 1 for n in range(6)}})

    assert [row["sponsor"] for row in rows_of(run.response)] == ["Zed", "Abe", "Other (6 more)"]
    assert run.response.message == "Zed is the largest group: 3 of 11 trials (27.3%)."


async def test_groups_with_the_same_count_are_tied_and_all_named() -> None:
    run = await bars({"Zed": 3, "Abe": 3, "Bo": 1}, top_n=3)

    assert run.response.message == "Abe and Zed are tied as the largest groups: 3 of 7 trials each (42.9%)."


async def test_a_long_tie_is_cut_to_its_first_names() -> None:
    run = await bars({f"S{n}": 2 for n in range(6)}, top_n=15)

    assert run.response.message.startswith("S0, S1, S2 and 3 more are tied as the largest groups: 2 of 12")


async def test_a_top_n_ranks_by_trials_and_not_by_the_sum_of_a_multi_valued_split() -> None:
    """Abe has two trials with three intervention types each; Zed has three trials with one type each."""
    studies = [
        *(record(n, sponsor="Zed", types=("DRUG",)) for n in (1, 2, 3)),
        *(record(n, sponsor="Abe", types=("DRUG", "DEVICE", "PROCEDURE")) for n in (4, 5)),
        *(record(n, sponsor=f"Small{n}", types=("DRUG",)) for n in range(6, 12)),
    ]
    analysis = {
        "kind": "aggregate",
        "dimension": "sponsor",
        "series": "intervention_type",
        "time_unit": None,
        "top_n": 1,
        "statistic": None,
        "of": None,
    }
    p = plan(
        dim("sponsor"), dim("intervention_type", "series"), pub=public(analysis), relation="series", top_n=1
    )

    run = await answer(p, studies)

    kept = {row["sponsor"] for row in rows_of(run.response)}
    # Summing the cells would rank Abe (6) over Zed (3); Abe has 2 trials and Zed 3.
    assert kept == {"Zed", "Other (7 more)"}
    assert "Other" not in run.response.message
    assert run.response.message == "The largest group is Zed for Drug, with 3 trials."


# --- ties elsewhere -----------------------------------------------------------------------------------------


async def test_two_periods_that_share_the_peak_are_both_named() -> None:
    studies = [
        record(1, start="2020-01"),
        record(2, start="2021-01"),
        record(3, start="2021-02"),
        record(4, start="2022-01"),
        record(5, start="2022-02"),
        record(6, start="2023-01"),
    ]
    analysis = {
        "kind": "aggregate",
        "dimension": "start_date",
        "series": None,
        "time_unit": "year",
        "top_n": None,
        "statistic": None,
        "of": None,
    }
    p = plan(dim("start_date"), pub=public(analysis), window=Window("year", "2020", "2023"))

    run = await answer(p, studies)

    assert run.response.message == "1 trial started in 2023; the peak was 2, tied between 2021 and 2022."


async def test_the_latest_period_can_be_one_of_the_tied_peaks() -> None:
    studies = [record(1, start="2020-01"), record(2, start="2021-01"), record(3, start="2021-02")]
    studies += [record(4, start="2022-01"), record(5, start="2022-02")]
    analysis = {
        "kind": "aggregate",
        "dimension": "start_date",
        "series": None,
        "time_unit": "year",
        "top_n": None,
        "statistic": None,
        "of": None,
    }
    p = plan(dim("start_date"), pub=public(analysis), window=Window("year", "2020", "2022"))

    run = await answer(p, studies)

    assert run.response.message == (
        "2 trials started in 2022, tied with 2021 for the most of any period shown."
    )


async def test_series_that_share_the_peak_are_both_named() -> None:
    studies = [
        record(1, start="2020-01", types=("DRUG",)),
        record(2, start="2020-02", types=("DEVICE",)),
        record(3, start="2021-01", types=("DRUG",)),
    ]
    analysis = {
        "kind": "aggregate",
        "dimension": "start_date",
        "series": "intervention_type",
        "time_unit": "year",
        "top_n": None,
        "statistic": None,
        "of": None,
    }
    p = plan(
        dim("start_date"),
        dim("intervention_type", "series"),
        pub=public(analysis),
        relation="series",
        window=Window("year", "2020", "2021"),
    )

    run = await answer(p, studies)

    assert (
        run.response.message
        == "Drug in 2020, Device in 2020 and Drug in 2021 are tied at the peak of 1 trial started."
    )


async def test_enrollment_bins_with_the_same_count_are_tied() -> None:
    studies = [record(1, enrollment=5), record(2, enrollment=500)]
    analysis = {
        "kind": "aggregate",
        "dimension": "enrollment",
        "series": None,
        "time_unit": None,
        "top_n": None,
        "statistic": None,
        "of": None,
    }
    p = plan(dim("enrollment"), pub=public(analysis))

    run = await answer(p, studies)

    assert run.response.message.startswith("The most common size ranges are tied: ")
    assert run.response.message.endswith("with 1 trial each.")


async def test_links_that_share_the_greatest_weight_are_all_counted() -> None:
    studies = [
        record(n, countries=pair)
        for n, pair in enumerate(
            [("A", "B"), ("A", "B"), ("C", "D"), ("C", "D"), ("E", "F"), ("E", "F")], start=1
        )
    ]
    p = plan(
        dim("country", "node"),
        dim("country", "node"),
        pub=public({"kind": "network", "source": "country", "target": "country", "link": None}),
        relation="network",
    )

    run = await answer(p, studies)

    assert run.response.message == (
        "6 nodes and 3 links; 3 links tie as the strongest, each in 2 trials: "
        "A with B, C with D and E with F."
    )


async def test_an_edge_does_not_cite_the_same_string_for_both_ends() -> None:
    from ctviz.catalog.drugs import DrugNormalizer

    studies = [
        record(1, names=["alpha"], arms={"alpha": []}),
        record(2, names=["beta"], arms={"beta": []}),
        record(3, names=["alpha + beta"], arms={"alpha + beta": []}),
        record(4, names=["alpha + beta"], arms={"alpha + beta": []}),
    ]
    assert DrugNormalizer.fit(studies).known >= {"alpha", "beta"}
    p = plan(
        dim("drug", "node"),
        dim("drug", "node"),
        pub=public({"kind": "network", "source": "drug", "target": "drug", "link": "same_arm"}),
        relation="network",
        pairing="same_arm",
    )
    from .engine_memory import execute

    _, result, _, _ = await execute(p, studies, prefer_walk=True)

    (link,) = [cell for cell in result.frames[0].cells.values() if cell.key == ("alpha", "beta")]
    cited = [trial.evidence for trial in link.sample]
    assert cited and all(len(items) == len(set(items)) == 1 for items in cited)


async def test_a_top_n_under_comparison_says_it_ranks_by_the_groups_together() -> None:
    from ctviz.ctgov import essie
    from ctviz.ctgov.params import BoundTerm

    from .engine_memory import scope

    def group(name: str, scope_id: str) -> Any:
        term = BoundTerm(
            kind="country", text=name, term=name, parameter=None,
            expr=essie.area("LocationCountry", name), definition="country_exact", note="",
        )  # fmt: skip
        return scope(name, scope_id, terms=(term,))

    studies = [
        record(n, sponsor=f"S{n % 4}", countries=("France", "Spain") if n % 2 else ("France",))
        for n in range(1, 13)
    ]
    p = plan(
        dim("sponsor"),
        pub=SPONSORS,
        top_n=2,
        scopes=(group("France", "s0"), group("Spain", "s1")),
        compare_kind="country",
    )

    run = await answer(p, studies)

    rules = [item.rule for item in run.response.meta.truncation.items]
    assert rules == ["The 2 largest by trials of all compared groups together, the rest summed as Other."]
    assert any("largest by the trials of all groups together" in a for a in run.response.meta.assumptions)
