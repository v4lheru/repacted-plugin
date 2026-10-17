# VD-C1a: survivors and direction

Context: read this when a mutant survives, when a guard's red looks like proof it is
load-bearing, or when you are about to write "no test would catch this". Which test killed
what is in [vd-c1a-reproduce-and-kill-attribution.md](vd-c1a-reproduce-and-kill-attribution.md). Entry file:
[vd-c1a-prove-it-can-fail.md](vd-c1a-prove-it-can-fail.md).

## Canonical rules

### What a survivor means
- **C1a.31** Not every survivor is a weak test. Check in order: did it land on the right site and fail behaviourally; does a second mechanism enforce the rule; is it equivalent? Only then is the test weak.
  - Two mechanisms enforcing one rule make each unfalsifiable: collapse them (one nullable object) rather than testing around both. Keep both only when an input can separate them (see vd-c1b).
  - Show equivalence with a table over every reachable input. A fabricated kill asserts an impossible distinction and looks like coverage forever; record the equivalent mutant with its reason.
  - An equivalence verdict must be MEASURED, never derived: enumerate every observable the mutated expression feeds (a returned counter as well as the flag).
  - A branch whose arms return the same action is invisible to action assertions: assert that the reason strings differ.
  - A threshold pair can be blind on both sides (the positive sits AT the bound, the negative exits early). Assert both sides in one test at a value strictly between the bounds.
  - To audit misleading test names, enumerate the decision sites in the SOURCE and mutate each boundary; searching test titles is noisy and trusts the names.
- **C1a.32** Mutate a guard in both directions: the defect must go red AND correct code that merely LOOKS like the defect must stay green. A gate red on correct code gets `allow_failure`, then ignored.
  - Match the defect's SHAPE, not its vocabulary ("where else does this string legitimately appear?"), then re-run the true-positive mutation to prove the narrowing kept its teeth.
  - Enumerate a guard's outcome states and, for each state no test reaches (the "cannot determine" branch), invert the direction it resolves to. Reaching it needs a constructed, labelled fixture.
  - A one-directional mutant marks an under-specified rule: for each mutant ask for its mirror, and if none is possible, say why.
  - Prove a recorded-value monitor (tripwire, snapshot, golden) trips, growing and shrinking; the proof also catches your own wrong model. When firing means success, assert the failure message's content.
  - Over-corrections (`if (true)`, `&& false`) must fail a test too, or the guard is untested in the direction that costs functionality.
- **C1a.33** Prove mutations are independent: state which test each mutant must redden, and treat a mutant that reddens several as coupling. Say which required test alone protects each invariant.
  - Put the reason on the carve-out itself, not only in the test that protects it, or a tidy refactor deletes it green.
- **C1a.34** Red tests prove a guard is OBSERVED, not that it is the net. Diff the failure MESSAGES: the same refusal through a different message means another mechanism protects you.
  - Delete each guard in turn; one whose removal breaks nothing is decoration. Remove it unless a test proves a case only it covers, and pin the real control with an inverted mutation test.
  - Where a check runs can be the control (a host check on the RESOLVED value); say so in the comment.
- **C1a.35** A test is justified by coverage (the mutant survives without it) or by diagnosis (it names the property that died). Say which, and apply the mutant before writing "no test would catch this".
- **C1a.36** Take the mandatory RED against a stub reproducing today's behaviour, not a missing import, then read which tests passed in that run: those a do-nothing stub satisfies do not discriminate.
  - Pair every "X survived" assertion with proof the operation ran. Before trusting a mutation verdict, run the mutated function on the input you expect to move and print before and after.
  - When the right and wrong fix share a coarse outcome, only a mechanism assertion discriminates: write the wrong fix down and ask which assertion changes value under it.
  - A test that stays green across a change to its own premise is the suspect: list the premise's dependents, expect failures, re-run the green ones on a fixture that exercises the change.
  - A count equal to the whole population, or zero, means the predicate is broken (`undefined === undefined` matches everything).
  - Confirm the SUBJECT before refining the METHOD: does the named column, table or file exist and cover what the request assumes? Authority is the aggravating factor.
  - A rule written into a header feels implemented. Ask what fails if someone ignores it, and write the test or type first; a consumer's special case is the counter-example to your rule.
  - Validate a reconstructed derivation (a hash, a partition name) on two known values before trusting it on the unknown, and print a response body once before asserting a path in it.
  - A state claim ("tree clean", "the fix is at the cited location", "nothing staged") is a fact with a timestamp. Re-read the artifact at the moment of ACTING, and re-measure just before the irreversible step, not before announcing it.
  - When corroborating a teammate's figure, say whether you measured before or after seeing theirs; knowing the expected answer suppresses the anomaly that exposes a broken instrument.

## Examples
- C1a.31: raising a transport-failure threshold `>= 3` to `>= 1` killed neither test: one sat at 3, the other returned through an earlier branch.
- C1a.32: inverting a guard's "unavailable" direction to silence killed zero of 681 tests.
- C1a.34: neutering a completeness check turned three tests red, yet the payload was still refused by a later loop over the expected ids; only the message changed.
- C1a.36: of ~190 assertions, exactly one caught a gate that fired on a null rating; every band-level assertion held under both fixes.
