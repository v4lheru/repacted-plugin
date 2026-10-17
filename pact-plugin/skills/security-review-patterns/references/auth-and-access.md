# Auth and access

Context: reviewing (or writing) authentication, sessions, authorization gates, SSRF defences,
trusted-header identity and internal-tool middleware, drawn from real review findings
(web dashboards, single sign-on, database-backed
apps, Apps Script, launchd scripts). Read when a diff or design touches a login, a token, a gate,
an outbound fetch, a header that carries identity, or a scheduled script.

## Canonical rules (a checklist: run top to bottom)
1. Deny by default when a credential env var is empty or unset: `if (key) { check }` with no else is fail-open; return 503 and log. Look for `.env.example` lines like "leave empty to disable".
2. A session or signing secret with an empty-string fallback (`?? ""`) makes tokens forgeable; require a startup throw, in the module that GATES requests (the edge verifier), with a length floor.
3. A `DEV_MODE`/`dev_mode` that defaults to true (accepting a client-supplied `X-Dev-User-Email`, often with a privileged default role) is an auth bypass unless prod sets it false: want secure-by-default or a prod-marker assertion.
4. Never grant a bypass on a spoofable header (`sec-fetch-site`); carry "already authenticated" in a local variable. Browser pages: Basic Auth or SSO; machine callers: `X-API-Key`.
5. Compare secrets in constant time. Edge runtime has no `crypto.timingSafeEqual`: a hand-rolled compare must not short-circuit on length.
6. Next.js middleware: check the RESOLVED version for CVE-2025-29927 (`x-middleware-subrequest`; fixed in 14.2.25 and 15.2.3): read the installed `next` package.json or the lockfile, not the caret range.
7. `X-Forwarded-For` leftmost is the client's claim: rate limiters take the rightmost minus the trusted hop count (on Vercel `x-vercel-forwarded-for`).
8. Trusting an upstream identity header (`X-Forwarded-Email` from an SSO proxy) is safe only with an ingress NetworkPolicy limiting who can reach the backend; the policy and the trust ship together or not at all.
9. On that trust swap, grep six things: header name on both sides (and strip-then-set of inbound copies), env names ↔ Helm, secret names ↔ Terraform ↔ Helm refs, migration columns ↔ writes, no secret or header VALUE in logs, and the policy's values keys really exist.
10. SAML forks: set `validateInResponseTo` with a request-id cache (replay and login CSRF), `wantAuthnResponseSigned: true` once the IdP signs the envelope, and never log the raw assertion or attribute dump.
11. Supabase: server guards call `auth.getUser()` (re-verifies), never `getSession()` (trusts the cookie); the auth client uses the anon key; a `NEXT_PUBLIC_*SERVICE_ROLE*` is always a finding.
12. Verifying a Supabase JWT in a foreign backend: explicit `algorithms` allowlist (never `none`), signature and `exp` on, `aud == "authenticated"` (rejects anon and service_role tokens), `iss` pinned; generic 401 on any failure.
13. Use PyJWT or supabase `get_claims()`, never python-jose (algorithm-confusion CVE-2024-33663/33664; unmaintained). Drop it from requirements if unused.
14. Resolve org and role server-side from `sub`, scope every query by it, and return 404 (not 403) across tenants.
15. On a SHARED Supabase project, `auth.users` spans every app and Google OAuth admits any account: the email-domain allowlist is the sole authorization. Probe it with `x@allowed.example.evil.example`, `a@b@allowed.example` (split on the LAST `@`), lookalikes, case, trailing dot, empty, and env-unset (must not allow all). Exact `includes(domain)`, never `endsWith`.
16. A domain allowlist authenticates the ORG, not the app: an auto-join trigger picking a tenant by position (`order by created_at limit 1`) becomes a cross-app grant. Prefer de-exposure (schema absent from `pgrst.db_schemas`) over RLS policing.
17. PostgREST exposed schemas ARE readable from SQL, per role: `pg_roles.rolconfig` for `authenticator` (the `current_setting` probe returns NULL).
18. OAuth callbacks build redirects from the request's own origin, never a user `next`/`returnTo`; check the provider's redirect-URL allowlist in the deployed settings.
19. Keep the authz gate on the exported action, then fence that EXACTLY ONE call site passes the dangerous mode of a shared helper; authorize before validating arguments; match sibling error messages.
20. A direct server-action call reaches the same authorization the UI grants: frame the finding as missing authorization, not "UI bypass", or the fix hardens a dialog.
21. Click-to-mint one-shot approvals: attack the BINDING. A regex alternation with `finditer` consumes a shared verb (leftmost match), so two ids yield one pair and pass a distinct-count gate; negation-blind verb matching lets a refusal option ("do NOT delete X") mint authority.
22. A signed client that follows vendor URIs (HATEOAS lists, `nextPage`, redirects) must send EVERY follow through the same SSRF-gated chokepoint BEFORE signing; a second bare `fetch` in the loop is the finding.
23. SSRF: parse, resolve DNS, check ALL resolved IPs against a range table, then fetch the resolved IP with a Host header. Regex checks miss decimal, hex and octal IPs, IPv4-mapped IPv6 and DNS rebinding.
24. Allowlist quality: https only, reject IP literals (v4 and v6), `localhost`/`.local`/`.internal`, match the registrable domain exactly, not `includes()`.
25. A VPN resolver returns `100.64.0.0/10` for some public hosts and `is_global` is False there: confirm with `dig @8.8.8.8` against the system resolver, and never widen the shared guard; override locally outside the repo.
26. Before implementing or preserving a hardening, reproduce the threat: a mitigation aimed at a non-existent vector is pure cost and can corrupt real output.
27. Read-only prod access means passive reads (get, describe, logs, a normal GET). Never send an attack-shaped request to prod; prove reachability locally on the pinned version, and disclose at once if you did.
28. Single-user HMAC cookie sessions are sound when: HMAC (not encryption only), constant-time compare after a length check, server-side expiry, HttpOnly + SameSite + Secure in prod, a secret-length floor that throws. Don't flag them as home-rolled crypto on sight.
29. Login routes compute the password hash compare unconditionally, or a wrong-email vs wrong-password timing difference enumerates users.
30. Apps Script: `getUi()` throws in trigger context (guard it); `LockService.tryLock` with release in every exit path; prefix sheet writes starting with `= + - @` with `'` (formula injection); surface only `error.message` from API errors.
31. Apps Script credentials live in Script Properties; a settings sheet showing `***last4` must not write the mask back over the real key; triggers run as their creator.
32. Apps Script `doPost` with `access: DOMAIN` still lets any same-domain user post a valid body: check a shared-secret token against Script Properties BEFORE parsing; `doGet` double `JSON.stringify` is the template-injection guard.
33. launchd and cron scripts: pass paths to `python3 -c` via `sys.argv`, never interpolation; `umask 077` early; guard nvm discovery on dir existence and a non-empty result.
34. A credential in a crontab curl line leaks via `crontab -l` and `ps`; read it from a file at runtime or use a Kubernetes CronJob with the secret as env.

