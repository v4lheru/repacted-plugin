# VD-C2a (split): job runners, one-way operations and recorded state

Context: read this when a scheduled or batch job reaps, re-queues, applies IaC, repairs files,
propagates blocks, owes a follow-through, or when a change decides what future rows record.
Entry file: [vd-c2a-fail-open-and-unknown-states.md](vd-c2a-fail-open-and-unknown-states.md).

## Canonical rules
- **C2a.31** In a job runner, keep the reaper above the concurrency guard, make the stale window exceed the longest step timeout, and give a paced step an internal wall-clock budget.
  - Fix the guard's predicate (ignore rows older than a liveness bound) instead of adding a second reaper; use `COALESCE(started_at, created_at)`.
  - A relationship spanning two files is invisible reviewing either; pin it in a test that parses the real values file.
  - The gap distribution separates hung (one long gap) from correctly paced (a tight mode at the interval). `Promise.race` does not cancel the loser and discards its counters.
  - Revisit a duplication justified by a dependency constraint once the constraint is gone; import the constant. Grep tests for assertions that codify the defect you are fixing.
- **C2a.32** An incremental job that derives "done" from its output table re-queues zero-output items forever; add a state source keyed on the version stamp, and suppress only after N consecutive zeros.
  - A catch-all that returns empty makes transient failure look like genuinely empty, so never suppress on the first observation. Split the counter (`items_no_output` plus ids); do not flip the run status forever. Log the id at the skip site.
  - Test a new row shape on a temp table built from the real schema; never stream a test row into a production BigQuery table.
- **C2a.33** Execute a generator's emitted output, not its source; keep an irreversible operation and its assertions in one transaction, or assert first and act second.
  - Ask of any script: if the last line fails, what is already permanent? A validation after the point of no return is a report, not a guard.
- **C2a.34** Desired-state IaC deletes live resources missing from the file: re-read live state when acting, diff the resolved config against live in both directions, and gate on zero destroys.
  - A warning comment is a weaker control than a correct file. Watch for a value the IaC duplicates from elsewhere; it re-points the setting on a later apply. Railway specifics: repacted:vendor-gotchas.
- **C2a.35** Scope a write-then-validate tool's rollback to errors attributable to the edited file; reverting on whole-repo failure deadlocks multi-file repair.
  - Back up targets first; compare structural edits field by field, not by line diff. Apply a bulk correction per record against the field's real predicate, not uniformly.
  - Do not satisfy "path does not exist" by creating the path. Make a sabotage fixture avoid unrelated failures so the assertion you care about actually runs.
- **C2a.36** Propagate only TERMINAL blocks backward along a dependency edge; a dependent blocked as "waiting on the prerequisite" deadlocks the pair.
  - Enumerate the dependent's block reasons and exclude each that exists because the prerequisite has not run. Over-refusal on a fail-closed write path is a real failure. Reuse an existing reason over minting one.
- **C2a.37** An obligation with a promised follow-through needs two recorded facts plus a PUSHED reconciliation; ask whether an unreconciled row means something was LOST or RETAINED.
  - A query nobody runs is as invisible as the missing fact. Surface overdue rows on a schedule to a named destination; assume a dashboard is not read.
  - For always-on intake, a dead feed looks exactly like a quiet week: alarm on time since last success. For erasure the reconciliation IS the safeguard.
  - Test by seeding an overdue row and asserting the ALERT FIRES, not that a view returns rows. Grep nullable outcome columns (`decided_by`, `resolved_at`, `notified`).
- **C2a.38** Before backfilling a nullable audit or log column, ask whether it claims the value was USED (transmitted, presented) or records context that is always true; fill only the latter.
  - On append-only stores weight a false row far above a gap: a gap is honest and fixable, an inferred value has no tell. Check whether a join already answers it.
  - A lead instruction is not a reason to write a permanent falsehood. Refer to findings by real name (column, file), never by a per-report index.
  - Report such a null as "null by design because the column asserts X", never "null although the value is available", which invites the wrong fix. If completeness is wanted, add a column with honest semantics.
  - Reuse an existing value only if it records THIS fact, not because it fits mechanically. Name the fact the actor can establish (`reconciled_present`, not `reconciled_applied`) and prefix human-adjudicated values.
  - Boundary: a row that reports what the writer genuinely knew when writing ("call issued, result unrecorded") is a report, not a fabrication.
- **C2a.39** Do not defer a change to what gets WRITTEN (a code, enum, provenance column): its cost rises per record, and the evidence that it mattered is the data you did not record.
  - Read-side changes (a query, view, label) can wait. For write-side ones "later" is a different dataset, and the column means two things either side of a date.
  - Add a new code alongside the old one; never narrow an existing code's meaning retroactively. Query the live schema before writing a migration, and grep whether the codebase already ruled on the shape.

## Examples
- C2a.31: a stale window of 1 h against a 90 min step ceiling, and 0 successes in 538 correctly paced runs.
- C2a.32: two image-only decks re-queued every 6 h for weeks: 50 runs of `success / files_updated=2 / chunks_written=0`.
- C2a.33: Python `%` formatting ate SQL's `RAISE` placeholder after a publish function had armed freeze triggers; only `BEGIN … COMMIT` saved it.
- C2a.34: an env block listed 5 variables as "all of them"; within the hour production had 17, including every auth variable.
- C2a.35: five fixes each reverted themselves because the validator was red on the other four (error count fell 8 → 6/7 inside each).
- C2a.36: blocking a create call whenever its paired link call was blocked would have made every new record unwritable.
- C2a.37: four instances in one project (notify, safeguard request, cap override, erasure), each found by a different agent.
- C2a.38: a lead ordered `actor_user_uri` filled for three write ops; two transmit no owner, and the coder refused correctly.
- C2a.39: two refusal causes shared one review code while both groups were still empty, the one moment the split was free.
