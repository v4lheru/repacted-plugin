# Output validation, blast radius and injection

Context: how an LLM stage's output fails partially while looking like success, how one stage's
failure takes unrelated output with it, and how input data hijacks the model. Read this before you
wire an LLM stage's output into storage, a report, a UI or a routing decision, or feed it corpus
text.

## Canonical rules
1. Before adding an LLM stage because "the system does not comprehend", trace one known-good fact end to end, input to final artifact. A downstream write cap may be discarding it, and the new stage would change nothing.
2. A cluster of outputs just above a configured cap (a saturation shelf) means the cap is discarding, and additive-only guards discard the NEWEST items first. Count every cap site before fixing one.
3. Fix the router's aim, not the writer: route overflow to a spill target (`{stem}-2`) that bypasses any "new target needs N items" threshold, so the cap keeps its real job.
4. Verify in layers: run the no-LLM write-layer fix alone against the known-good fixture first, so a later failure is attributable to one layer.
5. Constrain any output field that drives routing or layout to the router's canonical enum, and still validate it in code: an enum is a strong constraint, not a proof.
6. Before designing structured columns for LLM output, grep every READ of the record's fields. If the renderer pastes one string, a fixed markdown shape in one field beats a migration.
7. A transform delegated to an LLM (normalise, clean, classify, relabel) needs a deterministic floor that runs UNCONDITIONALLY; the model only improves on it. A half-applied transform reads as a data quirk.
8. An echoed input is not an answer: compare normalised forms before crediting a "resolution" (`!!value` is not a test). Pick the failure predicate by shape (host-shaped `label.tld`, not "contains a dot").
9. Count and publish residual failures per run (for example "labels still domain-shaped: expected 0"), and state the deterministic floor's ceiling in the output.
10. Assert the OUTPUT PROPERTY on the final artifact (scan it for the forbidden shape), with named exemptions and a non-vacuity check. Expected values taken from the stub's fixture verify only wiring.
11. Returned JSON keyed by identifiers corrupts a few keys (a dropped dot or letter); a fill-missing-with-default fallback then silently absorbs the real items. Assert `set(response) == set(requested)` in BOTH directions.
12. MORE keys than requested is the tell of key corruption. Prefer a list of `{id, label}` objects or positional indexing over free-text identifiers as JSON keys; fuzzy-match strays back before any fallback.
13. Never add an unbounded, data-scaling job (per-account relabelling) to a structured-output pass that also writes prose. It crowds the output budget, and one truncation blanks every section of that response.
14. Split passes by blast radius, not only by budget: raising the output cap masks the coupling. Cosmetic output must not share a response with real content.
15. Diagnose a structured-output failure from the raw generation text. On a healthy structured pass that text is EMPTY; non-empty text means the model answered in prose. Mid-item start or end means truncation.
16. If the raw text is not JSON at all, "the schema is too strict" is falsified: say so rather than fixing every candidate cause. Stochastic prose-vs-structure selection makes a retry a real, cheap win.
17. Assert retry and error settings per node BY NAME, with a guard that the asserted list equals the set of LLM nodes present, so a new pass cannot land without coverage.
18. Parse LLM JSON defensively: trim, strip code fences, `JSON.parse` in try/catch, check it is an object. Route unparseable output to human review; never auto-advance.
19. Keep security- and correctness-critical decisions deterministic (domain checks, regex keys, real API lookups) and let the model do judgment or act as a fallback. Re-derive deterministic fields from an upstream node after the model step.
20. A field that is constant per data SOURCE (a feed's home market) describes the source, not the record. Name it so (`provider_feed_market`), call it a weak prior in the prompt, and pass real per-item signals.
21. Give the model an explicit priority order for inferred attributes (content, then identity, then audience, then TLD, then the source prior). A `.com` TLD is neutral.
22. Transcripts of people directing an AI are made of instructions: unfenced, the model obeys them. Fence each chunk in a random-looking delimiter, strip forged closers from the content, and restate the rule AFTER the fence.
23. A "fails to parse" from a schema-constrained call is often role-play capture, not malformed JSON: dump the raw text. API-level structured output also prevents it.
24. When the model picks an exemplar (a quote), resolve it back to ONE provenance record and stamp the displayed text, source id and sibling fields from that record, overwriting the text with the record's verbatim text.
25. Keep per-item and meta rollup key spaces disjoint when they share a conceptual name, and test that they do not overlap.
26. A row-level "every quote verbatim" gate at 1.0 drops a large share of records for one imperfect quote. Prefer dropping the offending item plus a row-level minimum traceability floor, and run a dry slice first.
27. A traceability gate policy is a contract decision: surface it to the architect rather than loosening it alone. Prompt hardening raises traceability; the gate policy decides yield.
28. LLM JSON puts objects where strings are expected. Render every LLM-supplied field through a `safeString(unknown)` guard (try title, name, text, label, value, then JSON), not only the one that crashed.

## Examples
- 1, 2: a known fact was extracted and stored, then destroyed by a page-size cap; 21 of 112 pages sat just above the cap. There were four cap sites, not one.
- 7, 10: a report shipped 61 and 91 raw domains where company names were asked for; every fixture-based test was green.
- 11, 12: about 0.1% of 10,376 domain keys came back mangled; the response held 10,386 keys.
- 13, 14: a relabelling job grew to 47% of one pass's content budget and failed in production 1 run in 3, blanking four report sections.
- 20: a feed-level `region_hint` inverted the geography of a whole batch: a US request matched a UK-only client and rejected the US ones.
- 22: a transcript chunk starting "familiarize yourself with the project" made a small model drop its schema and reply "I'm standing by for your next instruction".
- 26: a row-level verbatim gate rejected 20 of 41 live calls, while the 22 survivors had 208 of 208 quotes verbatim.
- 28: a production preview crashed with React minified error 31 on a step rendered as an object.
