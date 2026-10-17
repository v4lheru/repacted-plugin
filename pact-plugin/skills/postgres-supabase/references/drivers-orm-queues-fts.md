# Drivers, ORMs, queues and full-text search

Context: what the Postgres drivers and ORMs silently do to your values, how pg-boss v10 drops or
breaks queues, and how Postgres FTS really behaves. Read this before you move a query from an ORM
builder to raw SQL, splat a query result into a model, interpolate into a drizzle `sql` template,
touch pg-boss queues, or design or judge a full-text search.

## Canonical rules
1. Moving a query from the ORM builder to raw SQL (`db.execute(sql...)`) drops the per-column decoders: bigint arrives as a string and timestamptz as a space-separated non-ISO literal. No type error follows.
2. A type annotation on a raw-SQL result is an assertion `tsc` cannot check. Diff one real row of the old and new responses field by field before calling them equivalent.
3. Render timestamps in SQL as ISO (`to_char(col AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')`) and coerce numbers once at the boundary (`v == null ? null : Number(v)`).
4. postgres.js returns int, bigint and numeric as STRINGS, so `a + b` concatenates. Drizzle's typed builder coerces; ad-hoc scripts and `db.execute` do not.
5. A uniform shift by exactly one decimal place across unrelated rows indicts the instrument (string concatenation), not the data.
6. asyncpg returns `json`/`jsonb` as `str` unless a codec is registered (`set_type_codec('jsonb', ..., schema='pg_catalog')`, usually in the pool `init=`). A `->>` text sibling that decodes fine is your control.
7. The DB round trip is a producer in its own right: test the READ-side path that feeds the call site, not the same-named write-side producer.
8. Postgres rejects NUL (0x00) in text and jsonb. After `json.dumps` the NUL is a six-character escape, so stripping the raw byte from the dumped string is a no-op.
9. Strip NULs recursively from the live structures (str, dict, list, tuple) BEFORE serialising, keep the surrounding text, and read the error DETAIL: "unsupported Unicode escape sequence" means the jsonb path.
10. Writing a NUL escape in tool input (an Edit body, JSON metadata) can land a literal NUL byte in the file. Count `b'\x00'` bytes after editing; spell it `chr(0)` in test sources.
11. A JS array interpolated into a drizzle `sql` template expands to `($1, ..., $n)`, a row constructor; `::bigint[]` then fails 42846. Bind it as one value with `sql.param(arr)`. `ARRAY[${arr}]` fails the same way.
12. `DrizzleQueryError` hides the SQLSTATE: read `err.cause` for the `PostgresError` code. Settle placeholder counts from the rendered SQL (`dialect.sqlToQuery(expr)`), not by reasoning.
13. A mocked query builder accepts any SQL by construction: no test that mocks the db client can catch a binding defect.
14. Inside a correlated drizzle subquery over tables sharing column names, `${table.col}` can drop its qualifier. Use `sql.raw('"table"."col"')`.
15. You cannot bind inside a SQL string literal. Share an interval constant as `${N} * INTERVAL '1 day'` (the parameter is inferred as float8). That proves the expression resolves, not that the rows are right.
16. Filtering a parent list by child properties needs BOTH the filter threaded into every per-row aggregate subquery AND a parent `EXISTS (... child ... <filter>)`, or zero-match parents stay with zero columns.
17. A sibling endpoint (for example a CSV export) that must return the same rows imports one shared filter builder. Copy each filter's operator verbatim (exact `=` vs `ILIKE` substring) and check counts are equal.
18. pg-boss v10 `send()` to a queue with no row in `<schema>.queue` inserts nothing and returns null. `work()` does not create the row, so a "worker registered" log proves nothing.
19. Call `createQueue(name)` for every registered handler at boot. Diagnose by comparing handler names with `SELECT name FROM pgboss.queue`.
20. pg-boss v10 `create_queue` returns early on conflict, BEFORE the partition DDL. A boot "ensure queue" loop heals a missing ROW and never a missing PARTITION (send fails 23514 "no partition").
21. `delete_queue` throws 22004 when `partition_name` is NULL, and deleting a queue row CASCADE-deletes its schedule and subscriptions. Delete-then-create is not a repair.
22. Repair in one transaction: capture schedule and subscription rows, delete the queue row, `create_queue`, re-insert the rows, re-check, COMMIT; on failure, roll back and log.
23. Detect a missing partition via `pg_inherits` with `pg_get_expr(relpartbound, oid) = format('FOR VALUES IN (%L)', name)`, not via `partition_name IS NULL`. Partition names are `'j' || encode(sha224(name::bytea),'hex')`.
24. pg-boss needs LISTEN/NOTIFY: connect through the direct or session connection, not the transaction pooler.
25. pg-boss v10 renamed the `work()` concurrency option `teamSize` to `batchSize`. Read the installed `types.d.ts` (and `src/plans.js`) before configuring, since specs and older notes lag.
26. `ts_headline` highlights every query lexeme regardless of AND, phrase or NOT, so a highlight is not a match. Gate on `to_tsvector(cfg, field) @@ q` (vector computed once per document in a MATERIALIZED CTE).
27. To tell two query variants apart in a test, build a fixture where only `@@` discriminates (for example a title-only document gated on an `in_title @@` flag).
28. `websearch_to_tsquery` ignores parentheses and `&` binds tighter than `|`. Compose alternation by OR-ing separately parsed queries: `websearch_to_tsquery(cfg,$a) || websearch_to_tsquery(cfg,$b)`.
29. Emit that second arm only when the spellings differ, so unchanged queries stay byte-identical for regression harnesses.
30. Name the engine version: `regexp_count` is PG15+. On mixed 14/16 stores, write one portable form (`length(x) - length(regexp_replace(...))`).
31. Before building on an existing tsvector column, read the maintenance trigger body (`pg_get_functiondef`). A `CASE lang ... ELSE RETURN NEW` never indexes the other languages, and coverage stats look like a backfill gap.
32. The same trigger often already applies `setweight` (title A, synonyms B, body C): `ts_rank('{0.1,0.2,0.4,1.0}', tsv, q)` gives title-first ranking, and it may reveal an unused synonyms column.
33. `ts_rank` saturates at 1.0, so ties are common: add a deterministic tiebreaker (query-correctness-and-perf rule 1). Ship recall increases in the same release as ranking.
34. Regex `~*` "beating" FTS on raw HTML is usually the regex matching inside `href`, `alt` and `src`. Decompose the difference with `regexp_replace(content,'<[^>]+>','','g') ~* 'term'` before accepting a recall claim.
35. `to_tsvector` discards HTML tags on its own (the `tag` token maps to no dictionary): no stripping step is needed. URLs survive as whole `url` tokens.
36. Do not union FTS with the old regex "for recall": it re-imports the markup noise and forces the seq scan. Keep a substring arm only for identifier-shaped queries (`@`, `/`, `://`, a bare domain, quoted).

