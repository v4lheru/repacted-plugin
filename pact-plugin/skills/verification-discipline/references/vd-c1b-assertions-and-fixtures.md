# VD-C1b: assertions and fixtures

Context: read this while writing or reviewing an assertion, a fixture, a mock or a test double,
and before citing a test as coverage. The failure here is a live, specific, well-named
assertion that is true for a reason other than the one it names. Whether a check can fail at
all, and mutation mechanics, are in [vd-c1a-prove-it-can-fail.md](vd-c1a-prove-it-can-fail.md).
Runner and pipeline scope is in [vd-c1c-runners-and-toolchain.md](vd-c1c-runners-and-toolchain.md).

This family is split eight ways:
- this file: assertion shapes that pass for the wrong reason (C1b.1–C1b.8);
- [vd-c1b-assertion-targets.md](vd-c1b-assertion-targets.md): narrowing guards, both arms, bounds, copy and keys (C1b.9–C1b.14);
- [vd-c1b-where-the-risk-lives.md](vd-c1b-where-the-risk-lives.md): outcome over mechanism, the risk's file, what a guard does not catch, oracles (C1b.15–C1b.21);
- [vd-c1b-reach-and-mechanisms.md](vd-c1b-reach-and-mechanisms.md): tests that never reach their branch, second mechanisms, strings that are code (C1b.22–C1b.28);
- [vd-c1b-fixture-realism.md](vd-c1b-fixture-realism.md): fixtures that cannot discriminate, captured responses, literals and domains (C1b.29–C1b.39);
- [vd-c1b-fixture-coverage.md](vd-c1b-fixture-coverage.md): states, caps, real data, reject sets, replay blind spots, populations of one (C1b.40–C1b.50);
- [vd-c1b-copies-mocks-and-seams.md](vd-c1b-copies-mocks-and-seams.md): transcriptions, pins, hand-built intermediates, shared implementations and untested seams (C1b.51–C1b.57);
- [vd-c1-harnesses-doubles-and-quirks.md](vd-c1-harnesses-doubles-and-quirks.md): harness prop identity, wrappers, mirrored precedents, delegated tests, doubles and goldens (C1b.58–C1b.63).

## Canonical rules

- **C1b.1** Assert the PROPERTY a value must have, not its literal and not a relation to another value. A literal defends the author's wording, and the wording may be the defect.
  - A literal catches drift, never a misreading; the natural repair (update the expected string) launders the regression. Keep literals for drift, and add property assertions above them.
  - Collect every string carrying the same fact into one array and loop the property over them; prove it with a one-word mutant.
  - `A != B` is satisfied by garbage (`"runs through null"`) and `A == B` by two empties. Guard both sides non-null first, then silence one side and watch the equality go red.
  - To corroborate an IDENTITY, make the check NAME the value (`GROUP BY` and read the rows); a `COUNT(DISTINCT)` of 1 fits any single value. `max()` on a TEXT version column is lexical (`'v9' > 'v12'`).
  - When two candidates are almost always equal, verify on the rows where they DISAGREE (`WHERE a <> b`); a sample of agreeing rows cannot tell them apart.
- **C1b.2** A negative or universal assertion (disjoint, `not.toContain`, "did not fire", `toEqual([])`) passes on empty input. Pair it, in the same test, with a positive assertion that the scenario was reached.
  - For disjointness assert both sets are non-empty; beside `expect(bad).toEqual([])` put `expect(candidates.length).toBeGreaterThan(0)`. Print the denominator beside every verdict.
  - A parse that silently yields `""` or `[]` makes every downstream loop pass forever (`for (x of "")` iterates zero times). Count field presence with two parsers and diff the counts.
  - A "clean" or "no findings" verdict needs a positive control on known-dirty input more than a dirty verdict does. Prefer a whole-file scan to a section-slicing regex, which adds a way to find nothing.
  - "Clean because empty": a credential grep over an audit column is worthless if the column is null or constant; check distinct values per row. Zero hits over an allow-list builder's output proves the builder, not a filter.
  - Expect a parser fix to turn a long-green validator red; pre-measure the blast radius instead of relaxing the check.
  - A reported failure line that holds no assertion means the file moved under you. Run your own lane in isolation beside the full tree, and re-measure before anyone acts.
