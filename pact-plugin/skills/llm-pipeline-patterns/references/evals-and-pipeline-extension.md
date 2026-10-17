# Evals, measurement and extending an LLM pipeline

Context: how to measure an LLM stage without fooling yourself, how to compare models, and how to
extend a frozen map-reduce report without a costly re-run. Read this before you run an eval, a model
comparison or a before/after on a pipeline with an LLM in it, before you extend a report contract,
and before you build a streaming LLM UI.

## Canonical rules
### Extending a frozen report
1. Before re-running an LLM map, tier every ask: T0 renderer-only (the data is already in the report JSON), T1 re-aggregation (on the persisted row, never projected), T2 re-extraction (only the model can produce it).
2. Audit the projection gap first: fields often sit on the persisted insight row but were never carried through the reduce. Surfacing them is a deterministic re-aggregation with no map key.
3. Keep every extension additive: one new OPTIONAL per-item key (absent means a safe default), never remove, rename or tighten a field, so no schema version bump.
4. Folding a new per-item categorical across a cluster: if users FILTER on it, fold to the dominant class with a deterministic conservative tie-break, never single-or-None. Carry a per-item count distribution when several cuts are needed.
5. Flag any tie-break that can fabricate a class absent from the cluster (always-default-to-X on a tie between two non-X classes).
6. Re-extraction usually reuses the existing harness if it imports the LLM module and rebuilds through the existing entry point: check that before speccing new plumbing.
7. Frontend builds against an EXTENDED synthetic fixture carrying the new field in every enum value plus an absent case, before the live re-run lands.
8. Stamp paired fields (quote, deep-link id, sibling answer) from ONE provenance exemplar, never from independent lookups (output-validation-and-safety rule 24).
9. Make the pipeline testable without keys or a database: a `Protocol` LLM client with a deterministic fake (schema-valid, quotes that are real substrings), a store `Protocol`, and an injectable fetch.
10. Pin an in-code rollup to the canonical SQL with a hand-computed expected fixture: the hand-computed expectation is the contract.
11. Fake-LLM tests cannot prove a prompt change. Run a tiny live slice through the real model, prompt and validator on author-written synthetic inputs (no PII), report it as directional, and hand the statistics to the full run.
12. Type-split duplicates in entity extraction (one intent as task, deliverable and follow-up): add a cross-type dedup rule with an example, verb-led titles, a tense rule, and a minimum content length.
13. Inject known-entity lists (clients, projects) at call time, fetched once per batch. Collapse overlapping types into one `kind` enum only as a last resort: every consumer queries on type.

### Measuring an LLM stage
14. An LLM step makes result counts vary run to run: a single-run before/after means nothing when the effect is smaller than that spread. Measure whether the quantity is stable before hunting a mechanism for a gap.
15. Freeze the LLM's output (pass a fixed query list past it) and the rest of the pipeline becomes byte-deterministic. Ship a named unfrozen companion check (N repeats, a distribution): freezing hides the frozen stage.
16. An LLM supplies a subject the input never had: a subject-less input returns a CONFIDENT wrong result, not an empty one. A selectivity diagnostic does not catch it; marking model-supplied queries in the output does.
17. Input length is not a proxy for "too little signal": the unrecoverable inputs omit the subject, which is a different variable.
18. Before a counterfactual or paired A/B suite over an LLM, measure the within-condition noise floor (the same input N >= 20 times). Assert that the between-variant delta is not distinguishable from the within-variant delta.
19. Agree the response to a failing bias or counterfactual suite before running it (a product or legal decision, not a widened tolerance). Temperature 0, where the model allows it, shrinks the floor.
20. When auditing an LLM in a pipeline, diff the RAW model output against the post-processed record before grading the model; attribute each error to one or the other.
21. In that audit, check for model fields no routing condition reads, rules that fire only after an upstream extraction succeeds, and prompt-version changes across the graded period.
22. Model comparison: swap the model through an in-process seam so production prompt, parse and ranking code run unchanged; never transcribe the prompt into the harness.
23. Size repetitions from the baseline's OWN noise and use a paired bootstrap before calling a rank difference. Report the pre-registered gate verdict and the robustness check side by side.
24. Check parameter parity per model: the smallest reasoning tiers reject `effort: none` (minimum `minimal`) and non-reasoning models reject `reasoning` outright (verified 2026-09-22, OpenAI API).
25. Hard-check that output URLs are a subset of input URLs: cheaper models alter characters inside signed URLs. Compute such counts with `len()` on the full set, never from a truncated print.
26. Before a draft-generation study on historical change requests, check the live target: already-applied changes measure restraint, not change application, and can make a model delete valid content.
27. Guard a long in-pod comparison against a mid-run rollout with an md5 check of the code under test per chunk; abort on mismatch.
28. Before sizing a reranker's K, find where the must-surface items sit in the UNBOUNDED pre-cut pool, and report the aggregate and the named tail case: they point to different K.
29. A per-query cap shared across sources makes the candidate pool depend on the source mix. A rerank is a second nondeterministic stage: check rerank-off (byte-identical), a frozen-judgement replay, and live stability.
30. Calibrate characters per token from recorded `usage.input_tokens` against prompt length; never assume. Check that the per-user LLM rate limiter is ATTACHED to the route, not only defined.
31. A past model-comparison run is a free frozen eval set: mine its stored queries and per-item ranks for headroom before new labelling.
32. Merging ranked lists with `count + 1/best_rank` lets the count dominate. Use reciprocal-rank fusion (`sum 1/(K + rank)`), sweep small K, and trust only a broad basin of good K.
33. Count how often the arbitrary tiebreak decides rank 1: if an id comparison decides many top results, the tiebreak is ranking, not the score. A fused score is a sort key; render array order, not the value.
34. An embedding dedup threshold is valid only for the text register it was tuned on: raw transcript chunks share scaffolding and over-collapse. Measure and count the collapse ratio separately from other drops.
35. A scheduled map job's concurrency is a requirement: compute items times per-call latency before shipping a serial loop.
36. Bumping a `(item_id, pipeline_version)` skip key re-processes all history: use it deliberately and say so in the commit.

