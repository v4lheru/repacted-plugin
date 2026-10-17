# VD-C1b: reach and second mechanisms

Context: read this when a test is green and named for a property, and you need to know whether
it ever executed the branch it names, whether a second path produced the same result, or
whether its own setup removed the hazard. Entry file:
[vd-c1b-assertions-and-fixtures.md](vd-c1b-assertions-and-fixtures.md).

## Canonical rules

### Did the test reach its branch
- **C1b.22** A green test named for a property can be satisfied by an EARLIER guard and never execute its branch. Build each fixture so only the targeted guard can fire, and ask which guard actually returned.
  - A module-level reachability probe (a throw at function entry) gives a false all-clear here; only per-branch mutation, in both directions per state, finds it.
  - Fixture ergonomics decide which mistake is convenient: a helper taking a count made the wrong fixture easy. Give fixture helpers predicates.
  - When two guards protect one path, a surviving mutant of the inner one usually means the outer fires first. Build a fixture that disables the sibling by construction, and fix the test, not the code.
  - For a route table, read the handler's check ORDER: a row whose earlier gate (a key check) answers first never reaches the injected failure. Satisfy gates per row and guard that the hook keys are real.
- **C1b.23** Before verifying gated behaviour, check the harness can reach the gate at all; if you author the fixture that reaches it, prove the predicate flipped by observation, not construction.
  - Direct proof: render the predicate's computed value. Differential proof: a token emitted only on the gated arm is present in one render and absent in the sibling.
  - Ask reachability per STATE, not per task, and grade an unreachability claim (loop-invariant vs "not how the input list is built").
  - A clean result on a path you cannot show was exercised is NOT RUN, not PASSED. For a multi-branch validator, print the rejection REASON; a control whose reason names another check is exercising that check.
  - Isolate-mutate: delete exactly one check and re-run the control, beside a whole-validator mutation and an unmutated run. Pin each control to its expected reason string.
  - Record results against stubbed runtimes as "executed against a read model", and ask which source files the harness actually loads.
- **C1b.24** When a second path yields the same outcome, a test cannot fail on the mechanism's absence. Choose inputs only the mechanism can serve, and assert the diagnostics that differ between paths.
  - A rule specified but never implemented is invisible to sabotage (there is nothing to break); only executing the spec's cases finds it.
  - An `except X` is live only if the callee lets X escape; data shows a dead retry (`attempts=1` on a three-attempt job).
  - A mutation that provably landed and still passed means two mechanisms: change the test's CONDITIONS so they separate; never loosen the assertion.
  - Never write "this test was run red" before running it; paste the failing test names.
  - Making an unreachable predicate reachable can break an existing test's PREMISE while it stays green through a second sufficient cause. Probe what old fixtures now resolve to; assert the intermediate value.
  - A masking side effect is not a control: report "the known residual is reachable in more environments", never "provably safe".
- **C1b.25** A test's own setup can remove the hazard it tests. An env override that short-circuits `ROOT = process.env.X || derive()` never evaluates `derive()`, where the bug lives.
  - Build fixtures that CONTAIN the hazardous input (a space, unicode, a quote, a newline, a very long name); a macOS `mkdtemp` path has no spaces.
  - Watch the discarded sink (the polluted field is never stored or read) and boundary absorption (`trim()`, `parseFloat` tolerating trailing junk, a single-row fixture). Use interior rows.
  - When a defect is unobservable through the public surface, call the fix defensive rather than inventing a test; label synthetic fixtures with their reason.
  - An error can name the wrong subject: Node reports an invalid `cwd` as `spawnSync git ENOENT` against the binary. Falsify the obvious reading first.
- **C1b.26** A fixture that seeds wrong values is half a finding: count the assertions that READ the column. A column that only appears in the seeding INSERT is asserted by nothing and cannot fail.
  - Report "fixture representative" and "an assertion that can redden" as two separate items, so the easy half cannot discharge both. Assert the write payload, with a control that the write happened.

### Things that look tested and are not
- **C1b.27** A string that is really code (SQL, a regex, a template, a cron expression, a JSONPath) is inert until executed. Ask whether any test EXECUTES it; run it once against a real instance.
  - Assert on built output as EMITTED: dump the bytes first. Minifiers strip attribute quotes and emit `:before`; styles may be inlined; a CSS transition means a computed style read immediately after a toggle is stale.
  - Assert server-rendered output on the file bytes, not the DOM (the browser repairs missing `<html>`); a virtual-time-budget screenshot never advances `requestAnimationFrame`. Details: repacted:frontend-verification.
  - Before work starts, verify the measurement an acceptance criterion depends on can move, and that each named mitigation has been exercised; a criterion bound to a constant cannot fail.
  - Score a deliverable on its function: anything deciding whether an irreversible action is permitted is an authorization control and needs an adversarial non-author pass commissioned at dispatch.
  - A red test is not a finding until you know its SCOPE (project, filter, env vars) and its CLOCK (file mtimes in a shared tree). Read the failure message, not the file path, before attributing it.
  - Correcting someone else's attribution is the riskiest moment for an unverified attribution of your own; verify the correction to the claim's standard.
- **C1b.28** Claims about documents and states go stale like code. Reference specs BY PATH, never paraphrase, and never put a size count of a moving document in an instruction.
  - After a reversal, sweep for counting words (two, both, all, only) as well as the subject, and re-read sections that enumerate. Sweep for the class, not the reported line.
  - State a count of your own edits only after running it; an overcount survives where an undercount self-corrects.
  - Separate diagnostic commands with `;`, not `&&`: a grep that correctly finds nothing aborts the chain, and the empty output reads as a finding. Print a marker before each.
  - Do not claim a safety a schedule is providing ("nothing built becomes wrong" because the coder has not reached that step). Classify doc edits by effect on the build: SUBSTANTIVE vs CLARIFYING.
  - When justifying a defensive construct, state the affirmative fact ("nothing downstream sorts these"), not a plausible concession.
  - Diff emitted SQL against the reference function line by line; use fully qualified `project.dataset.table` names. A destination you do not own is an external dependency (repacted:google-workspace).

## Examples
- C1b.22: a test named "an uncovered hour makes the whole window unobserved" passed through the baseline path 38 statements earlier; the current-window branch was unguarded both ways.
- C1b.22: deleting a "resolved" break left 62 tests green because the "opened" break fired first in every fixture.
- C1b.23: a re-run safety control fixed four times and sabotage-verified five ways had never rendered its gated branch; every fixture left the predicate false.
- C1b.23: a tiny synthetic JPEG truncation fixture died in the header walk; deleting the end-of-image check left the self-test at exit 0.
- C1b.24: removing `setOffset(0)` left 8 of 8 green because a clamp rescued offset 20 on a 7-row source; a 23-row source separated them.
- C1b.25: a path-handling test suite was 16/16 green while the tool crashed on every real checkout path containing a space.
- C1b.26: 21 `expect` calls in a parity file, zero touching the column the fixture seeded.
- C1b.27: 179 green tests surrounded `WHERE (a, b) = ANY($1::record[])`, which the driver rejects on first execution.
