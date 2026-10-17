# VD-C2a (split): destructive sync, reconcile and idempotency

Context: read this when code deletes, overwrites, reconciles, resumes, retries, dedupes or
records progress, or when a change decides what future rows will contain. The defects here
turn a transient failure into permanent loss or duplication, usually under a green status.
Entry file: [vd-c2a-fail-open-and-unknown-states.md](vd-c2a-fail-open-and-unknown-states.md).

## Canonical rules
- **C2a.22** In a clear-then-rebuild reconcile, treat every "nothing this run" outcome (outage, over-cap, no-match) alike: clear only when the landed set is non-empty.
  - Trace every branch that reaches the delete with an empty set, not only the error branch. Accept the trade: a truly emptied entity keeps stale data until a good run.
  - Review the WHOLE fix commit; a bundled remediation can add a worse bug than the one fixed, and a regression test covering only `landed > 0` misses the wipe.
  - A best-effort stage after the primary artifact is persisted must degrade, not fail the job; surface skipped items in the run summary.
- **C2a.23** Gate destructive deletes on the job's own completeness signal (errors == 0, all roots resolved), not on a ratio of the delete set.
  - A ratio floor is noise at small N and gets widened the first time it trips. Count the error-swallow sites (`catch { log; break; }`, `continue`) and feed them into an integrity accumulator.
  - If a ratio clause stays, add a floorless clause for the catastrophic shape (`would_withdraw == live`): `absent > max(FLOOR, pct * live)` exempts every population below FLOOR.
  - `x <> ALL(ARRAY[])` is true for every row: an empty seen-set means "delete everything". Count affected rows with the mutation's own predicate, never by arithmetic.
  - Make completeness a control-flow tri-state (`complete | partial | unknown`, default unknown). A source-reported total exempts every cursor paginator.
  - On trip: apply updates, suppress deletes, emit a distinct status. Exclude degraded runs from the baseline, or the gate ratchets down. Give separate gate ids to cases with different operator responses.
  - Provide a cheaper correct repair than disabling the control (apply exactly the suppressed set), bounded by run id or age so a stale set cannot be replayed.
  - A trigger `UPDATE OF col_list` never fires on a column missing from the list. Key an append-only revision log on `(item, run_id)`, not the content hash (A→B→A is legal), plus `CHECK (hash IS DISTINCT FROM previous_hash)`.
  - A heuristic correct only by coincidence of configuration yields identical output from correct and broken builds; assert the resolved value against a declared `EXPECTED_*` constant and fail loud.
  - A documented degraded signature is fiction unless some mechanism (an `onError`, a catch, a default) makes that path reachable. A lookup whose error path returns the same value as "no data yet" silently stops guarding.
  - Decide fail direction per risk: degrade-to-empty is right where it gates skipping work, wrong where it gates destroying data. Degrade-and-suppress usually beats abort when a safe partial exists.
  - Prefer a query-time join over persisting a projection of someone else's mutable data. Resolve-by-name must extend to the container (tab, table, section), not only the fields.
- **C2a.24** Before adding a throwing validator to a loop, check for a per-item guard and for per-item progress stamps; together they turn one bad row into a permanent total outage.
  - Use `safeParse` plus a per-item try; make refusal a first-class outcome that names what was rejected and emits no numbers. Do not stamp the failing item, so it retries.
  - "We now validate this" changes the failure path: price the failure path, not only the happy one.
- **C2a.25** Build any key compared across two computations (plan hash, idempotency) only from determinate, self-owned values that identify the external side effect.
  - Hash the DESIRED target literal, not an observed value that can resolve to "unresolved"; use an identity present in both phases (a normalised domain, not a vendor id created on apply).
  - A dedup UNIQUE on a shared vendor account must exclude internal tenant or list ids; keep them as attribution columns, or two tenants both win the claim.
  - A crash between claim and apply leaves an indeterminate `claimed` row. For a non-idempotent op, re-check that the external record does not exist before reclaiming.
- **C2a.26** For "record only after success", ask how many items can sit delivered-but-unrecorded at once; if more than one, commit inside the loop or say why not.
  - Same shape: queue acks, cursor or offset advances, idempotency-key sets, "last processed" watermarks.
  - Report the observable commit point, not the storage ("returns only after the write lands"), and name the residual race and which way it fails (duplicate vs loss).
- **C2a.27** Classify a failure on its payload, never its envelope, and default recovery to "do nothing" backed by an evidence-based resolver; a bare catch that discards valid state creates the gap.
  - Measure both misclassifications: wrongly transient costs one cycle, wrongly fatal discards good state. Unbounded patience on a genuinely dead cursor is its own silent outage.
  - Give delivery a budget RESERVE recomputed every run; reordering fails when the repair sweep itself produces work.
  - Make re-seeding a no-op while a sweep is in flight. Make it harmless first, then quieten it, keeping a non-convergence alarm (`workDone === 0` with work queued).
- **C2a.28** Do not resume a batch by index when the batch list is a filtered subset; accept a progress file only if it covers its slice, and write progress atomically.
  - Batch N's membership depends on both the source and the filter; changing either maps stale results onto different items with a correct-looking count.
- **C2a.29** When a fix spans two changes, ask what the system does after the first lands and before the second; land the describing half (label, bucket) before the producing half.
  - "Startable" is usually a compile-order claim, not a claim about operator-visible behaviour mid-rollout. Escalate even when a senior said it is startable.
- **C2a.30** A suppression branch must still write whatever a later read depends on, and a learner that explains events away must train on the population that produces them.
  - A skipped write makes the next cycle unable to date the episode, and the undatable branch fails open: suppression holds for one poll. Test across two cycles on an OPENING episode.
  - Measure the decision-relevant population (the next occurrence), not the historical average.

## Examples
- C2a.22: an enrichment reconcile protected the outage path but wiped on over-cap and no-match.
- C2a.23: four `catch { log; break; }` sites turned partial reads into deletes with `status='success'`, twice in production. A floored guard let live=3, absent=3 through.
- C2a.24: a saved-report digest moved to `schema.parse` on a jsonb blob validated only on write; one bad row would stop every report.
- C2a.25: a vendor key hashed the observed stage and a tenant id; the vendor account is shared across tenants.
- C2a.26: `appendEmitted(deliveredIds)` after the emit loop satisfied "record after 2xx" and reopened the duplicate window.
- C2a.27: a bare catch read a throttle as a rejected cursor; the repair sweep (1,395 s against a 240 s budget) starved ~6 delivery runs.
- C2a.28: 51,832 fresh domains = 74,957 unique minus a 23,125-row label cache; a cache refresh would have mislabelled whole batches.
- C2a.29: emitting two ops before the `awaiting_create` value existed would have shown every new outlet as `indeterminate`.
- C2a.30: a weekday learner fed a budget script's 158 pauses over the human routine's 46 suppressed 9 Thursday alerts and 0 Friday ones.
