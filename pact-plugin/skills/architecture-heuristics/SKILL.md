---
name: architecture-heuristics
description: "Use when designing or reviewing the shape of a system or change: component boundaries and C4 sketches, API and producer/consumer contracts, impact on other readers, reconcile and sync design, idempotency ledgers, data integrity, re-aggregate vs re-extract, anti-patterns to avoid."
---

# Architecture heuristics

Context: field heuristics for whoever designs or reviews the shape of a change. They caught real
defects: second consumers of a contract, reconcile and sync designs that wipe data, idempotency
keyed on the wrong identity, and structural fixes accepted without a coverage test. Textbook
patterns (SOLID, GoF) are deliberately absent; you know them.

## Workflow
1. **Enumerate every reader and writer of what you change.** Grep the concept tree-wide (symbol
   AND raw name), check jobs, crons, second routers, exports and metadata readers, and NAME the
   ones you could not check. → `references/change-impact.md`
2. **Decide explicitly whether the change alters a contract.** If it does, record it as a
   decision. Before conceding one, read the hash projection and the dedup UNIQUE: often neither
   changes. → `references/change-impact.md`
3. **Sync, reconcile or backfill: define the behaviour on an empty, over-cap, partial or failed
   read BEFORE writing the delete path.** A reconcile keeps prior data and clears only after a
   complete read. → `references/data-contracts-and-integration.md`
4. **External writes: key idempotency on the external target**, split the immutable audit from the
   reclaimable claim, and keep a ledger the retry path reads. → `references/data-contracts-and-integration.md`
5. **Before accepting a structural fix, walk it against each known instance.** If it would not have
   caught the second one, it is untested; say which instances it covers.
6. **Write the consumer list and the degradation behaviour into the design doc**, with a C4
   context and container sketch that shows every entrypoint, including cron jobs.
   → `templates/c4-sketch.md`
7. **Name what you did not verify** and the trigger that would make an accepted decision wrong,
   placed where the person who trips it will read it.

## Map
- `references/change-impact.md`: consumer enumeration, ruling scope vs new consumers, hidden
  table readers, notifier fan-in, count guards, helper reuse vs binding, product-of-constraints
  defects, targeted vs structural fixes, contract-change checks, hashed-store widening, repointing
  columns, comment decay, and the field-tested anti-patterns.
- `references/data-contracts-and-integration.md`: idempotency ledgers, preview-then-apply UIs,
  two-way sync, body/index/provenance, derived vs frozen values, evidence-gated scoring, enum and
  audit-column semantics, backfills, checkpoints, safe re-runs, reconcile, boundary and
  API contract checklists, OAuth with async workers, content slugs.
- `templates/c4-sketch.md`: context and container diagrams, consumer and degradation sections,
  decision-record stub.

## Architecture review checklist (non-generic items)
- Every entrypoint is listed with the clients and pools it constructs.
- Every external call has a timeout; errors are translated at the boundary.
- External APIs are reached through a backend proxy (credential rules: repacted:security-review-patterns).
- Every destructive or clearing path has a defined behaviour on an incomplete read.
- Decisions carry a falsifiable revisit trigger.

## Related skills
- repacted:verification-discipline: `vd-c2a` (fail-open, degrade and destructive-sync lessons),
  `vd-e` (denominators and aggregates). Use it to check a specific claim or test.
- repacted:llm-pipeline-patterns: re-aggregate vs re-extract for LLM reports.
- repacted:postgres-supabase: constraint, grant and RLS mechanics behind these designs.

## Refine
At the end of a session that used this skill, run `protocols/self-refine.md`.