### Streaming LLM UIs
37. React error boundaries catch render errors only. Pair a boundary with a hook for stream failures: throttling (429, backoff), reconnecting (5xx), recovering (stream cut), failed (retry chip); an AbortError returns to idle silently.
38. Classify stream errors by status first (`err.status ?? err.statusCode ?? err.response?.status`), then by message. The provider owns the stream in a store that outlives remounts.
39. Keep prompts as versioned files composed by ONE function; validate the frontmatter slug against the filename at load. Cite with inline `[[trace:<id>]]` tokens post-processed into chips, not streamed structured output.
40. Fade in word by word (split on `/\S+\s*|\s+/g`), not letter by letter; honour reduced motion. The browser-direct Anthropic header is for demos with a user-supplied key only; production goes through a backend proxy (repacted:security-review-patterns).

### Extending someone else's rubric
41. Before writing anchors in an author's voice, lay the existing anchors of each level side by side and derive their CONSTRUCTION (for example: middle = real capability plus a named shortfall; bottom = displaced work, never an absence). State the derived rules.
42. Diff anchor wording against the scale's own level labels: an anchor reusing another level's label word pulls the scorer there. Surface it as a question; do not silently edit an approved anchor.
43. Where "contradicted" and "not demonstrated" are separate states, the bottom anchor must describe displaced work, not "no evidence". The middle anchor is coupled to the alert threshold: its wording is a precision knob.
44. Version anchors as their own axis (`anchors_version`). Prove verbatim carry-through by substring match, diff the config against the approval doc, and plant a violation to prove each scanner fires.
45. "N+ years in X" is an indirect age-discrimination vector (UK): replace it with the structural feature it proxied, and flag it loudly rather than omitting it silently.

## Examples
- 1, 2: 5 of 6 stakeholder asks that looked like a costly re-extraction were deterministic re-aggregations of persisted rows.
- 14: the same prompt returned 89, 83 and 81 results; a doc's "94" and its screenshot's "89" were two samples, not a bug.
- 16: "Cross-posting for visibility" (no product named) returned 80 confident results with the degradation flag false.
- 20: every clear category error in a 92-execution audit came from one post-processing override of a correct model verdict.
- 23: a baseline's own rank-1 count moved 50 to 42 between two identical reps.
- 25: a truncated print suggested 3 corrupted URLs per model; `len()` gave 19 of 26 and 5 of 26.
- 28: a named must-surface item sat at position 151 of 203 with the UI capped at 100.
- 32, 33: a precise article scored 5.0 and lost to a glossary at 8.0; with RRF, rank-1 went from 36 to 49 of 85, and tiebreak-decided rank 1 from 18 to 0.
- 34: a 0.86 cosine threshold collapsed 77 transcript chunks to 61, dropping an acceptance-test literal.
