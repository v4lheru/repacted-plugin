# VD-A1: claimed controls, mechanism claims and source-text scans

Context: read this before citing a guard, control or mechanism as working, and before writing
or trusting a scan over source text. The general rule (a declared thing is not a used thing)
is in [vd-a1-existence.md](vd-a1-existence.md).

## Canonical rules

### Claimed controls and mechanisms
- **A1.46** "The guard WORKS" and "the guard RUNS" are two claims. Proving a guard happens on a branch where it protects nobody, so check that it is on the branch that runs and in the shipped pipeline's job list.
  - A memory of a control is weaker evidence than the control. Mark decisions SPECIFIED, SHIPPED, SUPERSEDED, UNVERIFIED or CONTESTED, because a blank reads as SHIPPED.
  - Spend scepticism on the claim that flatters you. A false claim usually lives in more carriers than the one you were pointed at.
  - A decision containing BEFORE, UNTIL or FIRST is an ordering. It needs a watcher, or the passage of time silently satisfies it.
  - Before an acceptance criterion depends on a measurement, verify the instrument is live (a cost column bound to a literal 0.0 can never move).
- **A1.47** Consistent data is not a constraint. When a design depends on the database REFUSING something, query the schema (`pg_indexes`, information schema), never the rows.
- **A1.48** A mechanism claim ("the node does X", "the API requires Y") is a hypothesis with a named owner until someone reads the source. Record who verified it, because provenance has a length.
  - Reading the thing NEXT TO the artifact (a README diff, a repaired log, an empty table, a line count) and reporting it as the artifact is the family. Track a live document by PATH, never by line count.
  - A describer error is invisible to its author, because re-reading shows the describer again. The remedy is a second reader, so budget one for solo lanes on documentation-heavy work.
  - A citation to a method is not evidence the method ran. Ask what artefact the check would have LEFT. Two emit sites or two relay routes are one source.

### Source-text scans
- **A1.49** A text scan over source matches comments, docstrings, annotations, imports and declarations. Strip comments through ONE helper, then assert a POSITION (call site, set member), not presence.
  - Grep `name(` with the paren, not `name`, and pair any count identity with a floor (`0 === 0` passes). Bind a file-level probe to the column it is about.
  - Prefer set EQUALITY plus call-site counts to absence. A fourth call site using an allowed value trips no token check.
  - Stripping comments fuses a declaration with a use. Assert that the fence fires on a use and stays silent on a declaration, and pin known cases where the property cannot be expressed statically.
  - "N expected reds" in a dispatch is a hypothesis. Read what the fence actually matched before whitelisting.
  - A remediation comment inflates a banned-token count. Split live code from comments, report both, and treat a count that does not fall as fixes land as a broken instrument.
  - A substring check cannot tell an asserted phrase from a quoted one. A removal check should return LOCATIONS for reading, not a verdict.
  - Knowing this failure mode does not prevent it. The protection is structural or it is nothing.
- **A1.50** A guard that fails correct code gets ignored. Fix the scanner (a `\s` that crosses a newline, or its own literals tripping it), and add a positive control proving it still fires.
- **A1.51** When your own scanner falsely flags your code, ask why. A name that means two things fools readers too, so fix the code. Fix the check only for a construct it does not model.
  - Parse the expected value OUT of the source, never from a list you typed. Mutate a COPY, print the diff, and confirm only the mutated site flips.
- **A1.52** A repo-scanning test is exercised by the environment. Scan the filesystem, not `git ls-files`, never turn a missing tool into a pass, assert non-vacuity, and re-run after committing.
  - A tracked-file scan gains a member at commit time: the test file itself. An alternation like `a|b` can match a neighbouring clause. Only mutation finds either.

## Examples
- A1.46: a version guard proven red three ways was cited as having protected a release. It was never on master.
- A1.47: one primary per client in prod was consistent with both "a unique index exists" and "nobody ever tried".
- A1.48: an inline comment claimed a check "confirmed by following" an endpoint. Live, 189 of 470 lessons carried text there.
- A1.49: a verb fence matched `method: 'POST'` in a TYPE annotation while the real verb was a variable. The fence was inverted.
- A1.50: `KEY\s*=\s*\S+` read an empty assignment plus the next line as a leaked secret.
- A1.51: an alias `const record = target` fooled a free-variable analyser for the same reason it would fool a reviewer.
- A1.52: one tree-wide guard broke three ways, all where it gated: a self-match after commit, `PASS (0) FAIL (0)`, and `git` absent in CI.
