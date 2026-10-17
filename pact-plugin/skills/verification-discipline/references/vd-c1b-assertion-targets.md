# VD-C1b: what to assert against

Context: read this when choosing the expected value or the comparison: a guard that narrows an
over-broad signal, a check that might be satisfied two ways, a documented bound, reused
message copy, a renamed key, or a routing table. Entry file:
[vd-c1b-assertions-and-fixtures.md](vd-c1b-assertions-and-fixtures.md).

## Canonical rules

- **C1b.9** A guard that NARROWS an over-broad signal is invisible to "did it fire": the broken version fires on the positive case too. Assert set EQUALITY on the reported entities plus a control window.
  - Report a count of spurious outputs over a stated denominator of known-quiet inputs; "does not fire on quiet days" carries equal weight with "fires on the incident".
  - Record which test kills each mutant, and watch for optimistic mutants (a decision allowed to see later data): they announce themselves as success.
  - A wrong expected value MANUFACTURES the bug it guards: the only way to pass is to break the thing that failed it. Derive expected values from captured data; split them per component class.
  - When something is legitimately excluded, assert it is reported as EXCLUDED-BY-<reason>, never merely absent, so a filter bug and a detection bug stay distinguishable.
  - Watch for a self-concealing denominator: if the fault removes items from the population measured, define the population as would-be-present-but-for-the-fault and mutate to the naive one.
  - A detector keyed on a count of anomalies goes quiet when the anomaly becomes universal. Pin both flavours of "we don't know" (absent vs unrecognised) as a pair.
  - An assertion can be exactly right and never reached: for any bound or membership assertion, name the scenario in the file that could violate it, and script the step that must fail.
  - Before contriving a negative control, measure the real outcome matrix; prove a non-firing row with the RECIPROCAL mutant that adds the behaviour where the row says there is none.
  - Prose next to correct tables inherits their credibility: grep a plan for each headline number and check every occurrence agrees.
- **C1b.10** Before trusting a green, ask what ELSE would satisfy the check. If the broken case passes too, the check carries no information however rigorous it looks.
  - Assert names in order, not a count ("10 columns" passes if the wrong column rendered and another vanished).
  - Hold the data constant and vary the code: a control that compares two databases measures sync drift, not the change.
  - Verify both arms of a three-state fix: "unmeasured renders as unmeasured" also passes on a build that blanked the column; the arm where real values still render is the one that discriminates.
- **C1b.11** A documented upper or lower bound needs a test pinning the SIGN of its error against an independently measured actual. Monotonicity and magnitude tests pass for an estimate uniformly short.
  - Measure `actual` with a different mechanism than the estimator uses; locate the boundary by search, not by guess, and assert the search found a transition.
  - Derive the algebra so the fix is provable; prefer a provable bound over a tight one; fix the guarantee, never the docstring.
- **C1b.12** Reusing a message builder transfers structure, not copy. Parameterise every noun, guard each surface with negative assertions, and mutate the builder to prove the parameters are live.
  - A cross-surface "they all differ" check passes on the wrong metric's wording because one clause still differs; only per-surface negative assertions catch it.
  - Name a field for what it must be TRUE of, rename the shared symbol too, and remember the unit of disclosure is the page: a sibling component can contradict your message.
  - A copy argument that is accepted and discarded passes every test whose expected value equals the default; add a test using nonsense nouns no surface would supply.
- **C1b.13** When a renamed string is an object KEY, prove the lookup resolved: assert fields unique to the resolved config, and trigger a conditional that compares against the new key.
  - The heading often renders from the picker list, not the resolved config, so its presence proves nothing. TypeScript cannot see string-literal keys into `Record<string, T>`.
  - Check what the build script runs: `vite build` alone does not typecheck; run `tsc --noEmit` separately.
- **C1b.14** An assertion that every rule routes SOMEWHERE passes when two rules are wired to each other's handler. Assert the rule-to-handler PAIRING by contract, and prove it red by swapping two targets.
  - Derive the pairing from what the handler's own shipped code declares it produces; mutate the connection graph rather than code, so a mutation cannot raise a syntax error the runner mistakes for detection.

## Examples
- C1b.9: deleting a serving-eligibility filter changed the reported campaigns from 9 to 228 and the detector still fired on the real incident.
- C1b.9: a detector counting disapprovals inside the "currently eligible" set found zero at 100% disapproval, because disapproval removes an ad from that set.
- C1b.9: a header claimed a cookie is deleted on every outcome; the measured matrix was 7 of 9, and the two non-deleting rows became the controls.
- C1b.11: a capacity estimator documented as over-estimating was one short per storage shard and passed a 13-mutation suite.
- C1b.12: injecting one chart's wording onto another left `new Set(texts).size === 3` passing.
- C1b.14: swapping two rules' targets left a 98-assertion suite identical to the healthy graph, because parser tests invoked each parser by name.
