# VD-D1: units, labels and provenance

Context: read this before you put a noun next to a number, reuse a label, record an ID, compare
versions, or restate a figure you did not produce yourself in this session. These are the errors
where the VALUE is right and re-deriving it confirms it every time: the unit, the subject, the
label or the provenance is what is wrong. Entry point and the other D1 splits:
[vd-d1-denominators-and-frames.md](vd-d1-denominators-and-frames.md).

## Canonical rules

### Unit and subject
- **D1.23** Re-deriving a number cannot catch a wrong unit label. Ask what the unit is (rows, files, folders, entities) and where units diverge: merges, dedup, shared keys, shared filenames.
  - Staleness is fixed by recomputation; a mislabelled unit is wrong when written and stays wrong through every refresh.
  - When a merge exists, suffix every count with its grain (`_rows`, `_files`, `_folders`) and report both readings side by side. Prove the merge (md5 the two sources) before collapsing on a name.
  - A gate asserting equality across a many-to-one mapping can never pass on a correct build, and an unsatisfiable gate is worse than none: pressure deletes it. Fix the predicate ("every row maps to an existing file, every file to at least one row"), not the threshold. Express "no orphans" as SET equality.
  - Sweep your own reports and specs for the same conflation; it is rarely alone.
- **D1.24** State the unit beside every count, the environment beside every run, the property beside every proxy and the subject beside every figure, in the same sentence, hardest when green.
  - One word carries two distinctions: "measured" means run-not-read AND, to a later reader, production-not-fixture. An mtime is "last written", not "alive".
  - Successes are the dangerous case: a correct, unlabelled figure survives every review. Compute both readings side by side so the label is obvious.
  - When correcting a label, sweep EVERY appearance of the number, including the ones that are not wrong. Degrade per claim, not per record.
  - Binding errors surface when you USE a finding. Correct it before anyone builds, and say whether a ruling issued on it still survives, and why.
  - Measure a peer's proposed fix before it is built; it can be a no-op. Readiness takes two signals: the author says ready AND a source-mtime hash shows nothing moved.
- **D1.25** A precise number reads as the output of a check that already happened. Ask "did it measure what it appears to measure?", not "is it still correct?".
  - A re-run repeats the original method, so a wrong population comes back confirmed; a small delta then reads as drift.
  - A threshold inherits the epistemic status of the number it is set from. A partial-run guard set from a configured constant never catches a source mis-scoped from the start; have the run measure its expectation from the source.
  - A count that is a correctly applied but unwritten filter: the deliverable is the missing RULE, not the number. A basis label creates the seam a reviewer can question.
- **D1.26** When an answer's type differs from the field it fills (range to value, noun to URL), someone closed the gap. Record who, at that moment, and carry provisional words verbatim.
  - "30-45+" becoming `45` acquires the stakeholder's authority unless the interpolation is attributed. "Start at 20% and test" is an opening position; dropping "and test" turns a hypothesis into a decision.
  - A real-looking invented value passes review; a `TODO_SET_ME` gets checked. When you check a placeholder, upgrade its label ("verified non-resolving").

### Labels and vocabularies
- **D1.27** Before reusing an existing enum value, status or reason code for a new fact, ask whether the label DESCRIBES the fact or merely ACCOMMODATES it.
  - Accommodation gives a label a second meaning, and that one gets misread (an `api_error` meaning "may have landed" told an operator to retry).
  - A distinct value beats a free-text distinction: queries can filter on it. Check the bucketing layer; a new code in the "retry these" bucket keeps the hazard.
  - An enumeration written from memory is an unverified completeness claim. For a contradicted concept, grep the CONCEPT, never a list of locations. A type-only union cannot be iterated by a test; give an exhaustive set a runtime representation.
  - Bare integers are a vocabulary too (task ids, checklist items, finding shorthands). Qualify every number with its namespace across agents, and prefer real names.
- **D1.28** A derived "was measured" flag's name generalises beyond its definition. Diff its field set against what the gated block renders, and census the partial-null bucket first.
  - Census four buckets: no rows, all-null, PARTIAL, fully measured. If partial is non-empty, fix per-field fallbacks instead of gating the block.
  - A sibling that "works" may do so by data coincidence. If the source stores a literal 0 for absent data, it is a data defect, not a render defect.
- **D1.29** When two paths write the same column, check they mean the same thing (success vs attempt). Grep every writer before trusting the column as evidence; gate on a success-only signal.
  - The fraction of rows carrying each meaning moves with configuration, so a metric changes meaning with no code change. Prefer a column only the success path can produce (a joined child row, a non-null payload); document at the column which writer means what.
