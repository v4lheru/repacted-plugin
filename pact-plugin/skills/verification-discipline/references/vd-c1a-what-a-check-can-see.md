# VD-C1a: what a check can see

Context: read this when a check passes and you are about to rely on it: a relational
invariant, a metric with a default, a gate that has passed every time, a checker sitting in the
repo, or a verification step in a runbook. The question here is which WRONG states the check
accepts. The entry file for this family is
[vd-c1a-prove-it-can-fail.md](vd-c1a-prove-it-can-fail.md).

## Canonical rules

- **C1a.6** A check whose expected side was typed from its subject verifies transcription at one moment, not the property. Ask where EXPECTED came from, and derive it from the source.
  - A checker that RESTATES its authority (a hardcoded suffix instead of reading the runner's config) agrees with it by construction and mutates red perfectly well, about the wrong thing. Ask: does it derive, or restate?
  - A clause whose failure is unrepresentable in the type it reads is reported UNKILLABLE, which presents as "strong". Treat unkillable as a question, not a pass.
  - An artefact in the POSITION of a control (a declared CI stage, a named test, a checker in the repo) persuades by position. Ask "was anything checked at all?" before "did it test the right property?".
  - An unrun checker promoted from a scratchpad into the repo is a claim that a property is checked. Either build it into the suite with its own positive control, or leave it out and record the method.
- **C1a.7** Enumerate the wrong states that satisfy a check; an argument for why a checker is TRUE never answers what it CATCHES. Hunt uniform transformations, which pass relational checks.
  - A cross join multiplies both sides of `sum(bucket_counts) == volume` by the same factor, so the identity holds at any multiple. Only an external pin (a known population total) catches it.
  - Shapes whose two outcomes look alike: `A != B` (satisfied by garbage), `A == B` (by two empties), "the body contains X" (by a branch that can never run). Assertion design: [vd-c1b](vd-c1b-assertions-and-fixtures.md).
  - A fixture where two candidate rules agree (alphabetical and numeric order coincide) proves neither.
- **C1a.8** Read every metric's zero/empty branch before trusting its value (`if denom else 1.0`, `all([]) is True`, `.get(k, PASS)`). A perfect score is a prompt to check the denominator.
  - Finding a fail-open default puts a question mark over the whole historical series of that metric, not one run. An imperfect value authenticates itself; a perfect one may be the default.
  - Whenever you add a catch-all, default, coalesce, retry or fallback, ask which check just stopped being able to fail. The robustness gain and the lost detectability are the same edit.
  - Freezing, stubbing or snapshotting a source of variance to make a check deterministic makes a class of real change unobservable. Name that class when you freeze.
  - Never total blind spots and tautologies. A blind spot's past greens still carry information about the states it could see; a tautology's never did.
  - Quote a pass near a threshold as the integer pair (41/43 vs 40/43), not a decimal margin: the sample size, not the threshold, decides how close it is.
  - Eliminate categories first with a cheap deterministic query, then confirm what remains; elimination narrows, it does not identify.
- **C1a.9** A wrong state that looks normal beats one that looks broken, so run cheap checks unconditionally, on every instance, including the ones you are sure about.
  - A high pass rate concentrates a check's value in the rare failure, and the confidence from the passes is what gets the check skipped on that day.
  - An alert path that has never fired successfully is indistinguishable from a healthy system. Check its recent executions succeeded, not that it is assigned.
  - A per-node "continue on error" setting turns a checkpoint into a pass-through and every downstream success inherits it. Read the node's own output and confirm the effect at the destination.
  - Before reading a count off an instrument, ask whether its enclosing path has executed even once. Cadence times duration is not opportunities to fire; say "the instrument has had no opportunity".
  - When a prediction lands exactly, name the stages it covered; an exact match on the half you tested reads the same as one on the whole.
  - Absences that look like facts, all plausible and none erroring: a privilege-scoped view (`information_schema` lists only granted relations; use `pg_class`), a top-N cap read as a census (round totals), an identifier present without the content you need, a series that stops rather than errors, a figure whose real subject is the field list queried, a ratio that varies by channel (it measures something else), and a dangling id (resolve it; do not control it).
  - The author of a caution is not exempt from it: re-run your own cautions on your own work, especially in the section right after you wrote one. A plausible match is checked less than an implausible one.
- **C1a.10** A false claim inside a verification step (a checkbox, an acceptance criterion) manufactures assurance and spends the attention that would have found the gap. Audit those constructs first.
  - Judge each hit by its grammatical mood: a present-tense assertion ("the SA holds X") is a claim carrier, a prescription ("run this") is not. Document genre is a bad proxy.
  - Mark corrections inline in the row that gets quoted; stamp a never-executed step NOT APPLIED rather than deleting it.
  - Two documents sharing an ancestor are not independent corroboration. Grep the whole repo for a claim before correcting one instance, and read the hits.
  - A drift register without a status column reads like a changelog; identifying a false claim is not correcting it.
  - A comment decays in proportion to how much of it describes things its author does not own: anchor it on the invariant this code owns, and cite external verifications rather than restating them.
  - A property can be assumed, re-proven or cited. Check which one you are in before spending effort; three corrections to one block mean the block is the wrong shape, so fix the generator and close the loop.
- **C1a.11** An endorsement raises a claim's stakes without raising its evidence: the endorser owes the check the endorsement would otherwise deter. When a control guards the wrong thing, narrow the control.
  - A control narrower than the thing it appears to protect is a finding whose deliverable is the corrected control (for example, tag a commit so branch deletion cannot orphan it), not a note about the gap.
  - Publish a simplification watch list before implementation (where the simple-looking code is the wrong code), with the condition "tell me if an item is wrong"; without it, it is self-justification.
  - Bank the durable form of a lesson (with its exception), not the quotable blunt form that gets discarded at the first legitimate exception.

## Examples
- C1a.6: a dependency-array analyser compared against deps the author had typed, so it could not detect a changed array; parsing the array from source fixed it.
- C1a.6: a collection check hardcoded `.test.ts`; narrowing the runner's include glob dropped collected tests from 1346 to 464 and the check stayed green.
- C1a.7: under a persona cross join the funnel identity read 15/15 (true) as it did 5/5; a population-total pin caught the 4.83x inflation.
- C1a.8: a traceability metric defaulted to 1.0 on an empty denominator while three sibling metrics defaulted to 0.0; the recorded baseline of 1.00 was indistinguishable from the default.
- C1a.9: an error workflow was correctly wired and threw a parse error on all 4 fires since setup; a week of lost briefs read as silence.
- C1a.9: "~288 cycles of warnings to read" were 0 because the enclosing resolve path had never executed.
- C1a.10: a pre-flight checkbox said a service account held dataset-scoped roles; it held a project-level editor role.
