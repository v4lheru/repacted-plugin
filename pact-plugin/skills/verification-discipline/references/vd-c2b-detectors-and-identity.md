# VD-C2b (split): detectors and identity

Context: read this when a detector compares values, hashes, types, versions or inventories, or
when a check's input can quietly change form (encryption, normalisation, a default, an empty
set). These checks usually cannot fail, and look strongest exactly then. Entry file:
[vd-c2b-controls-and-construction.md](vd-c2b-controls-and-construction.md).

## Canonical rules
- **C2b.45** Never build an equality or change-detection guard over a non-deterministic representation (randomised encryption, salted hashes, map ordering); use a monotonic version counter.
  - AES-GCM draws a fresh nonce per encrypt, so `IS DISTINCT FROM` on ciphertext is true on every write and the guard can never fire. Ask whether two writes of one logical value can produce different bytes.
  - Enforce `new.version < old.version` → reject by trigger, exempt updates that legitimately carry no new value, and pin `encrypt(x) != encrypt(x)` in a test beside the reasoning.
- **C2b.46** A detector that compares VALUES cannot see a regression in STRUCTURE: two logically equivalent copies agree everywhere while the single-definition property is gone. Assert on structure.
  - Write the invariant in words first, then check the detector asks that. Prove it by breaking the property in a rolled-back transaction and showing the structure check alarms while the value check stays clean; keep them as separate checks.
  - `pg_get_functiondef` is STRICT and `to_regprocedure` returns NULL for a missing object, so `position(...) = 0` fails OPEN on a dropped function: use `coalesce(position(...), 0) = 0`. A text match is not a dependency proof (`pg_depend` is).
  - Name in the artifact what a check cannot catch; "documented" is only for gaps you cannot close. "Idempotent, safe to re-run" can go false when a later migration replaces the same object: re-apply, don't reason.
- **C2b.47** A static guard that cannot parse a value (an expression instead of a literal) waves it through; treat "skipped" and "unverifiable" as not passed.
  - Prefer the shape the guard can read. Computing a value the framework also determines (a route segment map vs the page directory) creates two sources of truth.
  - When satisfying a guard forces a second literal, add one assertion that both emitted values agree. Cover a legitimately dynamic case against emitted bytes.
- **C2b.48** Hunt transport knowledge encoded as a behavioural predicate (`startsWith('http')`), which shape-greps miss; a blocklist of the old bad shape fails open under a new transport.
  - An allowlist must require positive evidence of the good thing ("a label we RESOLVED"), not the absence of the known-bad shape. "Any string without a URI scheme" readmits a bare id.
  - If the consumer cannot know the good set, have the producer state it (`{resolved: true, label} | {resolved: false}`). Grep spec files for the vendor's literals too.
  - When a gate's input changes meaning while its type stays `boolean`, change the parameter TYPE so the compiler enumerates the callers.
- **C2b.49** Detect changed content by hashing the payload, not by key sets or file size; and a detector that degrades to "no baseline" must fail loud.
  - A key-set diff cannot see an in-place revision; file size moves for unrelated reasons. Treat "could not verify N items" as a blocking result, never a row.
  - Give every derived baseline a positive control (non-empty, a known member present). The latest snapshot is not a superset of the earlier ones: monotonic key growth is not monotonic content growth, so hash per key along the chain.
- **C2b.50** Widening a normaliser at one boundary can break exact-match joins and hash keys elsewhere; validate the form at load, and make the caller emit exactly the form a gate approved.
  - Grep every other consumer of the value (`===`, `Set.has`, a hash). Fixing the comparator instead of the source leaves the dedup key split.
  - A predicate that trims internally while the caller emits the raw value approves one string and ships another. An anchored `^[...]` sanitiser is defeated by anything before the anchor; argue on consistency, not exploitability.
  - Before claiming "no current test catches this mutant", apply the mutant and run the suite. Extra tests added for legibility are fine; do not sell them as closing a gap.
  - Enumerate a tri-state guard's matched, could-not-evaluate and nothing-to-evaluate branches; the empty one (`?? {kind: 'none'}`) often lands on a clean negative.
  - Before filing against a `x ?? DEFAULT` feeding a gate, read the producer of the null and count its population; the finding is usually the compensating control's scope, not the line.
  - State a positional guard as "exploitable" or "fragile", with the reason; look for a construction site that already bypasses it.
