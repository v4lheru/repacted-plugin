# VD-A2: how searches fail, and diagnosing an absence

Context: read this before trusting a grep, regex census, search over payloads or credential
hunt that came back empty or small. A miss is evidence about your pattern, scope and parser
first. What a clean result means, and how to scope a negative, is in
[vd-a2-absence-and-search.md](vd-a2-absence-and-search.md).

Also read this when expected data, output or rows are missing, a table or metric reads zero,
or a feature "produces nothing". Missing has several causes that look identical from outside:
never produced, produced and lost, filtered by design, written somewhere else, or not yet
written. How searches fail is the section above.

## Canonical rules

### How the search itself fails
- **A2.8** A line-oriented grep misses statements and prose that wrap, and returns a confident false zero. Scan multiline, or parse whole statements.
  - Parse SQL by statement: respect dollar quotes and quoted identifiers, and strip comments BEFORE collapsing whitespace. Make an extractor fail loud on any form it does not model.
  - Hoist any claim a guard must find by substring (a caveat, disclaimer or price) into a named string constant, which a formatter cannot wrap.
  - When your banned-phrase scan fires on your own comment repeating a stale claim, the scan is calibrated. Reword the comment, and never exempt comments.
- **A2.9** A regex census of call sites errs in both directions. Match AST call nodes, plant a positive control, and remember that `grep -c` exits 1 on zero.
- **A2.10** A grep miss on a pattern you built is evidence about the pattern and its scope. Count the population UNFILTERED first, run a known-answer control, then widen vocabulary and path.
  - Enumerate the TYPES that represent a category (a null return, a union, a third enum value) before choosing tokens. A zero for one module means look harder.
  - Aliases, import paths and re-exports break literal patterns, so match the distinctive tail. Scope the path last, and widest.
  - A hit on the CONTAINER (a function's name) is not a hit on the guard. Grep the distinguishing token, or diff the body.
  - A read-back that misses reads as data loss. Assert on a marker you just wrote, or on a structural count, never on remembered prose.
  - A parser that swallows errors into `[]` manufactures absence, so let it raise. A partial wrong-key read is the dangerous, plausible case. A too-clean zero is a gift.
  - When correcting a figure, measure the replacement and anything you add beside it.
- **A2.11** Two parties disagreeing whether a field EXISTS, both reading the artifact directly, points to a shape change (key name, casing, affix order), not staleness. Make searchers accept both forms.
- **A2.12** An "X is only set by Y" claim must enumerate initialisers (initial state, constructor defaults, fixtures, seeds, backfills) as well as mutations. A guarded dev-only seed is still a writer.
- **A2.13** "No credential exists" is a claim about a search method. Parse a credential file as structure before searching it by name, grep by value SHAPE, and label any fingerprint with its algorithm.
  - Print the line, the label and the length, never the value. Never derive a fingerprint by slicing a raw secret: hash it. A value that is correct yet unreproducible means the OPERATION differs.
  - Credentials also hide in permission files (verbatim allow rules). "Gitignored" is portable only if the rule is in the repo (`git check-ignore -v`). Handling: repacted:safe-ops.
- **A2.14** "No prior art" is usually a claim about a dependency. Grep the protocol's wire vocabulary to find hand-rolled implementations, then ask where they are incomplete.
- **A2.15** Truncating your own read of a payload turns a lookup into a blind search. Read the payload whole, and grep payloads you already hold before probing. Base API responses often carry sub-resource URLs.
- **A2.16** A source file git treats as binary returns nothing from `git show | grep`. Confirm HEAD is the audited SHA, then `LC_ALL=C grep -a` the working copy. Say what the hazard blinds and what it does not.

### Diagnosing an absence
- **A2.17** When data is missing, rule out NEVER PRODUCED before hunting a loss. Check the earliest stage that could hold it, and whether any stage's output even has a slot for it.
  - Several plausible but unconfirmed loss mechanisms are evidence that the premise is wrong, not that you have yet to find the right filter.
- **A2.18** A sparse collection fits both "never filled" and "filled, then drained". Age the sample, re-measure later, and find one survivor of the class you claim is disappearing (a known-negative control).
  - For lazily created files, enumerate them and read their contents. "Empty now" is a sample in time, and writers fire on inputs you did not predict.
  - A per-item lifecycle record beats a population snapshot. When a mechanism is falsified, record it as UNKNOWN rather than promoting the next correlation.
- **A2.19** An empty destination table plus a missing grep hit measures only the write side. Before calling a capability missing, ask whether a working downstream dependent proves the value exists.
  - An empty table of sensitive data can mean descoped, never wired, or scrubbed. Separate the three with evidence other than the row count.
- **A2.20** Before calling a filter's discards "data loss", find what writes its reference table. A curated list means out of scope, and a discovered one means lost. Run the converse check on the kept rows.
  - Name what the number would mean if you "fixed" it. The residual defect is usually silent discarding: a bare `continue` before every counter, plus a completed status.
- **A2.21** Before declaring damaged data unrecoverable, ask what it is derived from, and regenerate it. Indexes, caches, summaries and generated files are derived by construction.
  - Checking the easy property ("no entries lost") does not prove the one you care about. A truncation at a character count yields confidently wrong prose, not visibly broken prose.
- **A2.22** To judge whether a write-path bug is live, measure the SOURCE the writer will read next, not the sink it already shaped. Run any proposed regex fix against the real value distribution.
  - "Latent" and "fires on the next run" lead to different actions. If a job is running, say so first, so its owner can stop it.
- **A2.23** A field at exactly 0% means a whole class of writer stopped. Prove writer exclusivity with an IS NULL negative control across all partitions, and check whether an engine swap was designed.
  - Measure the replacement engine's ceiling on its healthiest run. Segment by the enrichment timestamp, not by the parent row's creation.
  - To separate two launches in one week, hunt the off-cycle event between them. A peer's cross-tab can reproduce exactly and still name the wrong writer.
- **A2.24** "The feature produces nothing" is often a silent drop. Trace one item end to end, positive-control against its history, grep for `return null` counted as success, and reconcile reported minus written.
  - A green pipeline is the signature of a silent drop. An output cap in the tool (truncated execution data) masquerades as a small result.
  - Throughput arithmetic (seconds per item against a retry ladder) and periodicity (one `GROUP BY hour`) can identify the live branch without a destructive repro.
  - The envelope and payload confusion repeats across layers, so audit every boundary. Sibling paths often agree on writes and still differ on counting.
  - Hunt for the instance that does NOT show the reported behaviour. One counter-example usually names the mechanism.
- **A2.25** An empty event table proves no event only if its writer is live. Check min and max write timestamps first: a tight burst followed by silence is a backfill wearing a ledger's name.
  - Ask what the table would look like if the event HAD happened. A table written after a filter cannot hold the rejected class.
  - A job or audit table is not a complete log. Find its writers, and `GROUP BY` the metadata discriminator before reading a coarse type column as a census.

## Examples
- A2.8: a partial-index grep returned zero against three wrapped statements, and a formatter-wrapped caveat failed `grep -F` on a correctly rendered page.
- A2.9: a substring census of call sites over-counted and under-counted at the same time.
- A2.10: an unaliased pattern scoped to one directory found 1 site. The real count was 19 aliased sites in 11 files.
- A2.11: variety rationales changed from a suffix key to a prefix key mid-session, and a correct search returned empty.
- A2.12: "only set by two reducer cases" missed the component's initial state, seeded by a QA fixture.
- A2.13: a mixed-format `.env` held 10 credentials, and a `KEY=` grep found 6. Sourcing it echoed a prose line holding a password.
- A2.14: a package search found no MCP dependency. A wire-vocabulary grep found a complete production client, missing only session-expiry recovery.
- A2.15: seven guessed export paths failed. The base response already listed the export URLs, on a different host.
- A2.16: a file with a BOM showed `Bin` in the diff stat, and `git show | grep` returned nothing for a fix that was present.
- A2.17: three agents chased a missing literal through a word cap and two dedup stages. No stage had ever extracted it as a unit.
- A2.18: a task-store listing was read as "completed tasks drain". It was taken when only two tasks existed. Hours later, five completed tasks were still present.
- A2.19: an identity table with zero rows was read as "never captured". Redaction needed identity, and it worked on all 11 scored documents.
- A2.20: a filter discarded 237 of 468 groups. The reference table was a contracted partner list, so the discards were out of scope, not lost.
- A2.21: 63 truncated index hooks were declared unrecoverable. Each was regenerable from its file's description field.
- A2.22: a sink showed zero negative amounts, so the bug was called latent. The upstream mart held 246, 10 of them in the next backfill window.
- A2.23: an IS NULL control across every partition proved which writer class had stopped. The "regression" was a designed engine replacement.
- A2.24: 924 items were reported as succeeded against 848 written. 924 − 848 = 76 = 71 never collected + 5 gone dark.
- A2.25: a "transitions ledger" held 358 rows written within 7 seconds, and none since. Six campaigns had changed status that day.
