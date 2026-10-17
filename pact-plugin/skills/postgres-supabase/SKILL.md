---
name: postgres-supabase
description: "Use when writing or reviewing Postgres or Supabase schema, migrations, grants, RLS, RPCs, triggers, views, query plans, typegen or drivers (postgres-js, asyncpg, drizzle, pg-boss, FTS): catalog checks over grep, default-privilege and anon traps, proving a policy or constraint fires."
---

# postgres-supabase

Context: keep Postgres and Supabase work from shipping schema, grants or queries that look correct
and silently do something else. Every rule here came from a measured failure.
The generic "can this test fail" method lives in
`repacted:verification-discipline`.

## Workflow
1. **Ask the catalog, not the text.** Before trusting a schema claim, query `pg_class`, `pg_proc`, `pg_constraint`, `pg_attrdef`, `pg_default_acl` and `pg_roles.rolconfig`. Migration files, ORM models and comments are claims about the database, not the database. → [migrations-and-typegen](references/migrations-and-typegen.md)
2. **For grants and RLS, enumerate who can reach the object**: `anon`, `authenticated`, `service_role`, `PUBLIC`, the owner, definer views and `SECURITY DEFINER` functions. Then PROVE it as that role with a positive and a negative control in the same run (`BEGIN; SET LOCAL ROLE anon; ...; ROLLBACK;`). → [roles-grants-and-rls](references/roles-grants-and-rls.md)
3. **On Supabase, `revoke ... from public` never revokes `anon`.** The platform grants `anon` directly, and `drop` + `create` re-grants it. Put `revoke ... from public, anon` in the SAME migration as the create, and audit the class with `has_function_privilege('anon', oid, 'EXECUTE')` over `pg_proc`. NEVER `GRANT ALL ... TO anon` on a custom schema. → [supabase-platform](references/supabase-platform.md)
4. **Reproduce the defect before you believe the fix.** A scratch cluster without the platform's default privileges, superuser rules and `pg_hba` cannot show a grant bug, so its green means nothing. The repro is faithful when the pre-fix ACL matches the platform's byte for byte.
5. **For a migration**: dry-apply it inside a rolled-back transaction with negative-before, positive-after, legacy-still-accepted and writer's-own-row cases. After a Supabase MCP `apply_migration`, read the stamped version back and name the file to match. Regenerate the types. → [migrations-and-typegen](references/migrations-and-typegen.md)
6. **For a query**: check NULLs in unique and upsert keys, a unique tiebreaker under LIMIT/OFFSET, latest-row queries across several writers, derived-column buckets, and the plan with BUFFERS on a selective query. → [query-correctness-and-perf](references/query-correctness-and-perf.md)
7. **For drivers, ORMs, pg-boss or FTS**, check the traps before coding: raw SQL drops decoders, asyncpg returns jsonb as `str`, drizzle arrays become row constructors, pg-boss v10 drops jobs for missing queues, `ts_headline` is not a match. → [drivers-orm-queues-fts](references/drivers-orm-queues-fts.md)
8. **Report the claim with its role, environment and as-of**: which role, which database (local, branch or prod), dry-apply or tracked apply, and when. Example: "As `anon` via PostgREST: `rpc/admin_stats` → 401; control as `service_role` → 12 rows; `has_function_privilege('anon','admin_stats()','execute')` = false (prod, 2026-09-26)." (repacted:verification-discipline).

## Map
- [roles-grants-and-rls](references/roles-grants-and-rls.md): default privileges, anon and PUBLIC, definer views and functions, column privileges, managed-PG role traps, multi-tenant RLS review, signup triggers, shared projects.
- [supabase-platform](references/supabase-platform.md): the anon default-grant trap with its known-negative control, RPC name-set resolution, exposing or hiding a schema from PostgREST.
- [migrations-and-typegen](references/migrations-and-typegen.md): MCP version stamping, rolled-back dry-applies, enum and CHECK traps, generated columns, pg_restore, drift checks, drizzle-kit push, postgres.js transactions, typegen and ssr skew.
- [query-correctness-and-perf](references/query-correctness-and-perf.md): pagination, NULL upsert keys, latest-per-group, derived buckets, jsonb_set, plans, TOAST, window counts, and rotating-refresh-token single-flight.
- [drivers-orm-queues-fts](references/drivers-orm-queues-fts.md): postgres.js, asyncpg, drizzle, NUL bytes, pg-boss v10, tsquery composition, tsvector triggers, regex vs FTS.

## Related skills
- Credential rules, auth review and incident checklists: `repacted:security-review-patterns` (go there first for auth code; come here for the grant and RLS layer).
- The controls method (positive and negative controls, reproducing a defect, vacuous tests): `repacted:verification-discipline`.
- Schema design beyond correctness: `repacted:architecture-heuristics`.

## Refine
At the end of a session that used this skill, run [self-refine](protocols/self-refine.md) and record what it changed.
