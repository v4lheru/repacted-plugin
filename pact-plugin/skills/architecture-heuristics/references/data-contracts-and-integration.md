# Data contracts and integration design

Context: design-time rules for writes to external systems, sync and reconcile, backfills,
checkpoints, enums and audit columns, derived values, and component or API boundaries. Read this
when you design or review idempotency ledgers, two-way syncs, preview-then-apply flows, backfills,
incremental jobs, status vocabularies, or an engine/instance or web/worker split.

## Canonical rules
1. Key an external write's idempotency on the EXTERNAL effect: `(op_type, external_container, target_ref, target_state)`. Keep tenant id and internal row id as columns, out of the UNIQUE.
2. For a create with no external id yet, key on a normalised natural key (e.g. the domain). Make the key a DB-generated column or a claim function so call sites cannot drift.
3. Split an immutable audit log (every attempt, never mutated) from a mutable claim table (`claimed | applied | failed`, `claimed_at`); one table cannot be both append-only and reclaimable.
4. Claim = `INSERT … ON CONFLICT (key) DO UPDATE … WHERE status='failed' OR (status='claimed' AND claimed_at < now() - stale)` in ONE DB function. `stale` > API timeout + max backoff.
5. A stale reclaim is safe only if every external op is idempotent vendor-side (search-before-create, ignore-duplicates, set-idempotent PUT). State that dependency in the design.
6. A "vendor already has it" skip takes NO claim and is never terminalised as `applied`; record it with its own outcome (`skipped_precheck` ≠ `skipped_idempotent`). Test: zero claim rows added.
7. Kill client trust in a write job: the client sends a minimal reference plus `planHash`; the server re-derives the plan from gated reads and blocks on mismatch. Preview and apply share one derivation.
8. A preview-then-apply UI uses ONE render type for preview and outcome, keeps the approved plan immutable with a separate result key, and joins on a composite op key `(entityId, action)`.
9. Model drift and failure per item; the job that safely refused a drifted item stays `succeeded` with an `applied | partial | drift_blocked` banner.
10. Bucket outcomes by OPERATOR ACTION (investigate / re-run / reconcile-do-not-retry), mapped exhaustively with a `never` guard. "May have landed" must never sit in a "retry these" bucket.
11. Check every rendered instruction against its neighbours: a whole-batch "re-run" swallows a row-scoped "do not re-run". Scope a gate to the op that is unsafe to repeat; ask per op what repeating does.
12. Never prescribe an operator step the system cannot observe, or one that races a timer. When no safe completion exists, say so and give a real next step (escalate).
13. Positional or count words in operator copy ("above", "first") re-couple what the code decoupled. When you narrow one instruction's predicate, re-check every instruction keyed to the old one.
14. Mirroring a read-only integration covers about 70% of a two-way sync. Write-back adds an explicit conflict policy, an unscoped-write safety harness, DRY-RUN default and on-demand triggering.
15. Drop template machinery the new vendor does not need: a static secret stays env-only (no encrypted-token column). Test: does the value change at runtime and have to survive a restart?
16. Cache-vs-live is a false binary: live BODY at display time, a derived disposable INDEX that yields candidates only, and PROVENANCE (`source`, `fetched_at`, freshness) rendered on every result.
17. A missing `modified_at` is a first-class UNKNOWN, never defaulted to `fetched_at` or `now()`. Emit a per-run coverage record; a degraded run must not render as a clean one.
18. Allow an LLM only where a human sees and judges its output. Retrieval and prioritisation stay deterministic (their failure silently shrinks a set); query expansion is the safe adjacent use.
19. Insert an abstraction under live traffic with zero behaviour change (proved on a golden set), then change behaviour separately behind a flag. No tests? The characterisation harness is commit one.
20. Need a second per-tenant identifier? Derive it from an already load-bearing config value and resolve by name, exactly-one-or-block (0 and >1 both block). Say "narrows", not "eliminates".
21. A view over parts is safe only if the FORMULA is fixed. If the rule is versioned, store the result AND the denominator used as frozen facts, checked by a deferrable constraint trigger at COMMIT.
22. Gate AUTOMATION on evidence quality, never the score: keep the number immutable and resolve the band from score, coverage floor (per band) and caps. Sort by (band, score).
23. Audit a spec's own worked example against its own formula; an unreachable example is the most persuasive finding. Express rubric gaps as share of WEIGHT, and probe aggregates with non-uniform inputs.
24. Derive constants from the versioned policy at read time (`threshold - 1`, `100 × f(3)`); a frozen literal keeps its name and loses its meaning when the policy changes.
25. A type with no identity fields guards what flows downstream, not what was never found: a redactor also needs a runtime post-condition that every known identity token is absent, failing closed.
26. Name discriminator values so one is the literal negation of another (`demonstrated | not_demonstrated`); a "neutral" name (`assessed`) lets the collapsed states back in through vocabulary.
27. Before reusing an enum value for a new case, ask whether it DESCRIBES or merely ACCOMMODATES the fact. State the fact each member carries at its definition; fix the bucketing layer too.
28. Before backfilling a nullable audit column, classify it: CONTEXT (always true, fill it) or ASSERTION of what was SENT (leave null on paths that sent nothing). Append-only makes a wrong fill permanent.
29. A prod backfill is verified only when: regenerated from the DEPLOYED commit, run on a mirror proven equal to prod, its inverse restores exactly, and each guard aborts with no partial state.
30. One transaction, every check `RAISE`s; snapshot ALL rows so the inverse check (`IS DISTINCT FROM` → 0) is total; state expected counts inline; list the rows in comments; note shelf life.
31. An incremental job whose checkpoint is read by one path and written by another silently degrades to correct full re-walks. Confirm the scheduled path is among the checkpoint's writers.
32. Watermark on the run's START instant, write the checkpoint before marking complete, resolve ties toward the older value; hold back to the oldest retryable failure (inclusive), never a permanent one.
33. Test an incremental job on WORK DONE (pages fetched, items processed), never on output, which is identical either way.
34. Safe re-run: detect already-completed state FIRST and return a distinct sentinel; clean up only your own interrupted WIP marker. Never auto-revert prior success or overwrite its derived tables.
35. A reconcile keeps prior data: clear only after a complete read, guarded on the source row count, never on "no match" or an over-cap run. On guard refusal the job completes, it does not fail.
36. Engine/instance architectures: components receive typed props and never import data or config; one scoped grep over `src/components/` proves the boundary, and any hit is RED.
37. OAuth consent for an async-only worker: web owns PKCE, `/authorize` and the callback (verifier in a short-TTL httpOnly cookie keyed by `state`); the worker owns token exchange via enqueue.
38. Consent code: CSPRNG single-use `state`; admin-gate initiate and callback; never log code or verifier; pin `redirect_uri`. Grep for an enqueue helper before adding a worker endpoint.
39. In file-based content collections, route on a required, validated `slug` field, not the loader-derived filename id; derive canonicals from `type + slug` in one function.
40. Component boundary: public interface defined, internals hidden, dependencies point to stable parts, testable alone, owns its data. Split on different change rate, scale, owner or security.
41. API contract: one error envelope with a request id, cursor pagination for live data, actions as `POST /resource/:id/verb`, idempotent retried writes, timeouts on every external call.

## Examples
- 1: a draft key `(tenant, list_target, action, project)` let two campaigns create the same record in a shared CRM account.
- 6: marking a vendor-side skip `applied` would have made a domain uncreatable for every tenant, with no reclaim path.
- 10: folding "call may have landed" into `blocked` made the banner tell operators to retry a possibly landed write.
- 21: a derivable score as a view would have restated every past candidate when the renormalisation rule changed.
- 31: 1 checkpoint row among 1,271 job rows; the cron re-walked the whole upstream twice a day for 94 days.
- 34: a "paid → pending" revert added for idempotency re-invoiced a prior month on re-run.
