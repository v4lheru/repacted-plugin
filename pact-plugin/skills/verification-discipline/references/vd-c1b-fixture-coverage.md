# VD-C1b: what the fixtures cover

Context: read this when deciding whether a fixture set covers the states, sizes and conditions
that matter: named states, multi-state rendering, generated fixtures, caps, real data, reject
sets, and conditions that only exist in unsettled data. A fixture holds exactly the conditions
someone imagined. Entry file: [vd-c1b-assertions-and-fixtures.md](vd-c1b-assertions-and-fixtures.md).

## Canonical rules

- **C1b.40** A fixture named for a state it does not represent (a `HEALTHY` object missing a required field) is worse than a wrong assertion: every assertion around it is true and about something else.
  - Read a fixture's name as an assertion and check it. Build fixtures from what the PRODUCER emits and import the producer's type; all-optional consumer types hide incomplete fixtures.
  - When a fixture changes materially, re-run every mutation proof. Do not delete a runtime guard because a new type forbids the bad shape: the type describes one path and arrived after the bug.
- **C1b.41** A field deliberately frozen across a state transition ("last observed", "does not decay") silently changes meaning in the next state. Render every state's FULL output and read it as the user.
  - Tests written from the spec cannot see a true-but-misleading composite. Pin the resolved rendering as different from the open one; fix with tense and a status qualifier, not by dropping the field.
  - Adding a state to a union is compiler-checked; adding it to every `if/else if` chain that renders PARTS is not. Grep every branch keyed on the state.
  - Length limits bite the longest variant: render at the widest realistic inputs. Truncate at the last space so the visible text is an exact prefix, and assert the truncation path ran at least once.
- **C1b.42** A script that regenerates a committed fixture must be exactly reproducible: freeze clocks, UUIDs, seeds and ordering at the seam it reads, and prove it with two runs and matching checksums.
  - A check whose output differs on every run is not a check; post-processing the output is a second place the format is decided. Write the generator down, or staleness is undetectable.
  - To add showcase rows to a seeded PRNG fixture, append explicit rows with stable greppable ids; overriding a seeded index shifts every later draw and the baseline it anchors.
- **C1b.43** When a field's docstring names how it can be wrong ("heuristic", "may over-report", "not a measurement"), treat that as a fixture specification: reach the state and check the COPY built on it.
  - The flag will be wrong as documented; the question is whether the sentence rendered from it stays true. An independent oracle proves the flag actually misfired.
- **C1b.44** A control whose options derive from observed data renders empty on a clean store. That is a fixture gap, not an untestable feature: ask where the options come from before filing NOT TESTABLE.
  - Bring a stale local DB forward with only the feature's migration delta, in a NEW scratch DB; seed for the states the checks need, pick unambiguous inputs, and verify the buckets with a `GROUP BY` first.
- **C1b.45** A passing comparison is evidence only if the fixture could have produced a mismatch. Size fixtures above every cap, page size and truncation limit in play.
  - Below a cap the two sides have no way to differ. Check the wording beside a capped count: "20" and "20+ (showing first 20)" are different claims.
  - Seed probe rows under a greppable prefix, delete them in the same pass, and re-assert the fixture counts afterwards.
- **C1b.46** A fixture holds only the conditions you imagined, and real data inside a constructed scenario is still constructed. Get the real corpus in front of the code once before shipping.
  - Build a `--limit N` / `--only-<id>` path early that bypasses any already-processed guard; suspect a small round bound never approached in tests; predict a specific counter before the real run.
  - A comment inside a fixture-driven test that asserts a fact about the WORLD ("prod uses model X") can never be falsified by the suite; delete the dated measurement rather than re-dating it.
  - The one fixture that can surprise you is a failed run's captured payload: replay the fixed code against it first. But replay verifies a TRANSFORMATION; only a real multi-item run verifies an ORCHESTRATION.
  - Anything about cardinality, iteration, ordering, batching or per-item semantics is invisible to a single-item replay; n = 1 is the one size where last-row and per-item bugs cannot appear.
- **C1b.47** When fixing a gate, filter or threshold, build the must-REJECT corpus your fixtures lack before choosing the fix. An over-widening looks identical to success on the accept set.
  - A wrongly rejected item waits where a human can find it; a wrongly accepted one is processed on bad data and looks normal. Vary the reject set along the dimension the fix moves.
  - Pin the residual as a test, prefer one instrument per regime over one "more principled" one, and record the calibration evidence next to the constants.
