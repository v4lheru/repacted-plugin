# VD-D2: matching and identity

Context: read this before you claim two sets are the same, trust a key, normaliser or matcher,
report a duplicate or overlap figure, or infer a dataset's state from something that correlates
with it. The shared failure: a count or a score agrees while the identities underneath differ.
Entry point: [vd-d2-time-identity-and-zeros.md](vd-d2-time-identity-and-zeros.md).

## Canonical rules

### Same size is not same set
- **D2.12** A matching count proves consistency, not identity. Close a membership claim with set differences in BOTH directions, and explain each element that moved.
  - To attribute a measured set to a named cause, also show nothing matches outside the boundary (the converse arm) and that the untouched population is intact. Copy the boundary (date list, id list) verbatim from the artefact that created it, never from a relay.
  - "N improved, 0 reduced" is satisfied by different sets; run `old EXCEPT new` and `new EXCEPT old`. Decompose a "nothing moved" check into its transitions (null to number, number to null, number to number) instead of one count.
  - A structural well-formedness check ("every line starts with `- [`") is a count check in disguise. When a delimiter can occur inside the field it splits, the count is guaranteed to agree and only the set can disagree.
  - A claim about MEMBERSHIP closes only with a membership test. A cheap count that contradicts a stale claim feels like refutation but establishes nothing about the named members; check each named member individually.
- **D2.13** To establish a dataset's state, query the property that defines it. Timestamps, counts and totals produce FALSE AGREEMENT, and high agreement is what makes a wrong proxy durable.
  - Ask which event writes a timestamp: `created_at` dates the INSERT and says nothing about a later UPDATE or backfill. Record the effect-level evidence you trusted, not the proxy that agreed beside it; a conclusion travels further than its warrant.
  - Name which system OWNS the fact (employment: HR; entitlement: billing; identity: the directory). A proxy from another system that agrees 8 times in 9 cannot be refuted from inside that system; you need a second source, not a better query. A state your source cannot express reads as something else, not as a gap.
  - A key namespaced per container collides across containers, so a lookup in the wrong one returns a real, well-formed wrong record. Pin the container first (by mtime against event times, never by a declared name) and read one record to confirm it is yours.
- **D2.14** Publish a key's cardinality (what a query on it admits), not just its meaning. A many-to-one tail needs an identity term that uses the same normalisation as the derived column.
  - A total mapping with an `ELSE` bucket makes that bucket shared (every unmapped language on one config); that is the price of totality, so document it and scope on identity separately.
  - A predicate on a RAW column beside a derived column that NORMALISES its input is a latent silent-loss bug. Put the normaliser in one immutable function both sides call. Verify an implied term empirically before deleting it as redundant.

