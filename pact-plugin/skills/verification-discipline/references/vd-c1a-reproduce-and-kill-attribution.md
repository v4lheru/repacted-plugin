# VD-C1a: reproducing claims, test rot, and which test killed it

Context: read this before relaying a coder's "N tests failed pre-fix" or "M mutants caught",
before citing a teammate's harness, and before trusting a suite, fixture or counter-test that
was written against an earlier world. Harness mechanics are in
[vd-c1a-mutation-harness.md](vd-c1a-mutation-harness.md).

Also read this when a mutation run has produced reds and you are about to say what they
mean: "my test catches it", "this test is redundant", "the fix is covered", or "this
counter-test proves the gate". What a survivor means is in
[vd-c1a-survivors-and-direction.md](vd-c1a-survivors-and-direction.md). Making the run itself
honest is in [vd-c1a-mutation-harness.md](vd-c1a-mutation-harness.md). Entry file:
[vd-c1a-prove-it-can-fail.md](vd-c1a-prove-it-can-fail.md).

## Canonical rules

### Reproducing claims, and test rot
- **C1a.20** Reproduce a coder's red-baseline and mutation claims in a `git archive` scratch export, never in the shared worktree, and run a control proving each revert took effect.
  - Extract the base commit, symlink `node_modules` from the main repo, copy the post-fix test over the pre-fix source, and call the repo's runner binary by absolute path (`npx` may fetch its own).
  - Grep-confirm each revert, then run the OTHER suite on the reverted tree and watch it go red; a stash that silently failed once produced "green with the fix reverted" on an unreverted tree.
  - The highest-value mutation is the over-correction one. Also note which tests passed pre-fix: they prove preservation, a different claim from change.
- **C1a.21** Run a coder's shipped mutation harness yourself: the mutation SET, not the assertion count, is load-bearing, and the harness can disprove the design document.
  - Count CAUGHT and MISSED lines rather than eyeballing a tail; read which mutations exist.
  - State what the harness cannot reach and what its stubs are models OF; record such results as "executed against a read model". Then check the design doc and comments were updated.
  - A coder who deletes a mutation that cannot fail is applying the discipline; record it as a positive.
- **C1a.22** Tests, fixtures, counter-tests and scope rulings rot silently. Use PASS/FAIL/PENDING runners, relational assertions and harnesses committed to the project tree, and build the rot announcement in.
  - PENDING means the subject is not built yet and carries a named precondition predicate that arms the test. Not-built must not read as FAIL (people stop reading red) or as skip (a comfortable green).
  - Assert relationally (`fallback output index === rule count`), not literal counts that stop meaning anything at the next change.
  - A harness in the session scratchpad is a deliverable-shaped hole. Commit harness, fixtures and a machine-readable result; snapshot-pin the code it loads and re-run the previous snapshot before reporting a red.
  - Four staleness shapes: a ruling against a changed world, a counter-test keyed to a lapsed condition, an expectation captured from a wrong system, a mutation that passes on a parse error.
  - Ask of every expected value: from the CONTRACT or from OBSERVED behaviour? Label characterisation tests as such and never let them assert correctness.
  - When a red survives a fix, rule out your own stub first; drive the real upstream rather than your model of the contract under test.
  - Falsify the premise before designing tests around it, name the environmental assumption behind any "by design" acceptance with a test that breaks with it, and ask whether a resilience claim INDUCED the failure or read a setting.
  - A loop that aborts the batch on the first failing item is an amplifier: assert partial degradation ("an item that resolves is still honoured"). A diagnostic with no working sink is no diagnostic; verify the sink separately.
  - Keep evidence classes mechanically apart (verified in production with an execution id, offline only, unverified).

### Which test killed it
- **C1a.23** A mutation proves the SUITE catches the mutant, not that your test does. Record per-test kill lists, run the mutant against your spec alone, and use one mutant per guard clause.
  - A fixture constant can be the real discriminator. With a null-guard removed, `0 > null` is false and `7 > null` is true, so the test seeded with 7 survives. Name load-bearing seeds IN the fixture, with the arithmetic.
  - Measure per mutant before deleting anything as redundant: the badly named test can be the sole guard and the purpose-built one vacuous. Fix the name, keep the coverage.
  - A comment claiming a test "pins" something is the claim to distrust. Rewriting a fixture is itself a trigger to re-mutate: the old proof was about the old input.
  - Prefer asserting the REASON (which branch fired, via a log event or discriminant) over a value a coercion can reach by two routes.
