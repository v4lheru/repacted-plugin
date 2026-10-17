# Change impact and consumer review

Context: how to find who a change really affects before you design it, and how to judge a
proposed fix. Read this when you change a value, a table, a field, a notifier, a shared helper or
a contract, when you review a whole app for composition defects, and when you choose between a
targeted and a structural fix.

## Canonical rules
1. List every CONSUMER of the service you change (router, cron or batch entrypoint, harness, other surface), not call sites. Rules that live in the router are missing for every other consumer.
2. For each batch entrypoint, compare the pools and clients it constructs with what the service it calls needs. Prove it with logs from a real run.
3. Trace each wire field producer → consumer with a grep plus a positive control; fields produced and rendered by no component are invisible to producer-side tests.
4. A complete, tested scheduler can have no trigger. Grep its callers repo-wide; check `package.json`, deploy manifests, `instrumentation.ts`, CI crons. A "cron exists" comment is not evidence.
5. A correct ruling becomes a defect when a change routes a NEW consumer through it. Read which consumer its rationale argued for ("a user clicks…"); is the failure loud for the new one?
6. Report that case as "decided for consumer A; this change adds B; does it still hold?", graded a blocker, never "the old decision was wrong". Only the ruling's owner can widen it.
7. Before reusing a table, grep its ORM symbol AND raw name; classify hits: writers (DELETE-then-INSERT?), data readers, METADATA readers (`MAX(created_at)`, `NOT EXISTS`), unique-index dependants.
8. A metadata reader is where reuse breaks silently: a second writer stamping `created_at` can starve a scheduler's backlog cursor while every row stays correct.
9. Adding sources to a shared notifier, digest or template re-scopes every narrative sentence in it. Re-read the body against each new source; prefer a classifier over one universal body.
10. Before re-sourcing a list that feeds a pipeline, grep consumers for `.length ===`, index pairing and set equality. Keep the strict guard (often the only loud failure) and slice the source to match.
11. When slicing a structured source, keep its shared preamble and renumber ordinals contiguously. Anchor an activation predicate on the MAINTAINED signal; let a new, empty column only veto.
12. Never truthiness-test a spreadsheet checkbox (`'FALSE'` is truthy). Join across systems on an explicit key column in the artifact that changes with the relationship; never fuzzy-join tenants.
13. Name the region where work is CLAIMED before it is DELIVERED. Write the invariant: a claimed-but-undelivered item leaves a durable trace. Move config reads before the claim.
14. Check that tuning config is read anywhere (`dedup_days` with no time predicate = permanent dedup). Dead config advertises behaviour reviewers then stop checking.
15. Diff recorded INTENT against live BEHAVIOUR: neither alone shows the defect, the divergence does. Mark a tradeoff "emergent, not decided" when no record shows it was weighed.
16. Reusing a canonical helper reuses its logic, not its cost profile or emptiness assumptions. `count(*)` the new binding and check every coalesced-away term is empty in YOUR data.
17. When the binding is wider, narrow in SQL (window, newest-per-key) and hand the rows to the shared helper, so one definition of the semantics survives. Report both numbers.
18. Some defects exist only in the PRODUCT of two correct constraints (a reclaim predicate and a dedup key scope). Review them in one pass, and check every terminal state has a reclaim path.
19. Ask whether a terminal state was derived from a lossy match; a terminal state on a fallible comparison is an un-reclaimable bet. Look for an existing measurement of the loss.
20. Targeted vs structural fix: walk the structural fix against EACH known instance and say which it covers. If it misses one, the targeted fix is a correctness call, not appetite.
21. Pair a declined structural fix with a falsifiable revisit trigger ("a third instance of this kind"), and substitute cheap changes that do cover real instances (`Omit<T,'k'>`, one shared predicate).
22. Give a distinct fact its own code: a by-design wait is not "indeterminate", and a new reason code the UI still buckets as "retry these" leaves the hazard. Rule the bucketing with the enum.
23. Reference work by SYMBOL (function names), not bare integers; renumbered checklists give "item 7" a second meaning.
24. An adopter's special case is a bug report about the contract. Every "callers should X" needs, in the same breath, a test or type that fails when they don't.
25. If a fix costs more with every row written, deferral is not free: the window where it is free is the window where nothing seems to justify it. Ask what waiting costs in units of data.
26. To add a distinction, KEEP the old code for the case its wording is still true of and ADD a new code; never narrow an old code's meaning retroactively. Grep the repo for a ruling already made.
27. "This is a contract change" is a hypothesis. Read whether the hash hashes an explicit canonical projection, and whether the dedup UNIQUE includes `op_type`; often neither needs changing.
28. Distinguish plan SHAPE from plan CONTENT, and a dedup IDENTITY from a record ADDRESS: the op where they coincide is what hides the op where they don't.
29. Before accepting "widening this projection will mark N rows CHANGED", read the upsert's RETURNING and the revision writer; birth is usually graded separately from mutation.
30. Then find where the exposure really lives (already-stored rows whose projection widens), and specify a read-only dry run that classifies NEW / MOVED / STABLE instead of estimating.
31. Never subtract counts with different denominators. "Fall back to the deeper level" re-commits a stop-at-first-non-empty bug; union is correct, and its migration cost follows from that.
32. Check which fields are in the hash before pricing an enrichment (metadata usually is not). Guard a suppression flag on its wrong polarity ("suppression active when it should not be").
33. Put new data where the DROP decision is made, not where a fetch looks natural; trace which stage excludes items first.
34. Read an agreed before/after number back through the write path. If the "after" needs rows to become NULL and no code path clears anything, the missing clearing mechanism IS the design.
35. With 2+ uncoordinated writers, give each its own column and derive provenance at read time (`CASE WHEN b IS NOT NULL …`). A defaulted `NOT NULL` source marker decays silently.
36. Decide what ABSENCE writes by what the field means: current-state claims clear, immutable dated facts are retained. Clear only on a complete read, guarded on the SOURCE row count (see data-contracts).
37. A comment rots in proportion to what it describes that its author does not own. Anchor on stable referents (tables, not another table's columns); measured facts you own do not rot.
38. When a behaviour changes, grep for the words describing the old behaviour ("NOT FIXED HERE", "interim", "KNOWN RESIDUAL"), not just the identifier. Limitation comments outlive their limitation.
39. Before copying a note to a twin site, re-measure there: two sites can share a hazard and not the property. Docstrings are heritable (clones copy them), so keep time-bound facts out of them.
40. Put a revisit condition in the file whose EDIT trips it, not in the ticket. Document the property the owner will commit to (array order), not the one that is easy to write (a score formula).
41. When you redirect someone to a primary source, you have claimed it is current. When deleting a constant, leave a DO-NOT-RE-ADD note naming the winner and why.

## Anti-patterns to avoid (field-tested subset of PACT's catalogue)
- **Database as integration point:** several apps writing one table with no owner. Symptom in the field: a second writer silently breaking a scheduler cursor (rule 8).
- **Distributed monolith:** services sharing a database or needing coordinated deploys. Check the deploy manifests before calling a split "independent".
- **Router-owned semantics:** a safety or availability rule in the HTTP layer that a batch consumer bypasses (rule 1). Put the rule in the service.
- **Leaky abstraction:** a vendor error code or exception type crossing a boundary unchanged; translate at the adapter, and re-check reachable errors when the callee gains a dependency.
- **Lava flow and dead config:** unused code or a config key with zero read-sites that still advertises behaviour (rule 14). Prove the zero with a positive control, then delete with a tombstone.
- **Golden hammer / mirrored template:** porting machinery the new case does not need (see data-contracts rule on mirrored integrations).

## Examples
- 1: a digest cron called the service directly, opened one of two DB pools, and closed `ok` with 29/29 knowledge-base queries failing.
- 5: "keep a non-existent filter value and show zero rows" was right for a deep link and wrong for an hourly Slack digest.
- 10: moving a client switch to a sheet while passing the full brief to the model failed a strict count guard on every request.
- 16: a canonical fold helper would have hauled about 90,000 rows per request on a wider binding.
- 34: "fill goes 434 → 229" required a clearing pass that existed nowhere in the repo.
