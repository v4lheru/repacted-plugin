# VD-E: figures and severity

Context: read this before you send a figure, repair an artefact you are citing, escalate a
finding, set a score or severity someone downstream will act on, or accept a limit instead of
closing it. What a number counts is in [vd-d1-denominators-and-frames.md](vd-d1-denominators-and-frames.md);
this file covers how a figure and its weight travel. Entry point:
[vd-e-relay-and-reporting.md](vd-e-relay-and-reporting.md).

## Canonical rules

### A figure carries its derivation
- **E.33** A figure travels with its derivation, basis (denominator, predicate) and date, in the same clause. How it was obtained predicts whether it survives checking.
  - A code trace proves a hazard is reachable, not that it has fired or is firing; pair it with an outcome query for the hazard's shape in history. Entity count times time slots is an upper bound, never an estimate.
  - Did you run the query that ranks by THAT dimension? A superlative read off a list sorted by something else is an inference. A share figure inherited from someone carries their denominator; restating it in other units is a new claim.
  - Outbound (stakeholders, and your messages to the lead): prefer a drift-tolerant qualitative claim over a precise total that is the only falsifiable element; date what remains ("as of 25 August"). A stability argument is not a re-measurement.
  - Count the collection in the message itself (`len(...)`), never quote a total from a summary field. Never subtract figures from different revisions of an evolving artefact; a residual you cannot explain is first a version question.
  - "Did I check?" and "can anyone else check?" are different disciplines: attach the predicate to the number ("34 events after 15:08 inclusive"). Be most suspicious of your own figures inside a correction.
  - Basis has two axes that fail differently: population (a denominator audit) and time (a re-measure). The discipline outlives the number: carry a stale figure framed as "a specimen not to quote", dated, rather than deleting it. Surfacing is not validating.
  - Prefer the narrower true claim over a broader checkable-false one. Grep for every copy of a claim when correcting it, and keep the wrong version behind a dated CORRECTED marker.
- **E.34** Before a repair write, preserve the prior state; a repair overwrites the only evidence of what was wrong. Snapshot an artefact you cite before fixing it; regenerate to a new path.
  - Include the snapshot or a byte-level rendering in the report; a description of what was there is not what was there. This matters most for defects invisible on review surfaces (NUL bytes, whitespace, homoglyphs, encodings). A peer's non-reproduction raises the value of the destroyed evidence.
  - When the prescribed remedy is itself a write (re-bank, re-save, re-run, restore, force-push the good copy) and the warning is about a bad write, verify the damage exists first; applied to healthy state, the remedy is the corruption. mtime ordering settles most write-collision claims in one command.
  - A clean read after someone else's repair proves only the present. Before saying "it was never broken", compare mtimes and look for a backup; a repair leaves fingerprints (formatting, key order, size). A first-party account of a change outranks your later read.
  - An absence read from an artefact whose owner has said they are about to write to it is a race, not a finding: re-read, and report absences with their read time.
- **E.35** Before designing, audit a brief's central claim against a ledger of every figure's basis. Render evidence class as a table column, with the weakest class left empty by stated policy.
  - If the headline adjective ("checkable") does not survive the table, the adjective is the bug. A class shown as a badge on a sentence reads as a caveat; in a column it is data. State each class's honest scope.
  - When two audiences read one number oppositely, change its UNIT (project count to running integrations) rather than hiding it behind a click; the impression forms from the surface.
  - A stylistic "improvement" can turn a claim into an over-claim ("a re-measurement caught it" to "I caught it"); freeze such strings with a protective source comment. A file can contradict itself; read its correction blocks before escalating a cross-file conflict.
- **E.36** A cost figure has a scope. Before citing an incident's cost to justify a branch change, verify the incident actually executed that branch; recall is not evidence.
  - The vivid number is the dangerous one. If the incident did not take the branch, measure it or argue on mechanism alone, which is weaker and honestly so.

