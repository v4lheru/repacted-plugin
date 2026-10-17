# VD-C2b (split): construction patterns

Context: read this when a correct change on one side of a boundary could silence a control on the
other, when a blocklist or an exemption carries a safety decision, when an invariant spans two
modules, or when the default way to build something may drop the property you chose it for.
Entry file: [vd-c2b-controls-and-construction.md](vd-c2b-controls-and-construction.md).

## Canonical rules
- **C2b.2** Never cast (`as T`) a value crossing an ownership boundary: import the producer's type, mark always-emitted fields required, and add no index signature.
  - A cast asserts instead of checks, so a CORRECT upstream removal silently disables a downstream warning with clean `tsc` and green tests. Both halves are correct, which is why review misses it.
  - Check what the consumer ANNOTATES with: an all-optional local copy proves nothing. "The consumer uses my type" is an EDGE claim; "exactly one declaration exists" is a GRAPH claim. A contract needs both.
  - Write warning gates so only the reassuring value suppresses (`x?.overdue !== false`); `x != null && x.overdue` reinstates the bug, so pin it with a test. Do not collapse "slipped" and "never existed" into one message.
  - Keep a contract test for the wire (`JSON.stringify` drops `undefined`), and prove its field list discriminates by adding a conditional field. A contract test's field list decays like any consumer.
  - Do not retire a runtime guard because a type now forbids the shape. Conditional or `try`-assigned fields stay optional; a typed return does not guarantee one is returned.
  - After a fixture changes materially, re-run the mutation proofs. A "healthy" fixture missing a required field makes every assertion in its file test something else.
  - Write "the only path we have exercised", not "can only throw via X", and date it. Verify a teammate's NEGATIVE claim before inheriting it.
- **C2b.3** When a blocklist cannot discriminate or cannot fire, invert it to an allowlist of the known-good states, so anything new or unknown is caught or a no-op by default.
  - Three allowlist rules: every occurrence listed, no stale entry (a listed string matching nothing is a pre-approved hole), and no entry may reach the forbidden source. Write the checker as a pure function over text and mutate against REAL files in both directions.
  - A control is a PAIR. Deleting an unfireable detector strands its consumer; do not delete the consumer, invert it to act only on the one good state (`kind === 'complete'`).
  - Narrow the producer's return type so a `never` check compiles, and mutation-prove it by widening the type back. `never` erases at runtime, so the runtime default must be safe alone. Sweep for parameters only the deleted half read.
  - Accept the friction: a human classifying each new occurrence IS the feature when the defect came from nobody classifying one.
- **C2b.4** An exemption the control mints for itself is protected only by the current call graph; brand the type and expose a factory if you want the strong claim.
  - A plain interface with an optional field can be hand-assembled, and there is usually already such a literal in the codebase. Report "two set-sites, both inside the control: holds by convention, not construction."
  - A default protects against forgetting; a brand (`unique symbol`) protects against asserting. Correct an overstatement even when it arrives as praise.
- **C2b.5** Before taking an exemption plus a second guard to police it, look for the option that makes the gate pass honestly; that pair is two controls where zero are needed.
  - Usually the honest pass removes a capability you did not need (route output through the logger instead of exempting `print`). When an authority offers exactly two costly options, look for a third.
- **C2b.6** For an invariant spanning two modules, export a PREDICATE that takes the other side's value, not a shared constant, and mutation-test it from both sides.
  - A local literal shadowing the other side's DERIVED value (`GATE_DAYS*24`) cannot even be found by grepping the number, and your test stays green against your own stale mirror.
- **C2b.7** When you choose an approach for property P, check separately that its default implementation has P; the tidy default often removes it while everything still works.
  - Run that sweep once at design freeze. Postgres views default to DEFINER semantics, so a view launders access around a grant; use `WITH (security_invoker = true)`.
  - A fallback that improves a metric can poison the data when its error correlates with the inferred attribute (bare local numbers assumed domestic).
  - Reference data is a component with a correctness condition (shared dialling codes must enumerate member territories). A scope reduction that drops the cases that would falsify your model is a different thing that cannot fail.
  - Derive ambiguity from cardinality instead of a stored boolean. Never ship an assertion whose non-zero result is expected: cover everything, allowlist legacy cases with justification, assert the allowlist does not grow.
  - When two implementations agree on every observable and differ only under failure, READ the code: execution cannot discriminate (`information_schema.columns` is privilege-filtered, `pg_attribute` is not, and both return the same count today).
  - An instrument must not share a source with its subject (a guard reading the view it checks; a test importing its expected constant from the module under test). Test with a discriminating case and name the test for the property.
  - Before adding a guard, name the input that fires it wrongly AND the one that fires it rightly; if only the first exists, record the gap instead.
  - A strict check that fails on every developer machine gets switched off. Put the unskippable half where no environment can be absent (migration time) and the environment-dependent half in a one-time gate whose recorded result (`before=1, deleted=1, after=0`) is the evidence.
  - Do not accept that a control EXISTS; ask when it last DID something. A zero is informative only if a non-zero was reachable, so verify first on a named subject pre-checked to hold the row.
  - Reuse governs the logic, not the row volume: copying a helper's pattern to a finer binding can haul far more rows. Narrow in SQL and report both counts.
- **C2b.8** When one area keeps producing defects of one class, ask whether the source system can emit the field you are reconstructing, before writing the next guard.
  - Check whether upstream already emits it for another column: that turns a feature request into a consistency request. Cost the guards honestly against the ask.
  - Name what the simplification stops doing (an email lookup that skips deactivated accounts fixes routing but not leaver detection). Sweep for stale go-live steps when you pause on an upstream dependency.

## Examples
- C2b.2: a status builder returned `Record<string, unknown>` and the page cast it; after a correct upstream rename, `undefined === false` hid the only scheduling warning.
- C2b.3: a regex could not tell a money field from a count with near-identical names, so a section stayed permanently excluded and its test asserted it was still broken. A count-based contamination detector could never fire because the vendor caps rows at the request size.
- C2b.4: every set-site of an exemption cause sat in the gate module, but `pipeline.ts` already hand-built the result literal for another case.
- C2b.5: eight `print()` calls failing a logging gate went through `logger.info`, so no exclusion and no second guard were needed.
- C2b.6: a consumer copied a producer's derived window instead of calling `marginIsSafe(consumerFloor, …)`.
- C2b.7: a geo-origin view read tables as its owner; both existing views in the schema had `security_invoker` unset. An erasure log's one row showed a DELETE that had never removed anything.
- C2b.8: name-matching a requester to a directory needed a full scan of the whole member directory every run, put ~15 MB of profiles into execution data, and produced a public claim about leavers that was wrong for 3 people.