- **C1b.48** Conditions that exist only in unsettled data (the in-progress period, late-arriving values) are absent from any replay. Test them by construction, and keep ABSENT distinct from ZERO.
  - Enumerate what is unsettled at decision time up front; say in the report that a green replay is SILENT about this class, not reassuring.
  - A value with no time dimension (a current-state field) queried by date stamps today's value onto every past row.
  - The inversion: a captured fixture can hold keys no schema reads. Assert the set difference of capture keys minus modelled keys; grep the capture before designing around "the API cannot do X".
  - Permissive schemas (`.passthrough()`) make an unread field silent. Capture authority (who may capture) and capture cadence (when to re-capture) are separate policies; check both.
- **C1b.49** Check the reachability of TEST INPUTS and cap conditions before writing boundary tests: compute the maximum achievable with the predicate pinned, and the step size of the score.
  - Prove an extraction is not silently empty by counting source elements against extracted text, and validate a hand transcription against an independent declared total (a category checksum).
  - When a comparison passes, read the TREND across its rows: a monotonic drift toward the edge of the sample is a prediction you can test. Commit the failing case with an expected-fail marker.
  - Name the exact model id in any capability finding; one model's refusal is not the family's. Test a narrow band deterministically with a fixed scorer, never by authoring an input to hit a number.
  - A sole-member category is a single point of failure where score and coverage move in opposite directions; sample boundary cases repeatedly and record the spread on the discriminator.
  - jsdom does no cascade or layout, so it cannot assert computed style or contrast; put that test in a real browser and keep any unit-tier proxy labelled as weaker.
  - When scratch files collide, verify content against a fixed manifest of anchors, never by file size.

- **C1b.50** Code that never discriminated on the live population is untested: a filter that removes nothing, an aggregate over a parent that always has one child, a branch that always emits zero items.
  - Report such a measurement as "unexercised", not "verified": it is evidence about the population, not the code.
  - An aggregate (`min`, `max`, "latest across all X") grouped by a parent the writer creates fresh each time has never run; the feature that creates a second child activates it with no diff to the aggregate.
  - Settle it on a scratch DB with a one-row control beside a two-row subject, then mutate the aggregate. Grep for any path that reuses an existing parent id: if one exists, it is a current defect.
  - A required step that has only ever run against zero rows is in the same state.
  - For workflow Code nodes, run the SHIPPED code offline against real captured payloads, widen the window to force rare branches, and tally verdicts per sub-type; a sub-type with no verdicts is the alarm.
  - Verify field population per sub-type, never on the aggregate; treat a mutation patch that matched nothing as a harness error and verify the restore.

- **C1b.64** A diff that touches a shared fixture or seed changes every test that loads it. The runs that accept it must cover every consumer of that fixture, not a `--filter` subset chosen for the feature; grep the fixture's name for the consumer list.

## Examples
- C1b.40: a `HEALTHY` intake fixture had no `scope`; it surfaced only when a peer made nine fields required and four compile errors landed in the fixtures.
- C1b.41: a resolved alert rendered "30 of 30 ads" in the present tense under an all-clear; 37 green tests and 9 of 9 mutations had not seen it.
- C1b.42: a capture script stamped `datetime.now()` into five fixture files, so every re-run produced a diff.
- C1b.43: a truncation flag documented as `count == limit` was true at exactly 20 rows with nothing cut; "20+ (showing first 20)" stayed true there.
- C1b.46: a spill cap of 5 against 77 real extractions shipped behind 374 tests, per-guard sabotage and a replay of a real copied page.
- C1b.46: an HTTP node built its body from all input items and ran once per item, so 25 leads caused 25 identical calls; no single-payload replay could show it.
- C1b.47: three gate parameterisations had zero false rejections on 21 must-accept documents; a constructed reject set of two-column documents killed two of them.
- C1b.49: a score in steps of 0.2 made a requested 64.9 boundary test unreachable, and 3 of 9 cap conditions could never exceed 80, so their tests passed with the cap code deleted.
- C1b.50: the same record was kept for months less once a second record shared its person, measured on a throwaway Postgres; one ticket sub-type never carried an email, so its check emitted nothing.
- C1b.64: a fix extended two shared seeds and ran only the matrix tests (1 each); the lead accepted on those. A reviewer found 4 other classes on the same seeds had not run since; they then passed 75/75 and 80/80.
