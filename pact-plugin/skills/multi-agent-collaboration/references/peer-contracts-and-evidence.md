# Peer contracts and evidence

Context: building against a peer's contract, consuming their output, and passing claims on. Read
this when you wire to a peer's payload or type, when a peer fixes a cross-boundary bug, when two
lanes share one contract, and when you decide what to tell a lane that is still working.

## Canonical rules
1. Build to a peer's committed code, not their prose preview: prose can understate the implementation, and the omitted case is usually the interesting one.
2. Read the construction expression that fills a payload, not just its declared type, and enumerate the off-diagonal combinations of a discriminator and its fields.
3. Report a spec-versus-code divergence as a divergence, say plainly when the code is better, and test the undocumented case with a mutation that discards it.
4. With a backend in flight, scaffold types, adapters and hooks now; gate only the UI-to-endpoint wiring on "endpoints are live", then diff keys against the real route or response.
5. Map a peer's field names in one adapter, behind one swap-point barrel; reconcile to the committed contract before shipping, never ship a divergent mirror.
6. A green build against a local mirror of a shared contract proves self-consistency only; each side must diff against the COMMITTED artifact before calling it agreement.
7. When a peer fixes a cross-service bug, check every optional or nullable field you emit against their stated requirements; the same defect usually sits one field over.
8. When a peer's constraint makes part of an operation impossible, enumerate what still works before proposing to skip the whole thing.
9. When a peer changes behaviour, grep your own comments and tests for claims about them.
10. When you own a schema others consume, ask them to enumerate what they will and will not treat as evidence; the gaps appear in the consumer's seat, not yours.
11. If a guard is accreting exclusion clauses one bug at a time, rewrite it as the single positive property that makes a row count.
12. A rename spanning two owners needs a per-FIELD tolerant reader; prove it with a half-renamed payload and a wrong alias that must fail loudly, and mark the tolerance transitional.
13. To stop rendering a sensitive field, delete it from the type, not just the template, and test with a payload that still contains it.
14. Agents sharing one git or API identity are unattributable in `merged_by` or author fields; state your own actions with timestamps, and label attribution as inference.
15. When two actors may touch one sequence, order the steps by what a double application costs, and put the human-gated step where that cost is highest.
16. Volunteer hazards and methods to running lanes at once; send conclusions and figures to a lane re-deriving them only sealed behind a reference (a path, a key) it chooses to open.
17. What arrives before a lane measures is framing; what arrives after is a check. Mark early context as yours, with dates, so it can be contradicted cheaply.
18. Classify, act and report the classification; do not re-open a settled rule by asking permission you no longer need.
19. Answer in three states, never two: WITNESSED (quote it), UNWITNESSED (say what you searched), INFERRED (labelled as inference).
20. Naming your own failure mode in advance does not prevent it: treat a stated risk, yours or a teammate's, as the place to demand evidence.
21. Doubting the input (the brief's arithmetic, a "settled" ruling's provenance) finds more than doubting your own judgement; the least-confident item is the high-yield field.
22. When two agents derive the same fact by different routes, say so: independent agreement is stronger evidence than either, and it can justify a deviation.

## Detail

**Count-match verification.** The strongest check that a frontend reproduces a backend's logic is a
count equal to the backend's own computation over the same payload (the backend marks 44 stale,
exactly 44 badges render). Keep belt-and-braces input guards even when one layer pre-empts another,
and say which is redundant in the handoff.

**A peer's diagnosis at the point of detection.** Reaching a human with a generic downstream alert is
not the same as telling them something actionable: diagnose where the failure is detected, and
suppress the redundant downstream alert for the same event.

**Leading the witness.** Supplying a number and then reading agreement with it as confirmation is
the same error one step earlier. Pointers ("glance at their section 8") falsify conclusions;
restated findings invite agreement. Cross-lane asides do real verification work, so keep them
checkable.

**Seals are references.** For an agent, reading a message is atomic; a conclusion inline behind "do
not read yet" is already read. A hazard sent after the run is archaeology; a conclusion sent after
the run is corroboration. A lane dispatched as a deliberate independent replication says so in its
task text; any lane that does not is open.

**Reporting back.** Labelled claims, falsified premises first: repacted:verification-discipline
`vd-e-relay-and-reporting.md`.

## Examples
- A peer's message said a failed read yields "unreadable, units null"; their code kept the last good
  number and its age, which was the version an operator could act on.
- A frontend mirror of a shared contract diverged on four points (artifact model, map cardinality, a
  label, a job name) and stayed green throughout, because it only agreed with itself.
- A peer rejected `sale: null`; the same service could still emit `refund: null` from a different
  cause, and neither side had looked.
- A teachback named "confirming my own published conclusion" as the risk; the agent then asserted
  that conclusion, and only the lead's refusal to accept without the test caught it.
