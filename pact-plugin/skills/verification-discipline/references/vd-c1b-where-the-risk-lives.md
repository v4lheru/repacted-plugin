# VD-C1b: where the risk lives

Context: read this when designing a check, a gate or a guard rather than a single assertion:
deciding what it observes, where it points, what it cannot see, and which oracle it compares
against. Entry file: [vd-c1b-assertions-and-fixtures.md](vd-c1b-assertions-and-fixtures.md).

## Canonical rules

### Outcome, location, blind spots
- **C1b.15** Assert the OUTCOME a consumer or human needs, not that the mechanism fired. An escalation that destroys the evidence it escalates passes every check on the escalation machinery.
  - Omitting a column or structured slot is correct degradation; dropping the VALUE is not. Preserve every candidate input before omitting its destination, and assert on what a human can act on.
  - Prefer a detector structurally incapable of the wrong firing over one tuned not to, and mutate in both directions, including deliberately over-broad.
  - Instruments must traverse production's own credential binding: a harness authenticating with its own token cannot see an unbound node. Read the binding from the live artifact.
  - A non-secret reference (an id, a project name) belongs in versioned config, not the environment; a generator should refuse rather than print a warning and exit 0.
  - When a hardening makes an assertion impossible to pass, convert it into a negative control that reproduces the hazard by the old route; never delete it.
  - While a coder iterates the mechanism, assert the load-bearing INVARIANT over the whole output (no email anywhere in the serialised payload), not intermediate shapes.
  - Change an expectation only when a RULING changed the contract, never when the implementation failed it; then re-express it as an invariant and pin the distinction the new value encodes.
  - "We know it failed" and "we cannot confirm what happened" need different codes on an at-most-once write; bucketed summaries need a sums-to-total assertion; ask producers for `{code, text}`.
- **C1b.16** Point a check where the property can GO WRONG, not where it is described. Name the file that would have to change for the check to fire; if the check does not read that file, it is pointed wrong.
  - A check over a layer whose types make the bad state unrepresentable cannot fail, and a mutation harness reports it UNKILLABLE, which reads as strong. Prefer behavioural assertions over structural probes.
  - A contingency filed under the example that prompted it cannot see the next instance; name the trigger condition. Naming a failure shape is a search tool: re-sweep right after naming it.
- **C1b.17** Every guard states, as a required field, what it does NOT catch. A completeness guard ("did you get everything you enumerated?") cannot catch enumerating the wrong set.
  - The wrong set is never empty and never errors. Name the exact child resource, record the enumeration arithmetic (found N, dropped M because X), and keep `expected` commensurable with `processed`.
  - A disappearance guard's likeliest trigger is your own scope change: diff your own definition before the source, and declare scope as an explicit reviewable string.
  - A field present in several document types cannot discriminate between them; establish the document type first, prefer a ratio assertion to an absolute count, and show the discriminator discriminating.
  - A guard written against a hypothesised structure passes vacuously against the real one; run each guard against the defect it targets. Drop a field from an identity if you must, but assert it separately.
  - A code block in a document is untested code: extract and execute it. Put shared controls in a conformance suite parameterised over every registered participant.
- **C1b.18** A correct control can rest on a false justification, which survives because the conclusion is right. State the MECHANISM, not just the conclusion; if a document says "X prevents Y", run Y.
  - Name the OWNER of shared code, not its file path; enforce with an identity assertion plus a grep for local copies.
  - Store a signal where a human can act on it; hash only what defines the content. A missing value is UNKNOWN, never `false`; count the join and assert the field still exists.
  - For every rule you write, name the thing that fails if the rule is not implemented. A rule in a document with no failing consequence is a wish; the corrective is running the path.
  - Grades encode OUTCOME, not cause; put the cause in an error class. A guard you have to argue with gets bypassed: reword the comment rather than route around it.
