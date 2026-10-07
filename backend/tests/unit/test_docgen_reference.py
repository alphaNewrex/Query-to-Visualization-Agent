"""The Markdown reference, the example index and the README blocks: what they say, and that the committed
copies are fresh."""

import json
import re
import shutil
from pathlib import Path
from typing import Any, get_args

import pytest

from ctviz import docgen
from ctviz.catalog.fields import CATALOG
from ctviz.contract.plan import ChartType
from ctviz.errors import HTTP_MAPPING, ErrorCode

CELL_SEPARATOR = re.compile(r"(?<!\\)\|")


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    return docgen.json_schema(docgen.Contract)


@pytest.fixture(scope="module")
def reference(schema: dict[str, Any]) -> str:
    return docgen.schema_reference(schema)


def cells(line: str) -> list[str]:
    return [cell.strip() for cell in CELL_SEPARATOR.split(line.strip())[1:-1]]


def table_under(markdown: str, heading: str) -> list[list[str]]:
    """The rows of the first table after a heading, header first, as lists of cells."""
    lines = markdown.splitlines()
    start = lines.index(heading) + 1
    rows = []
    for line in lines[start:]:
        if line.startswith("|"):
            rows.append(cells(line))
        elif rows:
            break
    return rows


def by_field(rows: list[list[str]]) -> dict[str, list[str]]:
    return {row[0].strip("`"): row for row in rows[2:]}


# --- The reference --------------------------------------------------------------------------------------


def test_the_query_request_table_marks_query_required_and_prints_its_bounds(reference: str) -> None:
    rows = table_under(reference, "### QueryRequest")

    assert rows[0] == ["Field", "Type", "Required", "Default", "Constraints", "Description"]
    query = by_field(rows)["query"]
    assert query[2] == "yes"
    assert query[4] == "1 to 1,000 characters"


def test_defaults_and_limits_are_read_inside_unions_and_arrays(reference: str) -> None:
    request = by_field(table_under(reference, "### QueryRequest"))
    options = by_field(table_under(reference, "### RequestOptions"))

    assert request["drug_name"][2:5] == ["no", "`null`", "at most 5 items; each 1 to 200 characters"]
    assert request["top_n"][4] == "1 to 50"
    assert request["start_year"][4] == "1900 to 2100"
    assert request["group_by"][4] == "1 to 2 items"
    assert options["citations_per_datum"][3:5] == ["`5`", "0 to 20"]
    assert options["planner"][3] == "`llm`"
    assert options["include_trace"][3] == "`true`"


def test_everything_a_client_can_send_has_six_columns_and_the_rest_three(
    schema: dict[str, Any], reference: str
) -> None:
    sendable = docgen.request_side(schema["$defs"])

    assert {
        "QueryRequest",
        "RequestOptions",
        "CompareSpec",
        "AnalysisRequest",
        "QueryPlan",
        "Entity",
    } <= sendable
    assert not {"Meta", "Datum", "VisualizationResponse", "LabeledRequest", "ErrorBody"} & sendable
    assert len(table_under(reference, "### QueryPlan")[0]) == 6
    assert len(table_under(reference, "### Meta")[0]) == 3


def test_every_definition_of_the_contract_is_documented_once(schema: dict[str, Any], reference: str) -> None:
    headings = re.findall(r"^### (\w+)$", reference, re.MULTILINE)

    assert sorted(headings) == sorted(schema["$defs"])


def test_a_definition_that_no_section_lists_is_still_documented(schema: dict[str, Any]) -> None:
    extra = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}
    grown = {**schema, "$defs": {**schema["$defs"], "Surprise": extra}}

    text = docgen.schema_reference(grown)

    assert "## Other types" in text
    assert table_under(text, "### Surprise")[2][0] == "`x`"


def test_a_listed_name_the_contract_no_longer_has_is_left_out(schema: dict[str, Any]) -> None:
    shrunk = {**schema, "$defs": {k: v for k, v in schema["$defs"].items() if k != "CompareSpec"}}
    shrunk["$defs"]["QueryRequest"] = {**shrunk["$defs"]["QueryRequest"], "properties": {}}
    shrunk["$defs"]["AnalysisRequest"] = {**shrunk["$defs"]["AnalysisRequest"], "properties": {}}

    text = docgen.schema_reference(shrunk)

    assert "### CompareSpec" not in text


def test_fields_are_listed_in_the_order_the_models_declare_them(reference: str) -> None:
    request = [row[0].strip("`") for row in table_under(reference, "### QueryRequest")[2:]]
    bar_chart = [row[0].strip("`") for row in table_under(reference, "### BarChart")[2:]]

    assert request[:4] == ["query", "drug_name", "condition", "sponsor"]
    assert request[-1] == "options"
    assert bar_chart == ["type", "title", "subtitle", "orientation", "stack", "encoding", "data"]