### Keys, normalisers and matchers
- **D2.15** Refuse on ambiguity and require exact identity. Matchers produce confident arbitrary winners when keys lose identity, category signals outweigh identity, or a fuzzy score masks a discriminator.
  - Ask where identity lives per host (the path; YouTube's `?v=`; a CMS `?p=`). Extract ids before lowercasing (YouTube ids are case-sensitive). `matches[0]` with no `ORDER BY` is a coin flip; ambiguity is the absence of a match.
  - A uniform score across many rows, or ties at the winning score, means category signals fired and nothing row-specific did. Decompose a score into its addends. Category signals must never be able to sum past the confidence threshold on their own.
  - An order-dependent tie-break is a second defect: it flips rows when a source is added and manufactures phantom deltas. Rank by content-derived keys, then mark any surviving tie ambiguous.
  - A whole-string similarity lets a strong common part carry a mismatched discriminator (surname, house number, version). Require the discriminating component exactly as a separate, subtract-only predicate; a threshold change cannot fix it.
  - Count a guard's REFUSALS before calling it load-bearing; zero refusals makes it insurance, not a safety property. Ablate one guard at a time before quoting per-rule contributions ("first guard to fire" is not unique contribution). Review a small risky stratum exhaustively.
  - A blanket removal rule hides over-correction: removal must be shown wrong row by row. Measure an identity floor's blast radius per population.
- **D2.16** A 100%-populated field may have zero discriminating power. Measure a key's marginal contribution by ablation, and never hand a downstream agent an unmeasured near-certainty.
  - A lossy projection (an email local part derived from a name) can corroborate a match but rarely generate one; those need different plumbing. Say "populated on every row; discriminating power not measured".
- **D2.17** Before discarding a key component in normalisation, measure per-key collapse over the real corpus: `max(source_rows x target_records)` per key. For some records the discarded part IS the identity.
  - A degenerate key makes the match count go UP and look like coverage, and one outlier key hides in an otherwise clean duplicate distribution. Reproducing an expected match count does not validate the rule; look for an existing identity extractor.
  - Where collapsing is correct (tracking parameters), last-write-wins is still wrong; define the aggregation (SUM).
  - Verify the stated MECHANISM, not just the symptom; a defence built on a wrong mechanism passes vacuously. When a component is dangerous both to drop and to embed, make it a required, asserted parameter.
  - Never edit a shared normaliser from one consumer. Assert in your own lane and report the shared function; if you need a different rule, write a second documented normaliser with a test pinning where they diverge.
- **D2.18** Re-extracting stripped material into a hashed projection can undo the normaliser. Extract stable content identifiers (an iframe `src`), never storage pointers (an `img` `src` a CDN rewrites).
  - Justify each selector with a corpus measurement, and note beside the selector list which normalisation it bypasses. State the projection with any duplicate count.
- **D2.19** Substring and prefix matching silently include or drop neighbours. Parse and compare exact components (`new URL()`), or use word boundaries, and suspect the matcher when counts look round or far off.
  - `startsWith('https://...')` drops every `http://` row and reads as "the source is empty". `includes('/api/x')` silently counts a new `/api/x/child` route. `'hospital' in text` matches "hospitality".
  - Reconcile a match count against an independent estimate; one value dominating the matches is a matcher-bug signature. Keep a scheme check at an `href` sink even when upstream validates. For a "no request happened" test, vary the fixture so a cached key cannot make it pass.
- **D2.20** A purpose-looking structured field (label, enum, flag) can classify worse than the crude rule it would replace. Print the confusion in both directions over the real population before adopting it.
  - A vendor's label means whatever its content team used it for. Pin the finding with a test naming an item the label would wrongly catch.

### Duplicates and staged matchers
- **D2.21** A candidate shortlist finds only duplicates that look alike. Run all pairs, name the hashed projection, and positive-control a near-zero result on a sister subset in the same run.
  - Same name, same title and same content are three different claims. Strip boilerplate before fuzzy scoring, and count documents too short to score (they masquerade as unique).
  - Separate EQUALITY from CONTAINMENT (check fan-in) before recommending a fix. Fuzzy duplicates defeat exact-match constraints, so handle them in ranking, not at write time; report whether the score distribution is bimodal or a gradient.
  - A duplicate count is a property of the projection. Hash extracted text, not markup with per-record ids. Diff two should-be-identical records and keep neutralising until they are EQUAL; the number of mechanisms is itself a finding.
  - Make a dedup verdict three-valued (duplicate, overlap, distinct), or overlaps get deleted as duplicates and their extra rules are lost.
- **D2.22** A stage-2 matcher stricter than the stage-1 selector reports "not found" that reads as data. Decompose the unknown bucket by cause, and ship coverage beside any ratio computed over it.
  - An item is in your input because something matched it, so an unlocatable item is first a disagreement between two matchers (full-text stemming vs a literal regex). An `unknown` counted into a base rate silently moves the denominator.

## Examples
- D2.12: 37,516 NULL rows were attributed to a dated backfill only after "NULLs outside those dates = 0"; an index compaction kept 107 lines and lost one link.
- D2.13: a notifier treated a chat tool's `deleted:true` as "left the company"; the one disagreement carried live orders.
- D2.14: an Italian query on a shared `simple` text-search config returned Japanese rows, and the raw-language fix excluded `en-US`.
- D2.15: a URL normaliser put 840 pieces on one YouTube key and credited 12 conversions to videos never named.
- D2.16: an email field populated on 2,297 of 2,297 rows contributed 0 matches beyond the name tier.
- D2.17: canonicalising away the host put 380 locale URLs on English articles' ids; each run flipped the stored body.
- D2.18: two duplicate articles differed only by regenerated heading ids and 33 re-uploaded image URLs.
- D2.19: a `https://` prefix filter returned none of about 250 knowledge-base articles served over `http://`.
- D2.20: an LMS's "Template Course" label covered 1 of 6 real templates and 3 genuine courses.
- D2.21: a slug/title shortlist found about a tenth of the duplicates all-pairs comparison found; most were containment in one list page.
- D2.22: most "unknown" articles only contained the plural of a product term.