- **C1b.19** A proxy agrees with its fact across the ordinary range and diverges at the boundary. Name the fact a condition expresses, then test zero, one and empty against it.
  - Assert WHICH control caught a hazard, not that nothing bad happened ("which line would you delete to break it?"); a load-bearing control whose justification omits a path gets deleted as redundant.
  - Keep tests that are expected to fail against fixed code: they notice when a negative control stops being negative.
  - A right implementation can reach the right result by a route that bypasses the mechanism; construct the input only that mechanism can satisfy.
  - Before documenting a hazard, enumerate the full value space and run each value; a caution one member away from the real gap reads as coverage. Close a contradiction by the declaration's own logic.
  - When several conditions map to one status, evaluate the most serious first and test which reason wins. Assertions about your own process decay as fast as comments about others' code.

### Deviations and oracles
- **C1b.20** When an implementation deviates from a specified rule for good reasons, the finding is the COVERAGE DELTA: run the inputs the original rule would have rejected through the substitute.
  - Gate on the structure the parser already depends on: require at least N delimited fields where N is the highest index read; "contains the delimiter" is defeated by inline CSS or an entity.
  - An added gate inherits the rule ORDER: placed before a legitimate-empty sentinel, it converts real results to permanent unknowns.
  - A guard conditioned on `rows.length === 0` never fires when the parser maps every input row; `fetch` follows redirects, so a vendor 302 to an HTML page arrives as HTTP 200.
  - Build the 2x2 of a two-part fix and name what a run WRITES in each cell: a frequency fix can make a surviving corruption rarer and longer-lived. Follow a discriminator's write path to its consumer.
  - Measure the claims on a DO-NOT-TIDY comment; one constant doing two jobs is correct only by coincidence; a weak artifact sitting unlabelled beside strong ones inherits their standing.
  - Correct an overstated reachability claim even when it favours your finding; a verification has a commit scope, so re-run after later batched edits.
- **C1b.21** A gate's coverage is bounded by what its ORACLE emits. Sums and presence checks are permutation-invariant: they catch omission, not misdirection. Compare one distribution to a known input.
  - Before deleting a pipeline stage whose consumer went away, ask what tests point at it; keep an intermediate that carries the only executable oracle, and gate the new projection separately.
  - Write the blind spot beside the gate ("G1 does not cover this and never will"), audit the whole gate set once deliberately, and record an uncovered class as a GAP rather than quietly closing it.
  - Pin the population of an external count (file and row filter), and state fixture preconditions (a multiplier of 1 blinds a duplication test) as requirements on any replacement fixture.
  - Port null handling by whether the reference GATES the append; Python `dict.get(k, d)` defaults only on an ABSENT key, not a present `None`. RE2 `\s` omits the vertical tab and non-ASCII spaces Python `split()` treats as whitespace.
  - Make an unexecutable sub-question irrelevant (collapse whitespace before `TRIM`); a fix can be ruled, fixed and uncovered while the status reads done.
  - Arbitrate a divergence against the REFERENCE, never by adjusting one side toward the other, and set the default against the author in advance.
  - Whenever variance is frozen, stubbed or snapshotted for determinism, name the class of real change it made unobservable, where the check reports. Make the output distinguish "looked, nothing moved" from "cannot look".
  - Measure the magnitude before bounding an aggregate; if the concern is real in kind but unmeasured, ship instrumentation plus a falsifiable revisit trigger.

## Examples
- C1b.15: an ambiguity escalation deleted the routing value it had read, so the exception item showed one of two candidate answers; every escalation assertion passed.
- C1b.15: four deployed HTTP nodes had empty credential bindings; 496 green assertions could not see it because every instrument used its own token.
- C1b.17: filtering a sitemap index by path returned 3,840 URLs instead of 1,404 articles; tightening one lane's filter made 409 of 444 items withdrawal candidates in one run.
- C1b.19: an empty wrong corpus skipped an identity check and wrote its identity into the column meaning VERIFIED; only testing zero exposed it.
- C1b.19: a membership test covered "markdown" and "text" and omitted "none", the one value that disabled the strip.
- C1b.20: 4 of 4 hostile bodies at HTTP 200 classified as data, because the guard ran only when the parser produced zero rows.
- C1b.21: of ten gates, exactly one compared a distribution against a known input, and it was the only one that ever caught anything.