- **C2b.51** A type invariant holds only across the files the typechecker reads; guard on the positive shape you need (`typeof x !== 'string' || !x.length`), not on one imagined negative.
  - Excluded test trees and type-stripping runners let fixtures deliver `undefined` where the type promised `null`. A required parameter is a claim until you delete the argument and watch the compiler reject it.
  - A fix hardening a not-yet-reachable path must not regress a shipping one; write down the condition under which that stops being true.
- **C2b.52** In GitLab CI, a rule built only from defaulted variables matches every pipeline; rules are first-match-wins, so an earlier correct guard is redundant, never dead.
  - Calling such a guard "dead code" is what makes a later tidy-up silently change what deploys. An immutable commit-sha image tag, not the rule, is what saved production.
- **C2b.53** A capability or inventory check must assert NON-EMPTY, not only success: an under-privileged token can return HTTP 200 with a well-formed empty inventory.
  - A set comparison of expected against actual reports no mismatches against an empty actual. Zero has two causes and the API reports only the value; pair every inventory check with a cardinality assertion.
- **C2b.54** When one builder feeds both sides of a join, test the value through the OTHER side's real parser end to end; a format change moves both sides together and every internal test stays green.
  - "Both sides agree" is not evidence when both sides are yours. Prove it by mutating the format to one the foreign parser cannot read.
- **C2b.55** When renaming the config key a module reads, assert the literal name once and prove it live with a value DIFFERENT from the hardcoded fallback.
  - Tests that set the key through the exported constant cannot disagree with it, and a fallback equal to the deployed value makes a misspelled key behave identically.
- **C2b.56** A filter measured against a population that never exercises it is UNTESTED, not validated; report it as unexercised and pin it with a fixture containing the discriminating member.
  - "I measured it and the filter changed nothing" is evidence about the population, never about the filter. Mutation-prove the pin by deleting the filter.
- **C2b.57** Before scoping an include or exclude flag to "filtered axes only", read how the client encodes ALL-SELECTED; many clients send it as no filter at all.
  - The meaning of "no selection" is decided by the client's encoding, not the server's docstring. Mutation-test the naive symmetric version.

## Examples
- C2b.45: a trigger meant to force refresh-token rotation compared AES-256-GCM ciphertext and could never fire.
- C2b.46: re-running an earlier migration alone restored an inline copy of an extracted function, and the value-drift view stayed empty.
- C2b.47: a canonical-path scanner reported "3 verified, 3 unverifiable" until the literals were inlined; a route map and the page directory could drift into a silent deindex.
- C2b.48: `!stage.toLowerCase().startsWith('http')` let a bare option id through as "resolved" into a client-facing CSV export after a REST-to-MCP swap.
- C2b.49: a harvest census built on an empty prior-state regex reported 26 rows of "(no prior dump)"; the real delta was 7 tasks, not 4.
- C2b.50: `projects/123` and `123` were both accepted by a normaliser and split an idempotency hash; almost every client had no mapping, so routing that null to indeterminate would have been an outage.
- C2b.51: a worker's tsconfig excluded `__tests__/**` and vitest strips types, so a fixture's missing field arrived as `undefined` and `=== null` would have fallen through.
- C2b.52: a feature-branch push deployed production because a rule of two defaulted variables matched every pipeline.
- C2b.53: a scopeless OAuth token minted fine and `tools/list` returned `"tools": []` at HTTP 200.
- C2b.54: synthesised `custom_field_options/{id}` URIs round-tripped internally while the REST-side regex would have returned null for all of them.
- C2b.56: a DNC type filter returned 26 rules unfiltered and 26 filtered.
- C2b.57: two toggles applied to both scope axes; the symmetric fix would have no-opped because the UI omits the edition filter when all chips are ticked, and its mutation killed 8 tests.
