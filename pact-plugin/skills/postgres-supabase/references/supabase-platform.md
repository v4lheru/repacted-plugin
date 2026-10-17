# Supabase platform: PostgREST exposure, RPCs and the anon default grants

Context: the Supabase layer on top of Postgres, meaning what PostgREST serves, how it resolves
RPCs, and the platform's default grants to `anon`. Read this before you expose or hide a schema,
change an RPC signature, or trust a revoke on Supabase. The grant rules in general are in
`roles-grants-and-rls.md`.

## Canonical rules
1. Supabase grants EXECUTE on new functions DIRECTLY to `anon`, `authenticated` and `service_role` through `ALTER DEFAULT PRIVILEGES`. `revoke ... from public` never touches those grants.
2. `drop` + `create` re-applies that default every time. Changing a `returns table (...)` shape forces a drop, so every widened RPC is silently re-granted to `anon`.
3. Put `revoke execute on function f from public, anon;` (and `authenticated` where it applies) in the SAME migration as the create, then grant the intended roles explicitly.
4. Audit the CLASS from the catalog, not known names from the tree: `select proname, has_function_privilege('anon', oid, 'EXECUTE') from pg_proc where prosecdef;`. A grep guard over migrations cannot see drift.
5. Reproduce the defect before believing the fix: a scratch cluster with bare `create role anon` and no default privileges shows `anon=false` honestly and means nothing. Add the platform's ADP line first.
6. Put a KNOWN-NEGATIVE control in the same probe run: one function you know is closed must return 401. That proves a 200 elsewhere is a real read and not a quirk of the call.
7. A 404 `PGRST202` from an RPC probe means "no function with that parameter NAME SET", not "not exposed". Ask the catalog for the signature before concluding anything.
8. Keep "grant present" apart from "observed returning data" in the write-up. After the revoke, the pre-state is unmeasurable forever.
9. Before revoking, census the callers: per call site, name the client construction and the route's auth gate. The default Next.js browser client IS the anon client, even in an admin-looking page.
10. When exposure is confirmed live, ambiguity closes the function: a revoke is one idempotent statement and fails loudly if a caller needed it.
11. PostgREST resolves an RPC by the exact SET of parameter NAMES. One extra key is a hard 404 `PGRST202`, not an ignored field. The error lists params alphabetically, which is the tell.
12. In supabase-js a failed RPC arrives as `res.error`, not an exception. A call site that only logs `res.error` turns signature drift into silent data loss.
13. Adding a parameter breaks every caller. `create or replace` with a different argument list creates an OVERLOAD: drop the old signature.
14. Never "fix" name drift with a single unnamed `json`/`jsonb` parameter. PostgREST feeds it the whole body, so it accepts any body and ends name-set checking.
15. When the function is not deployed yet, prove the resolver on any deployed zero-arg `stable` function now, and schedule the specific check after the tracked apply plus a schema reload. Say which you have.
16. Exposing a custom schema to supabase-js needs `ALTER ROLE authenticator SET pgrst.db_schemas = '<existing list>, <s>'` (additive: keep co-tenant apps' schemas) plus TWO reloads.
17. The two reloads are `NOTIFY pgrst, 'reload config';` (turns `PGRST106` into `PGRST205`) and `NOTIFY pgrst, 'reload schema';` (clears the table-cache miss). Both are required.
18. Also add the schema in Dashboard, Settings, API, Exposed schemas: a later dashboard save can overwrite the role GUC.
19. The client must name the schema: `createClient(url, key, { db: { schema: '<s>' } })`. Without it, queries go to `public`. Raw SQL must schema-qualify or set `search_path`.
20. Read the exposed list with `select rolconfig from pg_roles where rolname = 'authenticator'`. `current_setting('pgrst.db_schemas', true)` returns NULL in a normal session and reads as a false "not exposed".
21. To HIDE a secret-holding schema: revoke all on it from `anon` and `authenticated` (plus RLS deny-all as depth), and leave it off the exposed list. The `pgrst.db_schemas` + NOTIFY recipe exposes; it never hides.
22. Workers reach a hidden schema over a direct Postgres connection. If the web app needs a status bit, expose a `SECURITY DEFINER` function in `public` that returns only safe scalars, with EXECUTE revoked from `anon`.
23. On a shared project, removing a schema from `pgrst.db_schemas` is the primary closer and table revokes are depth. A service-role app is unaffected by de-expose plus revoke.
24. Supabase signup configuration is dashboard-only (no SQL or MCP read). Infer it from `auth.users` behaviour and label it an inference (roles-grants-and-rls, Queries).

## Probe recipe
`curl "$URL/rest/v1/<table>?select=*&limit=1" -H "apikey: $ANON" -H "Authorization: Bearer $ANON" -H "Accept-Profile: <schema>"`,
or `POST $URL/rest/v1/rpc/<fn>` with a body naming the exact parameters. Run the known-closed function
in the same batch. Keys come from `env-get` (repacted:safe-ops), never from a pasted value.

## Examples
- 1, 3: `proacl` after the revoke-from-public idiom still held `anon=X` (verified 2026-09-14, Supabase).
- 6: a batch returned 200, 200, then 401 for the one function already closed. The 401 made the two 200s evidence.
- 7: a bare call to a function taking one `uuid` returned 404 `PGRST202`; it was wrong arity, not "not exposed".
- 11, 12: a live finalize RPC failed with `PGRST202` on every call behind a `logger.error`. A claim ledger never transitioned while the job reported success (verified 2026-07-28, PostgREST on Postgres 17.6).
- 16, 17: `reload config` alone flipped the error from `PGRST106` to `PGRST205`; `reload schema` fixed it.
- 21: a token-store schema stayed off the API by revoke plus absence from the exposed list, with a public status accessor for the UI badge.
