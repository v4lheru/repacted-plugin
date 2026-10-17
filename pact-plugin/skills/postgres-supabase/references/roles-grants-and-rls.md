# Roles, grants and RLS

Context: who can actually reach a Postgres or Supabase object, and how to prove it. Read this
before you write or review grants, default privileges, RLS policies, views, `SECURITY DEFINER`
functions or a lockdown migration, and before you call an anon key "safe to publish".

## Canonical rules
1. `REVOKE ... FROM PUBLIC` does not revoke `anon`. Supabase's default privileges grant `anon`, `authenticated` and `service_role` DIRECTLY. Name every role in the revoke.
2. `DROP` + `CREATE` makes a new object, so the default privileges re-apply and `anon` is re-granted. `CREATE OR REPLACE` on an existing function keeps its ACL.
3. The revoke travels WITH the create, in the same migration. A revoke in a later migration leaves every halted, partial or branch-from-prefix apply open.
4. Per-schema `ALTER DEFAULT PRIVILEGES ... IN SCHEMA s REVOKE` cannot remove a global or built-in default (function EXECUTE to PUBLIC). It undoes only a matching per-schema GRANT.
5. A global `ALTER DEFAULT PRIVILEGES FOR ROLE r REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC` (no `IN SCHEMA`) does work for functions `r` creates later.
6. Built-in defaults are asymmetric: tables and sequences grant PUBLIC nothing, functions grant PUBLIC EXECUTE. The tables half of a lockdown block is a no-op; the functions half is inert.
7. What works for existing functions is an explicit `REVOKE ALL ON ALL FUNCTIONS IN SCHEMA s FROM PUBLIC, anon, authenticated`. Re-issue it in each migration that adds a function.
8. Default ACLs are keyed to the CREATING role (`pg_default_acl.defaclrole`). Check the entry for the role that actually applies your migrations; `public` often has two.
9. On Supabase, `public`, `storage`, `graphql` and `graphql_public` default-grant `anon` full table rights. A table created there is anon-writable with no GRANT line in the diff.
10. A private app schema usually has no default ACL, so its tables are private by default. One missing `schema.` prefix, or a `search_path` that resolves to `public`, undoes that.
11. Custom schema that the app must reach: grant `USAGE` plus the SPECIFIC table privileges RLS protects. NEVER `GRANT ALL ... TO anon`.
12. Revoke `EXECUTE` on every `SECURITY DEFINER` function from `anon` and `PUBLIC` unless unauthenticated callers are intended. A definer function bypasses RLS by construction.
13. Report an anon-executable definer function as its own finding, separate from a caller-rights one, where RLS still stands in front.
14. `service_role` bypasses RLS but still needs `USAGE` on a custom schema.
15. A view runs with its OWNER's privileges unless created `WITH (security_invoker = true)` (PG15+). A definer view over RLS tables voids every table-grant conclusion for that path.
16. Put the view rule in a catalog assertion (`pg_class.reloptions` for `relkind='v'`) so the next view cannot reintroduce it. Fix or scope the existing views first.
17. Never ship an assertion that fails on day one: nobody acts on an alarm that is already red.
18. Assert column write privilege with `has_column_privilege(role, table, col, 'UPDATE')`. A NULL `attacl` means "inherits the table grant", not "no grant".
19. An `aclexplode(attacl)` guard is blind to `GRANT UPDATE ON TABLE`, which changes no column ACL. `information_schema.column_privileges` shows only grants involving the current role.
20. Anchor every privilege assertion: count the target columns or functions found (exact number), and check the grant is neither widened nor narrowed.
21. Scope a migration's "nobody has DELETE" check to the app role. The table owner holds all privileges implicitly, and on Supabase migrations run AS the owner, so the broad check aborts.
22. `information_schema.table_privileges` has no `table_owner` column. Name the role, and add a second assertion for `anon`/`authenticated`/`PUBLIC`.
23. `GRANT ALL ON DATABASE` gives CONNECT, CREATE and TEMP only. Make the app role own its objects (`createdb -O app_role`, migrate as it) rather than chasing four grant statements.
24. `default_transaction_read_only` is a session GUC the caller can undo. Read-only is enforced only by a role whose grants lack INSERT/UPDATE/DELETE.
25. `GRANT CONNECT ON DATABASE x` does not confine a role to `x`: PUBLIC holds CONNECT elsewhere. Scoping needs a REVOKE.
26. "password authentication failed for user X" is also what Postgres says when role X does not exist. Check `pg_roles` for the role before debugging the secret.
27. A scratch cluster reproduces the ENGINE, not the PLATFORM: local superuser, `trust` pg_hba, bare `create role anon` with no default privileges. Replicate the platform's ADP first.
28. Reproduce the DEFECT before you believe the FIX. The tell of a faithful repro is a pre-fix `proacl` byte-identical to the one measured on the real platform.
29. On managed Postgres (CloudSQL, Supabase), nobody is superuser: ASSERT role attributes in a `DO` block that raises; never SET them (`ALTER ROLE ... NOSUPERUSER` is rejected).
30. Prove access as the role: `BEGIN; SET LOCAL ROLE anon; SELECT count(*) ...; ROLLBACK;` with a positive control (a role that should see rows) and a negative control in the same run.
31. After `SET ROLE authenticated`, subqueries that build your test JWT claim are RLS-filtered too. Capture ids BEFORE switching role, or the claim is NULL and "0 rows" is an artifact.
32. The decisive RLS test is anon WITH a real tenant claim going from rows to 0. Anon without a claim staying at 0 proves nothing.
33. A `{public}` policy includes `anon`. If it holds only because a NULL claim never compares equal, a COALESCE or `IS NOT DISTINCT FROM` opens it. Prefer `TO authenticated`.
34. Narrow a policy's roles with `ALTER POLICY name ON t TO authenticated`: it keeps USING/WITH CHECK verbatim, unlike DROP+CREATE.
35. Pin `search_path` (`SET search_path = ''`) on the tenant function every policy calls. Before reporting a mutable search_path as live, check `has_schema_privilege(role, ns, 'CREATE')`.
36. JWT-claim tenancy is sound only if the access-token hook is revoked from public/anon/authenticated and granted to `supabase_auth_admin` alone. Verify the hook's grants.
37. Multi-tenant review goes BELOW the policy: read the `auth.users` insert trigger. A single-tenant auto-join trigger plus open signup gives every registrant the production tenant.
38. Fix auto-join at the row-creation chokepoint (the trigger raises unless the email domain is allowlisted). It fires on every path, OTP and magic link included.
39. On a shared Supabase project, `authenticated` spans every workstream's users. Count `auth.users` and its email domains before reusing a permissive-RLS precedent.
40. A shared project does not oblige you to share its auth: a server-side API boundary (service role, own SSO, schema not exposed) isolates an app with sensitive data.
41. A `USING(true)` `authenticated_all` policy usually marks a service-role app that never used RLS. It is harmless alone and a cross-app hole once another app's users hold `authenticated`.
42. On a HALT gated by two conditions, do not declare on half: report HALT-PENDING and name the one human check (for example the dashboard signup toggle) that settles it.
43. Count GRANT rows and OBJECTS separately: `information_schema.table_privileges` rows are grants, not tables.
44. `get_advisors(type:'security')` is a first pass, not a verdict. It missed `{public}`-role and cross-app issues that only the role and grant queries showed.

