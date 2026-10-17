# VD-D1: frames and instruments

Context: read this before you trust or report a reading that came from a directory, a clock, an
interpreter, a credential, a corpus, a harness or a config baseline. A wrong-frame reading is
well-formed: right units, plausible magnitude, no error. It is a correct answer about the wrong
object, so it never announces itself. Entry point and the other D1 splits:
[vd-d1-denominators-and-frames.md](vd-d1-denominators-and-frames.md).

## Canonical rules

### The frame travels with the number
- **D1.37** Capture a reading's frame (cwd, timezone, tree, tenant, corpus) in the same command as the number. Quote the frame whenever you quote the number.
  - Absolute paths, `pwd` and `date -u` in the same invocation, explicit tz on compared timestamps. Never `cd <relative> && <measure>`: cwd is not stable across tool calls.
  - Tiers, weakest first: frame-STAMPED (you print it); SELF-EVIDENCING (output only the right frame could produce, so a wrong frame fails loudly); EXTERNALLY FRAMED (CI on the committed tree, a registry digest, an accepted push).
  - A figure already reported from a relative `cd` is UNPROVEN, not probably fine. Readings that printed a frame-identifying value are salvageable from their own output; bare numbers must be re-taken.
  - If a wrong-frame reading would look exactly like this one, the frame is load-bearing (RC dump vs prod, same-named branches, region, tenant).
- **D1.38** The interpreter and its installed dependencies are part of the frame. A repo's own venv is not evidence of the project's runtime; match CI's image and the tree's `__pycache__` version.
  - A wrong-interpreter error names a teammate's file and line, so it reads as their defect. State the interpreter beside every count.
  - Build a matching venv in the scratchpad and install `requirements.txt` AND the test requirements file. Say out loud when a shared checkout carries a stale venv.
  - A generated artefact records nothing of its environment. Compare a borrowed interpreter's versions to the pin file; settle drift by regenerating under a pinned venv and diffing.
  - Test a wrong-version warning's behaviour under both versions and record the negative result. "It turned out fine" and "the control held" differ.
- **D1.39** A test result is indexed to one collection at one instant. State the project, filter and env-gated tiers beside the count, and establish the tree before attributing a red run.
  - In a shared tree, check mtimes before believing a red run (a peer may be mid-edit). Read the failure MESSAGE, not the file path; a deliberate guard reads as a broken test to whoever sees only the path.
  - The line number is the tell: a reported `file(627,18)` against a current `675-680` means a stale report, not a revert. Stamp every number you send "as of <UTC>".
  - A changed TOTAL for one file (44 vs 43) means the file differs. Check `git merge-base --is-ancestor <fix> HEAD`, compare tips with two-dot `git diff main..HEAD` (three-dot is silent about a branch that is behind), and change one variable per control run.
  - "Main is green, so red is yours" is a claim about main, not about your worktree. Correcting someone else's attribution is the riskiest moment for your own unverified one.

### The instrument belongs to a population
- **D1.40** A metric, threshold or calibration fitted on one population can invert on another. Re-measure the instrument, the window and the floor per population, from the live configuration.
  - Before accepting any option comparison, ask which population each row was computed on. When contradicting a table, say so and bring the measurement in the same message; the conclusion may survive while the reason dies.
  - Write the population into the plan sentence that records an instrument choice ("impressions, not spend, measured on Search"). One diverging day on the new population settles reuse.
  - Per candidate metric, compute the control-ratio distribution (p05/p50/p95, count at exactly zero; over ~5% zeros is unusable). Compare windows; a rolling window over a scheduled gap needs a floor.
  - Read the operative value from the running system, never a fallback constant. A restriction improving EVERY fit metric drops the hard cases. Check coupled threshold/floor; re-derive denominators when the premise moves.
  - A coarser grain dilutes as often as it deepens; compute both grains over the same event. Drop an unfalsifiable causal story from the design and build on the observable.
  - A fitted constant leaks through a DEFAULT parameter. Make it required and pin that with `@ts-expect-error` (bidirectional); its mutation test is a compile failure, so say so. Let the compiler enumerate call sites.
