---
name: llm-pipeline-patterns
description: "Use when an LLM is a stage in a pipeline or product (Anthropic or OpenAI APIs, structured output, caching, classifiers, evals, map-reduce reports): field-observed provider traps, output validation and safety, extending a frozen report without a re-run. Check claude-api first for model IDs and pricing."
---

# llm-pipeline-patterns

Context: catch the field-observed ways an LLM stage goes wrong: provider API drift, silent schema
rejection, cache misses, fallbacks that absorb corrupt output, injection from the data itself, and
evals that measure the wrong thing. This skill never states model ids, prices or cache floors as
rules; those come from the built-in `claude-api` skill, which is authoritative.

## Workflow
1. **For model ids, pricing, parameters or SDK shape, invoke the `claude-api` skill FIRST.** Never take a model id or price from memory or from this skill. Come back here for the traps it does not cover.
2. **Read the installed SDK's type surface and make ONE live call** before handing over any integration. A green unit suite cannot see a request-time schema rejection, a refused parameter or a cache that never reads. → [provider-api-traps](references/provider-api-traps.md)
3. **Before relying on structured output**, check the provider's schema restrictions (Anthropic refuses `minimum`/`maximum`; OpenAI strict needs every property required), then validate every response in code, with a deterministic floor for when validation fails. → [provider-api-traps](references/provider-api-traps.md)
4. **For OpenAI, branch on the model FAMILY**: every GPT-5-family and o-series model rejects a non-default `temperature` and needs `max_completion_tokens`. Check by family prefix, never by one model-name substring.
5. **Prove caching from usage, not from the parameter**: `cache_read_input_tokens > 0` on a second call with a different user message, breakpoint on the last STABLE block, one serial warm-up before fanning out.
6. **Size the blast radius**: what a wrong, partial or truncated output can write, whether a downstream write cap silently discards the stage's work, and whether a cosmetic job shares a response with real content. Assert the output property on the FINAL artifact. → [output-validation-and-safety](references/output-validation-and-safety.md)
7. **Treat corpus, transcript and ticket text as DATA, never instructions**: fence it in a non-forgeable delimiter, strip forged closers, and restate the rule after the fence.
8. **For an eval or model comparison**: measure the noise floor first, freeze the LLM's output for exact comparisons (plus an unfrozen companion check), diff raw vs post-processed output, and pre-register the criterion. → [evals-and-pipeline-extension](references/evals-and-pipeline-extension.md)
9. **To extend an existing report**, tier each ask: renderer-only (already in the report JSON), re-aggregation (on persisted rows), or re-extraction (needs a re-run). Keep new fields additive and optional. → same reference.
10. **Report with provenance**: the model id string actually used (from the response), the prompt version, the run count and spread, and the as-of date.

## Map
- [provider-api-traps](references/provider-api-traps.md): SDK drift, schema keywords, structured output, citations, caching placement and economics, usage accounting, sampling removal, residency and ZDR, OpenAI family traps, Vercel AI SDK streams, PDF and email ingestion.
- [output-validation-and-safety](references/output-validation-and-safety.md): write caps, deterministic floors, key corruption, output budget and blast radius, raw-generation diagnosis, defensive parsing, source-level hints, transcript injection, provenance stamping, verbatim gates, UI rendering.
- [evals-and-pipeline-extension](references/evals-and-pipeline-extension.md): report extension tiers, key-less test seams, run-to-run variance, confabulated subjects, noise floors, model swaps, rerank pools, rank fusion, dedup thresholds, streaming UIs, rubric anchors.

## Related skills
- Model ids, pricing, parameters and SDK usage: the built-in `claude-api` skill.
- Evals as measurement (controls, denominators, provenance): `repacted:verification-discipline`.
- Re-aggregate vs re-extract as a design choice, and contracts: `repacted:architecture-heuristics`.
- LLM output shown in a UI, and prompts that carry secrets or PII: `repacted:security-review-patterns`.

## Refine
At the end of a session that used this skill, run [self-refine](protocols/self-refine.md) and record what it changed.
