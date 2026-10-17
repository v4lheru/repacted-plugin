# VD-C2b (split): guard scope and proxies

Context: read this when a guard's scope or key might not match the harm: a transport change
under existing controls, a predicate on a symptom, an exemption or skip list, a proxy value, a
scanner's rule set, or a roll-up beside suppressed data. Entry file: [vd-c2b-controls-and-construction.md](vd-c2b-controls-and-construction.md).

## Canonical rules
- **C2b.32** Before swapping a transport, re-key every control that fires on a property of the OLD transport (verbs, status codes, thrown errors), and land the replacements before the swap.
  - Ask of each control: does it key on the thing being controlled, or on an incidental property of the current transport? Replace, never relax: relaxing a verb fence to `['POST']` admits any write.
  - Transports that return errors as RESOLVED values (MCP `isError`, GraphQL `errors[]`, a 200 with an error body) make every `catch`-based degrade flag unreachable. Grep catch blocks that set degrade flags: that list is the blast radius.
  - Write the error-shape conformance test before the adapter: the new protocol's error must make the client reject. Mutate the adapter to resolve-with-empty; if the degrade tests stay green, they were already vacuous. Capture real error payloads as fixtures.
  - Measure mock-layer blindness by construction site (specs that `new` the real client vs a whole-interface mock), reported in tests, not files.
  - A generic `call(tool, args)` collapses a typed read/write boundary into a string; keep disjoint literal unions. An auth-model change can reverse a deleted decision (a credential that must now be written).
  - A rationale reconstructed from the artifact recovers WHAT was decided, never what it was conditional on; read the decision record. An outcome statistic ("524/524 rows are dry-run") describes what happened, not the wiring: check the gate.
  - A premise falsified in the SAFE direction under a surviving conclusion is invisible to citation checks; make its re-derivation a named work item. Read the current shared plan before relaying an "urgent missing item".
- **C2b.33** Route or scope by the writer's own cause condition, never by a stored value or a NULL symptom that legitimate rows share.
  - Split "null-or-empty" into `IS NULL` vs `= ''` before routing; both fail to match and return a confident empty list. Prefer routing on an input you already know (a language code), and write the measured counts beside the predicate.
  - Before a backfill, count rows holding the target value under healthy conditions and list them in the header as NOT touched. Validate the failure signature on an incident whose cause is already known, and count known-good entities on the weakest dates.
  - Add a post-condition that aborts if anything outside scope acquires the correction, and prove the guard armed by a rollback run. `created_at` survives an upsert and dates the first insert only.
- **C2b.34** Audit an exemption or exclusion list as adversarially as the detector: every entry disables the control there, and a contaminated exclusion fails toward "looks clean".
  - A type check (`isinstance(v, str)`) is an exclusion list with no list; audit what the dispatch layer excludes before what the rules match. Validate a negative control's placement against the scanner's skip list.
  - A limitations section that omits one limit is worse than none: it certifies the reader's diligence. A guard's scope tends to follow its founding incident, not the hazard; ask what the guard is FOR.
  - Harvest exclusions from verified-controlled vocabularies, then filter for plausibility. Column names assert nothing. An allowlist constructor proving what is admitted is not evidence a filter would catch.
  - Key an exemption as narrowly as the decision: (file, function), taken from the detector's own output. Split PERMANENT carve-outs from PENDING ones, and fail if a permanent carve-out matches nothing (a rename leaves it protecting nothing).
  - A gate scanning the working tree disagrees with CI scanning a commit; check `git show HEAD:<path>` before striking an entry. Match a skip list against the path RELATIVE to the scan root, and make "0 files scanned" a distinct non-zero exit.
- **C2b.35** Key a gate on the fact it is about, not a proxy for it; a proxy leaks at its boundary values (0, 1, empty).
  - Name the fact, then check where your condition and that fact diverge at zero, one and empty. Split the states (measured and different / unmeasured with work / unmeasured with nothing to write).
  - An exception type is a proxy too: `except RuntimeError -> DATABASE_ERROR` misreports once the callee can also fail elsewhere. Identify by state.
- **C2b.36** Proving a scanner can fail does not prove its rules span the harm: write the rule set as a sentence, compare it to the defect's definition, and re-sweep without the tool's heuristic.
  - Re-survey what the tool models away: any receiver shape, `raise` sites, plain locals, and NESTED arguments (`extra={...}` is a dict). Include a nested variant in the known-bad fixture, paired with a top-level control.
  - A tool's "examined N" excludes unseen shapes from numerator and denominator; say so. Before certifying your own sweep, re-derive it with no name filter (`nameid` vs `name_id` defeats substring tokens).
  - Fix a data-flow leak where the detail is CHOSEN, not only where it is logged. Read what a compensating redactor is seeded from, not what it is named.
  - A redactor catching MOST secret-shaped lines is worse than none, because each mask implies it works. Presence evidence outranks a clean absence scan; do not defer to rank on it.
- **C2b.37** A coarser roll-up beside a suppressed breakdown adds one solvable equation per group, and withholding one group leaks through the complement; publish the new level all-or-nothing.
  - Check each group for a FORCED residual (hidden cells bounded in `[1, floor-1]`), not only group size. Never suppress a row the existing level already published to rescue the new one.
  - Publishing group membership elsewhere turns an anonymous bound into a named value. Test by executing the attack arithmetic from published values, paired with a scenario-was-reached assertion.

## Examples
- C2b.32: a read/write fence asserted HTTP verb sets, and every MCP call is `POST /v1/mcp`. `loadDncEmailSet` set `degraded` only in a catch, while 12 degrade tests stayed green on a throwing mock; 285 of 489 tests were structurally blind.
- C2b.33: 1,296 "null-or-empty" rows were 48 NULL and 221 EMPTY for one language. Of 68,647 zero rows, 31,131 were real measurements that "null every zero" would have destroyed.
- C2b.34: a product column in a redaction PROTECT list held customer names; a type check sent 7,522 numeric ids per sheet past every detector. An untracked-plans guard watched `docs/plans` while the recurrence sat in `docs/preparation`.
- C2b.35: gating an identity comparison on `if enum.refs` let an EMPTY wrong corpus record its identity as verified.
- C2b.36: a validated AST scanner keyed on the caught exception object missed a user identifier logged as a plain local.
- C2b.37: total 36 − unknown 8 − Asia 15 = Europe 13, then 13 − France 10 recovered a suppressed country of 3 by name.
