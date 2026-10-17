# VD-C1a: discriminating measurements

Context: read this when you are designing the measurement that will settle a question: a
component swap, a fix's validation, an A/B comparison, a filter finding, a performance
diagnosis, or a sample count. Build the case where the hypotheses disagree before you run
anything. Stories, symptoms and claims of protection are in
[vd-c1a-falsify-dont-confirm.md](vd-c1a-falsify-dont-confirm.md). Entry file:
[vd-c1a-prove-it-can-fail.md](vd-c1a-prove-it-can-fail.md).

## Canonical rules

- **C1a.55** Before swapping or choosing a component to hit a target, compute that lever's ceiling against the target. If it cannot move the binding quantity, the bottleneck is elsewhere.
  - Measure what the component makes available upstream of the failing stage; if that already meets the target, instrument the downstream stages.
  - Sort levers into shrink-the-contents and grow-the-container, and ask which touches the denominator. A bar met only by deleting the content targets the wrong variable.
  - Refuse an n=1 budget, and before specifying a measurement confirm the named instrument actually contains the variable. A caveat must live inside the element it qualifies.
  - Read a guard's scope in its config and call sites, not its name. Build two views from one collector under one dedup, and give a tri-state (`true | false | null`) fixture its null.
  - An absence assertion over hardcoded labels goes vacuous on rename: derive the probe from the real config, assert non-empty, and state red-ability as a three-row prediction.
  - A zero-spend live probe reuses the worker's own transport, calls only free tools, and proves a credit-table delta of 0.
- **C1a.56** Verifying a rule or fix against the case that motivated it is not a test. Test inputs it was not designed for, especially marginal cases just inside the region it must protect.
  - Solve the break-even algebraically and compare it to the gate threshold: a break-even below the threshold means the mechanism is inert over the whole visible region.
  - Assert where a value moved TO, not that it moved; state the region you verified whenever you call something "the primary defence", and correct everyone who acted on your endorsement.
  - Name your likely error in the teachback and check it; state the reasoning for findings you decline to raise.
  - Run the query that could return the inconvenient answer, re-measure with a different instrument, and verify the artifact rather than its report.
  - Hand fixers exact predicted values, not a direction of travel. A tolerance that masks the defect it documents is never the fix.
- **C1a.57** Run the wide, unfiltered discriminator before the narrow query a brief hands you, and name the candidate causes and their distinguishing observations in advance.
  - A fix applied before measuring destroys the natural experiment (the last clean run and the first failing run over the same period).
  - A lost stock shows as a level drop: compare the step across the break with the steps on either side, and find a second independent series. State the assumption and bound the result.
  - "Not measurable, and here is why" is a complete deliverable; an invented magnitude will be quoted.
- **C1a.58** When told two routes are equivalent, compute both, assert agreement, and log the symmetric difference. Make the self-contained route primary and raise loudly on an empty oracle.
- **C1a.59** With a nondeterministic stage upstream (an LLM), an end-to-end A/B delta is uninterpretable. Freeze the upstream output, prove replay fidelity, measure the baseline's own spread, use two freezes.
  - Feed the frozen input to the REAL function and compare item by item; add the per-item "what did the old version get that the new one does not" check, since equal totals can hide a swap.
- **C1a.60** Equal wall time on two endpoints localises cost to their shared prefix, not to a fixed cost. Re-measure interleaved with repeats, check the filter removes rows, time the prefix.
- **C1a.61** Design a test to discriminate between competing hypotheses, not to reproduce the symptom: include a case where they disagree, usually the no-match or disjoint case.
  - Beware the dramatic result; confidence should come from the test's discriminating power. Symptom reproduction is not diagnosis, and "measured" and "inferred" stay labelled.
- **C1a.62** A verified measurement describes the operation measured, not the goal. Ask whether the operation is needed at all before designing around its cost.
  - A filter finding needs three controls: an unfiltered baseline, a known-ignored parameter, and a nonsense term that must return 0. Cardinality alone matches by coincidence; check precision and recall.
  - An ignored parameter is evidence about its NAME, not the capability; read the vendor's own generated URLs for the right spelling.
  - A follow-the-link `nextPage` can drop your filter: the result is contaminated, not truncated, and a `listLen === numResults` completeness check passes on it. Probe filter semantics per endpoint.
- **C1a.63** Before testing an "unreachable" state, write the route; a route refutes the claim. Enumerate every call site of the trigger, and distrust a tidy story for why a guard is unnecessary.
  - A route you cannot walk is EXISTS BUT UNEXERCISED, neither a pass nor unreachable. Correct in the direction that costs you the argument.
- **C1a.64** n counts independent samples. A boundary, domain or event count chosen after the figures are visible is a preference: pre-register the classification and state its consequence beside it.
  - Correlated terminations from one external cause are ONE event. A domain of one is not a domain, and a threshold must never be crossed by a re-partition.
  - A ruling that happens to rescue its author's data is tested against exclusions made before the question arose, not dismissed as convenient.

## Examples
- C1a.55: a "switch the discovery tool" proposal: the current tool already surfaced 94 candidates against a target of 47.
- C1a.56: a shrinkage sort key checked at 8% coverage looked right; at 70% coverage (inside a 0.70 quality gate) the thin record still won, 88.0 vs 80.0.
- C1a.57: a wide 401 query returned one campaign id, with the other succeeding in the same job, which cleared the credential in one query.
- C1a.59: baseline recall read 70, 68 and 71 across three draws; a live -1 was inside that noise.
- C1a.60: two routes measured 21.4 s each; the shared prefix cost ~17 s and the "extra" filter removed 0 of 2,695 rows.
- C1a.61: two OR-combined filter conditions updated all 3 rows; a no-match condition returned 0, refuting "the filter is ignored".
- C1a.62: search-per-client collapsed an "enumerate everything" cost about 20x; the page-size parameter was `max_results`, not `pageSize`.