## Examples
- 1: `bigint(..., {mode:'number'})` came back as a quoted string, and timestamptz as `"2026-08-20 14:01:09.24+00"`, after a move to `db.execute` (drizzle + postgres-js).
- 5: a verification script reported about 30 "recovered" values (700 for 70, 4110 for 411); coercing with `Number()` gave the true 4.
- 6, 7: every request whose results carried a jsonb media row returned 500 while 1,652 backend tests were green.
- 11: 722 ids: `sql.param` used 1 placeholder, `notInArray()` 722 (toward the 65,535 ceiling), `ARRAY[${arr}]` still failed 42846 (PG16).
- 17: comma-joined product cells: exact `=` matched 0 partners, `ILIKE` substring 340.
- 18: three pipeline branches added after a v9 to v10 upgrade had live workers and no queue rows; every send was dropped.
- 20, 22: a queue left without a partition failed nightly while the self-heal logged success; the transactional repair took it from 16 to 17 attached partitions with its schedule intact (verified 2026-09-03, pg-boss 10).
- 28: `"co pilot or copilot is not available"` parsed as `(co & pilot) | (copilot & available)`.
- 31: 5 of 15 languages were indexed and the other 10 never would be (40% of the corpus); regex found 2 rows for an abbreviation, FTS 7 via the synonyms field.
- 34, 36: regex 548 vs FTS 417 rows; 122 of the 131 "lost" matched only inside markup and none were prose. FTS 6.3 ms, regex 44.6 ms, union 39.5 ms.