## Queries
- Definer functions anon can run: `select p.oid::regprocedure, has_function_privilege('anon', p.oid, 'EXECUTE') from pg_proc p join pg_namespace n on n.oid = p.pronamespace where p.prosecdef and n.nspname not in ('pg_catalog','information_schema');`
- Default ACLs: `select n.nspname, pg_get_userbyid(d.defaclrole), d.defaclobjtype, d.defaclacl from pg_default_acl d left join pg_namespace n on n.oid = d.defaclnamespace;`
- A standing assertion that no app-schema object has an anon/authenticated/PUBLIC grant groups by grantee INCLUDING the real app role, so an empty result cannot be a broken query.
- Signup behaviour (the flag itself is dashboard-only): `select raw_app_meta_data->>'provider', count(*), count(*) filter (where invited_at is not null) from auth.users group by 1;` OAuth identities with zero invited mean signup was open. That is a behavioural inference, not a config read.

## Examples
- 1, 2: `revoke ... from public` plus `grant ... to authenticated` left `proacl = {postgres=X,anon=X,authenticated=X,service_role=X}` on a definer RPC (verified 2026-09-14, Supabase). It shipped three times.
- 3: stopping after each migration file showed `anon` executable at every prefix until the revoke moved next to the create.
- 4, 6: a lockdown file claimed "locked down" while 19 of 19 functions were PUBLIC-executable (PG16); the per-schema ADP revoke recorded 0 rows in `pg_default_acl`.
- 12: three status and usage RPCs answered an unauthenticated anon-key caller with integration health and credit counts.
- 18: two protected ceiling columns had NULL `attacl`; a later `GRANT UPDATE ON TABLE` would have widened both and passed the old guard.
- 21: a "no grantee holds DELETE" assertion aborted the migration on the owner's implicit rights.
- 23: an app started, passed its health check, and 500'd on every data route with "permission denied for table".
- 26: an hour was spent on a password; the role named in the config had been dropped.
- 27: `anon=false` on a scratch cluster was cited in two commit messages while production said `anon=true`.
- 31, 32: a null-claim test and the SET ROLE artifact both returned 0 rows and nearly certified open signup as safe; with the real claim, anon read every row.
- 37: a `SECURITY DEFINER` trigger on `auth.users` put every new signup into the one production tenant (read, write, delete).
- 39: `auth.users` held users of other apps and `public` held unrelated tables; `authenticated` was not "the team that uses this app".
