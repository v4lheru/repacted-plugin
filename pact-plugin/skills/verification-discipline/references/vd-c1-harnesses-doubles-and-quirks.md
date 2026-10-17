# VD-C1b and VD-C1c: harnesses, doubles, and quirks that fake a red or a green

Context: read this when a harness, a wrapper test, a copied precedent, a delegated test or a
test double stands between the test and production. Each can differ from production in a way
that hides a whole class of bug. Copies of the rule and untested seams are in
[vd-c1b-copies-mocks-and-seams.md](vd-c1b-copies-mocks-and-seams.md). Entry file:
[vd-c1b-assertions-and-fixtures.md](vd-c1b-assertions-and-fixtures.md).

Also read this when a test result looks wrong in either direction: a caught-error test
goes red, a regression shows as "skipped", a suite passes on one machine only, a control
passes for no visible reason, or a required report never appears. A red is a claim about the
harness as much as about the code. The entry file for runner and pipeline scope is
[vd-c1c-runners-and-toolchain.md](vd-c1c-runners-and-toolchain.md).

## Canonical rules

### Harnesses, wrappers and precedents
- **C1b.58** A harness whose props are less stable than the real caller's cannot observe staleness bugs from memoisation. Copy how the caller PRODUCES each prop, not just its type.
  - Pass the setter itself when the parent does; suspect the harness before declaring a staleness mutant equivalent. A harness more stable than the caller hides re-render bugs.
  - Mounting the whole page is not testing it: assert at least one thing in the chrome around your subtree. When you guard one consumer of a premise, grep for the others; a disabled control is a claim too.
- **C1b.59** Traffic flowing past a wrapper is not a test of the wrapper. Name the function under test; if the verification does not import it, the verification does not cover it.
  - Run the wrapper offline with a mock transport (`httpx.MockTransport`) and assert what the other side RECEIVED per host.
  - httpx `aiter_bytes()` yields decoded bytes: drop `content-encoding` and `content-length` before rebuilding a Response. Strip `Authorization` on any cross-host redirect hop; treat a subdomain as another host.
  - For a control doing real DNS or IO, fake the stdlib call per test rather than adding a bypass seam, and keep one unfaked test. Never add a production off-switch to a security control for dev convenience.
- **C1b.60** Mirroring another surface's fix imports its conventions and its test gaps. Diff the two files' imports and local helpers first, and raise a divergence at teachback with both options costed.
  - A property claimed on "every" path is often asserted only on the convenient one: enumerate the paths, convert to a table over every outcome, and add an emptiness control for the recorder.
- **C1b.61** When a test delegates the real control to another tier's test, run that tier's EXACT fixtures against a mutant of the control. Fixtures placed where the engine neutralises the attack pass.
  - Find the accidental neutraliser before filing, derive "identical" goldens yourself from the old blob with a one-character mutation control, and pair with a discriminating positive control.

### Mocks and goldens that disagree with production
- **C1b.62** A test double may be simpler than production; it must never DISAGREE with it, least of all in the direction that checks less. A mock stricter than production disarms the gate it tests.
  - Whenever you add a filter or normalisation to production, grep the mocks and fixtures for that field and ask what older fixtures OMIT; the default branch is the one the whole suite runs through.
  - A mock that RESTATES a production rule drifts every time the rule moves, in either direction. Make the mock ask the same question production asks, resting on a required field.
  - A fixture that reproduces the right SHAPE of a population cannot answer a MEMBERSHIP question: when identifiers feed a lookup (region, tier, owner), take them from the real population and the real lookup.
- **C1b.63** A budget test that checks only honesty cannot catch over-truncation: when truncated, assert every slot is used, plus a boundary pair (largest N that fits, and N + 1).
  - A golden "identical except X" needs two comparisons: X-normalised AND exact equality on a fixture that holds X at its old value. Capture the golden from the unmodified code first.
  - A `null` that means both "unintelligible" and "not applicable" collapses a three-valued answer into an outage. Let non-applicable rows parse with their real type and count applicability at the caller.

### Harness quirks that fake a red or a green
- **C1c.14** A throw in a vitest `beforeAll` reports every test SKIPPED with 0 failures (exit is still 1). Catch in the hook and assert in a named test, so a regression reads as failures.
  - Pattern: `try { result = await run() } catch (e) { syncError = e }` in the hook, then `it("RUNS AT ALL", …)` rethrows with detail; a `requireResult()` helper gives dependent tests a readable failure.
  - In a mutation control, read per-test statuses, not the exit code: "exit 1" and "the test caught it" are different claims.
