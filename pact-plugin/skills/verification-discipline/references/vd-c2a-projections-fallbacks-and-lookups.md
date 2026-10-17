# VD-C2a (split): projections, fallbacks and lookups

Context: read this when a value reaches a control through a reused sentinel, a list projection, a parser, a
`value || default`, a lookup by name, or an optional dependency. Each can quietly substitute a
plausible value for a missing one. Entry file: [vd-c2a-fail-open-and-unknown-states.md](vd-c2a-fail-open-and-unknown-states.md).

## Canonical rules
- **C2a.6** Never route a new state through an existing sentinel such as `null`: it inherits every consumer's meaning. Use a counted channel and move any guard in the same commit.
  - Grep every reader of the sentinel before agreeing to the producer change. A floor like "rows arrived, none parsed ⇒ degrade" turns "nothing applicable" into an outage.
  - Fix by construction: let the non-applicable row still parse with its real type and count applicability at the caller, so the floor's predicate stays correct unchanged.
  - Rank the failures by severity, not correctness: a more correct parser that trips a guard written for the old code can turn a quiet inaccuracy into a blocked safety gate.
  - Write the new-state test BEFORE the change: the input that makes guard and classifier disagree often does not exist yet.
  - Delete an "expected because of bug #N" branch in the same change that fixes #N, or it classifies the bug's return as expected. Prefer self-cancelling pins (`it.fails`) keyed to the named mechanism; verify the flip by measuring after the fix.
  - Cite mechanisms (function and file), never a bare ticket number: a number can later resolve to an unrelated new ticket.
  - When a classification rule moves, grep the test doubles that mirror it in the same change.
- **C2a.7** Before relying on a field, check which projection feeds the consumer: list rows are thinner than detail rows, and a detail-only flag is absent from them.
  - Defaulting an absent safety flag (`dnc: false`) is a positive claim. Dropping the row fakes "not present". Throw into the per-item degrade path and pay the N+1 detail call.
  - Diff `Object.keys` of the list and detail shapes, spread detail over the row, and verify by clicking: an inert control still renders.
  - A mutation can persist (PUT 200) while the read endpoint never echoes the field; flag the read-path gap.
- **C2a.8** A per-row parser that hardcodes a type or kind erases a record class; keep "nothing matched the filter" out of the total-loss guard.
  - Tell: a literal assigned to `type`, `kind` or `status` inside a per-row parser. When a second transport exposes the discriminator, that is a bug report about the first.
  - "Nothing was readable" must degrade; "nothing matched" is a real answer of zero. Count them separately.
- **C2a.9** A `value || default` fallback is safe only if the default is valid in the same addressing mode; keep one mode on both paths and verify the degraded one.
  - `0` as a gid, a name, an index and a count are four defaults. Resolve to the representation both paths share instead of branching per mode.
  - The degraded path is the least tested and most load-bearing branch. Verify what the fallback does rather than asserting it in a handoff.
- **C2a.10** A lookup by header name survives insertion, not rename: a missing column must throw, never degrade to blank-means-permissive.
  - Try exact match first, then a unique prefix match; zero or two matches are hard errors. "Blank means active" applies only when the column was found and the cell is empty.
  - Make the halt fail-closed and loud (an error branch that posts), placed before any idempotency claim so it is retryable. Put the observed headers in the error.
  - A downstream count check can be satisfied by the very bug it exists to catch, when the bug restores the counted quantity.
  - Normalise three-state cells explicitly: `Boolean('FALSE') === true`.
- **C2a.11** Treat "this fallback is basically right anyway" as the warning sign: a plausible substitute survives every review. Make absence visible instead.
  - Audit the siblings: the same mask is usually applied uniformly, and a derived field must not be re-derived from inputs that may be missing.
  - When a wire change lands behind such a fallback, say explicitly that a green suite is not evidence it was clean.
- **C2a.12** Log every degradation path of an optional dependency in the run summary, and confirm the scheduled process can see the credential.
  - Invisible failure needs three things: plausible fallback output, unlogged likely paths, exit 0. Ask whether a fully degraded run is distinguishable from a working one by artifact alone.
  - launchd, cron, systemd, Docker and CI start without your shell environment. Fixing the code alone leaves the scheduled run blind.
  - Before diagnosing a quiet component, check it was ever built to do the expensive thing: "restore a step" and "add a capability" are different scopes.
- **C2a.13** When two parallel paths handle one failure class and only one has the error vocabulary, report the pattern after the second instance, not a third point fix.
  - Grep the richer arm's reason codes and list the call sites that never use them. Docstrings often describe the intended behaviour for both arms.
  - Score safety and diagnosis separately: a crude abort that preserves the invariant costs diagnosis, not correctness.

## Examples
- C2a.6: 26 domain-only DNC rules gave 0 parsed and `degraded: true` under the "more correct" parser; the control (26 email rows) was unchanged in both versions.
- C2a.7: a vendor search endpoint's slim rows carry no do-not-contact flag, the first branch of the exclusion check.
- C2a.8: a REST parser set `type: 'Email'` on every rule, so a domain rule shown ACTIVE in the vendor UI could never fire.
- C2a.9: an n8n Sheets tab moved from `list` mode (gid 0 = first sheet) to `name` mode, where the fallback `0` looks for a tab titled "0".
- C2a.10: a status column renamed mid-build would have nearly doubled the active-client count, and the count guard (7 labels vs 7 sections) would have passed. Single-cell Sheets writes: repacted:google-workspace.
- C2a.11: `meta.total ?? matches.length` produced the identical number when the field was renamed; `meta.limit` carried the same mask.
- C2a.12: an LLM summariser returned "" on missing key, missing package and exception, logged only the exception, and exited 0; the launchd plist declared only `PATH`.
- C2a.13: a read-only corpus arm returned `[]` on outage, could never be marked unavailable, and fell through to a 500 on export, while the write arm had its own reason code.