- **C1a.24** One red certifies ONE clause. Enumerate a compound check's clauses and target files, mutate each, and report per clause ("clause A fires, clause B cannot").
  - Ask whether the failure is even expressible in what the clause reads. "Vacuous" and "misnamed" are different verdicts, as are "cannot fail" and "cannot fail on a semantic break".
  - A warning pushed to a list the exit path never reads cannot fail; a whole-file substring guard is defeated by a second, legitimate occurrence of the substring.
  - Never read an exit status through a pipe (`cmd | tail` reports tail's), and state in the HANDOFF how codes were captured. Mechanics: repacted:shell-tooling-traps.
  - When handed a teammate's prediction, run it AND a control that should behave differently.
- **C1a.25** Evaluate a proposed fix or replacement assertion before recommending it: run it against the escape set, the target mutant, its sibling, a degenerate implementation and a correct-code control.
  - Sort escapes into BYPASS (code wrong, check green) and SILENT DISARM (code right, check stops guarding, for example an innocent rename of a loop variable). The distinction picks the fix.
  - An equality assertion is satisfied by a function returning a constant: pair every equality with a distinctness assertion. The intuitive floor (`> 0`) often turns can-never-fail into fires-on-correct-code.
  - When no clean fix exists, narrow the check's overclaiming comment and move the guard to a type-level test rather than inventing a weak regex.
  - For an error-path suite the degenerate is "make the primitive fail unconditionally"; only a success-path control can see it. Check the caller too, and verify at the fix commit AND at HEAD.
- **C1a.26** Mutate the RULE a check encodes, not only the subject it watches, and cross paired artefacts empirically (rule and subject, fixture and predicate, schema and validator).
  - A fixture built to catch a class of wrong answer tests only the relationship it encodes: "contains", "starts with" and "ends with" over-matches need separate rows.
  - When a guard uses another implementation of the rule (picomatch vs `matchesGlob`), diff the two on edge cases and flag experimental dependencies.
  - Never cite a test by its title; quote its assertion. `toEqual` rejects an added extra where `toContain` accepts it.
- **C1a.27** A cross-cutting mechanism (a global default, a config predicate) is the likeliest thing to have zero coverage, because it looks proven. Mutate it: disable it, invert it, break its primitive.
  - A per-site opt-in fails by omission (assert each site opted in); a global default fails by escape (opt-outs, code that never enters it), so prefer absence checks there. Count call sites, not pages.
  - Count the doors: code that never enters the mechanism, and the sanctioned escape hatch. Hunt handlers that turn failure into a successful value (`res.ok ? json : null`).
  - After a bug is fixed by hand, revert the fix. If every gate stays green, it can come back silently; that revert also settles which proposed clause must not be trimmed.

### Does the test reach the code
- **C1a.28** A counter-test must mutate the CODE PATH and make the gate's own assertion fail. A hand-mutated copy of the output that differs from the expected value proves only that two structures differ.
  - Monkeypatch the real helper, re-run the real entry point in `try/finally`, and factor the gate's assertion into a helper that both the gate and its counter call.
  - Assert the CONTRAST on one mutated run: the weaker gate still passes, the aggregates are unchanged, only the new check fails. Tell: a counter-test body with no call to the function under test.
- **C1a.29** To test "is this already covered?", kill the layer between the tested return value and the outcome (make the store's write a no-op). A spec that stays green is blind to that layer.
  - Pair every "nothing changed" assertion with a control on the same fixture that does change, and name a mutant that kills the control.
  - A session scratchpad is shared between teammates: namespace harness filenames per agent, and re-read a harness script before trusting a run you did not just launch.
- **C1a.30** A control must be INSENSITIVE to its mutation and must actually run. One that dies with the mutant is a duplicate; one written below the killed assertion in the same test never executes.
  - Split the control into its own test and confirm it shows as a PASS in the mutation output. An absence control can survive because execution never reached the branch; pair it with a positive fact.

## Examples
- C1a.22: a 65/65 offline suite written to a session scratchpad was unrecoverable 27 days later.
- C1a.23: two tests pinned one exemption; removing it killed only the badly named one, because its seed was 0 and the purpose-built test's seed was 7.
- C1a.24: a compound lint check was certified by mutating clause A; clause B pushed to a `notes` list and matched a whole-file `.includes(".sort()")`, so it could never fail.
- C1a.25: a proposed `cacheKeyFor(a=1&a=2) === cacheKeyFor(a=2&a=1)` also passed on a function returning a constant.
- C1a.27: five of five mutations of a global query error policy were uncaught, including setting the predicate to `() => false`.
- C1a.29: making the store's write a no-op killed 12 of 13 new tests and left the whole pre-existing spec green.
