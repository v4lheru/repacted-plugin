# VD-C1c: runners, pipelines and toolchain scope

Context: read this before quoting a pass count, "typecheck clean", "lint clean" or "CI is
green", and before trusting a report file, a harness or a pipeline to have run what you
think it ran. A count is a fact about what the runner COLLECTED, in which environment, in
which pipeline. How to obtain a trustworthy count through rtk, if installed (exit codes, `tcount`,
truncated output) is in repacted:shell-tooling-traps. Whether the test itself can fail is in
[vd-c1a-prove-it-can-fail.md](vd-c1a-prove-it-can-fail.md).

This family is split three ways:
- this file: does the gate run, and what does the count cover (C1c.1–C1c.6);
- [vd-c1c-clean-state-and-checkers.md](vd-c1c-clean-state-and-checkers.md): machine greens, the committed tree, clean entry points, built environments, linters and checkers (C1c.7–C1c.13);
- [vd-c1-harnesses-doubles-and-quirks.md](vd-c1-harnesses-doubles-and-quirks.md): harness behaviour that fakes a red or a green (C1c.14–C1c.20).

## Canonical rules

### Does the gate run at all
- **C1c.1** "The guard WORKS" (an exit code) and "the guard RUNS" (a pipeline property) are separate claims. Prove RUNS from the target branch's pipeline JOB LIST, per pipeline class, not the YAML.
  - A declared stage with zero jobs SUCCEEDS forever. The stage name, test files on disk and a green pipeline all point the same wrong way. Look for jobs assigned to the stage and for the runner invocation.
  - Job membership is per pipeline and decided by `rules:`: push, `merge_request_event` and default-branch pipelines of one commit can hold three different job sets.
  - Enumerate `allow_failure` PER JOB. One permissive job does not make the stage toothless, and a repaired instrument wired to a permissive job is still disconnected.
  - "CI runs X only" is two claims, what EXECUTES and what BLOCKS; check both against the real CI file. A single `allow_failure: true` on a shared YAML anchor is invisible at every job that inherits it: grep the anchor, not the job.
  - Also check typecheck escape hatches (`ignoreBuildErrors`, `ignoreDuringBuilds`) and what tsconfig `include`/`exclude` covers. Put a new gate where enforcement provably lives, often a lint script in an existing CI-gated idiom rather than a test.
  - The obvious remedy can recreate the defect: a CI job that sets a skip hatch (`ALLOW_SKIP_DB_TESTS=1`) runs green while the highest-value tier never executes. Provide the dependency or run only the in-scope project; never silence the tier.
  - "Job appears in a default-branch pipeline" is unreachable by whoever opens the MR. If the brief holds both that and "open the MR and stop", say so at teachback.
  - A comment-only edit asserted to be behaviour-neutral is still untested: re-run both polarities on the post-edit bytes.
  - `tsc --noEmit` is not the framework build (it skips Next.js route-type validation). Never offer a faster local typecheck as a substitute for the real build gate.
  - When reporting a missing tier, annotate earlier "tests pass" claims rather than deleting them, and scope it honestly: "the test tier is not wired into the pipeline" is not "the code is untested".
- **C1c.2** A new CI gate fails only in the DEFECT direction. Report the benign direction as information, or the gate goes red on correct code, becomes `allow_failure`, and then becomes furniture.
  - An always-red job (red by design, say a credential never provisioned) is worse than an ungated one: its failure carries no information. Promote the one deterministic job to blocking and make the red job informative or delete it; adding directories to non-blocking jobs beside it buys nothing.

### What the count covers
- **C1c.3** A pass count covers your change only if the runner's `include` glob and environment could collect a test for it. Check both, and whether any test references the changed symbol.
  - If coverage is structurally absent, say so in the HANDOFF as a dispatch input for TEST, and substitute a real smoke check (grep the production build output for the rendered string).
  - When the untestable part is LOGIC inside a component, extract it into a module the glob collects. That is decomposition; standing up a DOM test stack inside a bug fix is scope-smuggling.
  - Prove both halves: COLLECTION (the default-run suite total rises by your new tests) and DISCRIMINATION (mutate the fix back and watch them fail).
- **C1c.4** Vitest (esbuild) strips types and never typechecks. Report `tsc` and the suite as SEPARATE exit codes, never "tests pass" alone.
- **C1c.5** "Typecheck clean" often excludes the test tree. Read tsconfig `include`/`exclude` before trusting type errors, or type assertions, in specs.
  - To check a spec, extend the package tsconfig in a temp config without the test exclude, run `tsc --noEmit -p` on it, and report your file's errors apart from the tree's baseline. Delete the temp config.
  - Unchecked fixtures deliver `undefined` for a `T | null` field. A fail-closed guard must test the SHAPE (`typeof x !== 'string'`), not `=== null`, and the absent-field case needs its own test.
  - `@ts-expect-error` in an unchecked spec is theatre. `expectTypeOf`, `satisfies` and friends are enforced by `tsc` only, never by the runner: confirm CI runs a typecheck and pair each type-level guard with a runtime assertion.
  - To prove a type invariant bites, run `tsc` against a deliberately broken variant of the PRODUCTION source and capture the error code (TS2345, TS2554). When mutation-testing types, read the `tsc` exit apart from the runner's.
  - After a teammate changes a contract mid-task, re-derive: re-grep the interface, and switch any hand-rolled helper to the production function it now duplicates.
- **C1c.6** A report file that is not rewritten on every run returns an earlier or another agent's verdict. Delete it before running, require it to reappear, and check it names YOUR tests.
  - A fixed-path report (vitest's JSON output file under `.vitest/`) is shared mutable state in a multi-agent repo: it records whoever ran last. Check `testResults[].name` and the mtime, not the totals.
  - Predict the count before running. Identical totals across runs that should differ, or 26 tests when you asked for 7, is the tell.
  - A stale PASS during a sabotage check reads as "my test does not cover it", which sends you to rewrite the working test. Suspect a result hardest when it is reassuring.
  - When a wrapper swallows the output (rtk printing "All parsing tiers failed"), the real output never reached you: bypass it with the runner's verbose reporter via `rtk proxy` or the runner binary. Never `| tail` a run that is evidence.

## Examples
- C1c.1: a release-version guard proven red three ways and green once protected nothing for a day: it lived only on a feature branch. A claim "the one lint job is allow_failure" was refuted by measurement: 5 lint jobs, 4 blocking.
- C1c.1: a repo declared a `test` stage with no job; its ~180 vitest files had never run in CI.
- C1c.1: a claim that CI ran one test directory was wrong both ways: a second job ran two more directories, and an `allow_failure` on a shared anchor meant nothing blocked, including the named gate.
- C1c.2: a grid-column checker failing on `children > tracks` would have gone red on day one against normal wrapping grids.
- C1c.3: 31 files / 492 tests green on a `.tsx` edit, with a `.ts`-only include glob and `environment: node`. Extracting the bucketing logic gave 14 collected tests (suite 1107 → 1121).
- C1c.4: `vitest run` green at 404 passing while `tsc --noEmit` exited 2 (verified 2026-08-20, vitest).
- C1c.5: three mutated `expectTypeOf` assertions: `tsc` caught them with 4, 18 and 9 errors, the runner with zero failures (verified 2026-09-07, vitest).
- C1c.6: a stale report said 26/26 PASSED for a sabotage run; another agent's report said 77/77 for a file holding ~37 tests (verified 2026-09-09, rtk wrapping vitest).
