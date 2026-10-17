# VD-C1a: proving specific kinds of guard

Context: read this when the thing to prove is a particular kind of guard: a behaviour change
with consumers, a database constraint, a time-dependent test, safety copy or a config
predicate, a UI gate before an irreversible action, an invariant, or a scanner. Each has its
own way of passing for the wrong reason. Entry file:
[vd-c1a-prove-it-can-fail.md](vd-c1a-prove-it-can-fail.md).

## Canonical rules

### Before and around a change
- **C1a.37** Before changing behaviour, pin today's behaviour with characterisation tests and run them green. Afterwards report both counts, flipped and unchanged: the unchanged count measures blast radius.
  - Write the tests before the edit; written afterwards they produce the same file and none of the evidence. More flips than predicted reveal a coupling you did not know about.
- **C1a.38** Before a fix lands, record VERBATIM the whole output of a neighbouring arm that is already correct, reached through a condition the system really produces. After the fix it must match exactly.
  - Check replacement vs addition: a fix that adds the right string while leaving the wrong one defined passes every behavioural test.
  - A default that "looks correct" (it names the active workspace) is indistinguishable from a hardcoded one until you change the input and look again.
- **C1a.39** Before writing a regression test against a named mutation, execute the mutation and confirm it changes the answer. `COALESCE(x, 0)` inside `SUM()` is a no-op; the NULL semantics live in the divisor.
  - Right test with a wrong rationale decays: the rationale is what the next maintainer maintains the test against.
  - Uniform fixture values make distinct formulas coincide; use non-uniform values and assert ordering, not inequality.

### Databases and clocks
- **C1a.40** To prove a table constraint fires, build the row WRONG at INSERT. An UPDATE-based negative test is answered first by any BEFORE UPDATE trigger, even against a NOT NULL column.
  - Corrupt the stored value without corrupting the value the arithmetic uses, so only the targeted comparison can object; push a bounded score downward rather than past its upper bound.
  - Assert the error TEXT names the intended constraint, and that the adjacent good row is accepted.
  - A deferred constraint trigger does not run under `ROLLBACK`: the test needs a real `COMMIT` that is expected to fail. Postgres specifics: repacted:postgres-supabase.
- **C1a.41** A determinism fix for a time-bombed test can make its predicate unfalsifiable. Accept the fix on a sabotage of the SHIPPED code, never on the green run.
  - "Safely far from the boundary" and "unable to reach the boundary" are the same position. Prove the bound for every date, walking all weekday-hour combinations, not only today's.
  - Assert a tight bound (the trail stays under one day), not the failure threshold (the TTL).
  - A column written from app code (`new Date()`) and read in SQL against `NOW()` straddles two clocks. Translate into the reading clock's frame and make the translation an assertion.
  - `grep -c` counts matching rows of text, not occurrences; for a cardinality you will cite, use `grep -o <pattern> file | wc -l`.

### Contracts, copy and gates
- **C1a.42** When an instruction contradicts a passing assertion, change the code and escalate the conflict before editing either side. An inverted assertion is indistinguishable from a hollow green.
  - Sabotage any assertion you restore, invert or relax, and record its history in the test's own docstring.
  - Assert the serialised wire (`model_dump(by_alias=True)`), never the declaration (`model_fields`): a declared field can be excluded from serialisation.
- **C1a.43** A guarantee (safety copy or a config predicate) must live where the test runner can reach it. If it sits in untestable JSX or an inline options object, change the code's shape.
  - Moving it is half the fix: source-scan the consumer for the import AND the identifier in the render, plus absence of the banned literal, and prove that reverting the render goes red.
  - A config predicate is a pure function; two node-environment assertions cover it. If the only way to verify a control is a render pass, the control is in the wrong layer (repacted:frontend-verification).
