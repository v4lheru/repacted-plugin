# VD-C2a: fail-open defaults and unknown states

Context: read this when you write, review or rely on a fallback, a default, a degrade path, a
coercion, a parser or a lookup that feeds a control. The recurring defect is a degraded or
undetermined value that borrows the representation of "checked and clear", so a gate evaluates
cleanly on it and nothing is ever logged. This is the entry file for VD-C2a (C2a.1–C2a.5). Four splits hold
the rest:
- [Projections, fallbacks and lookups](vd-c2a-projections-fallbacks-and-lookups.md)
  (C2a.6–C2a.13): sentinels reused for new states, list vs detail rows, hardcoded discriminators, default modes, header lookups,
  plausible substitutes, silent optional dependencies, one arm missing the error vocabulary.
- [Outcomes, third values and status surfaces](vd-c2a-outcomes-and-status.md) (C2a.14–C2a.21):
  renderers, status mappers, catch placement, partial success, absent rows, early returns.
- [Destructive sync, reconcile and idempotency](vd-c2a-destructive-sync-and-idempotency.md)
  (C2a.22–C2a.30): wipes on empty runs, delete gates, keys, commit points, recovery paths.
- [Job runners, one-way operations and recorded state](vd-c2a-jobs-and-recorded-state.md)
  (C2a.31–C2a.39): reapers, zero-output items, irreversible ops, IaC, repair tools, blocks,
  follow-through obligations, audit columns, write-side deferral.

## Canonical rules
- **C2a.1** Split degrade policy by consequence: enrichment may degrade silently; a suppression call may never return a value indistinguishable from "checked and clear".
  - Fail-closed means WITHHOLDING the safety assertion (do not mark the item safe, do not short-circuit downstream checks). It does not mean blanket suppression; where no per-item signal exists, refuse to report success.
  - A suppression value may never COERCE to clear either. `coerceBool(v) = v === true || v === 'true' || v === 1` turns `'True'` and `'1'` into "not suppressed". Keep a separate coercion with a fail-closed default for suppression flags.
  - The return type is usually the bug: `string[]`, a bare `string` or `boolean` cannot say "could not determine". Return a discriminated union (`{kind:'resolved'} | {kind:'unresolved'}`) and treat unresolved as INDETERMINATE at the gate, never as false.
  - Trace the gate's output past the verdict. A `!excluded` branch usually PROMOTES the record (auto-enrich, copy the email, skip review), so "could not resolve" and "confirmed safe" become identical rows.
  - Map direction decides loud vs silent: `label → opaque` leaves no operand when the fetch fails (loud); `opaque → label` yields a raw value that never matches (silent). Pinning identity in tenant config beats a runtime label lookup.
  - Keep a refuse-to-downgrade guard so no degrade can overwrite a persisted positive verdict. A soft-empty upsert guard does not catch a degraded row, because that row is non-empty.
  - Check `??=` latches and unkeyed memos: one failed fetch poisons every resolve in the run. Three fail-opens in one subsystem mean a default posture; grep every sibling that returns a bare collection or primitive.
  - A fail-open that the tests pin as intended is a design disagreement; raise it as a failure-policy finding. Check what the degrade test does NOT assert.
  - A pre-filter feeding a per-item control that itself fails closed may degrade-with-marker instead of aborting. A partial sweep must never discard what it already collected.
  - Split the type change and the instance fix into separate commits. Look for a sibling function in the same file that already re-confirms correctly, and mirror it.
- **C2a.2** Treat "falling back to X is safe" in a spec or dispatch as a claim about the CONSUMING control; verify it against that control before writing it.
  - A specified fail-open passes every code review, because reviewers check implementation against instruction.
  - Emptiness is the wrong proxy for "this run carries no signal": a degrade can produce a non-empty but wrong result. Thread an explicit degraded flag out of the failing component.
  - Invariant worth carrying: a degraded input must not produce a confident answer, and a gate must not evaluate cleanly on a degraded operand.
  - Before scoring a "display-only" change, grep whether the field is read by logic anywhere.
- **C2a.3** A parser gating a costly side effect concludes success only on a positive success marker; an unrecognised or error-shaped response is failure.
  - Look for `x?.ok !== false` (undefined reads as success), `?? true`, `?? []` treated as "added", and a `catch` that returns a success-shaped value. Tolerant parsing suits reads, not spend or write gates.
  - Require the success SHAPE before checking emptiness: `(resp?.data?.items || []).length === 0` passes on a dead query.
  - An error body passed downstream as data (`neverError`) plus a fail-open guard turns a permanently broken idempotency query into "no duplicate found" on every run.
  - Validate a GraphQL document against the live API without writing: pass `null` for a non-null variable. Spec-copied types can be wrong (`[String!]` vs `[String]!`), and "verified" with inline literals does not transfer to typed variables.
- **C2a.4** Read the consumer's empty-case branch before choosing a fallback or default: an empty allowlist, denylist, filter or optional field usually means "no constraint".
  - `catch { return [] }` on an allowlist widens the hole; the conservative fallback is a real value. Comment what empty means, or the next reader "simplifies" it back.
  - Classify every aliased wire field LOUD (required, fails 422) vs SILENT (optional with a default). The silent ones whose default disables a filter are the worst: a broken alias returns 200 and silently unfiltered.
  - `extra="forbid"` makes silent drops loud but is a wire-contract change; a repo grep proves nothing about external callers.
  - A diagnostic naming one of several identical sibling fields is partial: measure the whole set.
  - Pair every empty-set assertion with a guard proving the collection was populated.
- **C2a.5** When a value is ambiguous, enumerate every state it could mean and give each a distinct slot; make the fused spelling unrepresentable downstream.
  - Ruling out the one wrong reading you spotted insulates you from the next one. List the states against the target vocabulary: an absent OAuth `scope` had four (never recorded, asked and told nothing, told and absent, told and present) and the vocabulary had three.
  - Name tests for the forbidden INFERENCE, not the value. Silence is evidence only if the speaker follows the spec that makes silence meaningful.
  - Normalise at the acquisition boundary. Never let `|| 0` or `?? false` sit where data enters; missing data is UNKNOWN or NOT_ASSESSED, never 0 and never healthy.
  - Route every vendor-enum comparison through one typed helper that knows the field's representation; int-vs-string mismatches fail toward "fine" with a silent zero.
  - Per-detector failure isolation ARMS a silent resolve when a boolean means both "checked, clean" and "never checked". Isolation is what exposes it, so fix the type first.

## Examples
- C2a.1: a failed options fetch cached an empty label map, so the client's own PR contact resolved to a raw URI, passed a `!excluded` gate, and had its email written to the list with `status:'ok'`.
- C2a.2: a lead's dispatch said falling back to the URI was safe; the fallback could never equal the exclusion sentinel, and a security reviewer filed it as a deliberate implementation choice.
- C2a.3: `obj?.ok !== false` read a 200 carrying error prose as "everything added", logging credits never billed. A work-tracker GraphQL `[String!]` variable failed pre-execution on every run while the idempotency guard read "no match".
- C2a.4: an optional edition filter with a broken alias returned the full unfiltered set under a UI saying Enterprise.
- C2a.5: `hasDisapprovedAd: false` meant both clean and never checked; adding per-detector isolation made a failed fetch resolve every open alert.
