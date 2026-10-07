# Early live checks of the planner call

Two small batches of live model calls made on 2026-10-06, before the service was built out, to find out whether the plan schema is accepted in strict mode and whether the model writes correct plans for the assignment's own questions. This file is a record of those early checks. It is not a measurement of the current system:

- The instructions were `plan-v1` (about 4,150 input tokens). The current ones are `plan-v7` (9,008 input tokens, 8,448 of them from the prompt cache, in the recorded run [`01-assignment-request`](examples/01-assignment-request/response.json)).
- The plan schema has since grown: exclusions, measures (median, mean, sum), states, `converse` for greetings, `previous` for follow-ups.
- The batches were not repeated against the current prompt. The later audits and hard prompts that exercised the whole service are summarised in the README, under "How correctness was validated".

Run with a script that is not in the repository, through `PlanService.produce` with the real `OpenAIPlanner`, `use_cache` false, today fixed at 2026-10-06. 17 model calls in the first batch: 1 on the fallback model, 16 on the planner model.

**Schema.** The `QueryPlan` schema was accepted in strict mode by both models, with no HTTP 400.

| Role | Configured | Resolved snapshot |
| --- | --- | --- |
| Planner | `gpt-5.4-mini`, effort `low` | `gpt-5.4-mini-2026-03-17` |
| Fallback | `gpt-4.1-mini`, temperature 0 | `gpt-4.1-mini-2025-04-14` |

**Correctness** meant: after `check_plan`, the plan has exactly the entities and roles, the analysis and dimension, the filters and the year that the question states, with no stray filter. Latency is the whole `produce` call; tokens are input and output of that call.

| # | Question | Run 1 | Run 2 | Latency (ms) | Tokens in / out (run 1, run 2) |
| --- | --- | --- | --- | --- | --- |
| 1 | The assignment's own request: "How has the number of trials for this drug changed over time?" with `drug_name` Pembrolizumab | right | right | 1,534 / 1,342 | 4,156 / 161 and 4,156 / 145 |
| 2 | How has the number of trials for pembrolizumab changed per year since 2015? | right | right | 1,269 / 1,153 | 4,154 / 151 and 4,154 / 130 |
| 3 | How are Duchenne muscular dystrophy trials distributed across phases? | right | right | 1,359 / 1,615 | 4,147 / 130 and 4,147 / 132 |
| 4 | What are the most common intervention types for Duchenne muscular dystrophy trials? | right | right | 2,481 / 1,897 | 4,150 / 193 and 4,150 / 210 |
| 5 | Compare phases for trials involving pembrolizumab vs nivolumab. | right | right | 1,613 / 1,438 | 4,149 / 176 and 4,149 / 176 |
| 6 | Which countries have the most recruiting trials for lung cancer? | right | right | 1,407 / 1,528 | 4,146 / 168 and 4,146 / 162 |
| 7 | Show a network of sponsors and drugs for Duchenne muscular dystrophy trials. | right | right | 1,312 / 1,671 | 4,150 / 138 and 4,150 / 210 |
| 8 | Which drugs frequently co-occur in combination studies with pembrolizumab? | right | right | 1,757 / 1,672 | 4,150 / 232 and 4,150 / 196 |

16 of 16 right. Every run took one model call, so the repair turn and the fallback were not exercised live (they are tested offline). `check_plan` made no adjustment in any run. Median latency about 1.5 s (1.15 to 2.5 s per call). Cost: 66,404 input and 2,710 output tokens on the planner model, about $0.064 at list prices without the cache discount.

**Second batch, run independently.** A second script of the same shape ran the eight questions twice again and compared each plan with a hand-written expected plan that also required `chart_preference` to be null: 19 calls, both models accepted the schema again. 14 of 16 plans matched exactly. The two that did not are question 8 in both runs: the model set `chart_preference` to `network_graph` although the question names no chart. Nothing else differed. The fallback model made the same mistake on its schema call (`bar_chart` for a question about phases). A stray preference matters on a trend question, where `bar_chart` over a time series would turn the line into bars; today `check_plan` drops a preference the data shape cannot take, and the instructions say `chart_preference` is null unless the question asks for a chart (rule 8 of the current prompt). Whether that fixes the habit has not been measured. Median latency of this batch was 1.44 s; about $0.062 at list price.

Both batches together made 36 calls on the owner's key, for at most about $0.14.

**Not covered.** Two runs per question on one day say little about variance. The traps (placeholders, "Phase 5", injected instructions, coordinated entities) were not run live in these batches, and no repeated-run scorecard of the planner exists.
