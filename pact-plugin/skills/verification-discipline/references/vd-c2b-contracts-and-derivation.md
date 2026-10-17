# VD-C2b (split): contracts and derivation

Context: read this when two surfaces, services or modules must agree about one fact, when a
list or enum is mirrored, or when you request or enforce a field in a contract or a ledger.
Agreement kept by discipline drifts; agreement made unconstructible does not. Entry file:
[vd-c2b-controls-and-construction.md](vd-c2b-controls-and-construction.md).

## Canonical rules
- **C2b.9** To make surfaces unable to disagree, delete the parameter (the owner mints a closure or returns a whole view object) instead of sharing a predicate value.
  - A shared boolean is a convention with a type annotation: a new surface can pass its own value, and a caller can recompute the predicate. Un-export the underlying labeller; a helper left exported with a safe default returns ungated copy.
  - Back it with a source scan asserting the predicate expression appears EXACTLY ONCE in production files (comments must describe it, never quote it). That is the only check that sees a duplicate while both copies still agree.
  - Proximity is not scope: "X within N characters after a guard" passes an unguarded read that sits near another guard. Bind guard and read to the same statement span, which also flips the failure to false-red. Never ship an unsabotaged source-scan control.
  - Gate WHOLE objects per branch (`{code, tone, text}`), not one field: gating the text while tone stays hardcoded renders a prohibition in the benign register. The compiler then forces every arm to supply a new field; verify by adding a throwaway field. Rename the accessor when its return type changes.
  - Widen a consistency control's unit (`{text, severe}` per surface) instead of adding a parallel test, and read `severe` from each surface, never by re-deriving the predicate.
  - A control specified for one surface must be re-derived for a set of surfaces. The tell: it fails on copy you are confident is correct. Across surfaces, assert uniformity plus mentions > 0, not count-equality.
  - Widen the prohibition pattern; never narrow the mention pattern, since the safest copy ("Do NOT re-run it") contains the hazard words. Never use the `g` flag with `.test()` in a loop (`lastIndex` persists).
- **C2b.10** Once a runtime list exists, DERIVE the mirror (`z.enum(CONST)`) instead of testing that two lists agree; a subset test against a derived list is tautological.
  - Prefer, in order: derive the mirror, an exhaustive `never` guard, then a test. Grep for a runtime const at implementation time, not design time.
  - Derived is not deployed: separate services carry the const from their own build time, so deploy skew and rollback need a tolerant parse at the boundary.
  - Blanking a whole artifact on an unknown value is fail-closed for rendering and fail-open for the operator, who re-runs it (a double-submit on a write path). Map unknowns to `{kind: 'unrecognized', raw}`, never to a member that reads as safe.
  - Deriving an enum closes the parsing channel only; semantic mappings over it still need their own exhaustiveness guard. `z.object` field lists are not derivable. A mitigation designed against a lifted constraint survives as ceremony; correct any peer already building it.
- **C2b.11** Exhaustiveness via `Object.freeze()` on a typed Record catches a missing key but passes an extra one; assign a `satisfies`-checked literal first, then freeze it.
  - A literal routed through a function call is no longer fresh, so excess-property checking does not apply. Keep the two statements separate.
  - A `Record<string, …>` annotation widens `keyof` to `string`; bind the literal to an intermediate const to recover the key union. Mutation-prove every exhaustiveness check in both directions.
- **C2b.12** Request contract fields as REQUIRED, not optional-with-a-default: a silent default makes absence unrepresentable, costing the writer nothing and every reader silently.
  - Ask what the default MEANS when the field is genuinely absent; if that differs from the default's meaning in the world, it must be required or explicitly three-valued.
  - With many producers, put any fallback at one render site as a commented `x ?? fallback`, never in the type. Copy fields (nouns, units, labels) are load-bearing: they can name things that do not exist.
  - When a peer's argument beats your contract request, withdraw and say which argument won.
- **C2b.13** A required field the sender legitimately nulls in a degraded case makes a strict receiver reject permanently while both suites pass; reject only when absence makes the operation impossible.
  - Ask what else is still correct without the field and do it, recording an anomaly that names what was NOT written and what a human should do.
  - When the receiver stops rejecting, tell the sender: its logic keyed on the expected rejection stops firing. Exchange concrete degraded-case payloads, not only the schema, and state a retry rule once.
- **C2b.14** A constraint enforces shape, not honesty: ask what the producer writes when it cannot express the truth in the legal value set, and fix the vocabulary upstream of the constraint.
  - A producer under pressure picks the nearest legal lie, and the store becomes internally consistent and factually wrong. Ask an LLM for the discriminator first and a rating only when one applies.
  - The renderer's job is to make a dishonest value noticeable (show the evidence behind the strong state), not to repair it. Schema-valid is not true, and a retry loop selects for whatever passes.
- **C2b.15** When baselining a ledger against history you cannot verify, put honesty in constraints: a reconciliation row must be unable to claim "applied", and "cannot tell" is a first-class status.
  - A ledger row is read as current state. Stamping the known set as applied turns "we do not know" into "we verified" in the one artifact people consult instead of measuring.
  - `CHECK (status <> 'applied' OR source = 'runner')`; reconciliation rows carry `observed_at` and method. Keep "unobservable by available instruments" distinct from "absent".
  - Ship it EMPTY if you cannot measure, with the reason stated in the artifact itself. Key on what disambiguates (`(chain, filename)`, not a bare migration number).

## Examples
- C2b.9: a re-run hazard predicate was computed twice, 235 lines apart, while a comment said no surface may compute its own; the fix had been "shared" three times and the class kept returning.
- C2b.10: a subset test was about to be written against `BLOCK_REASONS` after a peer had already added the runtime const.
- C2b.11: a 245-code country table caught missing, deleted and wrong-value keys and passed an extra key at exit 0.
- C2b.12: `entityNoun?` defaulting to "ads" would have rendered "5 of 5 ads" for a campaign type that has no ads.
- C2b.13: `refund: null` got a 4xx, the sender retried only 5xx, and the event was lost, three times in one build.
- C2b.14: a CHECK let `rating = 0` exist only with `contradicted`; a model scoring "not demonstrated" as 0 would be written as a contradiction to get past it.
- C2b.15: a migration measured absent from production would have been stamped applied.