- **C1c.15** A persisting throwing `mockImplementation` is re-reported at teardown and fails a correct caught-error test. Use `mockImplementationOnce`, or make the RETURNED object's method throw.
  - Verified 2026-08-27 on vitest 3. `vi.fn(() => ({ select: () => { throw new Error("boom") } }))` is honest and often the more realistic failure (client built, call failed).
  - When a caught-error test goes red, instrument the return value before touching the source. The quirk attacks exactly the error-path contracts ("degrades to unknown, never throws"), and in a mutation sweep a false red is banked as "guarded".
- **C1c.16** pytest installs its capture handler on the ROOT logger during the call phase, after fixtures (verified 2026-09-21, pytest). Isolate root handlers INSIDE the test body, never in a fixture.
  - Otherwise `basicConfig` no-ops (the root already has a handler), the stream stays empty, and `assert SECRET not in output` passes vacuously. A scenario-reached assertion exposes it.
  - Worse, `for h in root.handlers: h.addFilter(F())` attaches your filter to pytest's own handler, which outlives the test and filters every later test's output, including a control that must show a leak.
  - Use a context manager (save handlers and level, clear, yield, restore in `finally`), and monkeypatch `sys.stdout` before the call, because `basicConfig(stream=sys.stdout)` binds at call time.
  - General form: code that enumerates and mutates global interpreter state (root handlers, `sys.modules`, warnings filters, `atexit`, signal handlers) also reaches the harness's registrations.
- **C1c.17** In a Node `vm` harness, build every `instanceof`-checked value INSIDE the context. A host-realm `Date` or `Array` fails `instanceof` and silently takes the fallback branch.
  - Use a factory: `vm.runInContext('new Date(__ms)', ctx)`. The tell: a type-dispatch test passes under every variation, including ones that should break it. Assert the precise branch's distinctive side effect, not the final value.
  - Same failure across iframes, worker boundaries and duplicate copies of a package in one tree.
- **C1c.18** New async tests collected early can break later tests whose helpers call `asyncio.run()`. Run the full suite in collection order on the committed tree, with the base commit as control.
  - Bisect by RANGES of files in `--co` order, not by pairs (every pair passes). Report it as harness fragility, not a regression.
  - A DB test that skips without its DSN counts as NOT RUN.
- **C1c.19** A unit test that dials the network makes the suite's verdict a property of the machine. Assert the pure predicate at an offline seam; delete or re-site the test, never raise the timeout.
  - Find the seam where the positive case is reachable offline (a route that short-circuits before the model call). Rank tests by duration: anything over ~50 ms in a pure suite is a network call or a real timer.
  - In an `&&` chain (`typecheck && vitest run && test:css`), an early failure silently disables every later guard, and nothing reports it.
  - Live calls are mandatory once, deliberately, OUTSIDE the unit suite.
- **C1c.20** A report on stderr (Postgres `RAISE NOTICE`, warnings, `console.error`) can be swallowed by a runner or wrapper while the exit stays 0. Emit a required report as a RESULT SET on stdout.
  - Put the reporting `SELECT` after `COMMIT` so it reads committed state, and name columns as the assertions to check (`rows_updated`, `untouched_after`).
  - `RAISE EXCEPTION` stays the refusal mechanism: the abort path is loud by construction, the success path is where a report disappears.
  - Absence of output is a claim. Test the channel with a trivial emission before concluding the value was empty or the branch never ran.

## Examples
- C1b.58: an inline arrow prop rebuilt every render hid a staleness mutant; passing the real `useState` setter turned it red.
- C1b.59: 50 of 50 raw `httpx.get()` calls proved egress while the shared wrapper, which leaked credentials on redirect and double-decoded gzip, never ran.
- C1b.62: a mock filtering on `type === 'Email'` dropped typeless rows the real parser read as Email, turning three write-suppression tests red.
- C1c.14: a regression test against the broken code printed `PASS (0) FAIL (0) skipped (6)`; after the fix to the hook the same run read 3 passed / 3 failed, each named.
- C1c.15: a writer returned `{rowsWritten: 0, failedBecause: …}` and did not throw while its test was red; the coder went looking for a missing `try/catch` that was already there.
- C1c.16: a redaction filter leaked forward through pytest's handler and made a control that asserts the unprotected path leaks show `[REDACTED]`.
- C1c.17: an Apps Script "mixed string/datetime column" assertion had only ever exercised the string fallback.
- C1c.18: 19 unrelated HTTP-ingest tests failed with "There is no current event loop"; the CI job ran one directory only and stayed green.
- C1c.19: a "supported model id does not throw" test took 244 ms (fast 401) on one machine and timed out at 5000 ms on another, and its failure skipped the compiled-CSS guard behind it.
- C1c.20: a backfill's required row count printed nothing; the output was `BEGIN / DO / SELECT 4 / DO / UPDATE 4 / DO / COMMIT` with `client_min_messages = notice`, because the wrapper ate stderr.
