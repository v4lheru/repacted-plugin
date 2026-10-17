# VD-D1: diagnosing a number

Context: read this when a figure looks wrong, when you are handed a number together with the
mechanism that supposedly explains it, or when two measurements of "the same thing" disagree.
The recurring failure is editing the code path a story blames before proving that path produces
the number on screen. Entry point and the other D1 splits:
[vd-d1-denominators-and-frames.md](vd-d1-denominators-and-frames.md).

## Canonical rules

### Reproduce before you believe the story
- **D1.60** A reported number can be right while its stated mechanism is wrong. Reproduce the displayed figure digit-exact with the code's own measurement before editing the blamed path.
  - Emit every candidate aggregation side by side with the displayed value, then attribute the residual exactly. A near-miss reconciliation is the most dangerous outcome: valid arithmetic, wrong grain.
  - An unexplained gap between your computed number and the rendered one is the whole signal, never a footnote. Ask early whether the figure is server-computed or a client reduce.
  - Run the code's own measurement function on the same input. If it disagrees with the handed figure, say plainly that the diagnosis names the wrong path.
  - Apply a three-way check (SQL, API JSON, DOM) to every finding or to none; unequal evidence behind equal-looking findings misleads.
  - Edit only the path that reproduces the displayed number. Fixing every enumerated site as a hedge is justified only when the other sites are themselves shown to be wrong; editing correct paths corrupts working code.
- **D1.61** An aggregate and its causal story are separate claims. Read about 20 of the underlying rows before acting on the story, especially when the aggregate confirms what you suspected.
  - Reproducing a document's figure to the digit proves you found its QUERY, not that the query was right. Re-run it under the filter of the code path it is meant to justify. Re-scoping can strengthen the author's conclusion; do not frame it as adversarial.
  - A tiny failure set supports almost any story, because every attribute has a fair chance of splitting 3 to 1. Compute the same attribute's rate across the SUCCESSES; a feature common among successes is not the cause.
  - Write the refuted story down, or the next person re-derives it. "Scattered, no pattern, 0.8%" is an honest finding.
- **D1.62** Before ruling on a diagnostic ratio, regroup it along the axis the mechanism acts on, and rebase any period comparison to equal elapsed time.
  - A flat count ratio understates impact when the failing items are concentrated; a partial-day figure against a full prior day overstates it. Both can hit one incident, so finding one is no reason to stop.
  - Compute the same-hour cumulative control yourself. A dispatch asserting an artefact is "already excluded" is a claim to verify. Report the corrected figure even when it weakens your own finding.

### Read the shape of the discrepancy
- **D1.63** Report a discrepancy's direction and changed-row counts per field, not only a net magnitude. A one-directional 2% is a bias that accumulates; a two-directional 2% is noise.
  - Discriminate cause by shape before theorising: ABSENT rows point at truncation or a dropped page, PRESENT-BUT-DIFFERENT rows point at upstream restatement. Prefer an independent capture already on disk.
  - Before reusing someone's curve or table, state what its axis is indexed on. A right measurement on the wrong axis is defended by its own correctness.
- **D1.64** When a metric collapses into one bucket, re-bucket the same records by other fields. If they distribute normally, the absence is one upstream field and no downstream fix exists.
  - If the records collapse on every facet, they are degenerate, a different problem. A ratio built from the collapsed field draws numerator and denominator from different populations; on a sortable surface withhold the column.
  - Settle "is the reader broken?" by history: has this code path ever produced a non-zero value? One `GROUP BY` over all history beats recalling the vendor's vocabulary.
- **D1.65** A metric moving toward a feared defect is not evidence of it; benign and fatal causes share the shape. Diff for WHICH item left and why, and test the feared mechanism's precondition.
  - Name the candidate causes and their distinguishing observation before looking. Prefer a diff that yields an identity over a delta that yields a magnitude. A zero-count precondition is often the cheapest settlement.
  - Rule out harness explanations by reading the client source, not by reasoning about defaults. Say which half of the evidence is a measured transition and which is inferred from current state.
- **D1.66** Settle a population question on raw-input versus delivered-output counts, not on documents. Documents describe intent; the input/output pair shows what the process did.
  - "The artefact never exercised the rule" is a claim about the input: check the input before discounting the evidence.
  - Reconcile a known-good delivered artefact against today's code on the same input. An exact difference pins a regression to one clause.
  - Encode a rule derived from dated evidence with its start date, not as timeless. When one principle yields opposite answers for two populations, state both together so nobody tidies one away.
  - Hand a verifier predicted VALUES, not a direction of travel; "should go down" passes almost any change.

### When measurements disagree
- **D1.67** When two measurements agree on the mechanism but disagree on the threshold, suspect UNITS: one label is naming two quantities.
  - A mitigation proposed by analogy imports a belief from a domain where it holds. Require the measurement, and state the direction you expect so a result moving the other way reads as a refutation, not noise.
- **D1.68** When told a figure "does not reproduce", reconcile the two computations before picking a side. Look for a differing window, population or filter.
  - Say "X% against <named baseline>", never "X% of normal". When agreement between two figures carries a conclusion, check that it survives a reasonable change of baseline.
  - A correction instructing you to distrust a sound figure can itself be the error. Test a plausible proposed cause before accepting it.
- **D1.69** Settle a measurement disagreement with a THIRD, independent method, never by re-running yours. Two numbers derived by different routes beat any self-written check.
  - Never hand-roll a parser for a format that has a library; a broken parser can be internally consistent and wrong, and re-running it confirms it. "I measured it" is a claim about your instrument, so state the tool.
  - A peer's specific, checkable counter-claim (a row number, an exact string) is evidence; check it before defending. Measure both directions of an asymmetry (A-without-B and B-without-A).
  - For a figure that will outlive the conversation, ask someone else to re-measure it.

## Examples
- D1.60: a filed "per-piece sum, inflated 2.28x" was not the running code; the per-partner dedup total matched the screen to the digit.
- D1.61: four dead URLs out of 507 differed on four attributes; the "old asset" story died against the 503 successes.
- D1.62: 90 of 188 disapproved ads (48%) were 100% of the serving ads in nine campaigns; a "97% collapse" compared 5 elapsed hours against a full day.
- D1.63: a restatement dismissed as "~1.2%, immaterial" was +2.87% with 201 rows up and 0 down.
- D1.64: trials collapsed to `(direct)` on one facet and spread normally on two others; the upstream payload lacked one field.
- D1.65: a shrinking capacity denominator was one paused campaign, and no disapproved entity existed anywhere.
- D1.66: a disputed account class was 1,570 and 16,550 rows in the inputs and zero in every delivered output.
- D1.67: "fails at 4 concurrent" and "passes at 4 concurrent" were a request and a query batch, about 6x apart.
- D1.68: 67.4% and 47.4% "of normal" came from the same rows over six- and three-month baselines.
- D1.69: a hand-rolled regex over `sheet.xml` swapped two counts by one row; `openpyxl` settled it.
