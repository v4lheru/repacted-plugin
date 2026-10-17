# VD-C2b: controls and construction

Context: read this when you are asked for a guard, write one, review one, or rely on one. A
control can be declared and never armed, keyed on a proxy, exempted too broadly, or silently
disabled by a correct change elsewhere. The strongest fix usually removes the bad state instead
of guarding it. This is the entry file for VD-C2b: it holds the ranking every
control decision starts from (C2b.1), and C2b.38–C2b.44 below. Six splits hold the rest:
- [Construction patterns](vd-c2b-construction-patterns.md) (C2b.2–C2b.8): casts at ownership
  boundaries, allowlists and dead detectors, branded exemptions, exemption-plus-guard,
  cross-module predicates, defaults that drop the chosen property, asking the source.
- [Contracts and derivation](vd-c2b-contracts-and-derivation.md) (C2b.9–C2b.15): one parameter
  instead of a shared value, derived mirrors, exhaustive tables, required fields, honest ledgers.
- [Armed and loud](vd-c2b-armed-and-loud.md) (C2b.16–C2b.24): silent guards, binding gates,
  unprovisioned controls, prose invariants, recorded-but-unchecked values, liveness, notices.
- [Monitors and witnesses](vd-c2b-monitors-and-witnesses.md) (C2b.25–C2b.31): test-firing,
  witnesses vs detectors, `&&`-chained guards, redundant guards, directional bounds, excuses.
- [Guard scope and proxies](vd-c2b-guard-scope-and-proxies.md) (C2b.32–C2b.37): transport
  swaps, cause vs symptom, exemption lists, proxies, scanner coverage, roll-up leaks.
- Seams, extraction and nouns (C2b.38–C2b.44, in this file): caps at
  the I/O seam, extracted names, per-field strictness, satisfied scanners, search scope, nouns,
  chosen vs generated identities.
- [Detectors and identity](vd-c2b-detectors-and-identity.md) (C2b.45–C2b.57): vacuous equality,
  structure vs value, unparseable values, baselines, normalisers, type reach, empty inventories.

Related: fail-open defaults and unknown states are in
[vd-c2a-fail-open-and-unknown-states.md](vd-c2a-fail-open-and-unknown-states.md); which layer a
guard hooks, and reading the order of actions, is A2.33 in
[vd-a2-instruments-and-controls.md](vd-a2-instruments-and-controls.md).

Also read this when a guard counts, caps, extracts, scopes or identifies something: where a
cap sits, what string a name check actually sees, which field sets its strictness, which noun
its side effect touches, and which identity it enforces on.

## Canonical rules

- **C2b.1** Rank safeguards UNREPRESENTABLE > FAIL-CLOSED > GUARD > CONVENTION > COMMENT; when asked for a guard, first look for a way to make the bad state unreachable.
  - A guard is a line with no advocate, one refactor from gone; an unreachable state has no line to delete. Prefer the mandatory field over the optional one that identifies the same thing (store the verified email, not an optional display name).
  - A client-supplied field is a CLAIM, a session-derived one is EVIDENCE: remove it from the request body rather than validating it harder. Removing a subsystem (no storage bucket) removes its whole risk class.
  - A guard still earns its place against FORGETTING; removing the state protects against ASSERTING. Say which threat each control covers; sometimes you need both.
  - For a new degrade path producing a safety verdict, specify separate entry points with DIFFERENT return types (the wrong verdict is unobtainable). "Return a discriminated union" lands one tier lower: the failure is unmissable, but "treat as indeterminate" is a docblock the next consumer can omit.
  - "All current consumers handle it" describes the authors, not the type.
  - Place the assertion where the RISK is implemented, not where the concept is described (the cache that sorts, not the registry). A check whose failure is unrepresentable in what it reads is not a check; mutation testing reports it "unkillable", which reads as strong.
  - The ranking applies to PROCESS too: a memory note, a prose hold in a handoff, a skipped test, a TODO or a README claim are comment-tier. One rung up is cheap: an unstaged file, `it.fails()`, a fail-closed boot assertion. Predict the next instance wherever the artefact is a sentence.
  - Ask what the human SEES at the moment they act. Reasoning in a docblock on a different function is not in front of the person whose action it governs.
  - File a contingency under the PROPERTY that triggers it, with the prompting case as an example, never the reverse.
  - An invariant over a POPULATION breaks by addition (a new test file, a new caller) with no edit to the guard. Relocate it onto the line it protects where possible; a population assertion is a stopgap, and record its denominator and inert cases so it can be re-run.
  - Of any guard, ask what would make a competent person delete it, and whether they would be right about today. If yes, comment at the site and on its test that it has no live caller BY DESIGN, and why.

