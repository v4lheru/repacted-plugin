# VD-D2: time, identity and zeros

Context: read this before you compare periods, query history, reuse a timestamp, quote a duration
or a count that changes over the day, or render a value whose absence could mean zero. This file
covers TIME. D2 has two splits:

- Matching, identity keys and set comparison (normalisers, fuzzy scores, proxies, duplicates):
  [vd-d2-matching-and-identity.md](vd-d2-matching-and-identity.md)
- Fabricated zeros and null coalescence (`?? 0`, dashes, sentinels):
  [vd-d2-fabricated-zeros.md](vd-d2-fabricated-zeros.md)

Denominators, grain and rollups are in [vd-d1-denominators-and-frames.md](vd-d1-denominators-and-frames.md).

## Canonical rules

### History you cannot query
- **D2.1** A mutable current-state field cannot reconstruct history. Querying it with a date filter stamps today's value on past periods, so persist an immutable as-observed snapshot going forward.
  - The query never errors and never returns empty; it lies, and in a backtest the lie points toward "the detector was right not to fire". Classify EVERY field as event (safely re-fetchable) or current-state, per field, not per API.
  - The test: pick a row whose value provably changed and query it as of a date before the change. If today's value comes back, it is current state. Then never issue historical queries on it; poll, persist transitions, and run in shadow until enough history exists.
  - Say so when that makes an acceptance criterion unmeetable for a dimension; a known gap can be re-scoped, a gap hidden behind a green backtest cannot.
  - A point-in-time trace built from a refreshed "current" column collapses every period to the terminal value while the rollup SQL looks correct. Check the actual write path, not the DDL comment (a documented refresh may never have been built), and check WHICH table's copy is mutable.
  - The fix is one immutable `<dim>_at_row` stamped once at ingest, or protecting an existing freeze as an invariant. A one-shot seed stamps today's value on history, so flag seeded periods. A job that sees only arrivals cannot detect absence; derive absence at report time.
- **D2.2** Know which rows the writer revisits before sizing a window or quoting a fix's reach. A row stops being rewritten at the writer's revisit edge and its value freezes there.
  - A fix to logic that stamps a derived column only reaches rows the writer will touch again. Split the report into deployed-fix reach and backfill reach, and size the backfill by rows to ADD and rows to CORRECT.
  - Grep for a re-compute caller; never trust a file header that names an endpoint.
  - A refresh window is a settlement horizon, not a performance knob: last write wins, so the final refresh is all that counts. Size it from the upstream's settlement curve and assert the freeze age clears the knee, not the literal constant.
  - Waiting longer before READING cannot fix a frozen row. A backfill creates two populations in one table (settled history, frozen-short live) with a one-directional gap a baseline detector reads as a collapse.

### Partial and in-progress periods
- **D2.3** The newest in-progress bucket distorts every series and ratio on a surface. Check each one against the elapsed fraction; a guard on one series does not cover its siblings.
  - A ratio's distortion is `completion(numerator) / completion(denominator)`. Same-source terms nearly cancel; cross-source terms do not. Name each term's source table; divergence between same-source terms means your own query is mis-sourced.
  - It can FLATTER (a deflated cost-per-X reads as efficiency) and need not be a step, so "does the series look wrong?" is not a detector. Quote a deviation together with the adjacent point, or name the baseline.
  - Enumerate every series a surface renders and ask of each whether its newest bucket is complete. Exclude it client-side and SAY SO in the UI, and raise a follow-up for the endpoint, because other consumers (an LLM layer restating history) still see the fabricated decline.
  - A low figure matched against an independently derived expectation (percent of normal against percent of the period elapsed) is what shows a calendar effect rather than a performance change.
- **D2.4** Never slice an ISO timestamp to a fixed width before parsing; `[:19]` drops the UTC offset. Parse whole, convert to UTC explicitly, and truncate only display strings or grouping keys.
  - Appending `+00:00` to a sliced local time shifts every value by the offset, and two sources sliced the same way cancel. It surfaces only against a genuine `Z` source, where 20 minutes can measure as 1h40m.
