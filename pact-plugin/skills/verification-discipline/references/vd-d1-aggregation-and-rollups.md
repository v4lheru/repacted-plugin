# VD-D1: aggregation and rollups

Context: read this before you SUM, AVG, dedupe, group, normalise or bucket a column, and before
you trust a rollup someone else built. Every rule here is about a number that is arithmetically
valid and still wrong, because the aggregate does not match the grain, the unit or the basis of
what it summarises. Entry point and the other D1 splits:
[vd-d1-denominators-and-frames.md](vd-d1-denominators-and-frames.md).

## Canonical rules

### Grain and duplication
- **D1.11** Reduce a coarse-grain value stamped on fine-grain rows to one reading per grouping key before rollup. SUM multiplies it; MAX across keys under-reports.
  - Correct shape: `MAX(col)` per `(parent, group_key)`, then `SUM` across keys. Check both failure directions: what does a one-key parent report, and what does a many-key parent report? Verify on a real row of each.
  - Ask per column: is the row's date a WRITE date or a PERIOD date? A monthly value written daily reads ~7x per week, and the partial current bucket reads low ("the number keeps crashing").
  - AVG is safe there because it is duplication-invariant. State the discriminating property, not a plausible difference; the wrong reason invites the wrong fix.
  - Fix the query before the presentation: honest rendering of a wrong number makes it authoritative. Check each surface's source separately; one may need only a label.
  - Two surfaces rolling up one entity share ONE expression. `NULLIF` meaning "no data" is undone by `Number(x) || 0` downstream. `MAX(snapshot_date)` across period- and write-dated tables fakes staleness.
- **D1.12** Dedup fixes multiplication within an entity, not misattribution across entities. Test whether the group belongs to the entity with `COUNT(DISTINCT entity) GROUP BY group`.
  - A shared group is nobody's exclusive property. Contamination is usually partial: neither ship the blended figure nor delete the term wholesale.
  - Sinking a false row is not removing it: a list's caption claims EVERY row. Drop contaminated entrants from membership, state the loss, name the refinement that recovers genuine ones.
  - Remove a dropped term from the function SIGNATURE and pin the arity in a test. Put the caveat inline at the number.
- **D1.13** Rollups make zero-count members invisible. Reconcile a total against a decomposition that sums to it exactly, and solve for the unknown instead of explaining the residual.
  - Summing a descendants column drops every container holding nothing, often printed on the same page. Ask what your aggregation cannot represent.
  - In a GROUP BY rollup a zero is an ABSENT row: zero only if the snapshot is complete for that scope, fresh, and older than the item. Unknown creation date: do not mark.
  - Load by snapshot REPLACE per scope in one transaction, and have the load record its own coverage (scope, days, row count) there; rows cannot tell complete from truncated.
  - Shrink guards sit at the finest wipeable grain and never reach empty. Your first-ingest time is not an item's publication date.
  - A doc enumerating an open vocabulary carries the QUERY for the live set plus a dated count, list labelled as examples; keep a superseded count visible.
- **D1.14** Ask of any aggregate: could every row be right and the SUM still be wrong? Duplicate rows, cumulative snapshots and fan-out joins all answer yes.
  - `count(*)` vs `count(DISTINCT <intended key>)` is the cheap detector for an upsert that is not upserting. A NULL in a unique key defeats `ON CONFLICT` (Postgres mechanics: repacted:postgres-supabase).
  - A fix needs both halves: the key or index change AND a historical dedupe (latest per key). Fixing only the key leaves the old rows summing on screen, which reads as a failed fix.
- **D1.15** Proving an aggregation safe (disjoint, no overflow, types align) is not proving it meaningful. Name the unit of the result before you sum two columns.
  - If the unit needs an "and/or" ("likes across YouTube and LinkedIn"), it is two metrics. Make the blended field structurally absent and test its absence.
  - Never publish an aggregate that merges states with different behaviour ("null-or-empty"). Report the per-segment split; the aggregate is the resolution at which the trap is invisible.
  - A staleness or degraded indicator must read the component whose job is to know (breaker state, health endpoint, last-success record), share its query key with the existing widget, and clear itself. Do not flag forecasts, and do not flag when the status source itself has not loaded.
  - Audit the explanatory aside to the same standard as the result. The comment explaining why a rule exists is where an unmeasured magnitude survives.

