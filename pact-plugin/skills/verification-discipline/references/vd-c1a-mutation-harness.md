# VD-C1a: the mutation harness

Context: read this before building or trusting a mutation run, a sabotage script, a
calibration plant or a falsification matrix, and before reproducing someone else's. The
harness is an instrument and fails more often than the code it checks, always in the
direction that reads as a verdict about the code. Which test killed what, reproducing other
people's claims, and test rot are in
[vd-c1a-reproduce-and-kill-attribution.md](vd-c1a-reproduce-and-kill-attribution.md). Entry file:
[vd-c1a-prove-it-can-fail.md](vd-c1a-prove-it-can-fail.md).

## Canonical rules

### Make the mutation land
- **C1a.12** A mutation applied by string replace can silently no-op or land on the wrong site. Assert the anchor occurs exactly once, the file content changed, and the report was written by this run.
  - Make every precondition failure ABORT (`raise SystemExit`, `throw`). A printed `mutation applied: False` above a verdict line does not survive into a summary or a HANDOFF.
  - Anchor on a token that carries the function's identity (include its signature line). A near-duplicate line one function away is common in utility modules and makes a good test read as inert.
  - Prove landing by comparing the file's hash before and after (works for untracked files too), or diff against the held pre-mutation bytes. `git diff` on a tree with uncommitted work prints the whole feature.
  - Swap two values through placeholders: in a chained replace the second match fails once the first has rewritten the text around it.
  - Report `PATCH DID NOT APPLY` / `TARGET NOT FOUND` as its own outcome and count it as a miss. After a refactor, check you are mutating the file the constant now lives in.
  - Calibration plants in a checker obey the same rule: grep and print the planted token before running the checker.
- **C1a.13** Restore inside the harness from the held original content and assert the restore is identical. Open the `try` before applying, so a crash in the landing proof still restores.
  - Never hand-revert a mutant with `git checkout --` on a file that carries uncommitted work: it restores from HEAD and silently deletes the feature. The deciding question is whether the discarded content exists anywhere else (`git status --porcelain -- <path>`).
  - After any crashed run, grep for the mutant text before trusting the tree. Where possible, mutate an in-memory copy (a `structuredClone`) so there is no restore step to get wrong.
- **C1a.14** A mutant must fail BEHAVIOURALLY. A wide blast radius (a TDZ or syntax error failing unrelated tests) means you broke the module, not the property: predict the failures before running.
  - Prefer mutants that swap one valid value for another (a field, a comparator, a threshold) or append, over deletions and reorders, which can fail structurally. Make the runner reject any detection whose error is syntactic.
  - For "fails before the fix", anchor the test on something both versions render. "Element not found" for a testid the fix introduced proves only that the new code is absent.
  - Classify every pre-fix failure as structural or assertion-level and quote both counts; the headline test should fail on an assertion that reads like the bug report. Revert every file the claim depends on together.

### Make the verdict honest
- **C1a.15** A mutation verdict needs a third outcome for "could not run". Use 0 passed, 1 failed, 2 could not run; a crash, a missing file or an unparsed verdict is never a kill.
  - Run an unmutated BASELINE (require a non-zero passed count), a positive control (delete the feature outright) and a cosmetic negative control that must NOT be detected, all through the same harness.
  - Require the specifically expected test NAME among the failures. Red with no named failure (`RED*`) is a crash, not evidence.
  - In the suite, a check must FAIL, never ABORT: `split(m)[1]`, `.index(x)`, `[0]` raise on exactly the state a red guard reports and silently un-report every later check. Use safe accessors.
  - Preflight every dependency, resolve paths from the script's own location, and assert those anchors at import time; a harness run from the wrong directory scores "file not found" as a kill.
  - `process.exit()` skips `finally`: throw a tagged error, set the exit code and let cleanup run. Build scratch dirs outside the project tree and remove them in `finally`.
  - A probe that prints "3 FAILURES" and exits 0 is scored green by every automation. Put a build guard before the artifact write, and prove it by asserting the non-zero exit AND the artifact's absence.
  - A signal test that signals only the parent pid is vacuous while a sync child call blocks the event loop; signal the process group, and time the interruption so the resource exists.
- **C1a.16** Caches and shells can neuter a run: clear incremental build info between mutants, run source-rewriting Python with `PYTHONDONTWRITEBYTECODE=1`, and never word-split a list with `$VAR` in zsh.
  - `tsc -b` reuses `tsBuildInfoFile` and can skip the re-check (verified 2026-09-21, tsc); vitest and jest caches behave alike. Treat a NARROWER than predicted error list from a cached checker as suspect.
  - CPython invalidates `.pyc` by mtime and size only: two same-size mutations in one mtime tick reuse stale bytecode and mark a good test vacuous (verified 2026-09-03, CPython).
  - zsh passes `pytest $T` as one argument; pytest prints "no tests ran" under a `| tail` summary (verified 2026-09-23, zsh). Use `${=T}`, an array, or an inline list. Pipe and rtk mechanics: repacted:shell-tooling-traps.
- **C1a.17** A run that kills nothing, or everything, is an instrument alarm before it is a finding. Re-run one case by hand and read the raw output before reporting either.
  - The fault that reports a real kill as nothing reports a real survivor as a kill, silently and flatteringly. A surprising verdict in either direction is a reason to check the instrument.
  - A verdict grep that never matches the runner's real output format makes the "survived" arm unreachable. Parse the real verdict line and add a "no verdict line, cannot judge" arm.
  - rtk indents `git diff` hunks, so an anchored `^[-+]` grep returns a free clean verdict (verified 2026-08-25, rtk). Pair every empty grep with a positive control on the same pipeline.
- **C1a.18** Suspect the harness first when a reviewed, self-tested fix probes as completely inert. An unknown mock option passed through a conditional spread is silently dropped.
  - The failure case and the healthy control must DIFFER; byte-identical results mean the injection never fired. Grep the mock factory for the option name.
  - Mock factories should throw on unknown override keys; prefer overriding the method directly. Avoid hand-rolled fixtures with `as SomeType` casts.
  - Prove a mock applies by making its factory THROW and watching the run fail; a green before and after re-pointing a mock distinguishes nothing.
  - A reviewer's false Blocking is expensive: hold your probe to the falsification bar you hold the code to.
- **C1a.19** The instrument itself can be wrong: a fixture periodic in the sampling period makes baseline equal current and silently disables a calibrated threshold. Assert derived parameters too.
  - A mechanism that destroys its own evidence (a failing insert leaves no row) cannot be falsified from the surviving data; say "cannot be falsified from this data".

## Examples
- C1a.12: a `{ signal: sig }` mutation's anchor spanned several source rows and never matched; re-run with an applied check (6 → 5 occurrences) it failed correctly. Two people produced three no-op mutations in one session.
- C1a.13: `git checkout -- <path>` to undo one mutant deleted a whole uncommitted feature with exit 0.
- C1a.14: a revert against a new testid gave 3 failed (2 structural); anchored on the row title it gave 2 failed, both reading `expected "spy" to be called 1 times, but got 0 times`.
- C1a.15: a sabotage runner scored 9 of 22 mutations as THEATRE; all nine guards had fired and a later `split(marker)[1]` crashed the run before its summary.
- C1a.16: a harness one directory off scored 6 of 6 mutants killed without a single test running.
- C1a.18: a probe passed a failure option the mock did not support; three "FETCH FAILS" cases were identical to the healthy control.
- C1a.19: hourly fixture data `(i * 37) % 21` repeats weekly (21 divides 168), so every control ratio was exactly 1.000.
