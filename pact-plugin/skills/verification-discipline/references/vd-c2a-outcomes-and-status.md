# VD-C2a (split): outcomes, third values and status surfaces

Context: read this when a function, verifier, status mapper or UI must report an outcome that
has more than two honest values: loading vs unknown vs empty vs checked, a service fault vs a
document defect, row written vs follow-up lost. Two-valued vocabularies force one true case to
be published as a lie. Entry file: [vd-c2a-fail-open-and-unknown-states.md](vd-c2a-fail-open-and-unknown-states.md).

## Canonical rules
- **C2a.14** Give every state a distinct, asserted rendering: a renderer or status mapper with fewer branches than states turns unknown into a healthy-looking result.
  - A status UI has four outcomes: pending (render no claim), unknown (the read failed), never (the read succeeded and is empty), checked (a timestamp). Branch on `isPending` first; only a successful read may yield "never".
  - Blank is allowed only for the transient pending state. Anywhere else an empty string reads as healthy, so it IS the dangerous category.
  - Assert every case renders a NON-EMPTY string and that all cases DIFFER; one assertion on the alarm branch leaves the incident branches unguarded.
  - A distinction is only as real as its narrowest carrier. Never gate rendering on truthiness (`x &&`); use `x !== undefined`. Give abstention its own visual state (icon plus words, exposed as `data-tone`).
  - Assert the ABSENCE of hiding (`hidden`, `sr-only`, `opacity-0`, a responsive `display:none`) at a phone viewport. String-level tests cannot see a hidden element or an early `return null`; name that coverage boundary.
  - A status mapper's final `return` must not be the healthy value. Add a neutral state, and test with a discriminator (`statusKind(observed) !== statusKind(unobserved)`), not an equality.
  - Grep for the WRITER of every field a status surface reads. If the only writer is a mock module, the field is never populated in production.
  - Proposing a new status field creates this hazard: name its writer and trigger in the plan, and model never-written as its own variant (zero observations stamp nothing).
  - Smoke-test the loading state (throttle the request); a fast local API hides it.
- **C2a.15** When hardening a "could not verify" surface, audit the SUCCESS branch hardest: a configuration signal is rarely a per-item guarantee.
  - Ask of each healthy state: what exactly does this value prove, and what does the reader think it proves?
  - Grep whether the distinguishing fact (indeterminate, degraded, skipped) is persisted at all. If not, any copy asserting success is unfalsifiable and must be softened.
  - Pin the softened wording with a mutation that restores the over-claim. Also pin the opposite: a caveat that makes the healthy state read like a warning ruins the indicator.
- **C2a.16** When two truthful sources disagree, add a third status value and publish the raw inputs; never pick the source you trust more.
  - Print the generator's actual output and read it against the source data before asserting on it. A self-contradicting record is obvious in output and invisible in code.
  - Make the authoritative source the one that cannot overstate, and prefer a count ("4 of 13") to a boolean over an aggregate.
  - Do not invent a classifier the data cannot support; ship the documented limitation. Never silently drop a published entity from a projection.
  - A verifier judges STATE and reports HISTORY: completed Jobs, terminated pods, old ReplicaSets and archived rows get a third label (`PRIOR`) that never fails the check. Reclassify, do not filter them out.
  - Test both polarities of a new label, and run the instrument even when you are confident of the answer.
- **C2a.17** Catch at the layer that owns the context and return a distinct third outcome; never collapse a service fault into a document-defect outcome.
  - An error thrown past the function that built the context makes every caller re-implement the taxonomy, and callers diverge until one silently drops the record.
  - Classify the known errors and RE-THROW the rest; a catch-all hides breakage one level out.
  - Carry sub-reasons (unavailable, invalid response, refused). Put evidence fields (a payload hash) only on faults where the payload was actually transmitted.
  - Expect an existing "THROWS" test to change contract: keep its reasoning, change the asserted shape, add companions pinning both directions.
- **C2a.18** Classify a caught exception by where it was thrown relative to the write (succeeded, incomplete, unmeasured, failed), tag at the writing function, and check what consumes the status.
  - Assert the four buckets SUM to attempts. Anchor the tag classifier with `startsWith`, never `includes`, and never re-derive it by matching ORM error text.
  - Test at the PRODUCER: drive the real failure, assert the tag is emitted AND the primary row landed, plus a converse. Mutate the emission, not only the classification.
  - A misclassified failure often feeds a state machine (a `failed` status re-admitted items to selection). "Fixes the counter" and "fixes what the counter counts" are different claims; say which in the handoff.
  - Capture the driver's code and detail from `error.cause`; a truncated echoed SQL string cannot explain the failure.
- **C2a.19** When a primary write commits and a best-effort secondary write fails, return the secondary outcome (`written | not_applicable | failed`) inside the success result.
  - `err()` makes the caller revert a write that succeeded; a bare `ok()` merges two facts. A boolean collapses written and not-applicable and invites re-collapsing failed.
  - Widen the success type and update every caller. For bulk actions carry `expected`, `written` and `failed` counts.
- **C2a.20** Omitting an undetermined row can equal writing a wrong value: model RESOLVED / NEGATIVE / UNDETERMINED, persist only the first two, and assert completeness against the expected count.
  - A builder populating from empty must abort and write nothing on any undetermined entry. An incremental refresher may skip one, because the old row survives. Same code shape, opposite safety.
  - "Exit 0" and "a file was produced" cannot catch a partial result; completeness is a separate property. Check that a pacing constant matches the rate limit its comment claims.
- **C2a.21** Hoist every fault check above an early return for a normal empty state; a broken deployment otherwise answers cheerfully until data arrives.
  - When adding any early return, list each fault check in the handler and say which side of the return it is on. Add new fault checks beside the others.
  - A branch that runs only when data exists is unreachable on an empty fixture. Clear env vars the repo's own `.env` sets in `beforeEach`.
  - A blanket find-and-replace also rewrites claims in comments; re-read prose that mentions a renamed symbol.

## Examples
- C2a.14: a health header used `{freshness && …}` and `hidden sm:inline`, so "nothing has been checked for 14 days" did not exist on a phone while every test passed. All 60 production rules showed a green OK because the only writer of `lastStatus` was the mock-data module.
- C2a.15: the healthy branch of a banner said "no exclusion badge means checked and clear"; the signal only meant the client had a project mapping.
- C2a.16: a phase with plan `proposed` and 8 tasks in progress got a third value `preparing`. A post-deploy checker failed four `Succeeded` Job pods that finished two days before the fix existed.
- C2a.17: two new scorer errors escaped the scoring function, and the upload route saved no record at all for a valid upload.
- C2a.18: 257 pieces were reported failed daily while each held a snapshot row; the `failed` status re-queued them and doubled weekly API spend.
- C2a.19: marking a contact as the client's PR contact committed while the exclusion upsert failed, behind a plain `ok()`.
- C2a.20: a Slack-ID cache builder skipped 429s and wrote a partial cache with exit 0; pacing was ~500 req/min against a ~50/min tier.
- C2a.21: an empty-corpus short-circuit sat above the model check, so a misconfigured model answered 200; the same bug had been fixed one screen above.
