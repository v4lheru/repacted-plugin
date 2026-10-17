# VD-B2 (split): guard maintenance and accidental protection

Context: read this when you change, tidy, re-key, invert, narrow or delete anything near a
guard: a constant it compares, a helper it calls, a default it relies on, a flag it gates, the
noise it produces. Guards die by reasonable edits. Entry file:
[vd-b2-claims-comments-guards.md](vd-b2-claims-comments-guards.md).

## Canonical rules
- **B2.20** Never justify a control by a coincidence of current values: assert its permanent reason, because a stated reason that expires argues for the control's deletion.
  - A branch comparing two constants (`if (A === B)`) goes dead when either moves: it keeps compiling and passing while its coverage becomes zero. It is either dead or always taken; neither is a test.
  - Sweep test NAMES as well as comment bodies; a name is what a reader and a dead-code pass scan. Replace a contingent reason ("they might collide again") with a permanent one ("a version string is not self-identifying") and mutation-check it.
  - When you bump a constant, grep its old VALUE and all two-constant conditionals as part of the bump. Record the coincidence, if worth keeping, as dated history, and say it is not the reason.
- **B2.21** Before fixing a confidently wrong helper or narrowing a blunt destructive operation, enumerate the callers and later writes that the wrong behaviour was accidentally protecting.
  - When a fallback becomes a refusal, ask of each caller whether its safety depended on the wrong value being alarming; name the guard that actually holds the line and run its test. `null` is not neutral: downstream it usually reads as a determinate negative.
  - Narrowing a `DELETE` lets rows survive that a later `INSERT` may collide with under a UNIQUE constraint. Read the live constraints first, and add a second narrow delete keyed on the constraint's identity.
  - Check the test double can express the narrow form; if it cannot, broad and narrow are indistinguishable in tests. Extend it and pin the extension with a meta-test.
- **B2.22** If the obvious repair for a guard's false red would gut it, the guard is mis-specified: separate the invariant from the changing part and name the safe repair direction in the artifact.
  - Safe and hazardous copy share words; only polarity differs. Count mentions and prohibitions, assert they are equal, plus mentions > 0 (the first stops narrowing the pattern, the second stops deleting the sentence). Widen the prohibition pattern; never narrow the mention pattern.
  - Assert a sensitive flag's SHAPE (a bare literal) and its VALUE in separate tests, so arming it legitimately does not require loosening the structural check. An anchored shape check beats a blacklist; verify that by sabotage.
  - When a revert-catcher blocks the safe direction (disabling a flag in an incident turns CI red), say so in its failure message and comment, and offer the alternative. A flag flip leaves stale second copies (a boot log, a health field): bind them with a test.
  - Before relying on someone else's green, check the cheapest thing that would make it meaningless (a spec with `it.todo` and no assertions). Derived is not deployed: check deploy topology before treating derivation as the whole answer.
- **B2.23** Migrating one consumer by repointing a SHARED dependency field migrates every consumer of it: add a second field, and grep the field's consumers before any migration.
  - Give a read that crosses into a consumer you were told not to touch its own field, defaulting to the old one, so every existing caller stays byte-identical. Verify with a negative test plus a positive companion.
- **B2.24** "Make the default fail safe" is underspecified until you name WHICH failure; audit every caller before inverting a default, and derive intent from what a request structurally is.
  - A default safe for cost can be unsafe for freshness, and a permissive default may be a documented contract (a manual button relying on it). Enumerate callers across src, k8s, scripts and UI fetches first.
  - A contract lost once in a transport migration (script to Helm template) will be lost again: fix the call site AND the derivation. Key intent on a structural property (an API-key header vs a session cookie), not on a token a caller must remember.
  - Before disabling a flag, grep every WRITE it performs and ask who else produces that value; a flag that is the only writer of a state is a scheduling mechanism. Prefer a TTL predicate on the SELECT to a mass UPDATE, set it just under the cadence, and write an anti-freeze test.
- **B2.25** The most dangerous wrong helper is the one already imported: before reusing a shared-looking helper in a copied wiring pattern, check for a deliberately distinct domain twin.
  - The imports you inherit are part of what you copy. A comment or migration saying two things are "deliberately distinct" warns that a convenient shared path exists.
- **B2.26** Separate a value being ACCEPTED (the hole: fix at auth time, everywhere) from being CONFIGURED empty (often correct in prod); read the live config and secret before a startup refusal.
  - A guard keyed on `environment == "production"` is inert if the config defaults to development. Prove the guard with negative controls in both directions, and assert ordering when it matters (before the DB pool opens).
- **B2.27** Compute where a new control changes outcomes: inside another control's blocked range it does no work, and a guarantee resting on a numeric coincidence vanishes on re-tuning.
  - Solve the break-even and compare it with the neighbouring controls' active ranges. Replace aggregate checks with criterion-level ones that survive re-weighting. Collapse controls asserting the same thing into one function; a missing AND-clause fails permissively.
  - Calling a control that never engages "the primary defence" is worse than not having it, because the label decides how much scrutiny the other layers get.
- **B2.28** A fix that makes X reliable is never evidence against the guard on X: name the precondition your fix depends on and check whether the guard covers that precondition failing.
  - Making a failure rarer makes it less expected, which raises the value of disclosing it. A guard is not less necessary because what it guards against is not happening now.
- **B2.29** For a guard or test kept for a scenario that has not happened, write at the site that it has no live caller BY DESIGN, why, and what deletion re-arms; dead-code cleanup removes it otherwise.
  - Ask of any guard what would make a competent person delete it, and whether they would be right about today. The test defending it needs the same note, or nothing guards the test.
  - Tightening one end of a contract can orphan the tolerance at the other end; tell that end's owner.
- **B2.30** Before making a system quieter, name who noticed the noise stopping; put the formal detector live first, and prefer a positive heartbeat over absence of complaint.
  - After quieting, one quiet channel can mean success, a coverage hole or total death. Find informal monitors by asking how the last failure was found; if the answer is the client, that is the worst possible owner.
  - An undeployed watchdog plus a quieted channel is a decision to open a monitoring gap; state it and its duration. Key the heartbeat on hours since the last SUCCESSFUL run.

## Examples
- B2.20: a test block titled "the collision that makes the sentinel necessary" went dead when `REDACTION_VERSION` was bumped; one bump falsified four comments, a test name and a test body across three lanes.
- B2.21: `pickClientState(states, [])` returned another client's record whose do-not-contact stage had been blocking a write for the wrong reason; returning `null` made that gate pass.
- B2.22: a regex forbidding "re-run" went red on the safest copy, "Do NOT re-run it". A fence asserting `liveWriteProven: false` also forbade the token `true`.
- B2.23: `deps.vendor.client` fed both the annotate and the suppress stages, so a one-line swap would have migrated suppression too.
- B2.24: inverting `forceAll = !isCron` would have turned a manual "Sync now" button into a no-op six days in seven, and `forceAll` was the only writer of `enrichment_status='stale'`.
- B2.25: `parseEncKey` from another integration was imported eleven lines above; the domain twin `parseVendorEncKey` exists because the keys and AADs are deliberately distinct.
- B2.26: a demo password absent from both configmap and secret was correct for an SSO production; a startup refusal would have crash-looped it.
- B2.27: a coverage-shrunk sort key reordered only below 0.50 while the gate already blocked below 0.70.
- B2.28: a peer was told to drop a staleness disclosure after sampling moved to a twice-daily job; the repo had a commit titled "Re-enable sync CronJob".
- B2.30: a 334-hour silent poller death was noticed only because the client stopped receiving constant alerts; the quieting work shipped before its watchdog was deployed.
