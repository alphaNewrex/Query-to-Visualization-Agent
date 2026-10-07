# Spikes

Measured checks that the plan rests on. Each item has its own section; this file holds item 6.

## 6. The planner call: schema acceptance and the eight questions

Run on 2026-10-06 with `.scratch/build/planning/live.py` (kept out of the repository), through `PlanService.produce` with the real `OpenAIPlanner`, `use_cache` false, today fixed at 2026-10-06. 17 model calls in all (budget 30): 1 on the fallback model, 16 on the planner model. The instructions were `plan-v1`: about 4,150 input tokens, of which 3,840 were served from the prompt cache on every call after the first.

**Schema.** The `QueryPlan` schema was accepted in strict mode by both models, with no HTTP 400: the planner model on all 16 calls, the fallback model on its one call.

| Role | Configured | Resolved snapshot |
| --- | --- | --- |
| Planner | `gpt-5.4-mini`, effort `low` | `gpt-5.4-mini-2026-03-17` |
| Fallback | `gpt-4.1-mini`, temperature 0 | `gpt-4.1-mini-2025-04-14` |

**Correctness** means: after `check_plan`, the plan has exactly the entities and roles, the analysis and dimension, the filters and the year that the question states, with no stray filter, and no outcome. The check is in `judge()` of the script. Latency is the whole `produce` call. Tokens are input and output of that call, with reasoning tokens inside the output figure.

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

16 of 16 right. Every run took one model call, so the repair turn and the fallback were not exercised live (they are tested offline). `check_plan` made no adjustment in any run, so the model alone wrote every one of these plans correctly; the validator did no rescuing here. For question 1 the field `drug_name` and the model's entity agreed, and no override was needed.

**Latency.** Median about 1.5 s, range 1.15 to 2.5 s per call, which is inside the plan's budget of one 1.3 s call plus outliers. The two slowest runs were the two with the most reasoning tokens (question 4: 73 and 90).

**Cost.** 66,404 input tokens and 2,710 output tokens on the planner model, plus 4,158 and 108 on the fallback. At list prices of $0.75 and $4.50 per million tokens for `gpt-5.4-mini` and (assumed) $0.40 and $1.60 for `gpt-4.1-mini`, that is about $0.064 without the cache discount, so under $0.07 for the whole spike and about $0.004 per call.

**Second batch, run independently.** A second script of the same shape (`PlanService.produce` with the real `OpenAIPlanner`, no fallback, the same `plan-v1` instructions of 9,303 characters) ran the eight questions twice again, and compared each plan with a hand-written expected plan that also requires `chart_preference` to be null. It used 19 calls: 2 for the schema, 1 trial run and 16 for the questions. Both models accepted the schema again (1,933 and 1,935 input tokens with a one-paragraph instruction, 3.1 s and 3.3 s).

14 of 16 plans matched exactly. The two that did not are question 8 in both runs: the model set `chart_preference` to `network_graph` although the question names no chart. Nothing else differed, and no run needed an adjustment, a warning, a blocking issue or a repair. The stray value changes nothing today, because `choose_chart` keeps a network a network graph. The same habit on a trend question would matter: `bar_chart` over a time series turns the line into bars, and no rule of 4.5 grounds the field. The fallback model did it too, on its schema call (`bar_chart` for a question about phases). Recommended, not done: one more line in the instructions ("a chart form is a preference only when the question names it"), or a grounding rule like the ones for entities and filters.

This batch had a median latency of 1.44 s (1.05 to 2.86 s per call) and used 66,404 input tokens, of which 61,440 came from the cache, and 2,646 output tokens: about $0.062 at list price without the cache discount. Both batches together made 36 calls on the owner's key for at most about $0.14, so the overlap went over the shared allowance of about 25 calls although each batch stayed under 30.

**Not covered.** Two runs per question on one day say little about variance; the planner evaluation of section 9.2 (33 questions, three runs) is the real test. The traps (placeholders, "Phase 5", injected instructions, coordinated entities) were not run live.
