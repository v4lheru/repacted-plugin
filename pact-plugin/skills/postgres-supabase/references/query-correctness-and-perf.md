# Query correctness and performance

Context: SQL that returns a confident wrong answer with no error (pagination, NULLs in keys,
latest-row queries, derived columns, jsonb updates) and how to read a plan without fooling yourself.
Read this before writing or reviewing a paged, upserting, latest-per-group or FTS query, or before
judging whether an index is used. Rotating-token single-flight is at the end.

## Canonical rules
1. LIMIT/OFFSET needs a TOTAL order: end every paged ORDER BY with a unique column (the PK). Ties reorder between pages and silently duplicate and drop rows.
2. Test paging by fetching every page and asserting `len(ids) == len(set(ids))`. Anything aggregated client-side over the pages (sums, averages) inherits the loss.
3. A route is masked while rows fit one page: report it "armed, not firing", never clean. Exposure is a property of the current row count and page size, not of the query.
4. Severity is tie-group size times page size: measure `count(*) GROUP BY <sort key>` first. Enumerate offset sites by grepping `.offset(` / `OFFSET`, not by reading files one by one.
5. Record the literal ORDER BY, page size and offsets beside any drift figure. Two replays with different keys disagreeing is an instrument difference; the guessed key's number is a lower bound.
6. Add the tiebreaker in the ONE order-by builder every branch returns through, so future sort keys inherit it. Prefer keyset pagination for large sets.
7. A NULLable column in a UNIQUE index disables `ON CONFLICT` for NULL rows: every run INSERTs. Store `''`, index `COALESCE(col,'')`, use a partial index, or `NULLS NOT DISTINCT` (PG15+).
8. Detect NULL-key duplication with a histogram of per-key counts; a tail near runs-per-period is the signature. Split by `col IS NULL` for a free control. JS `x || null` feeding a key is the usual source.
8a. Postgres uses a PARTIAL unique index as the `ON CONFLICT` arbiter only when the INSERT repeats its predicate; supabase-js `.upsert({onConflict})` cannot, and fails 42P10. Dedupe in code with a plain INSERT, keeping the index as a loud 23505 backstop.
9. Fixing that index does not fix history: for cumulative snapshot rows, keep the latest `created_at` per key; never sum or average them.
10. A UNIQUE index on an optional enrichment column, inserted in the PRIMARY record's transaction, turns a duplicate (23505) into loss of the primary record. Check every index before filling a column.
11. Do not paper over it with `ON CONFLICT DO NOTHING` (hides the missing row behind success) or by reusing the matched parent (that is entity resolution). Leave the indexed column NULL and escalate the merge design.
12. Latest-row across several writers that fill DISJOINT columns must partition by writer (`DISTINCT ON (id, source)`), then merge the groups. A pooled `MAX(date)` nulls every other writer's columns.
13. Triage a latest-row site by writer count: `SELECT source, count(col) FROM t GROUP BY 1`. One writer is latent by construction; two or more is live. Fix both, price them differently.
14. Build a latest-row oracle in two independent spellings (window function and correlated MAX) and require agreement; one query checked against itself is green by construction.
15. JS `new Map(rows.map(r => [r.id, r]))` keeps only the LAST row per id. A `??` chain across disjoint column groups returns 0 forever once a writer stores 0 instead of NULL.
16. Choose `DISTINCT ON` vs `MAX() OVER (PARTITION BY ...)` on what the UNIQUE constraints PERMIT, not on today's zero ties. If a tie is possible, use the window form and filter `col = max_col`.
17. Prove the tie guard is armed: mutate the query to `DISTINCT ON` and confirm the test goes red.
18. Equality on a many-to-one derived column (language to regconfig, country to region, `COALESCE(x,'default')`) is a BUCKET test. Probe the mapping: `SELECT fn('it') = fn('ja')`.
19. The raw source column is usually un-normalised (`en-US` vs `en`), so scoping on it under-matches too. Scope on a generated normalised identity key (`lang_key = fn($1)`), then drop the redundant term.
20. Ask what a column ADMITS (its fan-in and cardinality), not only what it MEANS, before filtering on it.
21. Branch on the evidence column (`occurred_at IS NOT NULL`), not on a source enum that implies it today. Implication CHECKs (`col <> 'X' OR cond`) say nothing about future enum values.
22. When you widen a flag's expression, its COMMENT records the new meaning while the NAME lags. Rename mechanism-named flags (`is_observed_`, `is_synced_`) to the property in the same migration.
23. Make an ordering key a stored generated column, not an expression index that every consumer must reproduce exactly in ORDER BY.
24. A composite FK `(tenant_id, parent_id)` needs an exactly matching `UNIQUE (tenant_id, id)` on the parent (an id-only PK does not count). Add it in its own earlier prereq migration.
25. Scope that prereq to parents where cross-tenant forgery matters; a hot 1:1 child already isolated through its parent can keep a single-column FK plus RLS. Prove the FK rejects a cross-tenant insert.
26. `jsonb_set(t, '{a,b}', v, true)` silently no-ops when parent `a` is missing (UPDATE 1, nothing changed). Seed the parent with a nested `jsonb_set` in the same statement.
27. Judge index use on a selective query (a few percent of rows), never one matching a third of the table. Get the default plan, the forced plan (`SET enable_seqscan=off`) and BUFFERS for both.
28. The seq-scan cost ignores TOAST: compare `pg_relation_size` with the TOAST relation's size. A correct GIN index can lose on a small table with a wide TOASTed column.
29. Do not tune global planner settings (`random_page_cost`) to make your own acceptance check green; they belong to the database's owner. Report the measurement and recommend.
30. Near-identical twin queries can get opposite plans from the same indexes. Diff their `EXPLAIN (ANALYZE, BUFFERS)` with the REAL bound parameters (drizzle `.toSQL()`), and compare buffers, not row counts.
31. Replace a per-row correlated subquery with one window pass (`MAX() OVER (PARTITION BY ...)`). That removes the planner's freedom to choose badly, instead of coaxing it.
32. `COUNT(*) OVER ()` makes heavy SELECT-list expressions run for EVERY matched row before LIMIT. Page in an inner CTE (count, order, limit), then join back by PK for the heavy columns.
33. A CTE referenced twice, or a computed column used in WHERE and in `ts_rank`, is inlined and evaluated twice: use `AS MATERIALIZED`. Find heavy expressions with `EXPLAIN (ANALYZE, VERBOSE)`.
34. Prove a rewrite equivalent with an md5 over `string_agg` of (id, key columns, md5 of heavy output) across several pages, not page 1 only.
35. `pg_stat_xact_*` return this backend's UNFLUSHED counts, which carry across transactions (PG16). Measure writes as before/after deltas; assert HOT updates only as `>= 1`.

