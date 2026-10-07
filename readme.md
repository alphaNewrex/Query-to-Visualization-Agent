# CTViz Agent: ClinicalTrials.gov question to visualization

Ask a question about clinical trials in plain English and get back a chart specification as JSON, built from live [ClinicalTrials.gov](https://clinicaltrials.gov) data. Every value in the chart comes with the trial records behind it.

A language model reads the question and writes a small typed plan. Ordinary code does the rest: it calls the registry, counts the trials, picks one of seven visualization types and attaches the citations. The backend is FastAPI, and a Next.js chat UI is included as a demo.

**Demo video:** [a short walkthrough of the service and its UI](https://drive.google.com/file/d/1SDQznpA6DpLR_ksyTSkuI1vzYERXm6uG/view?usp=share_link) (Google Drive).

![A follow-up that keeps the drug and the start year and changes the split](docs/screenshots/conversation-follow-up.png)
*A question and a follow-up, "now split that by phase". The chips show what the follow-up kept and what it changed.*

![Sponsors and drugs in Duchenne muscular dystrophy trials, with the citation panel open on the node Ataluren](docs/screenshots/citation-panel-selected-node.png)
*A sponsor-drug network. Clicking a node lists the trials behind it, with the record field and the exact text that was counted.*

The chart types the assignment lists, drawn by the demo UI from real answers:

| Bar chart | Grouped bar chart | Time series |
| --- | --- | --- |
| ![Duchenne muscular dystrophy trials by phase, as bars](docs/screenshots/bar-chart-phases.png) | ![Pembrolizumab and nivolumab trials by phase, as grouped bars](docs/screenshots/comparison-grouped-bars.png) | ![Pembrolizumab trials started per year, as a line](docs/screenshots/time-series-per-year.png) |
| **Scatter plot** | **Histogram** | **Network graph** |
| ![Enrollment against duration for completed Duchenne trials, one point per trial](docs/screenshots/scatter-enrollment-duration.png) | ![Enrollment sizes of pembrolizumab trials, in bins](docs/screenshots/histogram-enrollment.png) | ![Sponsors and the drugs they test in Duchenne trials, as a two-sided network](docs/screenshots/network-graph-sponsors-drugs.png) |

The service also returns tables and single numbers. More screenshots are in [`docs/screenshots/`](docs/screenshots/).

## How to run

With Docker (Compose 2.24 or newer):

```bash
cp .example.env .env              # then put your OpenAI key in .env
docker compose up --build --wait
```

- UI: http://localhost:3000
- API and Swagger UI: http://localhost:8000/docs

Stop it with `docker compose down`.

Without Docker you need [uv](https://docs.astral.sh/uv/), Node 22 and pnpm:

```bash
make setup
cp .example.env .env
make dev      # API on http://127.0.0.1:8000, UI on http://localhost:3000
```

**Configuration.** `.env` holds `OPENAI_API_KEY`, `OPENAI_API_BASE` and `ALLOWED_MODELS`. The planner uses `gpt-5.4-mini` and falls back to `gpt-4.1-mini`. Every other setting has a default and is defined in [`backend/src/ctviz/settings.py`](backend/src/ctviz/settings.py).

**Without a key** the service still does everything that does not need the model. It serves the ten recorded examples and the schemas, and `POST /v1/analyses` runs a typed plan against the live registry. A free-text question returns `503 planner_unavailable`.

**Tests.** `make test` runs about 1,150 backend and 200 frontend tests offline, with no key. `make check` adds linting, type checks and a check that the generated docs are current. `make zip` builds the submission archive.

## How it works

Here is the assignment's own example, end to end. The full recorded run is in [`docs/examples/01-assignment-request/`](docs/examples/01-assignment-request/).

```bash
curl -s -X POST http://127.0.0.1:8000/v1/query -H 'Content-Type: application/json' \
  -d '{"query": "How has the number of trials for this drug changed over time?", "drug_name": "Pembrolizumab"}'
```

1. **Plan.** One OpenAI call with a strict output schema turns the question into a `QueryPlan`. The model sees the question, today's date and the request fields. It never sees trial data and it has no tools.

   ```json
   {"entities": [{"kind": "drug", "value": "Pembrolizumab", "role": "filter"}],
    "analysis": {"kind": "aggregate", "dimension": "start_date", "time_unit": "year"}}
   ```

2. **Check.** Code validates the plan against the words of the question, using 29 plan rules. It fixes what it can and sends the plan back to the model once if needed. A plan that still fails becomes a clarification.
3. **Resolve.** Each name is looked up in the registry. "Pembrolizumab" matches 2,971 trials as an intervention.
4. **Fetch.** The registry has no group-by, so the service gets exact numbers in one of two ways. For plain counts it asks the registry for one count per bucket, here one per start year. When the answer needs the records themselves, as a median, a network or a scatter plot does, it pages through every matching trial.
5. **Build.** Rules pick the chart type from the shape of the plan, and a single date dimension becomes a line. Titles, labels and notes come from templates. The sample trials returned with each count become the citations.
6. **Verify.** 17 invariants are checked before the response goes out. Two of them: every cited NCT ID was returned by the registry in this request, and every excerpt equals the value in the record. A failure is a 500, not a wrong answer.

This run made 36 registry requests and took about 7 seconds, half of it the model call. The response, heavily shortened:

```json
{
  "kind": "visualization",
  "message": "259 trials started in 2025; the peak was 299 in 2022 ...",
  "visualization": {
    "type": "time_series",
    "title": "Trials started per year: Pembrolizumab",
    "encoding": {
      "x": {"field": "start_year", "type": "temporal", "title": "Start year", "time_unit": "year"},
      "y": {"field": "trial_count", "type": "quantitative", "title": "Trials started", "unit": "trials"}
    },
    "data": [
      {"start_year": "2016", "trial_count": 195, "citation_count": 195,
       "citations": [{"nct_id": "NCT02852655",
                      "field": "protocolSection.statusModule.startDateStruct.date",
                      "excerpt": "2016-09-21"}],
       "source_url": "https://clinicaltrials.gov/api/v2/studies?query.intr=Pembrolizumab&filter.advanced=..."}
    ]
  },
  "references": {"NCT02852655": {"title": "...", "url": "https://clinicaltrials.gov/study/NCT02852655"}},
  "meta": {"filters": {"drug_name": ["Pembrolizumab"]}, "...": "plan, warnings, counts, source, timing"}
}
```

The model only makes choices. It decides which words name a drug, a condition, a sponsor or a country, which filters the question states, and what kind of analysis is wanted. Code produces everything else: the API parameters, the numbers, the rows, the titles and sentences, the NCT IDs and the citations.

Follow-ups work without a session. The client sends the previous question and plan back, and the model writes a complete new plan. A greeting or small talk gets a templated reply and no registry call.

### Data flow

```
 Browser
    │  HTTP, same origin
    ▼
 Next.js frontend :3000      serves the chat UI and proxies /api/backend/* to the backend
    │  JSON, plus Server-Sent Events for live progress
    ▼
 FastAPI backend :8000       one process, caches in memory
    │
    ├──▶ OpenAI Responses API            one call per free-text question
    │      sends:    the question, today's date, the request fields,
    │                and the previous question and plan in a follow-up
    │      returns:  a QueryPlan. No trial data is sent, and store=false.
    │      GET /models is called by /readyz only
    │
    └──▶ ClinicalTrials.gov Data API v2  read-only GET, no key
           GET /version                    data version, part of every cache key
           GET /studies?countTotal=true    exact counts, one request per bucket,
                                           with sample trials for the citations
           GET /studies?pageSize=1000      record reads, when the answer needs the records
```

`POST /v1/query/stream` runs the same pipeline and sends each step as a Server-Sent Event, which is how the UI shows live progress. Plans and responses are cached for a day and registry calls for 15 minutes. Registry traffic is limited to 8 requests per second.

## API reference

| Endpoint | Purpose | Request | Response |
| --- | --- | --- | --- |
| `POST /v1/query` | Answer a question | [`QueryRequest`](docs/SCHEMA.md#requests) | [`QueryResponse`](docs/SCHEMA.md#queryresponse) |
| `POST /v1/query/stream` | The same, with live progress | `QueryRequest` | Server-Sent Events: `stage` events, then one `result` or `error` |
| `POST /v1/analyses` | Run a plan without the model | [`AnalysisRequest`](docs/SCHEMA.md#analysisrequest) | `QueryResponse` |
| `GET /v1/examples` | List the recorded runs | none | JSON list |
| `GET /v1/examples/{slug}` | One recorded run | none | `request`, `plan`, `response` |
| `GET /v1/capabilities` | Dimensions and limits | none | JSON object |
| `GET /v1/schema/contract` | The contract as JSON Schema | none | JSON Schema |
| `GET /healthz`, `GET /readyz` | Liveness and readiness | none | JSON status |
| `GET /docs`, `GET /openapi.json` | Swagger UI and the OpenAPI document | none | HTML, JSON |

The full schema, generated from the code:

- Swagger UI at http://localhost:8000/docs while the service runs
- [`docs/schema/openapi.json`](docs/schema/openapi.json), the same OpenAPI document saved in the repo
- [`docs/schema/contract.v1.schema.json`](docs/schema/contract.v1.schema.json), the request and response types as JSON Schema
- [`docs/SCHEMA.md`](docs/SCHEMA.md), the same as readable tables

Errors always have one shape, [`ErrorResponse`](docs/SCHEMA.md#errorresponse): `{"error": {"code", "message", "details", "request_id", "is_retryable"}}`. An invalid request is a 422. Trouble reaching the registry is a 502, 503 or 504, and a missing model key is a 503.

## Request schema

`POST /v1/query` takes a `QueryRequest`. Only `query` is required. An unknown key is rejected with a 422, and a structured field always wins over what the model reads from the question.

| Field | Type | Meaning |
| --- | --- | --- |
| `query` (required) | string, 1 to 1,000 characters | The question. "This drug" refers to `drug_name` |
| `drug_name`, `condition`, `sponsor`, `country`, `term` | string, or a list of up to 5 | Trials must match all of them |
| `trial_phase`, `status`, `study_type`, `sponsor_class`, `intervention_type`, `sex`, `age_group`, `allocation`, `masking`, `primary_purpose`, `has_results`, `intervention_model` | list of enum values | Trials must match any of the values. Spellings such as "Phase 2" or "II" are accepted |
| `start_year`, `end_year` | integer, 1900 to 2100 | Year range |
| `date_field` | `start_date`, `primary_completion_date`, `completion_date` or `first_posted_date` | The date the year range applies to. Default: start date |
| `compare` | `{field, values}` with 2 to 5 values | Put drugs, conditions, sponsors or countries side by side |
| `exclude` | `{drug_name, condition, sponsor, country, term, status}` | Leave out trials that match |
| `group_by`, `time_unit`, `top_n`, `chart_type` | see [`docs/SCHEMA.md`](docs/SCHEMA.md#requests) | Optional hints that override the planner's choice |
| `previous` | `{query, plan}` | The previous turn, for a follow-up |
| `options` | object | `planner`, `citations_per_datum` (0 to 100, default 5), `drug_match`, `include_trace`, `use_cache` |

Trials can be grouped by 22 dimensions, among them phase, status, country, state, sponsor, drug, condition, four dates and enrollment. Median, mean and sum are available for enrollment, duration and site count. `GET /v1/capabilities` returns the exact lists and limits.

## Response schema

Every response has the same seven keys: `spec_version`, `kind`, `message`, `visualization`, `clarification`, `references` and `meta`. `kind` says which case it is.

| `kind` | Meaning |
| --- | --- |
| `visualization` | The question was answered, and `visualization` holds the specification |
| `clarification` | Something is missing or unclear, such as "this drug" with no drug named. `clarification.options` are complete requests a button can post |
| `no_data` | The plan ran and nothing matched |
| `unsupported` | Not about trials, or an analysis the service cannot do |
| `conversation` | A greeting or a question about the service |

`visualization` is `null` for every kind but the first.

A visualization has `type`, `title`, `subtitle`, `encoding` and `data`. `encoding` maps each visual channel to a key in the data rows, with units, number formats and sort order. `data` is flat rows that are already aggregated, sorted and zero-filled, so a renderer only has to draw them.

<!-- gen:response-types:start -->
| `type` | Extra keys | `encoding` keys | `data` |
| --- | --- | --- | --- |
| `bar_chart` | `orientation`: `vertical` \| `horizontal`; `stack`: `none` \| `stacked` | `x`, `y`, `series`, `tooltip` | One row per (x, series), following `x.domain`; with a series every combination is present. |
| `time_series` | `mark`: `line` \| `bar` \| `area`; `stack`: `none` \| `stacked` | `x`, `y`, `series`, `tooltip` | One row per (period, series), ascending and zero-filled. |
| `histogram` | none | `x`, `x2`, `y`, `label`, `tooltip` | One row per bin, contiguous and ascending. Bins may be uneven: draw one bar per row using the label. |
| `scatter_plot` | none | `x`, `y`, `series`, `size`, `label`, `tooltip` | One row per trial; each row cites itself with the fields its coordinates came from. |
| `network_graph` | `is_directed`: `false`; `layout`: `force` \| `bipartite` | `nodes`, `edges` | Nodes and links; `source` and `target` of a link are node ids. |
| `table` | none | `columns` | One row per record; `href_field` names a row key holding a URL. |
| `metric` | none | `value` | A single number, with citations and `source_url` like any datum. |
<!-- gen:response-types:end -->

A category channel carries `is_exclusive: false` when one trial can fall under several values, as with countries or drugs. Those values should not be drawn as parts of a whole.

**Citations.** Every datum (a bar, a point, a node, a link) carries `citations`, `citation_count` and `source_url`. A citation is `{nct_id, field, excerpt}`: the trial, the path of the field in its registry record, and the exact value found there. `citation_count` is the number of trials behind the datum. Five of them are cited by default, and `source_url` is a registry query that returns all of them, where such a query exists. Titles and links of the cited trials are in `references`.

**Metadata.** `meta` is not needed to draw the chart. It records how the answer was produced: the filters applied, the plan, assumptions, warnings, trial counts, every upstream request and the timings. It is described field by field in [`docs/SCHEMA.md`](docs/SCHEMA.md#metadata).

## Key design decisions and tradeoffs

- **The model writes a plan and never data.** It cannot invent a number, a trial or an NCT ID, because it never produces one. The cost is flexibility. A question outside the plan's vocabulary gets a clarification or an "unsupported" reply, not an improvised answer.
- **Two exact ways to read the registry.** One count request per bucket is exact at any size. A paged read of every matching trial covers what counts cannot: networks, scatter plots, medians and open-ended groupings such as sponsor. Tests require both to give the same result on the same records. Nothing is sampled. A question too broad to read within the 45 second deadline is answered with a request to narrow it.
- **One field catalogue and one engine.** Every chart comes from the same aggregation over 22 dimensions. A trend is a date dimension, a comparison is several scopes and a network is two dimensions. Adding a dimension is one catalogue entry.
- **Every answer can be replayed.** A response returns the plan it ran, and `POST /v1/analyses` runs a plan without the model. The same plan on the same data version gives the same chart.
- **Responses are verified before they are sent.** The 17 invariants cover what JSON Schema cannot, including that citations point at real records with matching text.
- **Text comes from templates.** No sentence written by the model reaches a title, a headline or a warning.
- **No session state.** The client sends the previous plan back for a follow-up, so a follow-up can be tested like any other request.
- **What I rejected.** A free tool-calling loop, because the model would read data and write numbers. A generative-UI layer, because the model would retype data rows. A bulk download into a local database, because it goes stale against the registry's weekday refreshes. FHIR as the data source, because it serves one study per request.

## Example runs

There are ten recorded runs. Each folder in [`docs/examples/`](docs/examples/) holds the request, the plan and the complete, unedited response. The index is [`docs/examples/README.md`](docs/examples/README.md).

| Run | Question | Visualization |
| --- | --- | --- |
| [01](docs/examples/01-assignment-request/response.json) | How has the number of trials for this drug changed over time? (`drug_name`: Pembrolizumab) | time series |
| [02](docs/examples/02-compare-phases/response.json) | Compare phases for trials involving pembrolizumab vs nivolumab. | grouped bars |
| [03](docs/examples/03-recruiting-by-country/response.json) | Which countries have the most recruiting trials for lung cancer? | horizontal bars |
| [04](docs/examples/04-sponsor-drug-network/response.json) | Show a network of sponsors and drugs for Duchenne muscular dystrophy trials. | two-sided network |
| [05](docs/examples/05-drug-cooccurrence/response.json) | Which drugs frequently co-occur in combination studies with pembrolizumab? | network |
| [06](docs/examples/06-enrollment-histogram/response.json) | What is the distribution of enrollment sizes for pembrolizumab trials? | histogram |
| [07](docs/examples/07-enrollment-vs-duration/response.json) | Plot enrollment against duration for completed Duchenne muscular dystrophy trials. | scatter plot |
| [08](docs/examples/08-largest-trials/response.json) | List the 10 largest lung cancer trials. | table |
| [09](docs/examples/09-recruiting-count/response.json) | How many recruiting trials are there for Duchenne muscular dystrophy? | single number |
| [10](docs/examples/10-no-drug-named/response.json) | The first question again, with no drug named | clarification |

Any of them can be replayed without the model:

```bash
jq -c '{plan: .meta.plan, options: .meta.options}' docs/examples/04-sponsor-drug-network/response.json \
  | curl -s -X POST http://127.0.0.1:8000/v1/analyses -H 'Content-Type: application/json' -d @- \
  | jq -r .message
```

## How correctness was validated

- **Offline tests.** About 1,150 backend and 200 frontend tests run with the network blocked. They cover query escaping, odd real records, every plan rule and most of the invariants, and they check that the two data paths agree on the same records.
- **Citation audit.** A script re-fetched every cited record for 68 responses. All 3,832 cited trials exist, and all 7,045 excerpts match the registry. The audit also found real defects, which are fixed: some scatter points cited the wrong evidence, and some `source_url` totals did not match their datum.
- **Aggregation audit.** 533 questions were answered by the service and recomputed independently from the raw registry records. 24 cases were flagged, mostly top-N and "Other" counts under a comparison, and fixed with tests. The full batch was not run again after the fixes.
- **Hard prompts.** Of 13 deliberately difficult questions, 12 produced a chart and 1 was refused because it asked for three measures at once. A few of the 12 were imperfect. One added a split that nobody asked for.
- **Replays.** Running the ten recorded plans again against the live registry reproduced the recorded answers.

## Limitations and what I would improve

- A drug class is not expanded into its members. "GLP-1 receptor agonists" searches for that phrase only, and the response warns about it.
- Drug aliases are not merged. A code name and a generic name count as two drugs.
- One measure per question. A question that asks for a count, a total enrollment and a recruiting count together is refused.
- Chart requests such as highlighting a bar are not applied. The "not applied" note also sometimes appears when nothing was asked for.
- Wording changes counts. "Heart attack" and "myocardial infarction" reach different trial sets in the registry's search.
- The model's reading can vary between runs, for example by adding a split. Caching keeps a repeated question stable, but there is no repeated-run evaluation yet.
- Citations are a sample, and nothing says why a cited trial matched. The registry matches synonyms, so a cited trial can look unrelated.
- Very broad questions are refused, not sampled.
- Not supported: investigator or site networks, maps, results data, and reading the free text of trial descriptions.
- It runs as one process with no login and in-memory caches, and the UI has no browser tests.

With more time I would add an evaluation harness with repeated runs, drug class and alias resolution from the registry's own terms, a reason for each cited trial, a shared cache in Redis and browser tests.

## How this was built

I used AI tools throughout this project, mainly to speed up implementation, testing and documentation. The system design I did myself.

Before any code was written I worked out the architecture, the data flow, the API and visualization schema, the aggregation strategy, the failure modes and the main tradeoffs. These went into an implementation plan that I reviewed and approved before coding started. Where there was more than one reasonable approach, I talked the options through with Claude models and then cross-checked the important decisions with OpenAI models to catch missed edge cases.

I also used a second model family as a reviewer, so that one model would not both introduce and overlook the same kind of mistake. OpenAI models reviewed and extended the test suite and the set of adversarial queries, with attention to aggregation, citations, ambiguous questions and odd ClinicalTrials.gov data.

AI review was only part of the validation. I also checked things by hand, without a model:

- read raw ClinicalTrials.gov responses
- traced selected queries through the planner and the execution pipeline
- verified filters and aggregates against the returned study records
- checked how trials with several sites or several interventions are deduplicated
- compared citations with their sources, and rendered charts with the data sent to the frontend
- used the app myself to catch UI, loading and error-handling problems that automated tests miss

The automated checks are listed under [How correctness was validated](#how-correctness-was-validated). The schema reference, example index, frontend types and documentation tables are generated from the code, and the build checks that they are current. The ten examples are recorded outputs of the system, not hand-written ones.

## Data source and attribution

All trial data comes from [ClinicalTrials.gov](https://clinicaltrials.gov), a service of the U.S. National Library of Medicine, through its public [Data API](https://clinicaltrials.gov/data-api/api). Every response names the source and its data timestamp in `meta.source`. Use is subject to the site's [terms](https://clinicaltrials.gov/about-site/terms-conditions), and this project is not affiliated with the NLM or NIH.
