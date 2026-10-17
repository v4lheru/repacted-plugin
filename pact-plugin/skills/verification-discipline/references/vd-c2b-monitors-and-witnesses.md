# VD-C2b (split): monitors and witnesses

Context: read this when a monitor, alert, error handler, test-repoint guard or pre-write check
is supposed to catch something, or when a defect was caught and you are about to call the
class covered. A monitor that never fired looks exactly like health. Entry file:
[vd-c2b-controls-and-construction.md](vd-c2b-controls-and-construction.md).

## Canonical rules
- **C2b.25** Test-fire every monitor at creation and check the monitor's OWN run history; until it has been seen working, "no alerts fired" is unknown, not healthy.
  - Force the failure it exists to catch and confirm the message lands where people read it. Configured is not the same as working; an alert to one person is already half broken.
  - Grade a run that yielded NOTHING as failed, uniformly; rank severity by how well a failure hides, not how dramatic it looks. Discriminate on the actor's own completeness assertion, not a cause list.
  - Count what your guards REJECT, not only what they pass. A fatal path must not skip the block that records the evidence, or you lose the audit trail only on the bad runs.
  - Decline a proposed patch by demonstrating the case it breaks. Controls that are right about their primary hazard are often wrong about an adjacent one; real traffic finds that, review does not.
  - Make a documented cross-component dependency an executable tripwire. If it goes red when a teammate does the RIGHT thing, keep it in a manually run script, say so, and note that it proves the rule LANDED, not that switching is better.
  - A null result from an instrument blind to the phenomenon (a content hash that strips the markup under test) proves nothing. When a hypothetical rationale later acquires evidence, move the evidence next to the decision.
  - A differ with no baseline reports "no change": it must fail loud. Grep your own output for per-row unavailability markers before reading the summary, and make a degraded state typographically different from a healthy one.
  - A fast instrument tests a PROXY (module resolution under node; presence in a DOM); ask what the real-environment property is and whether your instrument runs there.
  - An aggregate over a parent the writer always creates fresh (`min`, `latest`, `group by` over one child) has never run; the feature that adds a second child arms it with no diff to the aggregating code. Test at N = 2.
  - A tidy argument that a guard is UNNECESSARY deserves the most suspicion: demand the ROUTE to the guarded state before any test, since enumerations are unfalsifiable from inside.
  - Verify a repeating probe's method once before it loops. Operational litter in shared diagnostic surfaces (a browser profile, a console log, a scratch table) becomes someone else's evidence; clean it up or announce it.
- **C2b.26** Name who caught a defect: a party that knew a claim about itself was false is a witness, not a detector, and variants that name nobody (or the right party) pass silently.
  - Two catches measure how loud that variant is, not how rare the defect is. Say when the witness is the only detector.
  - Build a detector by reading the same data twice with implementations that fail differently, and treat disagreement as a property of the data first (a duplicate key), not a bug in one reader.
  - Before joining on a name-shaped key, compare distinct keys to row count. Two columns can share a name and not a meaning.
- **C2b.27** Chain a pre-write guard to the write with `&&`, or run the check inside the writing process; an unchained guard cannot fail closed, so its verdict carries no information.
  - Put the `&&` after a heredoc's closing delimiter. Prove the wiring by making the guard fail once on purpose.
  - After a guard threw, read the stored content back: the write may already have landed. The connector must match the intent: `&&` where a failure must stop the next step, explicit handling where non-zero is a legitimate answer.
- **C2b.28** When repointing a live workflow for testing, enforce restoration with a raising post-write guard that reads the sending node's own field, and mutation-test that guard.
  - Fail on: the scratch target present anywhere, the notify node not on the required channel, any leftover temporary node, a surviving test override, and an unreadable object ("treat as FAIL, not pass").
  - A string count over the whole document stayed green because the id also appeared in metadata. The checker is written under the same assumptions that produced the defect; mutation-testing it is what breaks that inheritance.
- **C2b.29** Do not add a second guard for the same failure if it makes the verification print the same thing whether or not the first guard works; overlapping guards can spend falsifiability.
  - Prefer the single, exercised guard that covers the wider case (deny-by-default over a subdirectory switch). If you need both, keep a test that disables one and asserts the other still holds.
  - Verify by negative control: move the guard aside, confirm the hazard returns, restore it. A branch whose test passes with the branch deleted is one control plus a decoration; delete the redundant path and re-point the test.
- **C2b.30** A bound whose contract has a DIRECTION ("at least", "conservative", "upper bound") needs a test on the sign, with the oracle measured independently of the implementation.
  - Tests of monotonicity, closeness and thresholds hold equally for an estimate uniformly biased the wrong way. Write the direction test first and watch it fail on the current code.
  - Prefer a provable bound to a tight one, and fix the estimate so the docstring becomes true, never the docstring down to the behaviour.
- **C2b.31** Treat `if (knownBug) treat as OK` as a detector for that bug's return wired backwards; it must move in the same change that fixes the bug, and self-cancelling forms beat silent ones.
  - The family: an `expected_because_of_N` arm, a skip or allowlist entry "until #N", a widened tolerance, an `it.fails` pin. Pin mechanics and citations: C2a.6.
  - When closing a defect, sweep control code for its ticket number, then read what each hit DESCRIBES: one number can name two defects. Ask at review what marks the day an allowance stops being correct.

## Examples
- C2b.25: an error workflow's Slack node had non-JSON `blocksUi` and threw on all 4 fires in 3 weeks; stakeholders found the failures. `next build` failed while `tsc` was clean and 1,143 tests passed.
- C2b.26: a task-claim hook misattributed claims, and was caught only because each agent knew the claim about itself was false. Two reads of one staff file disagreed because full names were not unique.
- C2b.27: an assertion threw and the separate `cli.py update --replace` ran anyway and returned `ok: true`.
- C2b.28: a live monthly workflow was found pointing at the scratch channel twice in one day; the schedule, not a control, protected it.
- C2b.29: adding `skipSubdirectories` would have made `clasp show-file-status` print the right two files whether or not `.claspignore` worked.
- C2b.30: `estimateAppendBytes` under-estimated by one byte per shard despite 81 assertions and a 13-guard falsification harness.
- C2b.31: a parity gate's `expected_rest_36` arm would have classified a regressed REST parser as expected.