- **D2.5** A partial incident day slips into a control set as its tightest point. Exclude incidents at hour grain with recovery tails, report the threshold sweep, and hold out unseen data.
  - A full incident day is obvious; a day where the outage starts mid-afternoon sits at the bottom of the control distribution, exactly where a min-based threshold looks.
  - Report episodes at each threshold: a flat plateau is robust, a steep rise is a knife edge. Deriving on a set and scoring the same set is partly tautological; the held-out result is the claim worth making.
  - When a correction moves your margin the favourable way, say so as a correction; that direction is least likely to be audited. A per-entity detector can misframe an account-wide cause, and a "loss" can be deferred, so state exposure, not loss.
- **D2.6** A comparison across granularities (a date against a month) is an unstated decision that can move money. State the side you took, what live data does under each, and its cost.
  - Deliver under a stated assumption rather than blocking. Prefer the reading that does not recast an already-settled record as wrong, and confine the comparison to one line so flipping it is trivial.
  - Never write "confirmed with the requester" unless they were asked this exact question. Put the concrete money consequence in the HANDOFF, not just "the boundary is ambiguous".
- **D2.7** Two gates measured from different origins (midnight vs the row's own timestamp) disagree at the boundary and silently select which entities get judged. Simulate the real predicate on real rows.
  - Take the OLDER of the two dates so both gates are satisfied. The damage is a selection effect, not a shortage: the dropped entities are the always-active, high-volume ones the detector most needs, and a random-looking subset is not random.

### Timestamps, durations and counts over time
- **D2.8** A `COALESCE(attested, observed)` time column fuses an exact timestamp with a late-biased one. Document both precisions, state the schema-permitted bound, and expose per-row uncertainty.
  - For timing, the vendor's attested (often backfilled) timestamp is exact and your live observation is biased late by up to the polling bracket. The intuition that live beats backfilled is right for existence and backwards for timing.
  - Offer uncertainty as a number (zero for attested rows; `observed_at - prev_observed_at` for sampled ones). State the bound the SCHEMA permits, not today's config; a worst-case row can land on the wrong weekday. Ask which way a consumer plans to weight.
- **D2.9** On a reused state row, `resolved_at - first_seen_at` spans every episode, not the incident. Reconstruct episodes from open and close events before quoting a duration.
  - A UNIQUE natural key with a re-open path is the tell. Pair each open with its next close per row. Equal open and close counts is the cheap sanity check; one open incident reads `1 opened / 0 resolved`.
  - Where you own the schema, add an episode id or reset the start on re-open.
- **D2.10** A count that cycles on a schedule must be bound to its measurement instant in the same clause ("4 pages checked at 18:10"), or readers see a trend. No timestamp, no count.
  - A count parenthesised onto a label reads as a standing property. A measured zero must still render. Ask whether a quantity cycles (diurnally, weekly, on someone else's schedule), and test the SHAPE of the rendered line so a revert to the label form fails.
- **D2.11** Turning an A-vs-B comparison into an N-period window re-commits stock/flow errors: a stock takes the latest reading in the window, a flow sums. Make the basis a required parameter.
  - Averaging levels smears a step into a slope. Test that the WRONG values are absent, and mutation-test by flipping stock to SUM. Verify stock vs flow against production data, not the column name; a series that keeps falling back to 0 cannot be a running total. Check that null-vs-zero survives the window aggregation.

## Examples
- D2.1: a date-filtered `approval_status` returned today's APPROVED for ads disapproved on the day; a call-analytics stage-per-month trace would have collapsed to the final stage.
- D2.2: a new matcher corrected 243 rows in replay, but only 35 were in the live period; 1,667 of 1,902 rows were frozen.
- D2.3: a cross-source cost-per-NPU read about 20% low in a partial month while a same-source ratio was off by 4.5%.
- D2.4: Jira returns an instance offset (`+0200`) and billing systems return `Z`; a sliced comparison moved records two proximity buckets.
- D2.5: the "lowest control" 0.3288 was the first afternoon of an outage; clean, the margin was 18%, not 9.6%.
- D2.6: a rate dated 27 April under "effective-from <= the statement's month" decided whether a paid month was short.
- D2.7: a conversions detector evaluated 10 of 14 campaigns under one gate and 13 of 14 under both.
- D2.8: a consumer planned to down-weight exact backfilled change times in favour of hour-late live polls.
- D2.9: "six alerts stuck open for 66–74 days" were 50 complete episodes with a median of 73 hours.
- D2.10: a probed-URL count shrank every afternoon on a client's budget script and read as a daily incident.
- D2.11: summing weekly traffic levels in a window reproduced a 3.19x–7.30x inflation that had just been fixed.