- **D1.41** Quota, rate-limit, visibility and capability readings are indexed to the principal or credential that took them. Identify it, and re-measure as the production caller.
  - An admin or service-role measurement never stands in for a delegated user. On a suppression path an under-returning read under-suppresses silently; the direction of failure decides whether the mismatch is a footnote or an incident.
  - Gate a cutover on same-query parity run AS the runtime principal. If parity fails, split the migration and keep the harm-bearing stage on the old path.
  - "Contradictory" quota readings are usually two keys. Fingerprint keys (`printf %s "$KEY" | shasum | cut -c1-12`), check the secret's VERSION history for rotations, grep by fingerprint not name.
  - Prove what a counter measures by spending a known quantity. Rule out a shared pool only with a positive control: idle-window zeros count only if the same counter moved when your own job ran.
- **D1.42** Harness settings silently redefine an experiment. Assert the post-condition on the thing you timed, reset sticky emulation at script start, and pin the viewport.
  - A fixed settle shorter than the variable under test measures something else; scale it (`max(floor, latency * 2 + margin)`). CDP network emulation survives on the target after the script exits. A headless default viewport can remove responsive navigation from the DOM.
  - Sweep the independent variable and check the response tracks it. Capture WHAT changed, not just when. Re-run earlier readings after a harness fix.
- **D1.43** A config section that overrides some keys inherits the rest, including values pinned for another purpose. Clear pinned keys, print the value actually USED, and assert it equals reality.
  - Mutation-test both directions (re-introduce the pin: red; remove it: green). A control that reads the pinned constant back is an echo: a control that cannot disagree with what it checks is not a control.
  - Sound arguments beside an unsound one launder it. Find the single argument the conclusion depends on and attack that one.
- **D1.44** Your own writes join the population you measured. Reconcile them before reporting drift; when others report drift against your number, check your own writes first.
  - Derive a verifier's expected count from the payload (`len(before) + len(payload)`), never type it. Assert terms against raw field strings, not `json.dumps` output (non-ASCII is escaped there).
  - Dedup asks "is this already here?" and never "does this make something here false?". For each new concrete fact (date, count, row, actor), ask which existing item it would falsify. Frequency of citation, including your own, is not evidence.

### Which corpus, which run
- **D1.45** Assert the identity of the DATA, not a config label. Fingerprint the corpus through the code's own connection pool, and discriminate on a floor over an append-only id sequence.
  - An environment variable is a label, a row count is a measurement. "It connected" passes against a reachable WRONG database. A stale snapshot cannot fabricate ids minted after it was taken, while two corpora can coincide on a count.
  - Enforce with two instruments: a behavioural test that lies about the environment and is still rejected, and a structural scan for config reads. Split failure reasons; refuse to run on an unidentified corpus.
  - Check `SHOW server_version` per store before trusting a local SQL test; a function can behave differently across major versions.
- **D1.46** A trend is evidence about the process that produced the readings. Establish that each reading was produced by the run you are judging before you read the trend.
  - A stale output file that parses cleanly and even rises between runs is not a run; a file on disk is not a run. A stale artefact that agrees is indistinguishable from a live one that agrees.
  - A deterministic effect with no causal path points at the instrument; say so instead of "no mechanism found". Ask a colleague first which instrument produced their baseline and whether it matches the one they just used.
- **D1.47** Mechanisms hold; locators drift. Re-verify a line number, byte offset, count or path at the moment you quote it into a brief.
  - Record a byte offset with its file size and a line number with its commit. State a figure once and cross-reference it; a figure in two places gets corrected in one.

## Examples
- D1.37: `cd backend && pytest` ran the shared checkout (519 vs 553); a CEST vs UTC timestamp made an image look built after its pod.
- D1.38: a checked-out venv on Python 3.9.6 produced a `str | None` collection error in a teammate's file; under the CI-matching 3.12 the suite passed.
- D1.39: three agents reported three different red states in one hour, all real: a unit-only run, a DB-tier guard, and a peer mid-edit.
- D1.40: impressions beat spend on Search, then on PMax impressions rose 122% while spend fell 46% on one day.
- D1.41: every figure in a migration surface document was taken as an admin; the production worker is a delegated user whose reads are filtered.
- D1.42: a 1500ms settle under 1600ms emulated latency reported a 14ms navigation that never happened.
- D1.43: a "deployed operating point" section inherited a pinned date and reported nine-day-old figures as today's.
- D1.44: an index-note count drifted by one entry and was raised as "the vault is receiving writes"; the write was the reporter's own.
- D1.45: a reviewer filed three "search defects" from a dev dump (max id 1435); production answered differently for all three queries.
- D1.46: test counts were read for an afternoon from a JSON file that the reporter mode in use never wrote; a peer's 1144 vs the file's 1123 exposed it.
- D1.47: a brief's `route.ts:158` was an accumulator; the defect was elsewhere.