- **D1.30** Two textually identical queries that measure different facts are indistinguishable in logs, traces and test doubles. Alias them distinctly so they self-describe in production.
  - The wrong fix is mock-side (call ordering): it repairs the test and leaves production blind. A double that cannot tell two statements apart is a finding about production observability.

### Where the figure came from
- **D1.31** Record an opaque ID with where it was read, the call that proves its scope, and which evidence you hold: a schema source (the field EXISTS) or a populated record (the mapping MEANS it).
  - IDs are scoped (Jira custom fields: instance-global, available per project screen; n8n ids: per instance; a deployment id: one version).
  - An ID empty on sampled records is a fact about the data, not the mapping: write "schema-only, unpopulated in sample", not "unverified".
- **D1.32** Never `max()`, `ORDER BY` or `>` a version string in a TEXT column. Enumerate values with counts and recency. A check meant to corroborate an identity must NAME the value, never count values.
  - Lexically version 9 beats version 12 ('9' > '1'), and the error grows as the project ages. `COUNT(DISTINCT) = 1` is satisfied identically by the right and the wrong version.
  - For a compound claim, source each half separately; checking the half you can read makes the whole feel verified. An independent attribute (the running image tag) is what breaks such a claim.
- **D1.33** Before stating a figure about a system's behaviour, ask whether you READ it or RECONSTRUCTED it from your model. Cost the artefact, never the design, and name the quantity.
  - Provenance predicts failure: query-derived figures survive, arithmetic on your own table and recall of an old thread break, and a figure from a peer's parenthetical is not accepted until the code is read. State the derivation next to the number.
  - A tool can return two counts for two questions (`rev-list --left-right`). Say "origin/master has 8 commits the branch lacks", not "8 stale".
  - When a conclusion survives a corrected input, retract the input out loud; a wrong number that changes no decision gets laundered into the record.
- **D1.34** A stakeholder saying "yes, that one" to your filled-in example confirms the FORMAT, at most the semantics, not the values you supplied. Record who originated each fact.
  - Keep `format-confirmed`, `semantics-confirmed` and `value-confirmed` distinct. To validate a value, ask them to produce it or diff against a figure they made without seeing yours.
  - If the output was never produced by hand before, there is no golden reference: validate field by field against the source system, and decide this at design time.
  - Marking a fact unverified is cheap at any depth. Correcting its provenance asserts knowledge of the previous hop and needs evidence from that hop; a wrong correction beats an unmarked fact for damage. Where a forecast and a baseline coincide, say so.
- **D1.35** A derived coordinate map (column letters, 0/1-based indices, offsets) rests on an assumed origin. Test every candidate origin against anchors already in the artefact before quoting it.
  - Run it programmatically over each origin and print which survive. If two survive, the anchors are too weak; say so. "Columns B..X" may number a different artefact. Pin the resolved ambiguity in the file.
- **D1.36** A denial message is evidence about the CALL, never about whether the object exists. Verify existence and shape separately from permission.
  - Fixing access alone can yield a correctly authorised query for a column that does not exist: a green path to a wrong answer.

## Examples
- D1.23: a brief said "440 folders"; 440 was rows (one person entered twice), 439 was folders, and two gates could never pass on a correct build.
- D1.24: a DPIA-bound sentence said "155 tables"; the query counted 155 grant rows over 60 objects.
- D1.25: "595 articles" went from a schema comment into a sizing decision and a guard threshold unquestioned.
- D1.26: "straight to <tool>" became a plausible tenant URL in config that did not resolve.
- D1.27: a vendor write-back reused `skipped_idempotent`, `api_error`, `indeterminate` and `plan_drift` for four new facts.
- D1.28: a video-engagement flag omitted `views`; 6 pieces still rendered a fabricated 0 and 1 real measurement disappeared.
- D1.29: a worker stamped `enriched_at` on success in one engine and on attempt in the legacy one.
- D1.30: a pre-write and a post-write count were character-identical SQL; the mock returned the guard's number for both.
- D1.31: of 8 Jira field ids, 3 were double-sourced (schema plus populated tickets) and 5 schema-only.
- D1.32: "prod runs prompt version 9" came from `max()` on TEXT; prod ran version 12.
- D1.33: "31 quota units per run" assumed a `videos.list` call the lane never makes; it was 16.
- D1.34: an accepted example's "MRR" came from a CRM field the stakeholder never produced; their own example gave a different figure.
- D1.35: a CSV column map was one letter off everywhere because the header numbered the stakeholder's sheet, not the CSV.
- D1.36: a BigQuery "permission" denial came from a stale billing project, and the requested field did not exist at any grain.
