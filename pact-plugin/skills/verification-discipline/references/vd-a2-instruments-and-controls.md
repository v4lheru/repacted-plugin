# VD-A2: instruments and controls

Context: read this before trusting a quiet result (a zero, an empty diff, "no matches", "no
prompt", a green suite) and before designing the control that would make it evidence. Most
degraded instruments fail GREEN: they return nothing, and nothing reads as clean. Spend the
check on the quiet results. Calibrating a particular instrument and reading a zero signal are
covered in [vd-a2-calibration-and-zero-signals.md](vd-a2-calibration-and-zero-signals.md).

## Canonical rules
- **A2.26** Every assertion of a NEGATIVE property (disjoint, absent, did not fire, no leak) needs a companion assertion that the scenario was reached: exact counts, exact contents, a non-empty input.
  - A search that returns nothing may never have run. Print a marker before each search, never chain a search after a command that can fail, and check the exit code and stderr, not only an empty stdout.
  - Three causes print the same thing: not there, never ran, empty needle. For "X does not appear in Y", ask what happens when the step producing X legitimately returns nothing.
  - A verification script must abort on its own precondition failure. A harness that cannot run exits with a distinct code, uses no ambient env, and removes any broken artifact it built in a `finally`.
  - Pair every universal (`forEach`, "all rows") with a non-vacuity assertion, because a loop you just wrote may run zero times. A helper that falls back to a wider scope must throw instead.
  - A failure line number that holds no assertion means the file moved under you. Re-check your own lane in isolation.
- **A2.27** Before running a check, name the result that would DISTINGUISH the hypotheses. A result compatible with both answers is not evidence, however cleanly it returns.
  - An empty result from a NEW interface is a question about your parser first. Re-run the same request with the filter removed: rows back means the zero is about the data.
  - When several instruments return nothing, and nothing has two meanings, ask a POSITIVE question instead. A teammate who stops to verify a premise before writing IS a control, so treat it as one.
- **A2.28** A zero-count or zero-writes result is informative only if a non-zero was possible. Seed or pre-check an eligible record, or flip the guard's INPUT and count what lands.
  - Log a before/after pair (before 1, removed 1), not a bare count. A wrong join and an empty table produce the same log line, so assert non-zero on the first populated run.
  - Where you cannot seed in prod, corroborate a zero with an independent mechanism that predates the fix, plus a control that stays non-zero.
  - Ask whether the proposed verification could detect a regression of the same bug. A zero that is correct under an unverifiable config, say which world you are in.
- **A2.29** A check that expects nothing proves nothing until the same instrument, in the same call shape, finds a known-PRESENT needle. A known-ABSENT needle proves it can come back empty. Use both arms.
  - Vary only the needle: keep the fetch, artifact, flags, argument form (a directory or a glob) and scope the same. Assert the control FIRST, so the scan cannot run unless the detector fires.
  - Pick controls you have OBSERVED in this corpus. A failing control voids the run. It never confirms the target.
  - A mutation harness needs two controls, baseline exits 0 and sabotage exits non-zero, read by exit code, plus proof the mutation applied. Any `else True` in an assertion generates false greens.
  - A guard on a compile-time literal cannot fail, so trace the asserted value back to where it is built. A control whose absence only warns gets credited anyway, so make absence FAIL.
  - Print the denominator and the byte count beside a clean result or a hash (two empty inputs hash identically). Match on word boundaries by default.
  - `curl ... || echo err` misses HTTP errors, so capture the status code and assert coverage. Never mutate a server while you are measuring it.
- **A2.30** Swallowed errors (a discarded stderr, a lenient parser returning `[]`) make a refused or failed command read as empty. Surface failures, and treat a zero as suspect until a control returns rows.
  - A positive control must exercise the SAME mechanism as the negative (a `find -name` miss needs a known filename, not a content grep).
  - Checks that agree cannot reveal a dead one: only disagreement audits an instrument, so make it disagree once on purpose.
  - Verification tooling you write is held to the same can-it-fail standard as the code you audit.
- **A2.31** A null result counts only if the instrument can observe the thing asked about. `git status`, screenshots, filtered logs and normalising detectors are blind by construction.
  - Grep for the emitted VALUE, never the parameter name (positional calls escape a `reason=` grep). A pattern encodes an assumption about call style, and the output never shows it.
  - A detector that normalises a field away cannot speak about that field. Settle it with a raw capture that keeps the field, and state the question OPEN until then.
  - Each entry in an exclude list is a separate claim that needs its own evidence. Force a filter to bite once to prove its call chain is live.
- **A2.32** A checking tool inside the system it measures changes the answer. Probe command-rewriting hooks with top-level commands only, parse structured text, and read hits instead of counting them.
  - A prose invariant is unenforced until you find the code that enforces it. A documented failure signature is unreachable until you find the mechanism that reaches it.
- **A2.33** A guard's silence carries no information until you know it covers the operation. Ask which LAYER it hooks, not which actions it names, and get approval BEFORE any irreversible API-path action.
  - Settle "was X authorised before Y?" from the order of actions in your own transcript, never from clock timestamps.
  - A quiet guard is not a green light, so work by snapshot, minimal mutation and verify-after. State the guard's scope whenever you cite its silence as evidence.

## Examples
- A2.26: disjointness held over two EMPTY arrays, a mutation harness read a stale report file, and a search chained after a failing `cat` never ran.
- A2.27: two grepped strings existed under either trigger implementation. The stored denominator (10000 against 399) decided it, and a new API's 0 of 518 was really 52%.
- A2.28: "0 exclusions, correct" passed with a comparison that could never match. Flipping a gate's input landed 168 rows graded `ok`.
- A2.29: `rg -il` on a directory returned zero for a 46 KB file while a glob-shaped control passed. A nonsense-string control catches a matcher that matches everything.
- A2.30: `find` with compound predicates was refused by rtk on stderr (verified 2026-09-03, rtk version not recorded). A discarded stderr turned the refusal into "file does not exist".
- A2.31: `git status` was read as proof that files were absent, but it shows only uncommitted diffs.
- A2.32: an `eval` wrapper hid a command-rewriting hook from its own probe, so every form measured "correct".
- A2.33: the merge guard hooks git CLI verbs, so `glab mr merge` went through with no prompt (verified 2026-09-05, PACT merge guard).