- **C1b.3** An absence check passes on a typo or a wrong target. Put the load-bearing weight on a paired PRESENCE check of the same needle, where a typo fails loudly.
  - Grep pair: the marker MUST match on your branch and MUST NOT match on the old ref. A doubly-empty result means you are in the wrong tree or directory, not that the thing is missing.
  - A broken collector empties every absence assertion at once: `\bdisabled\b` over markup matched Tailwind's `disabled:` variant and dropped every styled field. Anchor attribute regexes.
  - A presence assertion over a recorded collection needs an emptiness control: show the recorder can produce both answers.
  - Forward link integrity ("every reference resolves") cannot detect a deletion; count before and after, or enumerate the targets and confirm each is reachable.
  - An instrument whose reach is set by a proxy (a delimiter, a window of N rows, `head`/`tail`, one named producer) is right about what it cut and wrong about what you asked.
  - Assert a key is present before `delete`-ing it to simulate absence; grep that a selector used in a "must not render" assertion exists in the source at all (an invented testid is absent forever).
  - `await` the state you are testing before asserting anything is absent, name the tree or scope in the assertion (`git show <sha>:`), and remember a source-tree run is not a shipped-image run.
- **C1b.4** A "not X" witness is satisfied by the `unknown`, `null` or `''` a silently defaulting pipeline emits. Name the positive value (or known-good set) you require, and prove it red on pre-fix data.
  - Treat every "we don't know" sentinel as a value to exclude explicitly. A field constant across a whole fixture corpus is far more often a default that cannot fail than data.
  - Making a bug findable is not catching it; do not accept credit for the second because you earned the first.
- **C1b.5** Ask what the assertion reads if the mechanism were absent. Build-with-placeholder-then-overwrite makes "the value differs" vacuous: the placeholder differs too.
  - Restructure so the only possible mistake leaves the ORIGINAL value (a pure `withHash(item)` helper), and pin well-formedness (`/^[0-9a-f]{64}$/`) before difference.
  - A fixture whose value equals the type's default makes "the value survived" vacuous; choose a non-default value deliberately and say why in a comment.
  - A broad assertion ("zero webhooks sent", "alerts.length > 0") is satisfied by a neighbouring mechanism (an emit cap, a generic alert). Assert the outcome only the mechanism under test produces.
  - A zero-count oracle needs a positive control under identical wiring: two runs differing in one variable, the first asserting a non-zero count. Zeros from a dry-run mode are free.
  - When a parser or normaliser touches a fixture id before the branch, add a fixture guard (`expect(extract(url)).toBe(id)`); an unanchored regex silently truncated invented ids.
- **C1b.6** Assert VALUES the environment would never invent. HTTP clients inject `accept`, `user-agent`, `host` and encodings, so a name-only "header was forwarded" passes against a forward-nothing policy.
  - Pick a drop-header the client never injects, and pin the vacuity with a companion test (policy stubbed to `{}` still shows the client's own headers), or the value checks get "simplified" back.
  - `a is b` cannot prove a regex is shared: `re.compile` caches by pattern string. Assert the source text (the import present, no local compile). Same for anything interned or cached by value.
- **C1b.7** Asserting a conditional's BODY says nothing about whether the conditional can fire. Extract the gate into a named, tested predicate and assert the call site.
  - Replacing the gate with `false` left six body assertions green. Extracting an inline `x && x !== "complete"` into a boolean helper drops TypeScript narrowing; return a type predicate (`x is T`).
- **C1b.8** An assertion's SHAPE can let the mutant through. List-equality is beaten by padding, an overlap check by sibling contributors, `toContain` by an added extra.
  - Assert per-element membership (`fallback not in expansion`), trace a contributor by name through whatever records attribution, and prefer `toEqual` on rendered params and key sets.
  - A survivor is not automatically a weak test: first ask whether the mutation was on a path the test reaches, with a control run first.

## Examples
- C1b.1: safety copy covered by 8 literal assertions still misled a stakeholder; dropping one possessive word failed 6 property assertions and would have failed 1 literal one.
- C1b.1: `max(prompt_version)` returned `v9` for a month while production ran `v12` (verified on the live column 2026-09-04).
- C1b.2: two id sets from a stale fixture were both empty, so "disjoint" was trivially true; a log classifier sliced to an empty section printed "no raw payload found".
- C1b.3: a positive control returned nothing too, because the working directory had silently reset to a checkout without the feature.
- C1b.4: `scopeEdition !== 'enterprise'` stayed green while every row in every fixture was `'unknown'` from a `getattr` default.
- C1b.5: a stale-cursor detector test passed with the detector deleted because a generic page-bound alert also fired; asserting `action === 'token_below_retention'` fixed it.
- C1b.6: `assert "accept" in sent` survived a header policy returning `{}` (verified 2026-09-08, httpx 0.28.1).
- C1b.8: a mutant that discarded a whole query survived an overlap assertion because a sibling query returned the same articles.
