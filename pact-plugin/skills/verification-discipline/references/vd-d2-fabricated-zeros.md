# VD-D2: fabricated zeros

Context: read this before you change a `?? 0`, `|| 0` or `COALESCE(x, 0)`, render a dash for
"not measured", suppress a row, widen a query, or write an "unknown" filter or coverage figure.
"Wrongly 0" and "wrongly unknown" are both fabrications, and the second is harder to catch
because it matches the pattern everyone agreed is honest. Entry point:
[vd-d2-time-identity-and-zeros.md](vd-d2-time-identity-and-zeros.md).

## Canonical rules

- **D2.23** Before changing a `?? 0`, check whether the null is manufactured by a wrong SELECTION. For a selection fix a real number is the pass and a page of dashes is the failure.
  - Measure what the upstream query selected and what a correct selection would yield. If they differ, fix the selection first, in the same change; reusing an existing corrected selection helper keeps one query per route from spreading the defect.
  - A coalescence fix (the value really was unmeasured) passes on `--`; a selection fix (a real reading was being hidden) passes on the number. Read the route diff for a changed WHERE, `DISTINCT ON` or join before deciding which check applies, and say which to whoever verifies it.
  - Run the A/B at fleet scale (old total vs new total), and derive the expected numbers before observing. A sibling route that was never broken is not evidence for the fixed one.
  - Over-dashing is a failure too: a measured zero must still render `0`. Scope absence assertions to the cell under test, not the whole row.
  - A bulk backfill of historical rows barely moves a latest-row-selected render. Ask which rows the selection can reach before projecting a data change onto a UI. Say which question a population figure answers (a dash in one column vs a dash in any column).
  - Widen the API type (`number | null`) and let the compiler list every consumer. Keep the shared formatter narrow and add an opt-in null variant, so the next route still hits the compile error.
- **D2.24** The same `?? 0` token can need opposite fixes. Classify every hit before changing any; never batch-fix a grep list, especially one a peer handed you.
  - In a field EMISSION it fabricates a claim about one entity; inside a `+=` accumulation it is correct arithmetic. Propagating null through an accumulator makes every total null; its real gap is disclosure (basis, as-of, population), a different follow-up.
  - Ask whether the source can record a zero. A nullable value in an existing row: `0` fabricates. A NOT NULL column written only when something happened: the absent row IS the zero, and a dash fabricates the other way.
  - Run the all-zero-row census (`count(*) FILTER (WHERE a = 0 AND b = 0 AND c = 0)`); zero such rows means absence encodes zero. Without a per-parent sync marker, "never happened" and "never synced" cannot be told apart; price that as a schema change and report the classification even when told not to fix it.
- **D2.25** A query fix that returns more rows turns downstream `?? 0` mappers into fabricated data points. Audit the consumers before declaring the fix done.
  - Grep consumers of the affected columns for `?? 0`, `: 0` and `|| 0`. A mixed-source history feeding a single-source chart needs filtering at the consumer. Pre-existing zeros that appear after your change will be read as your regression.
  - The honest sparse chart looks broken, and the next person will "fix" it by restoring the fabrication. Comment at the filter that the sparseness is correct and what the old series contained; the right remedy for the ugliness is a "single reading, no trend yet" disclosure.
- **D2.26** Before dropping a term from a cross-source nullable sum, count per side the rows where only that side carries a reading. Identical-looking terms can be opposite cases.
  - The decisive count is `count(*) FILTER (WHERE b IS NOT NULL AND a IS NULL)`: exactly the population a "keep only a" simplification destroys. If non-zero, keep both sides through a null-preserving helper (sum when either is present, null only when both are absent). Reuse the repo's helper rather than adding a variant.
- **D2.27** Before suppressing rows, intersect them with what a recent fix just revealed. If they overlap, render an explicit "not measured" dash that keeps zero and never-measured distinct.
  - Two correct fixes can compose into an apparent revert: correctness composes, optics do not. Absence reads as "not applicable" only where the reader had no reason to expect the row.
  - Check whether the file already renders a dash for this meaning and reuse it. When sibling blocks legitimately differ (suppress in one, dash in the other), write the asymmetry and its reason into the code.
- **D2.28** Before asserting coverage or writing an "unknown" filter, `GROUP BY` the column to see how unknown is stored (NULL, empty string, sentinel). Never mix account-grain and row-grain percentages.
  - A truthiness check treats a literal sentinel as known and overstates coverage; cover every variant, including labels downstream code substitutes for blanks. A comment asserting a data rule that is not true is the same class of bug as wrong code.
  - Label account-grain and row-grain figures explicitly, state that they are not comparable, and say which governs the artefact. Check that a figure handed to you for hardcoding was measured at the grain your report renders.

## Examples
- D2.23: a `MAX(snapshot_date)` with no source filter blanked the displayed columns; the fleet total read 0 against about 154,000, and `?? null` would have hidden hundreds of real readings behind dashes.
- D2.24: one grep in one file found a nullable traffic value (0 was fabricated on 22 pieces) and a NOT NULL registrations count with 0 all-zero rows in 1,902 (a dash would have regressed 2,288 pieces).
- D2.25: restoring evicted rows plotted 52 coerced zeros against 30 real points on one piece's chart.
- D2.26: a social-views column was NULL on all 169,488 rows, while social likes were the only reading on 11,384 rows across 209 pieces.
- D2.27: all 31 rows to be suppressed had been made visible hours earlier by another fix, and 27 had no other engagement data.
- D2.28: "unknown" was stored only as NULL (96) and empty (62); account grain said about 67% unknown while call grain said about 84%.