## OWASP one-screen checklist
- **Access control:** deny by default; verify resource OWNERSHIP on every read and write (IDOR); random ids help but are not authorization.
- **Crypto:** AES-GCM with a random IV, TLS 1.2+, keys from a secret manager, never a dev key in prod.
- **Injection:** parameterised SQL; type-check NoSQL inputs (`{ $gt: '' }`); `execFile` with an argument array, never a shell string; GraphQL through `variables`, never string-built (see Examples).
- **Insecure design:** threat-model assets and actors; enforce business rules (coupon reuse, stock) inside one transaction.
- **Misconfiguration:** generic error bodies with an error id in prod; security headers present (CSP, HSTS, `X-Content-Type-Options`, frame options); restrictive CORS.
- **Components:** audit the RESOLVED dependency versions; commit lockfiles; `npm ci` in CI.
- **Auth failures:** rules 1–15 above; rate-limit auth endpoints.
- **Integrity:** SRI on third-party scripts; pinned CI components and images.
- **Logging:** log auth success, failure and denials with ids, never secrets or tokens.
- **SSRF:** rules 22–25 above; `redirect: 'error'` or a re-validated redirect hop.

## Auth implementation baseline (for writing, from the absorbed PACT references)
- JWT: short-lived access tokens, a `type` claim checked (access vs refresh), refresh rotation
  with a `jti` revocation store, verify with pinned `algorithms`, `iss`, `aud`.
- Sessions: regenerate the id at login (fixation), destroy on logout, one error message for
  wrong user or password, a custom cookie name.
- OAuth/OIDC: `state` and `nonce` in the session, PKCE `S256`, clear them after the callback.
- Password reset: store only a hash of the token, expire it within the hour, revoke every session
  on reset, reply identically whether or not the account exists.
- API keys: store a hash plus a short display prefix, show the raw key once.

## Examples
- 2: forks of one SAML sign-in stack shared `SESSION_SECRET ?? ""` with only a warning on short keys.
- 15: a shared Supabase project admitted any Google account; the domain check was the only control.
- 16: a signup trigger's "first tenant wins" granted a second app's users read, write and delete on the first app's data.
- 21: a guard admitting `<approved id> … -X DELETE <other id>` was reviewed as solid until the binding was attacked.
- 22: a signed client re-checked every HATEOAS and `nextPage` URI through its gated `get()` (the pattern to confirm).
- 26: `$`-doubling added against a non-existent back-reference injection printed doubled dollar signs (`$$`) on partner statements for three months.
- 27: one crafted `Range` header to prod crashed a handler and polluted the logs under audit.
- Injection example: a Monday `create_item` mutation "escaped" with `.replace(/"/g,'\\"')` is injectable because the backslash is not escaped (`a\", board_id: 999999, …`).