def test_a_union_lists_its_shapes_by_their_tag(reference: str) -> None:
    shapes = {row[0]: row[1] for row in table_under(reference, "### QueryResponse")[2:]}

    assert shapes["`visualization`"] == "[`VisualizationResponse`](#visualizationresponse)"
    assert shapes["`conversation`, `no_data`, `unsupported`"] == "[`MessageResponse`](#messageresponse)"
    assert len(table_under(reference, "### Visualization")[2:]) == 7


def test_a_row_may_carry_any_other_key_and_says_so(reference: str) -> None:
    datum = by_field(table_under(reference, "### Datum"))

    assert list(datum) == ["citations", "citation_count", "source_url", "*(any other key)*"]
    assert datum["*(any other key)*"][1] == "string \\| integer \\| number \\| boolean \\| null"


def test_every_table_row_has_as_many_cells_as_its_header(reference: str) -> None:
    width = 0
    for line in reference.splitlines():
        if not line.startswith("|"):
            width = 0
        elif width == 0:
            width = len(cells(line))
        else:
            assert len(cells(line)) == width, line


def test_links_point_at_headings_that_exist(reference: str) -> None:
    anchors = {
        re.sub(r"[^a-z0-9 -]", "", h.lower()).replace(" ", "-")
        for h in re.findall(r"^#+ (.+)$", reference, re.M)
    }
    targets = set(re.findall(r"\]\(#([a-z0-9-]+)\)", reference))

    assert targets <= anchors


def test_a_pipe_in_a_description_cannot_break_a_table() -> None:
    assert docgen._cell("a | b\n  c") == "a \\| b c"


@pytest.mark.parametrize(
    ("low", "high", "unit", "expected"),
    [
        (3, 1000, "characters", "3 to 1,000 characters"),
        (None, 5, "items", "at most 5 items"),
        (2, None, "items", "at least 2 items"),
        (1, 1, "items", "exactly 1 item"),
        (1900, 2100, "", "1900 to 2100"),
        (0, 20, "", "0 to 20"),
    ],
)
def test_a_range_reads_as_words(low: int | None, high: int | None, unit: str, expected: str) -> None:
    assert docgen._range(low, high, unit) == expected


def test_a_long_enum_can_be_shown_in_part() -> None:
    values = {"enum": list("abcdef"), "type": "string"}

    assert docgen._type(values) == "`a` \\| `b` \\| `c` \\| `d` \\| `e` \\| `f`"
    assert docgen._type(values, docgen.Style(preview=4)) == "`a` \\| `b` \\| `c` \\| ... 6 values"


# --- The tables of the README ---------------------------------------------------------------------------


def test_the_types_table_has_one_row_per_visualization_type(schema: dict[str, Any]) -> None:
    rows = docgen.response_types_table(schema).splitlines()[2:]

    assert [cells(row)[0].strip("`") for row in rows] == list(get_args(ChartType))
    assert "`orientation`" in rows[0]


def test_the_errors_table_has_every_code_with_its_status_and_the_proxys_code() -> None:
    rows = {cells(row)[0].strip("`"): cells(row) for row in docgen.errors_table().splitlines()[2:]}

    assert set(rows) == {str(code) for code in ErrorCode} | {"backend_unreachable"}
    assert "(45 s by default)" in rows["deadline_exceeded"][3]
    for code in ErrorCode:
        assert rows[str(code)][1] == str(HTTP_MAPPING[code].http_status)
        assert rows[str(code)][2] == ("yes" if HTTP_MAPPING[code].is_retryable else "no")


def test_the_capabilities_tables_list_every_dimension_and_the_limits() -> None:
    tables = docgen.capabilities_tables()
    keys = [cells(row)[0].strip("`") for row in tables.splitlines()[2 : 2 + len(CATALOG)]]

    assert keys == list(CATALOG)
    assert "reads every matching trial" in tables and "`network_graph`" in tables


# --- The recorded examples ------------------------------------------------------------------------------


@pytest.fixture
def recorded(real_repository_root: Path, tmp_path: Path) -> Path:
    """A copy of the committed examples that a test may change."""
    copy = tmp_path / "examples"
    shutil.copytree(
        real_repository_root / "docs" / "examples", copy, ignore=shutil.ignore_patterns("README.md")
    )
    return copy


def test_every_recorded_run_is_read_with_its_headline_and_counts(recorded: Path) -> None:
    runs = docgen.read_examples(recorded)
    response = json.loads((recorded / runs[0].slug / "response.json").read_text(encoding="utf-8"))

    assert [run.slug[:2] for run in runs] == [f"{number:02d}" for number in range(1, len(runs) + 1)]
    assert runs[0].headline == response["message"]
    assert runs[0].model == response["meta"]["planner"]["model"]
    assert "drug_name: Pembrolizumab" in runs[0].question
    assert runs[-1].kind == "clarification" and runs[-1].trials_matched == "-"


