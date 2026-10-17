# Counter-test-by-revert and testing-craft patterns

The full text of the counter-test-by-revert method and the codified testing-craft patterns. The router ([../SKILL.md](../SKILL.md)) carries each rule in one line and links here.

## Counter-test-by-revert methodology

A counter-test-by-revert pass falsifies a regression-coverage test by reverting the production fix and asserting that the targeted tests fail with the expected cardinality. Use it whenever you ship a regression test alongside a fix and need evidence that the test is actually coupled to the regression rather than an independent assertion that happens to pass.

Restore-mechanism rules (crash-atomic — survives an interrupted session without losing the original tree):

- **Prefer `git revert -n <sha>`** when the target commit can be cleanly reverted in isolation. `-n` (`--no-commit`) leaves the inverse change staged; `git revert --abort` drops it after measuring cardinality.
- **When revert can't isolate the fix** (the commit bundles test or fixture edits, or the fix is not committed yet), use the red-run recipe in repacted:concurrent-agent-git (`references/shared-index-and-commits.md`, rule 10): the fix stays in a commit or the index while the pre-fix file sits in the working tree.
- **Never `git stash`**, scoped or bare: the stash stack is shared by every worktree and session of the repo, so another agent's pop can take your fix.
- **Never** in-place-edit a tracked file and rely on `cp` from a `/tmp` copy or hand-typed restoration. A crash mid-measurement leaves the working tree in a corrupt half-edited state with no atomic recovery primitive.

After restore, re-run the test scope and confirm the original tree is byte-identical: `git diff --quiet -- <paths>` exits 0, `git status --porcelain -- <paths>` prints nothing.

Document the expected cardinality (`{N fail, M pass}`) in the design or test docstring so a future verifier can check the assertion without re-deriving it.

### Bundled-commit cardinality: source-only revert vs whole-commit revert