### Rotating refresh tokens (single-flight)
36. A rotating, single-use refresh token with reuse detection must be single-flighted across `[read token, POST /token, persist rotated token]`. A second use of the same token kills the whole family.
37. An RPC or supabase-js/PostgREST call cannot hold a lock across an HTTP call your app makes. Use a raw Postgres connection with a transaction held open across the call.
38. Inside that transaction, either `pg_advisory_xact_lock(<fixed bigint>)` or `SELECT ... FOR UPDATE` on the token row works. Add a version CAS on the final UPDATE, and an in-process mutex as the same-process fast path.
39. Throttling vendor API calls and single-flighting the refresh are separate primitives. Do not hold one coarse lock across a multi-minute job: a dropped connection releases it mid-run.
40. Never restore the token row from a backup (a stale token kills the family). On `invalid_grant`: mark `needs_reauth`, alert and STOP; no retry. Recovery is the one-time consent bootstrap.
41. Encrypt tokens in the app layer (AES-256-GCM, key outside the DB); pgcrypto puts the key in SQL text, and so in `pg_stat_statements` and logs. Decrypt only in the worker.
42. Keep the PKCE verifier on the web tier and pass it in the job payload; give the secret-holding worker no inbound surface. Credit-metered vendors need their own budget gate beside any USD ceiling.

## Examples
- 1: 6 pages of 500 returned 2,694 rows but 2,693 distinct ids; appending `id` fixed it exactly.
- 3, 4: a route whose SQL looked worst lost nothing at one page but 13.9% at its own default limit; an ordinary-looking route lost 82 of 1,894 rows (97.8% of rows in tie groups).
- 7, 8: a twice-daily sync with an `|| null` key showed 244,924 registrations against a true 13,155 (18.6x).
- 10: a partial unique index on email rejected a valid second record when one person signed up for two items.
- 12: a pooled `MAX(snapshot_date)` dropped a row holding 10,472 video views.
- 16: production had 0 ties on both twin queries; one was tie-free by a UNIQUE index, the other could tie any day.
- 18, 19: `tsv_config = 'simple'` matched six languages at once; the raw `language = 'en'` fix then excluded `en-US`.
- 22: a backfill made 100% of correctly imported rows FALSE on the flag consumers were told to filter by, and a partial index hid them.
- 26: every per-step completion write was dropped for weeks while sibling updates worked.
- 28: forcing the GIN index was 12.6x faster (144 vs 6,607 buffers); estimates differed by 0.6%; the main heap was 1.5 MB and the TOAST 51 MB.
- 30, 31: `=` vs `!=` twins ran 16.4 s vs 1.47 s; one window pass took 15.5 s to 262 ms with no new index.
- 32, 33: previews computed for 295 full bodies took 133 ms; paging first took 20 ms. MATERIALIZED took 628 to 390 ms (verified 2026-09-22).
