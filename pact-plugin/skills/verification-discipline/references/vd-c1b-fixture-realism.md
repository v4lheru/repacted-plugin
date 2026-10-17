# VD-C1b: fixtures that can discriminate

Context: read this when building or reviewing a fixture, a captured response, a literal the
test compares against, or a test environment standing in for production. A fixture that makes
the right and the wrong implementation agree cannot be evidence about either. Entry file:
[vd-c1b-assertions-and-fixtures.md](vd-c1b-assertions-and-fixtures.md).

## Canonical rules

- **C1b.29** A fixture where the correct and a plausible wrong implementation produce the same value cannot discriminate. Build the case where the candidates DIVERGE, then mutation-test.
  - Size fixtures past every boundary they touch: a cap (use cap + n), a page size (more than one page), a dedupe (a real duplicate), a sort (unsorted input), a filter (rows on both sides).
  - When a value could come from a STORED column or a RECOMPUTATION, build a fixture where they disagree. A partial vocabulary overlap (3 of 5 labels coincide) is worse than none.
  - Uniform inputs are where distinct formulas coincide: make fixtures non-uniform along every dimension, cross a decision boundary, and assert ordering rather than inequality.
  - Two independent axes holding the same value (two version constants both `1.0.0`) defeat a shape assertion: assert against the imported named constant (`toBe(REDACTION_VERSION)`), never a regex.
  - Ask whether the mechanism you are fixing applies to your own fix; and the example that motivated a fix is its worst test, so verify at the unremarkable middle.
- **C1b.30** To prove a field feeds a hash or composite key, vary ONLY that field. Two real records that differ elsewhere in the projection hash differently with the fix deleted.
  - Copy one record and change the single field; the realistic-looking pair is the vacuous one.
- **C1b.31** Fixtures for an external response must be CAPTURED responses, never written from the request you send or the vendor docs: a hand-written fixture agrees with the code by construction.
  - Save raw bytes with status and content type, replace wrong fixtures from new captures rather than editing them, and label any synthetic fixture loudly.
  - Prefer a NEGATIVE detector (recognise the confirmed failure signature) over predicting the success shape; pick by which failure direction is survivable.
  - When the datastore blocks a live smoke, drive the real app in-process with only the datastore and paid calls stubbed; capture one body per state and re-capture, never hand-edit.
  - Diff the section of a document that STATES a shape against the section that PARSES it; a syntax check (`node --check`) is not behavioural verification; keep the old implementation as a negative control.
  - To characterise a transform defect, print the ENTIRE output structure, not the fields you suspect. When an unknown gates severity, present severity as a table over its values.
  - Pair a lexical drift signal (unmapped labels) with a set-difference signal (missing expected labels) and state the residual hole.
- **C1b.32** Assert literals production actually emits and the store actually accepts. A literal defined only in SQL (an enum, a CHECK list) makes a test green over a domain production cannot produce.
  - Derive DDL-only domains from the migrations and assert engine vocabulary is a subset of the DB's, with a parser-sanity count and a discrimination check; never substitute a hand-written array.
  - Annotate a fictional-domain test instead of deleting it or "fixing" it green. Idealized vendor values (a clean label where production sends an option URI) make a feature dead and its tests green.
  - `page.goto` on a 404 does not throw, so un-skipping and updating snapshots blesses a 404 page; check readiness gates in both directions.
  - Reproduce a coder's sabotage across the WHOLE suite and ask how much of it noticed. A fixture's provenance header states what it does NOT license you to conclude.
  - Each exclusion-list entry is a separate claim needing its own evidence. Prefer a declared value over a detected one, and represent anything load-bearing in the medium the system reads.
- **C1b.33** Diff a parser's exact-match label set against the LIVE capture, label by label; a set copied from an older doc drifts silently and fixtures built from that doc agree with it.
  - Measure both wire variants: a delimited unknown label lands in `unmapped` (loud), a bare one is absorbed into the preceding value (silent). Run the parser body on live label text.
  - Prefix stems absorb drift but over-match prose; audit the remedy too (a label in both the exact set and a stem's reach bypasses the guard; a new stem-shaped label can hijack routing).
- **C1b.34** A config-key rename can pass every test when tests read the key through the exported constant and the fallback equals the deployed value. Assert the literal key once, spelled inline.
  - Prove it live with a value that DIFFERS from the fallback; same-as-fallback proves nothing.
- **C1b.35** A derived field stored beside its source diverges in hand-built fixtures. Export the single derivation for fixture builders and add a consistency assertion that throws naming both sides.
  - Test both directions of inconsistency and that a correct hand-built object is accepted. The inverse (derived half set, source half empty) makes source-reading functions pass vacuously.
- **C1b.36** A half-open range ladder (`[lo,hi)`) over a bounded domain excludes the maximum. Leave the top band unbounded and CONSTRUCT the maximum in a test; non-overlap does not prove coverage.
- **C1b.37** Reading dimensions is not validating an image: headers survive truncation. Check a JPEG ends with EOI and a PNG's per-chunk CRCs and `IEND`; macOS `sips` exits 0 on truncated or HTML files.
  - Size the fixture so the cut lands in the last layer, make each injected corruption catchable ONLY by the check under test, and assert the specific rejection reason.
  - A square fixture cannot detect a width/height swap; use non-square fixtures for any (w,h), (start,end) or (lat,lng) pair.
  - Mutate what a check READS, never a label both the producer and the oracle import: renaming a shared reason string moves both sides together.
  - An instrument banned as a validator is banned for a PROPERTY, not globally. When a test and the code disagree, settle it with a third source; a red is a disagreement, not a verdict.
  - Reconciliation proves arithmetic, not meaning: check that fields which should be empty on success are empty.
- **C1b.38** Prove an HTTP or SDK client timeout against a local server that accepts and never writes. A sleep inside a patched transport runs outside the network layer and ignores the client's timeouts.
  - Wrap the call in an outer `wait_for` guard and close the server properly. An unset SDK timeout is the normal state, not an edge case.
- **C1b.39** A test database can carry triggers production lacks, so a DB-tier assertion passes for a reason prod does not share. Detect it behaviourally in a transaction you roll back.
  - Reproduce production's state with `SET LOCAL session_replication_role = 'replica'` (one connection, no lock, reverts with the transaction); never `ALTER TABLE ... DISABLE TRIGGER`, which locks and can deadlock.
  - Connection-scoped suppression needs an executor seam in the function under test. Better still, build a private scratch DB. Postgres specifics: repacted:postgres-supabase.

## Examples
- C1b.29: a round-trip test with 2 excluded partners (cap 25) could not tell the true count from the list length; a cap + 5 fixture could.
- C1b.29: `20 × average_rating` was asserted for a weighted score; all-3s gave 60 for both means, a skewed input gave 32.0 vs 26.15.
- C1b.31: one test file carried four false premises about one upstream, including `{status: 401}` for an error that arrives as HTTP 200 and LF where success bodies are CRLF.
- C1b.32: a write op's only trigger was an enum value Postgres rejects at write time, so the op was unreachable while its test stayed green.
- C1b.33: a known-label set spelled one label with a single `c` where live forms used a double `c`; 69 of 69 tests passed because no fixture contained that label.
- C1b.36: bands `[95,100)` over `CHECK (score BETWEEN 0 AND 100)` gave a perfect score no band.
- C1b.37: a PNG CRC fixture flipped a position that landed in the `IEND` length field, so the length check rejected it and CRC verification never ran.
- C1b.38: a 0.3 s timeout with 1 retry returned in about 1.1 s; the SDK default is a 600 s read timeout with 2 retries (verified 2026-09-22, openai-python).
