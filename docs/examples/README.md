# Example runs

Actual outputs of this service for ten questions, recorded by `make examples` (`backend/scripts/run_examples.py`). Nothing here was edited by hand. Each folder holds:

- `request.json`: the body sent to `POST /v1/query`;
- `plan.json`: the plan the service ran, which is `meta.plan` of the response;
- `response.json`: the complete answer, with every citation and the trace.

Recorded on 2026-10-07 (UTC) from ClinicalTrials.gov data of 2026-10-06T09:00:05, with the planner model gpt-5.4-mini-2026-03-17. A model's plan is not repeatable run to run, but a recorded plan is: posting it to `POST /v1/analyses` re-runs the same analysis with no model, and the counts stay the same until the registry's data version changes. From the repository root, with the API running on port 8000:

```bash
jq -c '{plan: .meta.plan, options: .meta.options}' docs/examples/01-assignment-request/response.json \
  | curl -s -X POST http://127.0.0.1:8000/v1/analyses -H 'Content-Type: application/json' -d @-
```

| Run | Question | Outcome | Visualization | Headline | Trials matched | Model |
| --- | --- | --- | --- | --- | --- | --- |
| [01-assignment-request](01-assignment-request/response.json) | How has the number of trials for this drug changed over time? (drug_name: Pembrolizumab) | `visualization` | `time_series` (line) | 259 trials started in 2025; the peak was 299 in 2022. | 2,971 | `gpt-5.4-mini-2026-03-17` |
| [02-compare-phases](02-compare-phases/response.json) | Compare phases for trials involving pembrolizumab vs nivolumab. | `visualization` | `bar_chart` (grouped) | The largest group is Phase 2 for pembrolizumab, with 1,259 trials. | pembrolizumab: 2,971; nivolumab: 2,029 | `gpt-5.4-mini-2026-03-17` |
| [03-recruiting-by-country](03-recruiting-by-country/response.json) | Which countries have the most recruiting trials for lung cancer? | `visualization` | `bar_chart` (horizontal) | China is the largest group: 903 of 2,298 trials (39.3%). | 2,298 | `gpt-5.4-mini-2026-03-17` |
| [04-sponsor-drug-network](04-sponsor-drug-network/response.json) | Show a network of sponsors and drugs for Duchenne muscular dystrophy trials. | `visualization` | `network_graph` (bipartite) | 24 nodes and 15 links; the strongest link joins PTC Therapeutics and Ataluren in 13 trials. | 499 | `gpt-5.4-mini-2026-03-17` |
| [05-drug-cooccurrence](05-drug-cooccurrence/response.json) | Which drugs frequently co-occur in combination studies with pembrolizumab? | `visualization` | `network_graph` (force) | 25 nodes and 60 links; the strongest link joins Carboplatin and Paclitaxel in 145 trials. | 2,971 | `gpt-5.4-mini-2026-03-17` |
| [06-enrollment-histogram](06-enrollment-histogram/response.json) | What is the distribution of enrollment sizes for pembrolizumab trials? | `visualization` | `histogram` | The most common size range is 25-49, with 649 trials. | 2,971 | `gpt-5.4-mini-2026-03-17` |
| [07-enrollment-vs-duration](07-enrollment-vs-duration/response.json) | Plot enrollment against duration for completed Duchenne muscular dystrophy trials. | `visualization` | `scatter_plot` | 235 trials plotted, duration against enrollment. | 240 | `gpt-5.4-mini-2026-03-17` |
| [08-largest-trials](08-largest-trials/response.json) | List the 10 largest lung cancer trials. | `visualization` | `table` | 10 of 10 trials, ordered by largest enrollment. | 14,604 | `gpt-5.4-mini-2026-03-17` |
| [09-recruiting-count](09-recruiting-count/response.json) | How many recruiting trials are there for Duchenne muscular dystrophy? | `visualization` | `metric` | 61 trials match: Duchenne muscular dystrophy; Status: Recruiting. | 61 | `gpt-5.4-mini-2026-03-17` |
| [10-no-drug-named](10-no-drug-named/response.json) | How has the number of trials for this drug changed over time? | `clarification` | `clarification` (nothing to draw) | The question needs a drug name that it does not give. Add it to the question or its field. | - | `gpt-5.4-mini-2026-03-17` |