When a commit bundles new source AND the new tests that exercise it (a common pattern for refactors that can't be split without a transient broken-import state), `git revert -n <sha>` reverts BOTH halves at once — the inverted source is reapplied without the test that detects the regression, so the failure cardinality collapses to the small number of pre-existing tests that happened to cover the surface. This **masks** the protection the new tests actually provide.

For bundled commits, measure cardinality via **source-only revert** instead:

```sh
# Restore source files to their pre-commit shape; leave the new tests in place.
git checkout <sha>^ -- <source-file-1> <source-file-2> ...

# Run the affected test scope and record cardinality.
pytest <scope> -x

# Restore atomically.
git checkout <sha> -- <source-file-1> <source-file-2> ...
git diff --quiet -- <source-file-1> <source-file-2>  # exits 0
```

The two cardinalities are not interchangeable. Empirical example from a bundled predicate refactor + retargeted tests: `git revert -n` produced `{1 fail}` (only the pre-existing categorical-invariant test broke); source-only revert produced `{33 fail + 1 collection error}` — the 33 retargeted tests are the protection, and only the source-only technique surfaces them.

**Rule for Verification Matrices**: when documenting the expected cardinality for a bundled commit, specify the technique and the expected count. Example row:

> `Counter-test (source-only revert of <sha>): pytest <scope> → {33 fail + 1 collection error}. Source-only because <sha> bundles new tests with the source they exercise; `git revert -n` would mask to {1 fail}.`

Whole-commit revert is still correct for commits that ship source-only (or tests-only); the bundled distinction only applies when both move together.

### In-process mutation: re-arm per test, or mutate on disk

A mutation applied **in process** — monkeypatching the symbol on the module under test rather than editing its source — is silently undone by any `importlib.reload` of that module. The reload re-executes the module and rebinds the real symbol, so a mutation applied once at session start is disarmed for every test that runs after it. Test files reload a module to re-read import-time configuration (an env-derived constant, a registry snapshot captured at import), so this happens in the middle of an otherwise ordinary run and produces no error.

Apply the mutation from a **per-test hook** rather than once at configure time, or **edit the source on disk** — a disk edit survives reload, because reload re-reads from disk.

**Treat a total non-flip as an instrument alarm before a finding.** When arms you expected to redden all stay green, do not report that the code under test is unprotected until you have confirmed the mutation was still armed when they ran. Re-run one expected-red arm in isolation: if it reddens alone but not in the full run, the mutation is being disarmed — the arms discriminate fine. This check matters because a partially-disarmed sweep still reports *some* kills, so its output reads as a specific and plausible finding rather than as a broken instrument, and nothing about it looks wrong.

## Testing Craft Patterns

Testing-craft patterns that surface during PACT review cycles and reach the codification threshold (≥2 instances, multiple specialists, or explicit reviewer flag) live here. Each rule names a specific failure mode in how tests, fixtures, or HANDOFFs are constructed — and the canonical mitigation that closes the gap. The patterns compose: a single PACT cycle can hit all three independently, and each cites the sister patterns it interacts with through their pact-memory IDs.

### Author-blindness in HANDOFF arithmetic

When a HANDOFF author asserts a cardinality, set-membership, or fidelity claim about their own work — commit-test counts, suppression-cardinality matrices, SSOT-fidelity tallies — the claim often contains an arithmetic or set-shape error that the author does NOT catch in self-review. **Author-bias** on one's own work suppresses recounting: the asserting act feels equivalent to the verifying act, so the recount never happens. The **numeric pre-verification illusion** is stronger on arithmetic claims than on prose claims because numbers feel pre-verified at the moment they are written down.
<!-- planning-artifact-exempt: pact-memory ID, content-addressable via secretary / repacted:pact-memory skill, not a commit SHA -->
Canonical body: pact-memory `d319e8e1`.

#### Discriminator vs the ASPIRATIONAL-HANDOFF sister pattern

<!-- planning-artifact-exempt: pact-memory ID, content-addressable via secretary / repacted:pact-memory skill, not a commit SHA -->
AUTHOR-BLINDNESS and **ASPIRATIONAL-HANDOFF** (pact-memory `0bc2c78d`) are sister failure modes of HANDOFF-author bias but differ in the source-of-belief failure they expose:

- **ASPIRATIONAL-HANDOFF**: the author claims X-as-verified when X was actually believed-from-the-dispatch-prompt — a source-of-belief failure. The author never empirically checked X.
- **AUTHOR-BLINDNESS**: the author claims X-as-verified when the author DID empirically check X but mis-counted under author-bias — a counting-fidelity failure. The empirical work happened; the recount didn't.

Distinguishing the two matters because the mitigations differ: ASPIRATIONAL-HANDOFF wants a "is this from the prompt or from your verification?" challenge; AUTHOR-BLINDNESS wants a literal cross-stream recount of the cardinality claim.

#### Worked example — three instances

- **Tier-2 SSOT-fidelity miscount**: a Tier-2 SSOT-fidelity check miscounted prefix-tuple cardinality in the author's own HANDOFF — the empirical scan happened, but the recount step that would have caught the off-by-one was skipped because the asserting act felt sufficient.
- **Suppression-cardinality narrowing**: a fixture mis-characterization claimed double-suppression that empirical retest narrowed to single-suppression — the author had run the suppression check but the cardinality summary line in the HANDOFF was written from memory of the intended shape, not from the actual run.
- **Cross-pass cluster miscount**: a review-HANDOFF re-counted a test-emission tally and surfaced two author-blind miscounts in the SAME specialist's prior implementation HANDOFF — confirming the bias is stream-role-contextual (one specialist switching from implementation-role to review-role catches their own prior miscount only when the role-switch is explicit).

#### Canonical mitigation

<!-- planning-artifact-exempt: pact-memory ID, content-addressable via secretary / repacted:pact-memory skill, not a commit SHA -->
Cross-stream verification (pact-memory `f3f3d093`) MUST run on every cardinality, set-membership, fidelity, or structural-shape claim in a HANDOFF. The recount is performed by an agent operating in an explicitly different role-context from the HANDOFF author — one of:

- **Test-author** running review-role checks on their own implementation HANDOFF (same specialist OK if the role-switch is explicit).
- **Fix-builder** recounting the test-engineer's HANDOFF cardinality before integrating the fix.
- **Review-test-engineer-who-found-but-did-not-fix** recounting the fix-builder's claim before sign-off.
- **Fresh agent** with no prior context in the cycle, recounting from the dispatch artifact and the live tree.

The loop closes when the recount is done literally — re-run the count, re-list the set, re-derive the fidelity tally from the source — not by re-asserting confidence in the HANDOFF's number. AUTHOR-BLINDNESS is the problem; cross-stream verification is the treatment.

#### Detection signature

HANDOFFs that assert cardinality matrices, set-membership tallies, or fidelity counts about the author's own work are at elevated risk. The shape "I verified N of M cases" — especially when N and M are small integers — is the highest-yield surface for a cross-stream recount.

### count_active_tasks fixture-completeness audit

Any test asserting on `count_active_tasks` boundary conditions (`count == 0` vs `count == 1` vs `count == N`) MUST either register all task-owners in the fixture's `team_config.members[]` so the count reflects the intended suppression mechanism, or explicitly assert on the unknown-owner-exclusion path with a docstring comment naming the exclusion as the load-bearing mechanism being tested.

`count_active_tasks` filters by two conjoined conditions: `status == in_progress` AND `owner` is a registered teammate. Tasks with `owner=null` or `owner=<unregistered name>` are **silently excluded** from the count. An under-registered fixture passes a `count == 0` assertion for the wrong reason — the **wrong-reason green** is that the count reads 0 because no owners matched, not because the suppression-or-aggregation mechanism the test intended to exercise actually fired.

#### Worked example — fixture mis-registration

An SSOT-fidelity test passed because of unknown-owner exclusion rather than umbrella-suppression: the fixture's `team_config.members[]` under-registered the task-owners the test exercised, so the `count == 0` assertion held on the exclusion path while the umbrella-suppression mechanism the test was supposed to falsify remained un-exercised. A separate fixture under-registered task-owners in a way that masked which suppression mechanism was load-bearing in the assertion — the test stayed green through a change that should have produced a cardinality shift.

#### Canonical mitigation

**Register all task-owners** — default discipline: list the task-owners the test exercises in `team_config.members[]` so the count reflects the intended suppression mechanism. If the test is intentionally exercising the exclusion path, name it in the test docstring AND verify the assertion would hold without the exclusion (so the suppression mechanism stands on its own). The "would hold without the exclusion" check is the load-bearing verification — without it, the test is indistinguishable from a fixture-completeness defect.

#### Detection signature

Tests asserting on `count_active_tasks` boundary conditions (`count == 0`, `count == 1`, `count == N`) where the fixture's `team_config.members[]` lists only a subset of the task-owners the test exercises are at elevated risk. The shape "count assertion + sparsely-constructed `members[]`" is the highest-yield surface for a fixture-completeness audit.

### Sibling-file convention for parametrized noise-budget regression

Parametrized noise-budget regression tests are N×M matrices that count events across simulated scenarios. Each one MUST live in a sibling test file. That sibling file MUST cross-reference the primary file in its docstring. Do not pack such a test into a primary phase-specific test file.

Primary phase-specific test files count specific event types and require clean fire-counts to assert on Tier-N cardinality. Mixing them with parametrized N×M matrix tests reduces **signal-to-noise** on the cardinality assertions. The reason is that the setup and teardown noise of the parametrized matrix masks the tight fire-count assertions of the primary file. **Fire-count cleanliness** is the property that lets `assert events.count == N` in a primary test file localize a regression reliably. Once a parametrized matrix sits beside it, the same assertion must defend against matrix-induced cross-contamination.

#### Worked example — sibling-file split

In repacted's own tests, the sibling test file `pact-plugin/tests/test_background_work_adversarial.py` sits in the same directory as `pact-plugin/tests/test_background_work.py`. Its docstring names the primary file and records the cause of the split. The assertions of the primary file are tight predicate pins. A large adversarial matrix in the same file costs signal on those pins.

#### Canonical mitigation

**Sibling-file split** is the default discipline. If you add a parametrized noise-budget regression test for a phase-specific test family, create a sibling file. Do not append the test to the primary file. Cross-reference the primary phase-specific file in the docstring of the sibling, so a later reader can find the family. If three or more HANDOFF-cardinality-matrix patterns cluster in one review cycle, the cluster is a signal of a HANDOFF-shape risk factor. That factor is specific to phase-lull tests with N-cell parametrized cardinality matrices. Pair those tests with cross-stream-verifier review at elevated priority. See Author-blindness above.

#### Detection signature

Phase-specific test files that already count specific event types AND gain a parametrized N×M matrix test for the same event-type are at elevated risk. The shape "tight fire-count assertion in the same file as a parametrized matrix" is the highest-yield surface for the sibling-file split.

## Positive control for every refusal test

A test that expects a refusal (403, 404, denied, filtered out) MUST also prove that the same request succeeds for the rightful caller, with the same ids and the same fixture. A refusal alone is green when the resource was never seeded or the id is wrong, so it does not show that the access rule did the refusing.
