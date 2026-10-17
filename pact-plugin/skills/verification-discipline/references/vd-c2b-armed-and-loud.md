# VD-C2b (split): armed and loud

Context: read this when you are about to count on a control: a guard that stayed silent, a
fail-closed default "someone will notice", a documented role or checker, an invariant stated in
comments, a recorded value, a liveness flag, or a failure notice. Present is not armed, and
loud to nobody is silent. Entry file: [vd-c2b-controls-and-construction.md](vd-c2b-controls-and-construction.md).

## Canonical rules
- **C2b.16** A guard's silence carries information only inside its verified coverage; get approval before an irreversible attempt, and treat a guard that failed to fire as an environment finding.
  - Ask which LAYER a guard hooks (see A2.33): a merge guard on git CLI verbs does not see `glab mr merge`. Never structure an action as "try it and see whether something stops me".
  - Report a gap you fell through even when the outcome was fine: "it turned out fine" and "the control worked" are different claims. Establish order from your own call sequence, not cross-agent timestamps.
  - After a near-miss, ask what actually stopped it and whether it was aimed at the hazard. A barrier that refused for an unrelated reason means you are unprotected there.
  - The same broken approval channel can fail open for writes and closed for deletes. When blocked, stop, back up, leave objects archived, never route around it, and do not repair a user's security control uninvited.
  - Never revert with `git checkout -- <file>` while you hold uncommitted work in it; reverse the edit programmatically.
- **C2b.17** A fail-closed control is self-reporting only when it is the binding constraint; name WHO sees it fire, WHERE, and how soon, and assert the effect, not an invocation count.
  - Behind another closed gate (reachability, a parked feature flag, an unshipped caller) a fail-closed gate is as silent as a fail-open one. Re-check loudness whenever the outer control changes.
  - "Visible", "surfaced", "logged" and "alerted" are claims about a named READER. A queue provisioned with no consumer "waits visibly" only for whoever queries that table; raise an error at boot instead.
  - A consumer written against an unlanded producer needs a landing date, not a present-tense docblock. Copy promising what another component will do should describe the state, not the future event.
  - A status column records a WRITE, not the world: render "last recorded X: failed, <when>". First check which write sets the timestamp; if it only records when the attempt started, fix the producer or render no age, because a caveat beside a date is read as the date.
  - Verify claims about your own code before confessing to them; an overstated self-criticism is still an inaccurate report.
- **C2b.18** Before calling an access path bounded, verify the control object exists in the target environment; a design doc, role name and runbook can describe something never executed.
  - Read the doc's own status line. Report the third verdict when it applies: "safe by conduct, unbounded by construction".
  - The boundary is who can change a setting, not where it is stored: `default_transaction_read_only` is a session GUC the caller can switch off. An override string shared across call sites is spent everywhere at once, and a guard that makes you assert a false statement to proceed is a lying instrument.
  - A one-off checker either becomes a suite test (self-check that its input was read, a positive control per negative pattern, its own weakness stated, mutation-proved, committed separately) or stays out of the repo. "It's the only instrument we have" is an argument for building the gate.
- **C2b.19** An invariant stated only in comments or prose is enforced nowhere: pair it with a check that refuses, and let the comment explain that check to the person it stopped.
  - Enforce at the LOAD boundary, not the comparison site: teaching a matcher both forms leaves any dedup key built from the raw value split. Check every row, treat null and empty as violations, and throw rather than skip when the value defines a suppression scope.
  - Split the tests: one half tests the check; the other drives the pure functions past it, so deleting the guard later shows what it protected. Label "absence reads as clean" assertions as characterisation, not endorsement.
  - A guard with a comment and no test: the comment is the bug report. Check whether the suite already refuses before building a mechanism, and verify an enforcement claim in a comment by breaking the code and running it.
  - Write warnings around the durable OUTCOME and derive the trigger from the invariant; a warning aimed at the wrong trigger ("when a pager is added") gets discharged on the safe change and arms a false all-clear. Phrase it against a state, not a feature.
  - Prose drift sits in the union of a changed and an unchanged file, so no diff shows it; redundant assertions make it worse. Ask what states a rule about the thing this diff changes. Correct a figure everywhere it appears, and make derived numbers cite their source row.
  - Before changing a shared regex, constant or format, grep its distinctive SUBSTRING for a hand-copied twin across a language boundary (a SQL literal "mirroring" a TypeScript function); caller count is blast radius.
  - Read why a harness excludes a risk area before estimating the remedy. When a caller must special-case one member of your documented rule, that branch is a bug report about the rule; when writing "callers should X", ask what FAILS if they do not.
