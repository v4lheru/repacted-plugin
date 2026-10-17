# VD-A1: existence is not use

Context: read this before reasoning from a symbol, config field, column, predicate, signal or
grep hit. A thing existing attests to someone's intent. It says nothing about whether anything
reads it, runs it, or can reach it. Claimed controls and source-text scans are in
[vd-a1-control-claims-and-scans.md](vd-a1-control-claims-and-scans.md).

## Canonical rules

- **A1.32** A declared symbol, field, column or signal is not evidence that anything reads, writes or branches on it. Before reasoning from it, grep for the operation the claim needs.
  - "Cap X is enforced" needs a READ of X in a comparison. "Column Y records Z" needs a WRITE on that path.
  - "Marker M identifies path P" needs the format to DIFFER between paths. "Flag F disables Y" needs a BRANCH.
  - A symbol that confirms your hypothesis gets less scrutiny than one that contradicts it. That is backwards.
  - A test that passes a value production never reads is worse than no test, because it manufactures coverage.
  - A NULL column needs a writer before its silence means anything. A populated field can over-report, so check what it is populated FROM.
- **A1.33** A correct citation of the PRODUCING side is not evidence of the consuming side. Find the code that READS the value before accepting how bad something is, and say which half you verified.
  - "Used by: X" in a docstring, where X is a file another agent owns, is the highest-risk shape. Delete decoys, because a symbol that reads as protection keeps a gap alive.
  - Read the enclosing expression of every grep hit before citing it, and audit all hits rather than the first two.
  - Before adding an honesty signal, grep its consumers: the field often already exists, unread. Tests that only assert emission make the gap look covered.
  - Route-internal logic is unreachable from tests, so extract it into a pure module to make mutation detectable. Test a JSON-boundary field with a round trip through both real implementations.
  - Inherited severity framings shrink when the consumer is read. Storage used as transport and storage used as history look identical from the read site.
- **A1.34** Adopting a contract is a symbol claim too. Ask what else already carries that fact, then mutate the contract back and count the tests that fail. Zero means you shipped documentation.
- **A1.35** A type annotation constrains element TYPE, not set MEMBERSHIP. Guard invalid AND missing members, then falsify by deleting one. A type-only union cannot be enumerated by any test.
- **A1.36** A test that MENTIONS a field is not a test that DISCRIMINATES on it. A fixture holding the default values passes against a rebuild-to-defaults, so use a non-default fixture and say why beside it.
- **A1.37** Counting copies is not enough. Grep call sites and importers to separate the live path from a dead export, and flag a copy with zero consumers as YELLOW.
  - When concurrent lanes each claim a lead-ratified copy, the live one is the copy the producer imports. Magic numbers mark the dead copy.
- **A1.38** Every declared identifier needs a producer able to emit it. Compare the declared set with what the build can actually produce, in both directions.
  - Grep every member of an enumerated code union in the artifact, the tests and the graph. A zero may mean the emitter runs BEFORE the condition: unreachable by construction.
  - Derive the producible id set from the producer's source with NARROW regexes (`.+?` passes vacuously). Read every field family that can carry an id. Prove the check on the broken producer.
  - YELLOW, not RED, when a second detector still catches the harmful outcome.
- **A1.39** A predicate or a stale value you have read proves nothing until you check the short-circuits and live config above it and follow the value to a decision it can change.
  - Read the whole function from the top, and the LIVE config value (not the default, not the example env). A behavioural record, such as a runs table, beats both.
  - Report "live defect" and "latent, armed trap" as separate verdicts. Ask whether a writer was ever wired, not why it stopped.
- **A1.40** A defect shown on a hand-built input is half a claim. Before calling it live, check that a producer can actually emit that input.
  - Look for the out-of-band enforcer (a SQL-derived column, a CHECK, a trigger) and ask whether the planned work modifies it. That is often the real finding.
  - A warning read off the consumer establishes code behaviour, not that real data is exposed. Reshape your own fixtures to real producer output before handing a database to someone else, and say so.
  - Controls are for claims. Run them on STATES as well as on tools.
- **A1.41** Before fixing a named component, grep its imports and the built output to confirm it still ships. A brief is a snapshot, and repaired dead code invites re-adding what was removed.
- **A1.42** A coupling grep measures insulation, not whether an input's MEANING changes under a swap. Check gates and empty states whose meaning narrows, and grep files for their own stated invariants.
  - Booleans and empty collections are the dangerous inputs, because their types never change. Report your "unchanged" list as the least reliable part.
- **A1.43** A field that is emitted but never rendered makes readers document a UI that does not exist. Grep for the RENDER (`git log -S` on the access path), and prefer deleting the field.
- **A1.44** When expected copy is absent, find where the value dies: confirm the guard was satisfied, read the wire, grep production callers of its producer. A docstring about value flow is a claim.
- **A1.45** "X was removed" is a claim about one path. Check the read path, the producers and the data separately. A shared store hides a dead tenant, because every liveness check passes on the live one.
  - On a decommission, enumerate the PRODUCERS of what you removed. Rename an orphaned output so it is self-evidently dead, and refuse to record "disposed of" on a report alone.

## Examples
- A1.32: a column read as "clustering never ran" was NULL across 686,179 rows and written by nothing but its DDL.
- A1.33: "the operator reads false facts" cited a correct producing line. The field was rendered nowhere, only in a schema, fixtures and a docblock.
- A1.34: a `None`-means-failed contract was decorative, since a counter carried the same fact. Mutation killed 0 tests before the fix and 3 after.
- A1.35: a `readonly Foo[]` constant held 10 of 11 union members while its comment promised a compile error for a missing one.
- A1.36: a drift test asserted both fields and stayed green under the sabotage, because its fixture had no website, so the values matched the defaults.
- A1.37: a tag allowlist collapsed correctly to one array had zero consumers after an attribution pivot.
- A1.38: 1 of 9 contract codes had 0 occurrences, because its collector ran upstream of the pre-check.
- A1.39: "the poller does not run at weekends" cited a line under a 24/7 short-circuit. The runs table held 48 Saturday rows.
- A1.40: an "open write gate" state could not occur, because SQL derived one field from the other.
- A1.41: a component named in a brief had zero imports and was absent from all 7 built pages. It had been removed that day.
- A1.42: a coupling audit with zero HTTP calls across 11 files still missed a gate whose boolean changed meaning under the new transport.
- A1.43: three writers documented a per-content revenue column that `git log -S` showed was never rendered.
- A1.44: a mandated caveat had 13 test callers, 0 production callers and no response field.
- A1.45: a decommissioned cache left 124 dead rows in a live shared table and a producer still parsing them.
