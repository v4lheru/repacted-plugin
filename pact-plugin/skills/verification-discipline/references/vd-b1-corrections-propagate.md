# VD-B1 (split): corrections propagate

Context: read this when you correct, retract, supersede or update a claim, a record or a
shared file. A correction reaches only where it was written, and an "update" is often a
replace. Entry file: [vd-b1-snapshots-and-corrections.md](vd-b1-snapshots-and-corrections.md).

## Canonical rules
- **B1.14** A correction reaches only where it is written: enumerate every carrier of the claim, grep for its phrasing and its consequences, and fix each copy, including your own prose.
  - The correction's scope is the CLAIM, not the instruction that named one record. Grep by phrasing, never by id; append a pointer at each stale site rather than filing a contradicting sibling, and flag that you broadened the scope.
  - A keyword is not a claim: read what each hit asserts before correcting it, since over-correction manufactures an error while looking diligent.
  - Carriers include memory files AND their index line, handoff and teachback metadata, task subjects, descriptions and rationales, comments, docs and READMEs. A retraction in conversation reaches none of them.
  - Grep for the CONSEQUENCES of the false belief, not only its wording, and confirm the grep ran with a marker you know matches. A half-wrong claim usually needs a narrower true sentence, not deletion.
  - When an assumption is settled EITHER way (disproved or confirmed), sweep your own hedges in the same change; a code path labelled "unconfirmed" invites deletion. A commit message is not a correction.
  - Keep a retracted claim visible and marked withdrawn, and separate the surviving rule from the killed justification.
  - A correction INSTRUCTION is itself a claim: verify its premise first. It may cite a conclusion that its author already withdrew in a sibling metadata key; build harvest inputs from a metadata census, not recollection.
  - When compressing a summary, check whether the clause you dropped was load-bearing; conditions on command behaviour usually are.
- **B1.15** Co-locate a correction with the verdict it revises, record a refuted risk as explicitly dead, and re-open the source record rather than answering from any derived summary.
  - Lead the appended item with a supersession marker and keep the original visible. For a still-wrong discrete list item, replace it with the provenance embedded in the replacement.
  - A hazard propagates on plausibility alone: record it DEAD with the evidence and "if you meet this hazard, cite this". Put tombstones in a layer that is durable AND amendable (a task store is collected, a journal is append-only); a correction that exists only in message traffic is unfinished.
  - A derived note never receives the source's later corrections. Status claims ("never observed", "still open") expire; mechanism claims do not. Hand the source id along with any status claim someone will act on, and correct the person about to act before the record.
  - Attach a qualifier to a ruling when a reader of that row alone would misread it; an objection (changing what the reader DOES, not what they know) goes back to the ruler as a question. Compression drops conditions first, and index lines compress hardest.
- **B1.16** Treat "update" as usually meaning REPLACE: fetch the current state, compose from it, write, then read back and enumerate what survived.
  - A pact-memory `--replace` replaces every list field present in the payload (omitted fields are safe, partial lists destroy the rest). `TaskUpdate` merges only at the top level, so a nested key replaces its whole sub-object. `glab mr update --description` replaces the whole body. A whole-file rewrite of a shared index drops a concurrent writer's entries.
  - An additive correction does not supersede: both are served. Retire a claim with a complete replacement or a labelled `SUPERSEDED` item carrying its evidence basis. A rendered pin can crop later corrections, so diagnose the render before rewriting the record.
  - Safe replace: snapshot the record, build the payload from the snapshot, write, diff counts and sets against the snapshot. A stale `Edit` anchor means the file moved: re-read and re-anchor, never force a rewrite.
  - In an append-only ledger with two writers using different conventions, position stops encoding ownership; write self-labelling entries and grep a section body for foreign ids before any prune-by-header. Pact-memory mechanics: repacted:pact-field-notes.
- **B1.17** Never quote a retracted claim inside a string that ships into data (a provenance record, audit detail, log, frontmatter): data states only what IS, and history goes in comments or diffs.
  - A grep or a skim finds the quoted phrase without its negation. Prefer deleting a stale claim to updating its number, point at the measured field instead, and bind that pointer with a test that PARSES the string.
- **B1.18** After a mid-task correction, rewrite the handoff's decisions and reasoning in full, delete resolved uncertainty entries, and check every decision line against the code.
  - Piecemeal edits leave stale phrases, and a lead is entitled to trust the narrative at face value.
- **B1.19** When closing a ticket, read every citation of its number against the mechanism you actually fixed; some describe a neighbour defect that is still live.
  - Re-point those citations to a new task rather than deleting them, and verify a self-cancelling pin's flip by measuring after the fix (C2a.6).
- **B1.20** Accept a correction only at the narrowest scope it falsifies; correct the claim without discrediting its source, and require evidence before deleting a warning or closing a gap.
  - If both statements can be true of different objects (the name vs a proxy for it), it is a refinement: record both halves. The tell is a corrector supplying a new fact rather than contradicting yours. Never write "I was wrong" as politeness.
  - A supersession chain can return to its starting value: keep the whole chain visible and rank by distance to the source, not recency.
  - Deleting a hazard warning needs a higher bar than adding one. Re-derive the scope yourself, since a correction closing item A may not close item B. Say whether it retired the trigger or the mechanism.
  - A correction that STRENGTHENS or closes a gap gets the least scrutiny: check its basis, not its conclusion. A pre-registered intent constrains what a writer can claim; it does not witness what they did. Re-open the primary source before banking any correction.
- **B1.21** Before a scripted find-and-replace, inventory the token's LEGITIMATE occurrences; they often outnumber the stale ones and cluster in the best-documented files.
  - Blast radius grows with documentation quality; that raises the value of the inventory, not the cost of documenting. Two files is a hand edit. Warn about legitimate hits when you recommend the fix.

## Examples
- B1.14: a comment four lines below a corrected paragraph still said a bad release would "fail BEFORE an image is built". A dispatch asked for a correction based on a finding its author had withdrawn three minutes earlier.
- B1.15: a ledger note kept a "residual hazard, never observed" three weeks after the source entry was rewritten to RESOLVED, and it reached a task description via the lead.
- B1.16: a `--replace` meant for one field erased 12 lessons carried by a partial list in the same payload.
- B1.17: an `ingest_run.detail` sentence explaining an inversion quoted "MAY THEREFORE BE PRESENT", and the new absence test caught it.
- B1.18: stale "warning + collapse" text in a handoff made the lead request a fail-loud change twice.
- B1.19: three citations of one ticket number meant two defects; the sweep would have turned a live defect's only detector red.
- B1.20: a "Google Sheet" was corrected to CSV and then confirmed as a Sheet by the user; the relay had been right.
- B1.21: stale `JUNE 2026` docstrings sat beside August files that cite other months on purpose (collision-avoidance filenames).