### Seams, extraction and nouns
- **C2b.38** Put a rate, count or budget cap at the injectable I/O seam, not the calling loop; a seam counts real requests, including redirects, retries and secondary calls.
  - Throw past the cap; do not log. Size it from the structural worst case (`maxAttempts × (1 + maxHops) × N` plus extras), report that figure before the first request, and set the cap above it.
- **C2b.39** A guard on an extracted name inherits the extractor's bugs and can fail OPEN: parse, don't slice, normalise before the predicate, and sabotage extraction and predicate separately.
  - Write a smuggle test: a refused value carrying the accepted suffix inside the part that should have been stripped. The refusal message names only the extracted value, never the whole DSN.
- **C2b.40** Choose a guard's strictness from the field that establishes the fact, not a blanket policy, and check the strict version on the case you are fixing before shipping it.
  - Drive both states with identical input and assert they make DIFFERENT claims. A conditional fix that no-ops on its motivating case is worse than none, because it closes the ticket.
- **C2b.41** Making a scanner go green without changing the behaviour moved the check, not the risk: name what changed for the real-world outcome.
  - In a container, `print()` reaches the same stdout the logger writes to while bypassing every `logging.Filter`. Log structured, author-controlled pieces (`e.reason`, `e.detail()`), and decline "this message is self-authored" carve-outs when a structural answer exists.
  - Do not dismiss a report because its measurement is stale; re-derive its argument against the current state.
- **C2b.42** A search query is not a scope control: a missing label matches nothing and a malformed operand matches everything, both with HTTP 200; verify the scope on each returned record.
  - Refuse to run with no scope configured. Verify the named label exists before listing. Check a property of each record (its label ids), since only that survives an over-matching query.
  - Treat hitting the batch cap as a refusal of the whole run; the cap limits blast radius, it is not a filter. Mark raw free-text scopes as not verifiable per record. `resultSizeEstimate` is not a count.
- **C2b.43** Scope a guard to the broadest noun its side effect touches (CREATE ROLE is cluster-wide), and distinguish "checked zero" from "checked N, all clean".
  - A loop over an empty list passes vacuously; never mint a durable marker or credential from a check that can pass that way. Sometimes two nouns need two guards (a disposable database AND a managed cluster).
  - "Server-side" is not "server-enforced": the boundary is who can change the setting. Host and database name may not distinguish environments; the connecting user can.
  - When you fix a vacuous grant by demanding real evidence, check the evidence can exist at the moment authorisation is needed. A defect that only appears outside CI will not be found by CI.
- **C2b.44** Enforce a guard on a human-chosen, stable identity; a generated identifier (cluster id, instance UUID) can be recorded for provenance but fails as a blocklist AND as an allowlist.
  - Provenance wants the narrowest identity; enforcement wants the broadest that still fails safe. Prefer a probe whose permission cannot be revoked (a provisioned role name in a PUBLIC-readable catalog).

## Examples
- C2b.1: a reviewer's name could be empty from the auth claim; storing the mandatory verified email left no reachable blank identity. A test-harness datastore invariant held only because of which 3 of 85 test files happened to exist.
- C2b.38: 9 requests for 8 URLs because a redirect hop counted itself; worst case 156 against a proposed cap of 120.
- C2b.39: `app_dev?options=app_test` ended in `_test`, so a DROP guard would have passed the dev database (a dashboard project).
- C2b.40: `=== false` on a state reachable only when an ad is disapproved matched zero campaigns and silently no-opped the fix.
- C2b.41: a refusal rerouted to `print` bypassed the secret redactor installed two hours earlier.
- C2b.42: a malformed Gmail operand returned the whole mailbox and fed a classifier thousands of unrelated messages while every check passed.
- C2b.43: a "scratch database" check passed honestly on a scratch database created on the production instance, and `CREATE ROLE` landed on production.
- C2b.44: point-in-time recovery mints a new `system_identifier`, so a committed production id silently stops matching.
