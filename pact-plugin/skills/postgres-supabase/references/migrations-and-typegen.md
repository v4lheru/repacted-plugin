# Migrations, constraints, generated columns and typegen

Context: how to author, apply and prove a Postgres or Supabase migration, and keep the generated
types in step. Read this before you write DDL, a CHECK, a generated column or a trigger, apply a
migration through the Supabase MCP or a script, or build a schema-drift check.

## Canonical rules
1. Ask the catalog, not the migration text: `pg_class`, `pg_constraint`, `pg_proc`, `pg_attrdef`, `pg_class.reloptions`. Reading the SQL is not verification.
2. Supabase MCP `apply_migration(name, query)` stamps its OWN version at apply time. Apply, read the version back from `supabase_migrations.schema_migrations`, THEN name the local file to match.
3. If the file name and the ledger disagree, `supabase migration list` shows the file as unapplied and `db push` re-runs it. After a reconcile, prove a dry-run push is a no-op after the next MCP apply too.
4. The SQL you pass to `apply_migration` is what is stored. Paste the file verbatim, or reconcile the file afterwards.
5. Check the file against the ledger with `md5(rtrim(array_to_string(statements, E'\n'), E' \n'))`. Compare sizes with `octet_length`, not `length`. Strip whitespace and re-hash before calling a mismatch corruption.
6. Prefer renaming local files over `supabase migration repair` on a SHARED ledger: a rename writes nothing remote.
7. Edit a migration in place only if it was NEVER applied anywhere (no branch, teammate or scratch DB). Prove it: 0 commits behind main, no upstream, file absent from main's history.
8. A migration's `COMMENT ON` can be made false by the next migration. The later one re-issues the comment (it replaces), and the repo copy of the earlier file is fixed too.
9. Prove a constraint with a rolled-back dry-apply: (a) the value is REJECTED before, (b) accepted after, (c) every legacy value still accepted, (d) a bogus value still rejected, (e) the live writer's own row shape still inserts.
10. Locate the REAL enforcement point before changing a vocabulary: column CHECK, function body, TS/zod union, view. A parameter comment saying "validated by the CHECK" is not a CHECK.
11. Dry-apply via MCP `execute_sql`: collect results in a temp table and SELECT it before `rollback`; wrap each negative test in `begin ... exception ... end` in a `do $p$` block.
12. When you cannot trust the tool's transaction handling, end a `do` block with `raise exception 'DRYRUN: %', report;`: the abort guarantees the rollback. Re-read the catalog after to confirm nothing leaked.
13. A checked-in rolled-back check signals PASS by an exact success token; its exit status is always an error. No token (a connection error included) is FAIL.
14. A dry-apply proves constraint BEHAVIOUR; only a tracked apply proves the migration APPLIES. Record which statements the replay executed, and never let one stand in for the other.
15. `CREATE OR REPLACE FUNCTION` cannot widen `RETURNS TABLE (...)`. It needs DROP + CREATE in one migration, and the drop takes the ACL: restate every GRANT and REVOKE (roles-grants-and-rls rules 2-3).
16. "Not in the INSERT column list" is not "NULL": read `pg_attrdef`, triggers and generated expressions before constraining a column. `CHECK (raw IS NULL)` on a `DEFAULT '{}'` column rejects every insert.
17. A false "verified before applying" header is worse than none. When correcting it, rewrite the header, say you did, and keep the DDL byte-identical to what ran.
18. Before adding an enum value, read `pg_constraint` (and generated columns, partial-index predicates, RLS) for CHECKs that enumerate every member with no ELSE. `ADD VALUE` succeeds; the first INSERT fails, after deploy.
19. Ship `ADD VALUE` and the constraint rewrite in the same migration, with the CHECK naming ONLY already-committed literals plus an `ELSE` arm. Naming the new value fails with 55P04 (MCP wraps the file in one transaction).
20. Test the new value only post-commit, in a rolled-back transaction (55P04 blocks it pre-apply). Enum labels cannot be dropped; place them with `ADD VALUE ... BEFORE`.
21. Expect a vocabulary to grow? Use a lookup table plus FK: a new status is an INSERT, and the table can hold each value's meaning and flags. Keep a CHECK only for real cross-column rules.
22. Guard design: first ask whether the bad case OCCURS (an absence-shaped query, never a top-N listing), then pick the form. Match a known foreign sentinel by equality, not a range around it.
23. Prefer a guard whose failure mode is going inert over one that can start rejecting real data. State its weakness at the constraint site. Prove its narrowness with acceptances, not only the rejection.
24. Recreating a CHECK takes ACCESS EXCLUSIVE plus a full scan. On a large table use `ADD CONSTRAINT ... NOT VALID` then `VALIDATE CONSTRAINT`.
25. Replace a derive-on-write trigger with `GENERATED ALWAYS AS (...) STORED` plus a TOTAL, non-STRICT mapping function: no branch can return having set nothing, and writers cannot name the column.
26. `ALTER COLUMN ... SET NOT NULL` on the generated column is the population proof: it validates every row at apply time.
27. Replacing a generated column's function changes future writes only: repair with `UPDATE t SET col = col`, detect with `stored IS DISTINCT FROM fn(input)`, store the derivation KEY beside the value.
28. Generated expressions must be IMMUTABLE (`pg_proc.provolatile`): `convert_to()` is STABLE (42P17). Use pgcrypto `digest(x,'sha256')` (in `extensions` on Supabase) or an immutable SQL wrapper.
29. If a generated hash is a dedup key, prove value parity with the old expression before switching, or every future row is re-keyed and dedup silently stops.
30. A CHECK ACCEPTS a STABLE expression: `h = date_trunc('hour', h)` on timestamptz varies with the session TimeZone. Pin with `AT TIME ZONE 'UTC'` both ways and test under a half-hour zone too.
31. A generated column that calls `f()`, whose body calls `g()` unqualified, breaks `pg_restore` (`search_path=''`) at CREATE TABLE. Qualify `public.g()` in the body; `SET search_path` on f disables inlining. Prove by dump/restore.
32. Schema-qualifying an object breaks fixtures that apply `0*.sql` into a per-run schema via `search_path`. Run every pg test against the MERGED tree, and isolate by removing one migration.
33. Verify DB claims on a throwaway Postgres with the real migrations. Prove fixture fidelity first by matching production on several independent catalog axes; that match is your positive control.
34. Supabase-shaped bootstrap on vanilla PG: create `anon`, `authenticated`, `service_role` and app roles (NOLOGIN stubs), schema `extensions`, the extensions into it, and the database `search_path`. Keep it re-runnable.
35. A scratchpad path can exceed the Unix socket limit (about a hundred bytes): keep the data dir there and pass `-k /tmp/<short>` for the socket.
36. Every `DO` assertion gets a negative control: clean passes, injected violation raises, reverted passes again. "It applied without raising" proves nothing.
37. Shared-DB pg test files interfere under file parallelism: run the file alone, the feature's files, and the full suite single-worker, and report which. Rebuild at BASE before blaming a feature.
38. Estimate fixture cost from `\d table` after applying migrations: generated columns can make a plain INSERT enough; triggers and NOT NULL without defaults decide the rest.
39. A psql loop without `-v ON_ERROR_STOP=1` swallows failed migrations. `$$` inside an npm script is expanded to the PID: put DO blocks in a `.sql` file.
40. Drift checks: use `pg_attribute` + `format_type(atttypid, atttypmod)`, not `information_schema.columns` (enums become USER-DEFINED, numeric precision drops). Compare `indexdef` bodies, not index names.
41. `reltuples` is an estimate (-1 if never analysed); prove emptiness with `SELECT EXISTS (SELECT 1 FROM t)`. A drift check without prod access proves only chain-vs-model agreement: print that.
42. `drizzle-kit push` and `generate` DROP tables and indexes `schema.ts` does not declare. Guard in `drizzle.config.ts` (every invocation loads it) by an exact database-NAME allowlist, not host.
43. Add `tablesFilter: ["!other_chain_table", ...]` to bound the blast radius, and still check the INDEX count: push also drops hand-written indexes on managed tables.
44. A migration file with its own `BEGIN`/`COMMIT` run through postgres.js `sql.unsafe` needs `max: 1`; on a pool it throws AFTER the migration committed.
45. Inside your transaction, the file's `BEGIN` is only a WARNING and its `COMMIT` commits YOURS early. Detect transaction control statement-aware (not inside dollar bodies) and record the protocol per file.
46. Prove a migration's transaction is real: sabotage its own post-condition so it raises after the work, and confirm the table comes back byte-identical.
47. Regenerate types after every migration (`supabase gen types typescript`). If you must hand-patch, update `Row`, `Insert` and `Update`, and expect hand-built fixtures to need the field.
48. While types lag a new column, cast only that value (`as unknown as T`); jsonb arrays need `as unknown as Json`. Remove the cast after regenerating.
49. `@supabase/ssr` 0.5.x with `supabase-js` 2.100 or later types every `.from()` as `never`. Check the version pair first; bump ssr to 0.6+ or use canonical typegen (it carries `__InternalSupabase`).
50. For the ssr skew, `Relationships: []` alone is a symptom fix. Tells: every agent's files fail alike, and an explicit `from<'t', T>` fails "does not satisfy never".