### Normalisation and basis
- **D1.16** State the normalisation, grouping, basis and unit inline with every figure. Report strict and loose readings as a range, and rows that would CHANGE, not rows sitting in a group.
  - Never mix a semantic fold into a list billed as spelling variants. Ask for the smaller honest figure explicitly; the larger one travels.
  - The "inflates in the arguer's direction" tell works on ADVOCACY errors and fails on INSTRUMENT errors. Re-check a figure that fits your argument BECAUSE it fits; suspect the instrument when it contradicts you.
  - Say how numerator and denominator were each obtained; if the sentences differ, the ratio is meaningless. A container's declared size (sheet rows, API `total`) is never a population count.
  - A string sweep has four units: occurrences, matching lines, files, and code vs comment. `grep -c` counts lines. Never write a bare "appears N times".
  - A row count has a predicate too (exact project id vs a `LIKE` scope). A timestamp format that drops the date fakes a same-day span; filter by date BEFORE taking min and max.
- **D1.17** Before normalising mixed-basis figures, find the field that states the basis and test the conversion premise against the data. If neither holds, disclose and normalise nothing.
  - A fabricated normalisation is worse than a disclosed inconsistency: a reader can challenge the disclosure, not an invented multiplier. Test the premise even when the basis field is missing, because the ratio test can also show a conversion IS safe.
  - Make the disclosure carry its evidence (coverage numbers, the ratio test) and say how the figure may be used ("read as a ranking, not a total"). Surface the basis per row, not only in a footnote.
- **D1.18** When a column is labelled money, test it against a known money column in the same store: fractional share and cardinality. An observation becomes a test only against a known positive.
  - "0% fractional" alone is a number; 0% against nearly all on real MRR is a discriminator. About 60 distinct values across thousands of rows is not a continuous quantity.
- **D1.19** When you change the basis of one money figure in a report, audit every money aggregate in the same pass and convert or relabel each one, including comments.
  - Grep the generator for every money helper and reduce over a money field. A column that legitimately keeps the old basis gets a header saying so ("Deal size (CRM $)"). Present the full reconciliation once, not one finding per review cycle.

### Buckets, labels and series
- **D1.20** A top-N plus "Other" rollup breaks at N+1. Never aggregate a tail of one, and key the disclosure on an explicit `hasAggregate` flag, not a row-count comparison.
  - `rows.length < sorted.length` is false at exactly N+1, so the hidden member is named nowhere. Each half hides the other; fix both.
  - Test N-1, N, N+1 and N+2, and assert "every aggregated member is recoverable from something rendered". Make the aggregate name its count ("Other (9)").
- **D1.21** Fixed-length label truncation can merge distinct categories that share a prefix. Choose N by querying the real values for collisions, and comment that N is load-bearing.
  - `SELECT left(name, N), count(DISTINCT name) ... GROUP BY 1 HAVING count(DISTINCT name) > 1` must return nothing. A cycling palette can make the merged labels share a colour too. Prefer layouts that need no truncation.
- **D1.22** When severity seems to hinge on how consumers might use a payload, judge what the payload's shape claims. An ordered per-period array asserts its elements are comparable.
  - A consumer census over an HTTP endpoint is open, so any conclusion resting on it is unfalsifiable. "Is the newest bucket materially short at source?" is falsifiable and cheap.
  - Publish the partial period under a SEPARATE name (`current_month_to_date`), never a `partial: true` flag inside the same array; a flag protects only consumers that honour it.
  - Scope the ruling to what it is about. Range totals labelled "Total" are compared against nothing; trimming them would trade a comparability defect for an understatement. Put the reasoning in the code.

## Examples
- D1.11: a one-domain partner summed to about 50 times its true count; MAX would have cut a 40-domain partner to about 1/40.
- D1.12: about two thirds of the brand mentions sat on domains used by more than one partner; the flagship list was topped by a webmail host.
- D1.13: a taxonomy summed to 403 against 417; solving the decomposition gave exactly 14 nodes, three of them empty and printed on the page.
- D1.14: an empty-string-to-NULL coercion made every sync insert; a month's registrations summed to about 18 times the de-duplicated count.
- D1.15: a traffic KPI read 2.53 against a real ~67 during an upstream outage; the warning had to read the budget breaker, not a heuristic.
- D1.16: a count of "998 affected rows" was 155 (73 strict); one undeclared stopword fold contributed 138 rows where 1 would change.
- D1.17: "annual vs monthly" deal amounts; the basis fields were empty and Amount/MRR was 12x on only about a quarter of the deals (modal ratio 1.0).
- D1.18: a `$`-formatted "net new MRR" column was a unit-flag count: 0% fractional values against nearly all on a real MRR column.
- D1.19: a call-analytics retro took three review cycles to converge on one monthly-MRR basis, one money column at a time.
- D1.20: a chart showed "Other (1)" whose single member was named nowhere on the page.
- D1.21: five plan variants sharing one long name prefix rendered as one identical 18-character label; 26 separated them.
- D1.22: `monthly_trends[]` lost its short final element; the "Total" cards kept the elapsed month.
