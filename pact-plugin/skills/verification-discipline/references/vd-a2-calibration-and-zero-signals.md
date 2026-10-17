# VD-A2: calibrating instruments and reading zero signals

Context: read this when a metric, column or scan reads zero, empty or suspiciously tidy, when a
verifier or reporter is new to you, or before filing a defect against someone else's work. The
general control discipline is in [vd-a2-instruments-and-controls.md](vd-a2-instruments-and-controls.md).

## Canonical rules
- **A2.34** A zero or empty result is ambiguous between "never happened", "the writer was never wired" and "no run since the writer shipped". Check the writer's liveness and coverage before reading absence.
  - Grep for WRITES of the column (DDL only means vestigial). Compare each producer's last run with the commit date of the writer.
  - Trace one literal stage by stage (raw, then intermediate, then final). Aggregates say THAT something is wrong, and a traced literal says WHERE.
  - A clean sweep rules out only mechanisms that would have left the artifact you swept for. Name the entity your evidence covers: two tables are two questions.
  - A privilege-filtered catalog (`information_schema`) answers "what may I touch", and the system catalog answers "what exists". Force an explicit denial rather than reading an empty listing.
  - Saturation (sizes piled just above a cap, round totals per partition) marks a ceiling or a top-N leaderboard, not a census. `GROUP BY` the partition key before trusting a row count.
  - When scope shrinks, re-ask whether a control can still fail. If no black-box control survives, assert on the value the code resolved (white-box). A defect you cannot test for is MORE urgent.
  - A non-zero aggregate can come entirely from rows that measure the container. Partition by row shape. Check whether the experiment already ran in your data.
  - A decisive measurement outside your grant is a finding about scope: state it and name the query. Never fill the gap with an older number. Grep a diagnostic for writes before re-running it.
  - Check a provenance column (`source`, `created_by`) early. A single value across the table turns "where did this come from" into a lookup.
- **A2.35** Before adding instrumentation, look for persisted values that straddle the suspect filter. Before reading an instrument's silence as a negative, prove it can report a positive.
  - Run it wide across many runs, convert counts into NAMES, prove the join, and say "both are floors" when they are.
  - A transient or duty-cycled marker is absent by schedule, so select the correlated state beside it. State the instrument's coverage, such as 29 of 78 days.
  - Two agreeing NEGATIVES are far weaker than two positives. For each one, ask what it would have shown had the thing been present. A refutation you ISSUE costs more than a belief you hold.
- **A2.36** Calibrate an unfamiliar verifier or reporter in both directions before trusting its count or verdict. Known-bad input must fire, and known-clean input must stay silent.
  - Common false positives: a prohibition quoted in a message, the container's own schema using the hunted key, a role inferred from a mention, escaping depth.
  - Also: a probe whose assumption the measured population negates by definition.
  - Exactly 0% on one stratum usually means a structural impossibility in the probe. A test-name filter (`-t`, `-k`) can select siblings, so a filtered run must report which tests it selected.
  - A constant summary count on an untouched file means a broken reporter. A planted violation met with silence may mean the rule is off, so probe with a rule you confirmed is enabled.
  - Say which rule families the green covers. Before adding a ratchet guard, name the input that fires it wrongly and the one that fires it rightly.
- **A2.37** Before filing a defect against a peer, prove your own measurement: unique join keys, the right table and scope, a second independent method, and a positive control.
  - Query your own table, never a peer's top-N sample. Widen the search root one level (plan docs often live above the checkout), and say which root you searched.
  - A remedy needs its own verification: check the representation on BOTH sides of a boundary. The reassuring clause deserves more scrutiny than the alarming one.
  - Capture stdout and stderr to one file, because stdout is block-buffered when redirected. Record the near-miss rather than quietly correcting it.
  - Compare `count(*)` with `count(distinct key)` before diffing. Re-derive table names from the code under test, because a wrong but existing table returns a plausible number.
  - A mismatch count of one or two is usually yours, not the code's.
- **A2.38** A perfect zero, a clean NULL, a round total or an exact n of n means check the instrument first. A falsified mechanism points at the measurement, including someone else's baseline.
  - Ask what the result would look like if your instrument were wrong. Name keys from the producer's source, and prefer two independent arms that must agree.
  - Point the rule outward too: ask which instrument produced the baseline, and whether it is the one just used.
- **A2.39** After a pass, ask what would have made the instrument report failure. Assert on the capture itself: non-empty, the right shape, the right count, the exact URL.
  - URL substring filters match nested sibling endpoints. Anchor the match, collect into a list, and assert the expected number of entries.

## Examples
- A2.34: a column read as "clustering never ran" was written only by its DDL. `information_schema` listed 2 tables where `pg_class` held 287. Two BigQuery tables held exactly 420,000 rows each: a top-N export cap.
- A2.35: two adjacent job columns already held a before and after count for a filter that dropped 237 of 468 on every run. A change log that never records label changes was read as "labels were absent".
- A2.36: a transliteration tier scored 0% because the probe assumed matching spellings (19 of 19 once both sides were folded). `-t "'E'"` ran scenario A.
- A2.37: three agents were one message from filing a defect, and all three were artifacts of their own measurement. In one, a "missing" guard line had only been buffered.
- A2.38: `scannable` / `image_count` were the wrong key names and gave 0 and NULL. The producer emits camelCase and had written 82 rows.
- A2.39: a CDP filter on `/api/impact-analysis` also matched `/passages`. The loop overwrote the body the test existed to record.