- **C2b.20** Recording a value is not enforcing it: find the `if observed != expected` line, never test that a field is populated, and fail closed before any write.
  - On mismatch leave the guarded column NULL and put the observed value somewhere diagnostic; writing the wrong value satisfies the schema. Give the value one setter. Pair negative tests with a positive control.
  - A fail-open redundant guard whose storage is missing is inert and silent. Assert its storage exists at startup, and state in the handoff which guards are armed and which are pending.
  - A fact captured and never asserted on is indistinguishable from one never captured. Key the assertion on the broadest correct set, never trip on pre-instrumentation NULLs, and do not arm it while a known cause is unfixed.
- **C2b.21** Split an LLM guard into deterministic output checks (ship on tests) and prompt instructions (ship only after a live run shows them firing); append prompt rules so cutting one is a byte-exact revert.
  - Unit tests of a prompt rule prove the text is sent, never that the model obeys it. "Identical to the input" from an eval usually means normalised-identical; measure bytes before designing a byte compare.
- **C2b.22** A new contract (`None` = failed, `[]` = empty) is inert while a parallel mechanism still carries the same fact; derive one from the other, then mutate the contract back and count the dead tests.
  - Zero dead tests means you shipped documentation. Label the slots of a `gather` over a conditional task set (`("kb", result)`), or a failure is blamed on the wrong store. Cover the partial-failure middle state.
- **C2b.23** Derive job liveness from the AGE of the last recorded scheduled run, not a boolean the code sets; the verdict is three-valued and "undecidable" never renders healthy.
  - `now() - max(started_at) WHERE trigger = 'cron'` grows by itself when runs stop. A per-item table cannot tell "examined zero", "refused" and "never ran" apart; log every run in its own table, including refusals and empty runs.
  - Open the row at run START (platforms skip a run while the previous one executes), record the trigger, and count only scheduled triggers.
  - Declare the expected interval and measure the observation; err the declared interval SHORT, since a longer one silences the witness. Assert `expect(overdue).not.toBe(false)`.
- **C2b.24** Route failure notices to the operator who can act and post to stakeholders only on full success; test both directions, since a one-sided reporter is a constant.
  - Gate on the full success condition (`ok === true && !!url`). Delete failure strings from the stakeholder node rather than gating them behind a branch.
  - Force a real failure and confirm the stakeholder node ran zero times, then force a success and confirm the operator DM did not fire. Add a retry for transient blips first.

## Examples
- C2b.16: `glab mr merge` went through a merge guard that hooks git CLI verbs; the bound approval arrived afterwards. An n8n write guard never engaged on `curl -X PUT` it matched, yet blocked DELETE outright.
- C2b.17: a write gate closed on `unknown` capability could never be the reason anything was blocked, because the write path was parked behind a separate flag. Only the `SET_PENDING` statement wrote the status timestamp.
- C2b.18: a read-only role doc said "commands drafted, nothing has been executed"; the only route to production held DROP. Deleting a component's JSX left a 688-test node-only suite green.
- C2b.19: a prefixed `projects/{id}` in a column three comments called bare made the exclusion gate read `{kind: 'none'}` and write the client's PR contact as a prospect. A plan sized storage from 1,656 rows/day while its own evidence table said 181.
- C2b.20: pointed at the wrong workspace, a run authenticated, stored the wrong `source_identity` and graded ok. An escalation guard's column request never landed and it sat inert.
- C2b.21: a URL post-check caught 19 of 19 corrupted URLs; the prompt-rule sentinel fired 0 of 12 times.
- C2b.22: flipping `return None` back to `return []` broke zero tests until the counter was derived from it; then three died.
- C2b.23: `schedulerConfigured: false` was a hardcoded literal, honest only because nothing scheduled the job.
- C2b.24: a report node always said "published"; its replacement always said "FAILED" because it read `$json.body`.
