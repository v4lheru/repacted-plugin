# VD-D1: denominators and frames

Context: read this before quoting a count, a percentage, a rate, a coverage figure or any number
someone will act on. A number is a claim about a population, a unit, a frame and an instrument,
and only one of those four is printed. This file covers WHICH POPULATION a number counts. The rest
of D1 is split by question:

- How the rollup was built (grain, dedup, zeros, normalisation, money basis):
  [vd-d1-aggregation-and-rollups.md](vd-d1-aggregation-and-rollups.md)
- What the label says and where the figure came from (units, IDs, versions, provenance):
  [vd-d1-units-labels-and-provenance.md](vd-d1-units-labels-and-provenance.md)
- Which frame and instrument answered (cwd, tz, interpreter, principal, corpus, harness):
  [vd-d1-frames-and-instruments.md](vd-d1-frames-and-instruments.md)
- Whether a metric or threshold can do its job (can move, can fire, base rate, samples):
  [vd-d1-metrics-and-thresholds.md](vd-d1-metrics-and-thresholds.md)
- Diagnosing a wrong-looking number (reproduce it, regroup, direction, rebucket):
  [vd-d1-diagnosing-a-number.md](vd-d1-diagnosing-a-number.md)

Time windows, identity matching and fabricated zeros are in
[vd-d2-time-identity-and-zeros.md](vd-d2-time-identity-and-zeros.md). Relaying a number is in
[vd-e-relay-and-reporting.md](vd-e-relay-and-reporting.md).

## Canonical rules

### What bounded the result
- **D1.1** Before quoting a count, ask what bounded it (LIMIT, OFFSET, slice, `head`, page size, per-source cap, truncation). A count equal to your limit is your limit.
  - Coverage comes from `count(*)`, never `len(results)` of a paginated call. State a bound inline ("16, unlimited") or remove it and re-measure.
  - A small round number (3, 5, 25, 100) is the tell. When a peer cannot reproduce your count, suspect your instrument first and send the exact SQL and binds.
  - `ORDER BY date LIMIT N` labelled "last N weeks" is a row cap. On a mixed-cadence history table the frequent source evicts the rare one. Quantify with `row_number() OVER (PARTITION BY entity ORDER BY date DESC)` and count entities whose rare source fell outside the window; get a positive control (a record inside the window that renders) first.
  - A truncated fetch reports `min(true, N)` per item. Treat a count equal to the truncation value as unknown.

### The denominator is part of the claim
- **D1.2** A coverage percentage is a claim about its denominator. Name the population in the same sentence, and report both readings when they answer different questions.
  - "74.7% of all rows" and "99.8% of rows that can carry the value" are the same data. The first reads as a broken field, the second as a working carrier; neither alone is enough.
  - Before excluding a group from the bottom, ask whether it can genuinely never reach the surface the figure guards. URL-less rows that were still rendered turned "460/460 = 100%" into 443/581 = 76%.
  - When a summary filters and then says "N of which", measure against the PRE-filter count, or `covered === filtered.length` by construction and the partial branch can never fire. Force that branch in a test and render the artefact.
  - Revising a numerator re-certifies an aged denominator. Ask what the denominator counts and what it SHOULD count ("1 of 36": there were 27 rows, 9 of them active).
  - Two numbers printed as a range ("403–417") may measure two different sets. Check both endpoints answer the same question; if not, the band dissolves rather than narrows and no measurement can land in it.
  - Knowing this rule does not make it fire on your own number. A reviewer with no stake is the structural fix; stating the excluded group inline is the weaker, free one.
- **D1.3** Derive any number that describes a table (total, footnote, coverage, "N of M") from the same rows and predicate the table renders, never from a parallel computation.
  - Capture figures about excluded rows BEFORE the filter removes them. Watch projection and `.map()` boundaries, where an accumulated field dies silently.
  - When two predicates genuinely differ, keep both, name both, and say which one the footnote uses. Name fields for their basis (`lost_deal_size`, not `lost_value`).
  - A stakeholder spec whose rollup must sum a printed detail column is a hard contract that outranks any plan line. Re-derive its arithmetic before reading code; a short enumeration (a missing fourth bucket) shows up as a residual.
  - The fix that holds is shared derivation (the display filter passed as a named predicate), not a cross-check between two independent computations.
- **D1.4** Re-aggregate at the grain the claim is about. A row count is not an entity count, and history rows can overstate current-state impact by orders of magnitude.
  - Check that the count next to a noun was computed over that noun (rows to pieces, snapshots to partners, events to users). Backfill audits are the sharp case: huge write volume, tiny current-state impact.
