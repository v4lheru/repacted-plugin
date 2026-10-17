# VD-C1a: falsify, don't confirm

Context: read this when you are investigating a cause, validating a fix, choosing between
hypotheses, or about to accept an explanation, a verified number or a "this is already
covered". The failure here is a test or an argument that can only agree with you. Designing the discriminating measurement is in
[vd-c1a-discriminating-measurements.md](vd-c1a-discriminating-measurements.md). Entry file:
[vd-c1a-prove-it-can-fail.md](vd-c1a-prove-it-can-fail.md).

## Canonical rules

### Stories, symptoms and claims of protection
- **C1a.51** A plausible story attached to a gap stops anyone re-checking it. When a gap has a stated cause but no arithmetic or measurement, do the arithmetic; it is usually a single step.
  - When a number is put under review, grep what was computed FROM it: figures derived by subtraction propagate as measured, and a corrected headline leaves its derived figure stale.
  - Pre-register review criteria, the expected reject mode, and what will NOT trigger rejection before the number exists. Additions are dated addenda, never silent folds.
  - Auditing a section does not audit the number inside it; say which parts you did not check. Coverage denominators that shed rows unable to succeed are in vd-d1.
  - When a documented design decision explains a complaint, name the measurement that separates "working as designed" from "defect", and run it; never let the explanation be the answer.
  - Audit what the dispatch layer EXCLUDES (type guards, skip-lists, `isinstance` filters) before what the rules MATCH: an exclusion removes a population from every rule at once.
  - Agreement from opposite premises hides a false rationale that then gets inherited. When two parties agree, ask each for the PREMISE, and record the reason with its evidence.
  - Each correct local explanation can hide a recurring cause. Write exceptions to a list, and count incarnations from artifacts (rosters, journals), never from recall or a hand-kept tally.
- **C1a.52** When a symptom self-heals on a schedule (quota reset, TTL, retention window), production recovery cannot validate a fix. Rely on a test that fails on today's code, and say so if the calendar rolls.
  - Name the reset date early and carry the constraint verbatim, as a numbered heading, into the TEST dispatch. Re-check the live symptom just before the fix lands.
  - "Latent because the wrong answer currently matches" is a live defect. Make the failing test cover the branch that is actually firing, not the easiest one to mock.
  - A user's diagnosis of their own problem is a hypothesis: measure the phenomenon's signature (a day-of-week histogram), segment by actor, and print raw timestamps. Tight variance is a schedule.
  - Root-cause diagnoses expire: pin each finding to its snapshot instant and re-verify before implementation; say which snapshot each version was true for.
  - Compare at the period the process settles on (deferral, rollup, netting), and ship the exclusion rule with the number.
  - To tell "under-recorded" from "under-paid", read the artifact the counterparty received, and state both branches before you get the answer.
- **C1a.53** A wrong "already protected" claim closes the question while a wrong "broken" claim gets checked. Build the claimed-covered case from measured facts and run the SHIPPED code.
  - Measuring a mechanism's INPUTS does not tell you which branch runs; trace it or run it.
  - Unchecked-inference risk is highest right after good empirical work, because fresh measurement lends authority to the next assertion. Never downgrade a contrary flag to a follow-up.
  - "Recent data looks clean" is absence of the trigger, not of the bug. A belief confirmed at the surface is checked one level too shallow: go one level down (existence to values, claim to mechanism).
  - An absence claim generalised from a neighbouring subsystem's failure is inference; name the command that established it.
  - Label inferences so they can be killed; an unverified inference does not enter a memory store. Retire a refuted line by rewriting it, keep the counter-example's scope verbatim, and file negative results too.
- **C1a.54** Change one thing at a time. A bundled fix or retry that succeeds cannot attribute its success, and it installs the redundant cause as a false constraint.
  - For each candidate cause ask: would the failure persist if only the other were fixed? Record an unseparated bundle as "attribution unresolved", never as two causes.
  - Apply the same isolation to your own tooling: a retry differing in two dimensions yields a workaround. Report "observed N times, cause undetermined".
  - Before banking a fact about a tool, search your own session for counter-examples (earlier successful calls). Tag confidence inline; a trusted author's unmarked claim travels faster.
  - On refutation, state what was eliminated versus what was explained, retract in the channel it spread through, and leave a correction marker on the superseded record.

## Examples
- C1a.51: "probably the level-1 nodes plus the root" explained a 14-row gap; there were 11 nodes and 1 root, so 2 rows stayed unexplained even if it was right.
- C1a.51: a numeric id column skipped a string-only scrubber, so 7,522 ids per sheet never reached any rule while every detector was correct.
- C1a.52: a monthly API quota ran out around day 11 and every value read 0 until the 1st; data returning on the 2nd would have "validated" any fix.
- C1a.52: 0 of 133 script pauses survived to the next morning; a scheduled Friday pause and Monday resume was paging the user every weekend for 74 days.
- C1a.53: a lead measured two response shapes and declared both covered; building them into the fake and running the shipped code gave `alerts=[]` with `apiCalls=241`.
- C1a.54: a shorter and retyped retry "proved" a metadata size ceiling; five earlier writes in the same session, two larger, refuted it.
