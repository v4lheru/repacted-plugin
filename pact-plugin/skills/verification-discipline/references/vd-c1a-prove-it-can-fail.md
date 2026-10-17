# VD-C1a: prove it can fail

Context: read this before trusting or reporting any test, guard, gate, monitor, validator or
control, and before reporting "falsified", "mutation-tested", "covered" or "the check passes".
A green is evidence that SOMETHING made the assertion true; only a demonstrated red for the
predicted reason says the thing you care about is pinned. Assertion and fixture design is in
[vd-c1b-assertions-and-fixtures.md](vd-c1b-assertions-and-fixtures.md); runner, pipeline and
toolchain scope is in [vd-c1c-runners-and-toolchain.md](vd-c1c-runners-and-toolchain.md).

This family is split nine ways:
- this file: the core rule and its controls (C1a.1–C1a.5);
- [vd-c1a-what-a-check-can-see.md](vd-c1a-what-a-check-can-see.md): which wrong states a check accepts, defaults, and unconditional checks (C1a.6–C1a.11);
- [vd-c1a-mutation-harness.md](vd-c1a-mutation-harness.md): making a mutation land, run and report honestly (C1a.12–C1a.19);
- [vd-c1a-reproduce-and-kill-attribution.md](vd-c1a-reproduce-and-kill-attribution.md): reproducing a coder's claims, running their harness, and test rot (C1a.20–C1a.22);
- [vd-c1a-reproduce-and-kill-attribution.md](vd-c1a-reproduce-and-kill-attribution.md): which test killed what, per clause, and whether a test reaches the code (C1a.23–C1a.30);
- [vd-c1a-survivors-and-direction.md](vd-c1a-survivors-and-direction.md): what a survivor means, mutating both directions, which guard is the net (C1a.31–C1a.36);
- [vd-c1a-gates-and-guarantees.md](vd-c1a-gates-and-guarantees.md): specific guard types, from characterisation tests to DB constraints and UI gates (C1a.37–C1a.50);
- [vd-c1a-falsify-dont-confirm.md](vd-c1a-falsify-dont-confirm.md): stories, self-healing symptoms, claims of protection, one change at a time (C1a.51–C1a.54);
- [vd-c1a-discriminating-measurements.md](vd-c1a-discriminating-measurements.md): ceilings, margins, wide discriminators, replays and pre-registered samples (C1a.55–C1a.64).

## Canonical rules

- **C1a.1** A green never observed failing is not evidence. Break each guard separately and require a red for the reason you predicted before you trust or report the check.
  - Ask of every check, at specification time: what would this read if the mechanism were absent? If the answer is "the same", it is not a check.
  - Sabotage each guard on its own and restore between runs. A blanket sabotage hides the guards that have no discriminating test behind the failures of the others.
  - For each guard, name the input where the old and new behaviour diverge. If you cannot name it, either the guard is unnecessary or the test is not testing it.
  - Make "demonstrate a RED before a GREEN is admissible" a gate on the test phase, not an instinct: reading the check finds none of these defects.
  - The instruments deserve the sabotage more than the code: a broken feature announces itself in use, a broken check is consulted exactly when you want reassurance.
  - Understanding a failure mode does not protect you from it: a tool built to catch guards that cannot fire shipped a rule that could not fire. Only the two-sided proof transfers.
  - Report the falsification result in the HANDOFF, not just the pass count.
- **C1a.2** Read the failure TEXT, never the exit code alone. Write the expected failure reason down before the run; if control and subject fail identically, the harness is broken.
  - A red on a sabotage run is the result you hoped for, so it ends the investigation. That is why a coincidental red is harder to catch than a coincidental green.
  - A mutant that dies at the type layer (fails to compile) or with a SyntaxError proves the compiler works, not the test. On an exhaustive switch, flip an arm's return instead of deleting the arm.
  - Put the weight on a PRESENCE check (the new symbol exists with its value). An absence control (`not hasattr(mod, 'OLD')`) also passes on a typo, in the direction that reads as success.
- **C1a.3** Establish that the instrument could have returned the other answer: a positive control before an absence, a known survivor before an attrition claim, a known non-zero before a zero.
  - Which-guard form: with a guard in place every case passes, so survival attributes nothing. Verify WHICH guard protected you, not merely that you were protected.
  - Two control arms that cannot produce different answers license no conclusion, and their agreement feels like confirmation. Name the outcome that would have distinguished the hypotheses first.
  - Pair a negative control with a positive one. "I removed X and nothing changed" is consistent with both a dead path and a broken instrument; only the positive control separates them.
  - A blind instrument's zero means "this cannot answer". Replacing a wrong number with the blind instrument's zero is the same defect with the sign flipped.
  - A control has a DOMAIN: it validates the instrument only in the state it ran against (live vs exited, populated vs empty, authenticated vs not). Choose it for the subject's condition.
  - A control validates the instrument, never the inference. A correctly controlled absence can be a deliberate exclusion or a ruling you have not received: ask why before reporting it as a miss.
  - Priming can direct a search but cannot manufacture a negative control, so paired controls keep a briefed agent's finding admissible.
- **C1a.4** Sabotage proves a test CAN fail; only the suite count before and after a change proves a test EXISTS. Assume a gate covers nothing until you have watched it fail on that thing.
  - An unchanged count is also what a suite that never ran produces. First show the reporter can report a failure (remove one env var, plant one failing case), then read the unchanged count.
  - Prefer a control whose expected answer is known and non-zero. Two paths agreeing on "0 matches" can both be broken in the reassuring direction.
  - A suite parametrised over an empty collection passes everything. Assert the collection is non-empty as its own first test, and plant one non-conforming member once to prove the suite enforces.
  - After writing an edit table, re-walk it asking where the NEW behaviour should land. An inventory built by grepping the old behaviour cannot see a site that is not wrong yet.
- **C1a.5** Sabotage what the tool READS, never what it rebuilds. A tool that replays migrations, rebuilds artifacts or refreshes a cache at start wipes your sabotage and returns the same green.
  - A sabotage that yields green is ambiguous between "the control is broken" and "the sabotage did not land". Terminate on a positive observation about source state (`grep -c` the removed string), never another run.
  - The intuitive negative control often perturbs a shared dependency (emptying `PATH` also breaks the shell and the runner). Break exactly the thing under test, for example point the code at a binary name that does not exist.
  - To test a gate in production, break what the gate READS (its probe path), never what it protects. A gate never observed to refuse is indistinguishable from one that cannot.
  - Prove a new mechanism on a throwaway target first, by watching a control that would fail if the mechanism were wrong. Time pressure is the argument for this step, not against it.

## Examples
- C1a.1: sabotage testing caught six guards that a 90-to-147-test suite did not pin; twice the assertion was true for the wrong reason.
- C1a.2: a discrimination test failed on the control exactly as predicted and identically on the subject, both with `ModuleNotFoundError`; only the failure text showed it measured nothing.
- C1a.3: a real API key and a deliberately fake one returned the same 401 and the key was declared revoked; the vendor scopes keys per endpoint and an out-of-scope call returns that same 401.
- C1a.3: a log query's positive control ran against a live container; the subject had exited, which needed a different query shape.
- C1a.4: deleting a write left 118/119 tests before and after; no assertion anywhere covered it.
- C1a.5: dropping an index from a scratch DB left a schema-drift gate green because the gate replays the migration chain first; injecting a column into the schema source gave exit 1.
- C1a.5: a healthcheck pointed at a non-2xx path failed the deploy while the same image on the real path succeeded 37 seconds earlier, proving the gate refuses.