### Severity, scores and accepted limits
- **E.37** A score passed downstream is an instruction about how much doubt to carry. Under-scoring silently licenses skipped verification, so when unsure take the higher value.
  - Skipped diligence leaves no artefact and reads like a passed check; wasted diligence is visible. Bank the rationale with the digit, never shave a score to fit a route (record a deviation note), and re-derive a challenged score rather than negotiating it.
- **E.38** Severity is not entailed by a diagnosis. Check what the defect can reach (the WHERE clause, key scope, partition, retention) before escalating, and escalate the question, not the verdict.
  - An UNVERIFIED label protects the memory entry and does nothing for the message; urgency turns caveats into boilerplate. If severity is unmeasured, say so and name the one query that would settle it.
  - Never attach an operational recommendation (pause a job) to an unmeasured severity. Escalating is still right when the check is expensive and the downside real; do not decorate it.
- **E.39** After filing a finding, hunt the evidence that would weaken your framing and pre-commit to the correction. Narrowing an argument is not downgrading severity; "safe on today's data" is not safe.
  - Separate deployment invariants from code invariants when judging likelihood. "The test is mechanism, not adjectives": do not upgrade on atmosphere either.
  - "I can't determine X with this tool" is a claim about the tool; spend one round enumerating other observables before reporting an unknown. A capped review states in its body that the cap is not an all-clear.
  - A real defect filed inside an open-question task dies when that task is closed; dedupe by defect, never by topic adjacency.
- **E.40** Before escalating a scanner HALT, read which key it named; a false HALT spends the alarm's credibility. Before trusting a clean scan, plant a known fake secret and watch it fire.
  - This is not permission to suppress: identify, escalate a real finding immediately, and fix only a demonstrated artefact. An obsolete leniency in a pattern becomes a false-positive generator. The alarm should name the key, never print the value. (Scanner patterns: repacted:security-review-patterns.)
- **E.41** When accepting a gap, say which part is a check and which is an argument ("that is an argument, not a check"). Reserve the label for places a reader might act; a ubiquitous hedge stops being read.
  - Ask of every "unlikely", "probably fine" or "low risk": measured or reasoned? An accepted limit needs a time bound as well as an honesty label.
- **E.42** For every hazard you warn about, state the input that would trigger it and confirm such an input can exist. A warning whose triggering case cannot be constructed is noise, however prudent it sounds.
  - An over-cautious recommendation is never falsified, because the hazard never arrives, so bad caution accumulates. Apply this hardest to your own warnings. A guard against an impossible condition is permanently green and untestable.
  - A label that EXPLAINS the data is not a label the data SUPPORTS. A false "stale" mark is not the safe error: nothing ever contradicts it, and it destroys a measurement's standing without measuring. Read the run's self-reported config; "provenance unknown, check the run" is the honest default. Check the claimed risk's direction.

## Examples
- E.33: a sound "19,489 views" was pulled from a stakeholder note for carrying no basis or date; "seven residuals" read from summary prose were eleven in the list.
- E.34: a task description about a NUL-byte bug itself contained the NUL, and fixing it first left the lead able to verify only the symptom.
- E.35: an artefact billed as "checkable" had private, unverifiable measurements behind ~90% of its figures.
- E.36: a daily-spend figure was cited for a branch the incident never entered; its control probe had been healthy.
- E.37: "a 1 licenses a downstream lane to skip its own verification."
- E.38: a DELETE-then-failed-INSERT was escalated as ongoing data destruction; the DELETE was scoped to the date being rewritten, and 493 pieces held unbroken history.
- E.39: live data (61 of 61) falsified a supporting "no attacker path" claim; the claim was retracted and the severity kept.
- E.40: a secrets scanner "found" four keys that were documentation prose matched by a space-tolerant pattern.
- E.41: seven unverifiable tasks were reported as "unlikely to have grown, but that is an argument, not a check", and the lead accepted the gap.
- E.42: a warning that `>= 0` would admit negatives was false by definition (0 of 58 negatives satisfy it).