- **C1a.44** For a control gating an irreversible action, try to DEFEAT it (strip `disabled`, dispatch a real event), with a positive control, and verify non-submission at the network layer.
  - A tamper probe is an absence claim: run the same dispatch against a genuinely enabled control and show it fires.
  - "No request issued" needs a before/after delta taken in the ENABLED state, with requests identified by payload, not by count.
  - Before seeding a value to unlock a gate, read every consumer of that value; two consumers can key off different fields.
  - When something stays unverified, state the direction it fails in. A fail-safe gap is a defensible stop; leave the irreversible half to a cleared live smoke run.
- **C1a.45** When a ruling says "revisit if X" or "the test will tell us", interrogate the trigger: could a test satisfy the wording yet keep passing after the very change it exists to catch?
  - Assertions about non-events ("never invoked", "not present") need a positive control in the same test: a spy never wired reports zero as convincingly as a guard that held.
  - A "probably unreachable" hedge becomes a defect once the question is settled, because it understates a structural guarantee.
- **C1a.46** Compute expected strings from the real helpers and write them to a dated file BEFORE rendering, then compare mechanically. Never judge whether copy "sounds right".
  - Import the real modules; a reimplemented expectation agrees with itself.
  - First establish what the fix was supposed to change: the correct fix sometimes leaves a value alone and adds context around it.
  - A divergence between prediction and render usually means the source moved; re-grep before concluding either side is wrong.
- **C1a.47** For a hazard in a structurally unreachable state, pin the invariant instead of adding a guard, and check the invariant test drives the real mechanism rather than iterating fixtures.
  - A test that iterates existing fixtures passes today and after the change it exists to catch, because no fixture exercises the new path.

### Scanners and loops
- **C1a.48** Stop a hardening loop while findings are real but shrinking and repeating one class, and a high-stakes assumption stays untested by any instrument you own. Name that residual.
  - State the residual as a property of the instrument ("the fake cannot fail the way the real API fails") and name the next real evidence and what it would change.
- **C1a.49** A positive control written by the rule's author inherits the rule's blind spot. Build known-bad fixtures from a census of the codebase's real spellings of the call.
  - Before widening a detector, read what the widening newly flags; prune the sanctioned remedy (`type(e).__name__`) before walking arguments.
  - Grep source prose for named test paths and check each exists: "X asserts the two agree" naming a missing file is a false safety claim. Re-verify at report time.
- **C1a.50** Reproduce the DEFECT before believing the FIX: an environment that cannot exhibit the bug cannot demonstrate the fix. Put a known-negative control in the same probe run.
  - A scratch cluster built without the platform's default-privilege grants reported `anon=false` truthfully and meaninglessly; one missing line made its ACL identical to production (repacted:postgres-supabase).
  - A 404 on a function call may mean wrong arity; ask the catalog for the signature. "Grant present" is not "observed returning data".
  - Ask the catalog for the class (`pg_proc` with `has_function_privilege`), not the tree for remembered names.

## Examples
- C1a.37: 18 characterisation tests, 418 green before; exactly 3 flipped and 15 stayed green.
- C1a.39: a predicted `COALESCE` regression was a no-op in exact rationals (60.00 either way); the real mechanism was the denominator.
- C1a.40: 6 of 12 arithmetic checks "passed" by hitting the append-only trigger's error; the suite said 43 passed, 0 failed.
- C1a.41: deriving a simulated date from the DB clock put the fake instant ahead of the database, so the due-predicate was false for every TTL, including 0.
- C1a.42: a test asserting an internal host was never published was inverted to match a crossed instruction; the lead found it only by serialising the response.
- C1a.43: a query error policy set to `() => false`, `() => true`, and a deleted `!res.ok` throw all left lint, unit tests and tsc green.
- C1a.44: stripping `disabled` and dispatching a real click produced an enabled-looking, inert button, because the framework consults its props, not the DOM attribute.
- C1a.49: a scanner's known-bad fixture put every shape at top level while the codebase mostly logged through `extra={...}`; a live `str(e)` in the auth module was invisible.