## Examples
- 2: files named from the local clock were registered with versions about ten hours earlier; an older initial-schema file was 53 seconds off its ledger row (verified 2026-08-20, Supabase MCP).
- 10: a brief said "extend the function's CHECK"; the only gate was the column CHECK.
- 14: a migration believed "dry-apply validated on live PG" failed at the real apply on a non-immutable generated expression.
- 16: `CHECK (raw IS NULL)` on a `NOT NULL DEFAULT '{}'` column was shipped as "verified" and rejected the writer's own rows.
- 18, 19: a fifth alert type became uninsertable behind a four-arm CHECK; the fix that named the new value failed 55P04.
- 22: "zero occurrences" of a vendor's far-future sentinel came from a top-6 listing; 2 of 555 rows held it.
- 30: the bare `date_trunc` CHECK rejected an on-the-hour row only under Asia/Kolkata.
- 31: `pg_restore` failed at CREATE TABLE on PG 16.14 while migrations and live queries passed.
- 32: base 21 passed; merged 0 passed and 24 errors; merged minus the qualifying migration 24 passed.
- 39: a test-setup loop without ON_ERROR_STOP had built 9 of 13 tables for months.
- 42: `drizzle-kit push --force` took 13 tables and 57 indexes to 9 and 41, dropping a session-revocation table (revoked sessions became valid).
- 44: a runner printed ERROR and exited non-zero after a destructive dedup migration had committed.