def test_a_comparison_shows_the_count_of_each_group(recorded: Path) -> None:
    run = next(run for run in docgen.read_examples(recorded) if run.slug == "02-compare-phases")

    assert re.fullmatch(r"pembrolizumab: [\d,]+; nivolumab: [\d,]+", run.trials_matched)
    assert run.variant == "grouped"


def test_the_index_has_a_row_per_run_and_says_how_to_replay(recorded: Path) -> None:
    index = docgen.examples_index(recorded)
    rows = [cells(row) for row in index.splitlines() if row.startswith("| [")]

    assert len(rows) == len(docgen.read_examples(recorded))
    assert rows[0][0] == "[01-assignment-request](01-assignment-request/response.json)"
    assert "/v1/analyses" in index and "request.json" in index


def bars(stack: str, orientation: str, series: dict[str, int] | None) -> dict[str, Any]:
    return {"type": "bar_chart", "stack": stack, "orientation": orientation, "encoding": {"series": series}}


@pytest.mark.parametrize(
    ("visualization", "expected"),
    [
        (bars("stacked", "vertical", {"x": 1}), "stacked"),
        (bars("none", "vertical", {"x": 1}), "grouped"),
        (bars("none", "horizontal", None), "horizontal"),
        ({"type": "time_series", "mark": "area"}, "area"),
        ({"type": "network_graph", "layout": "force"}, "force"),
        ({"type": "table"}, ""),
    ],
)
def test_a_run_is_labelled_by_how_it_is_drawn(visualization: dict[str, Any], expected: str) -> None:
    assert docgen._variant(visualization) == expected


def strings(node: Any) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, list):
        return [text for item in node for text in strings(item)]
    if isinstance(node, dict):
        return [text for item in node.values() for text in strings(item)]
    return []


# --- Splicing the README --------------------------------------------------------------------------------


def block(name: str, body: str) -> str:
    return f"<!-- gen:{name}:start -->\n{body}\n<!-- gen:{name}:end -->"


def test_splice_replaces_only_the_text_between_the_markers() -> None:
    text = f"# Title\n\nby hand\n\n{block('one', 'old')}\n\nalso by hand\n\n{block('two', 'older')}\n"

    spliced = docgen.splice(text, {"one": "new\nlines\n", "two": "newer"})

    assert (
        spliced
        == f"# Title\n\nby hand\n\n{block('one', 'new\nlines')}\n\nalso by hand\n\n{block('two', 'newer')}\n"
    )
    assert docgen.splice(spliced, {"one": "new\nlines", "two": "newer"}) == spliced


def test_a_missing_or_an_unknown_block_is_an_error() -> None:
    text = block("one", "old")

    with pytest.raises(docgen.DocumentError, match="two"):
        docgen.splice(text, {"one": "a", "two": "b"})
    with pytest.raises(docgen.DocumentError, match="one"):
        docgen.splice(text, {})


def test_the_readme_is_found_whatever_the_case_of_its_name(tmp_path: Path) -> None:
    assert docgen.find_readme(tmp_path) is None
    (tmp_path / "readme.md").write_text("x", encoding="utf-8")
    assert docgen.find_readme(tmp_path) == tmp_path / "readme.md"


# --- Every document -------------------------------------------------------------------------------------


def test_the_committed_documents_are_up_to_date(real_repository_root: Path) -> None:
    stale = docgen.stale_documents(real_repository_root)

    assert stale == [], "run `make docs`"


def test_written_documents_are_fresh_and_a_changed_one_is_stale(
    real_repository_root: Path, tmp_path: Path
) -> None:
    root = tmp_path / "repository"
    shutil.copytree(real_repository_root / "docs" / "examples", root / "docs" / "examples")
    (root / "README.md").write_text(
        "".join(f"{block(name, 'x')}\n" for name in docgen.readme_blocks()),
        encoding="utf-8",
    )

    written = docgen.write_documents(root)

    assert {path.name for path in written} >= {
        "SCHEMA.md",
        "README.md",
        "openapi.json",
        "contract.v1.schema.json",
    }
    assert docgen.stale_documents(root) == []
    (root / "docs" / "SCHEMA.md").write_text("by hand", encoding="utf-8")
    (root / "README.md").write_text(
        (root / "README.md").read_text(encoding="utf-8").replace("`bar_chart`", "`bar`"), encoding="utf-8"
    )
    assert [path.name for path in docgen.stale_documents(root)] == ["SCHEMA.md", "README.md"]
