# Design decisions

## Provenance
Implemented with AI assistance (Claude). The decisions below are the specification the implementation follows; review them before trusting any result produced with this repo.

## 1. Compute in SQL, report only what SQL returned
The agent's final prose may contain only numbers that appear in a query result (or in the user's question). This turns "the model might misquote a figure" from a vibes problem into a deterministic check, and it forces arithmetic into the database, where it is auditable.

**Rejected alternative: accept derived values** (sums, ratios, differences of grounded cells). With a few hundred returned cells, pairwise combinations coincide with an invented number often enough to weaken the guarantee. Strict is simpler and safer; the agent can always `SELECT` the derived figure.

## 2. No code-execution tool
The original idea lists "Python analysis". Here the agent only has SQL. A `run_python` tool would let numbers reach the answer without passing through an auditable result set, and adds a sandboxing problem. Statistical needs (percentiles, correlations) are covered by DuckDB's SQL functions.

## 3. What the verifier does and does not prove
It proves: each number in the prose was returned by a query the agent ran.
It does not prove: the query answered the question. A real cell quoted for the wrong metric passes. That is why correctness is measured separately, by oracle (gold SQL), and a run must pass both.

Also: rounding tolerance means a small integer can be grounded by a nearby real value (6.7 grounds "7"). That is correct rounding, and also a source of coincidental matches; the oracle is the backstop.

## 4. Oracle scoring, no LLM judge
Gold answers are the result of executing each task's `gold_sql`. Comparison rules:
- scalars: absolute tolerance (default 0.005), accepting `12.35`, `"12.35"`, `"$12.35"`, `[12.35]`, `[[12.35]]`
- labels: case/space-insensitive equality
- tables: multiset of rows, ignoring row order and column names; floats snap to a grid of 2x tolerance so a SQL-rounded gold value and the agent's value agree
- A run passes iff `correct AND grounded`. Both flags are recorded separately in `runs.csv`.

## 5. Tasks that should be refused
5 of 26 tasks have no valid numeric answer: three ask for data that does not exist (age, NPS, ad spend), two are deliberately vague ("How are we doing?"). Expected statuses are `cannot_answer` / `needs_clarification`. A blanket-refusal strategy cannot score well because 21 tasks require an answer.

The vague tasks are blunt on purpose. A smarter ambiguity test would accept a reasonable stated assumption; that needs a rubric and is future work.

## 6. The harness is tested with mutants
Reference solvers that are wrong in one specific way (`evals/reference.py`) must fail, and the faithful one must pass. If a mutant ever passes an answerable task, the scorer or verifier has a hole. These run in CI.

## 7. Dataset traps (so failures are interpretable)
Synthetic, seeded, snapshot date 2025-12-31. Deliberate traps: subscriptions still `trialing` must be excluded from conversion rates; `refunded` payments are not `succeeded` revenue; some subscriptions are cancelled by `billing_error` rather than by the user; android has a higher payment-failure rate than ios.

## 8. Known threats to validity
- 26 tasks, one schema: wide intervals, no generalisation claim.
- Gold SQL is written by the author and can encode the author's interpretation. Each gold query is run and sanity-checked (non-empty, no ties for "top" questions), which does not prove the interpretation is the only reasonable one.
- Temperature 0 on the OpenAI-compatible path; the Anthropic path uses API defaults. Record exact model strings and dates when publishing results.