- **D1.5** Ask how the denominator is distributed over the dimension the number claims to generalise over. A right-sized n from one bucket describes that bucket.
  - This hazard lives inside ONE measurement, so there is no second figure to expose it. Say the concentration every time the figure is quoted, or do not quote it.
  - Label the hole: "nobody has measured X; it needs a spread of Y". An unlabelled gap gets filled by the nearest available number.
- **D1.6** When any aggregate is quoted (mean, band, ratio, coverage), ask for the spread and whether the population is homogeneous before anyone acts on it.
  - Ask it as a reviewer's question, of your own figures too. A threshold that selects on the property under test is circular.
  - A conversion factor between two measures is an aggregate in disguise. State which measure you used and cite the ratio only as evidence that it varies.

### Content, not cardinality
- **D1.7** Row counts and run status measure cardinality, not content. Report body-bearing rows and the body-size distribution against the source's known range.
  - Recipe: `min`, median, `avg`, `max`, `count(DISTINCT body)`, and a count below a floor. `median == min` means most of the corpus is one repeated string. `count(DISTINCT content_hash) < count(*)` is the same signal one level up.
  - A fill-rate or emptiness figure is scoped to the fields measured. Measure every text-bearing field separately; metadata-sounding fields (`description`, `summary`, `notes`) hide real prose.
  - Report rows AND body-bearing rows by type. State emptiness rules on EXTRACTED text: a non-empty body can render to zero words (a lone iframe).
  - Enumerate a sitemap per child sitemap, not by path filter (section roots and locale siblings ride along). Perturb a page-size limit both ways; documented defaults can be wrong while every call returns 200.
  - A stop rule is a test, not a threshold. A small count that reconciles exactly with the lane's own declared scope is the correct result.
  - Do not verify from exit status when `ok` and `partial` both exit 0. Go to the store.

### What the artefact records
- **D1.8** A file's final state is not a run's result. Take rates from the run's own stats or ledger files, which date the attempts; repaired rows carry no marker.
  - A repaired artefact always looks better than the run that produced it, so this error only ever understates what went wrong. Ask of any handed rate: "run outcome or file state?"
  - A partial deploy leaves artefacts biased to its CHEAP steps (an empty table, a committed JSON, a runbook). Interrogate contents (`COUNT(*)`, a stats file, a workflow id on the host), never existence.
  - One file can carry two moments: two internally coherent blocks that disagree. Cross-checking sub-totals is the trap; ask what moment each block was written at.
  - A sample that changes size between pass and repair, or a file with more ids than the run selected, is a ledger accreted across runs. Compare ids, not counts, before claiming two measurements used the same set.
  - Leave a discrepancy open when nothing discriminates its causes. When pointing at evidence, point at whichever outlives the other: a tracked file by path, a gitignored or scratch file by copying its values inline.
- **D1.9** A cost or usage column on surviving rows measures work that survived, not work paid for. Sum the attempt log and reconcile it with the bill.
  - Any discard path (`ON CONFLICT DO NOTHING`, dedup insert, retry, failed write after a paid call) is invisible to the result table, which still reconciles with its own token counts.
  - A sequence far ahead of the live row count (`last_value` vs `count(*)`) is direct evidence of discarded inserts. Rows written per night is not calls made per night.
- **D1.10** An exclusion leaves no trace in the data. Record what a decision excluded next to the mechanism that excludes it.
  - Pinning an API version freezes the field set; record the forgone fields. For an enumeration scope, state the population first, then each exclusion with its arithmetic (found N, dropped M because X).
  - A predicate that cannot classify some rows excludes them silently (a URL-space rule dropped every draft), and the gap looks like the rule working.

## Examples
- D1.1: a coverage claim of "3 rows" and "5 rows" came from `print(hits[:3])` and `limit=5`. Unlimited, it was 16.
- D1.2: a product field was 417/558 of all rows and 417/418 of the rows under the one tree that can carry it. Both were quoted as "the coverage".
- D1.3: a call-analytics report's footnote was computed after the display filter had removed the row it described, and its coverage numerator used a different predicate (34/45 instead of 33/45).
- D1.4: 37,516 rows had a NULL traffic value; 24 pieces had NULL as their latest row.
- D1.5: "17 work emails on 25 leads" looked like a 68% match rate; 23 of the 25 leads worked for one employer.
- D1.6: two group means were published as a project band while the underlying distributions spanned 8.8x and 42x.
- D1.7: a client-rendered page would have stored a 113–171 char CTA shell as every body and still reported 80/80 complete.
- D1.8: a call-analytics insights file showed 0 errors; the first pass had run at 0.50 and every failure had been re-extracted afterwards.
- D1.9: A dashboard project: the results table summed to $183.81; the audit log showed 62,066 calls, 45,946 discarded, $647.53 spent.
- D1.10: a scoping rule keyed on URL paths dropped every unpublished draft, and the resulting set looked complete.
