# Railway field notes

Context: measured Railway behaviour that the vendor-installed `use-railway` skill does not carry.
Use `use-railway` for the operation itself (deploying, variables, logs, domains); read this before
you trust a Railway config file, a deploy status, a token check or a cron service. Railway changes
often: every fact is dated, and a fact older than a few months needs a re-check.

## Canonical rules
### Config and builds
1. `railway.json` / `railway.toml` (Config as Code) is NOT applied: it is deprecated and enforced, yet deployment metadata still emits a `propertyFileMapping` that makes it look wired in (verified 2026-09-09, CLI 4.59.0).
2. Prove what is applied by comparing a value ONLY the file sets (for example `healthcheckTimeout`) against the RESOLVED `serviceManifest.deploy` of a live deployment. Set build, start and health settings on the service.
3. Keep `railway.json` out of repos, especially multi-service ones: it misleads readers and reviewers even when ignored.
4. Railpack bakes the start command into the build manifest (the framework default, such as `npm start`), and cron executions use that MANIFEST command. The service start command and `RAILWAY_RUN_COMMAND` can lose to it on a stale image.
5. Robust multi-service start: make `npm start` a dispatcher that branches on `RAILWAY_SERVICE_NAME` (for example `node scripts/entry.mjs`), so every service built from the repo runs the right process.
6. A Next.js service must bind `$PORT` in its start script (`next start -H 0.0.0.0 -p ${PORT:-8080}`); bare `next start` binds 3000 and every path 502s while the deployment says SUCCESS and the instance stays CREATED.
7. Railway prunes devDependencies after the build: anything the runtime imports (`astro`, `@astrojs/node`, `tsx` for a cron script) goes in `dependencies`. Check with `npm prune --omit=dev` and boot the entry.
8. Railpack's default Node major can change under you. Pin with the `RAILPACK_NODE_VERSION` service variable (a plain major such as `24`); a RANGE in `engines.node` fails to resolve and silently falls back to the default.
9. Railpack Node precedence: `RAILPACK_NODE_VERSION`, then `engines.node`, then `.nvmrc`, `.node-version`, `mise.toml`/`.tool-versions`, then the default. The build log line `railpack default` is the tell (verified 2026-06-27).
10. `railway up` filters the upload through `.gitignore`, then `.railwayignore`, then `.dockerignore`. A file dropped by the first two never reaches the builder: fix all three together.
11. `.dockerignore` disregards leading and trailing slashes, and a pattern without `**` matches only at the context root, unlike `.gitignore`. Never give a data-protection ignore rule the name of a source directory.
12. `railway up` from a git WORKTREE ignores `.gitignore` (`.git` is a file there) and uploads everything. Deploy from `git fetch && git archive origin/<branch> | tar -x -C <dir>`, and grep the extracted tree for the fix first.
13. A `railway up` that dies at "Uploading..." after about 30 s is usually tarball bloat (committed PDFs, screenshots), not the network. A `redeploy` "operation timed out" against the GraphQL API can be exhausted quota.

### Deploys and verification
14. `railway redeploy` (CLI) replays the PREVIOUS config snapshot and image; it does not pick up new code or re-resolve settings. Use `railway up`, or the GraphQL `serviceInstanceRedeploy`, which re-resolves.
15. Both redeploy paths record `reason: "redeploy"`, and such records carry distinct image digests because builds are not reproducible. Neither the label nor the digest tells you whether the source changed.
16. A deployment record carries no commit SHA. Deriving the commit from the build timestamp with `git log --until` is a derivation: label it so.
17. "SUCCESS" may predate your push: compare the deployment's `createdAt` with the push time. An env-var change rebuilds whatever code is there and yields a fresh green build of stale code.
18. Confirm the trigger before assuming it fired: services created through the API (`serviceCreate`) have no GitHub auto-deploy, and a repo may not be GitHub-connected at all. `railway deployment list -s <svc>` shows it; REMOVED means superseded, not failed.
19. `serviceInstanceDeployV2(environmentId, serviceId)` rebuilds the originally PINNED commit; pass `commitSha: "<full sha>"` for current code.
20. When a new deploy fails to boot, the OLD image keeps serving and status stays "Online". Read `activeDeployments` (not `latestDeployment`) from `railway status --json` and compare with the id your `railway up` printed.
21. That confound only produces false negatives ("my fix did nothing"). Capture the live BEFORE state first, and give images an HTTP-readable build marker; a deploy whose expected effect is invisible cannot be verified otherwise.
22. One repo can feed several services (web plus cron or worker): enumerate every deploy target and name which one you checked before saying "deployed".
23. Finish with the running system's own self-report: a boot log line, a build marker, or a DB column only the new code writes.
24. Prove a deploy gate by sabotaging the OBSERVER, not the app: point the healthcheck at a missing path on the same image and watch the deploy fail while the old one serves.
25. `healthcheckTimeout` is in SECONDS (Railway's own MCP tool describes milliseconds). The API builder enum has no `DOCKERFILE` (a Dockerfile build is selected by `dockerfilePath`), and a rejected mutation applies NONE of its fields.
26. The Railway MCP `list_variables` ignores its `serviceId` and returns the LINKED service; check `RAILWAY_SERVICE_NAME` inside the payload, and use `railway variables --service <svc> --kv` (verified 2026-09-19).
27. `railway add` can repoint the directory's linked service: pass `--service` on every command, since the link is shared state between agents.
28. Run a script with a service's secrets without printing them: `railway run -s <svc> -- node script.mjs`. Never `railway variables` into a transcript.

### Cron services
29. A cron service runs only at its scheduled tick, never on deploy, and a tick can be minutes late. Deploy status says nothing: verify with an application heartbeat (a row per cycle) after the first tick, and read service logs before calling a tick skipped.
30. A `cronSchedule` on a WEB service turns it into a boot-run-exit job (instance stuck CREATED, 502). Only the dashboard's "Run now" forces an off-schedule run.

### Tokens and API
31. A PROJECT token authenticates with `Project-Access-Token: <token>`; an account token with `Authorization: Bearer`. Both are UUID-shaped, and a valid project token under Bearer answers exactly like a dead token: probe both schemes before calling one dead.
32. Railway GraphQL returns HTTP 200 for auth failures, with the error in the `errors` array: classify on the body, never the status code.
33. A project token can read status, set variables (`--stdin` for secrets), redeploy and set domains. It cannot create services, read logs, approve a gated deploy or run account queries: plan those steps for the user.
34. The CLI account token lasts about an hour, and its expiry reads as "Not Authorized". Any CLI command refreshes it. Raw GraphQL needs a `User-Agent` header or Cloudflare answers 403 (1010).
35. The Railway MCP's auth can fail per project while account calls work: drive deploys through the `railway` CLI.

### Framework notes
36. Next.js `instrumentation.ts` `register()` with `setInterval` (gated on `NEXT_RUNTIME === 'nodejs'`) works as an in-process cron only on a long-lived server: guard tick overlap, persist status with an orphan sweep, and log each tick as a heartbeat.
37. Every replica runs that timer: fine for idempotent claims, wrong for exclusive work (use an advisory lock or an external scheduler), and never for wall-clock schedules. Expose `lastTickAt` on a health endpoint; when a digest says "0 processed", check that inputs arrived first.
38. Astro on Railway: `@astrojs/node` `mode: "standalone"` with `output: "server"` binds `$PORT` itself; no Dockerfile is needed; a server build needs at least one page.
39. Astro `output: "server"` skips build-time data validation for non-prerendered pages (`prerender = true` pages still validate). Add a `prebuild` validator gate chained with `&&`, not a pipe: `/bin/sh` has no `pipefail`.
40. Repeated Astro builds can fail on stale prerender chunks: `rm -rf dist .astro` between builds. An Astro SEO site's canonical domain lives in three places (`site`, the instance config, `robots.txt`).
41. Railway has blocked deploys of a framework version with a critical CVE: bump the framework rather than fighting the gate (verified 2026-05). The Supabase JS realtime client wanted Node 22+ for native WebSocket (verified 2026-05-24).
42. The deploy mechanism (GitHub auto-deploy vs `railway up`) is decided per project: ask, and do not turn one project's choice into a standing rule. Authorising the Railway GitHub App is the user's click.

## Examples
- 1, 2: a production service ran with no healthcheck while its file declared `/api/health`; the file said 120 s and the resolved manifest said null.
- 4, 18, 19: a poller showed "Ready / SUCCESS" for three days while it ran the web start command and never evaluated a rule; its heartbeat table was empty.
- 8: a poller broke with "Premature close" on the Google OAuth token call after a default Node change; pinning a major fixed it.
- 10: a route 404'd in production because `.railwayignore` alone still excluded its source directory.
- 12, 13: a worktree upload tried to send about 731 MB of `node_modules`; removing 57 MB of committed documents took the upload from a timeout to 4 s.
- 17, 22: both services said SUCCESS after a push; one build was older than the push and the other was eleven days old; a sibling poller had not built for 12 days.
- 24: the same image passed with `/api/health` and failed with `/api/nope`, 37 s apart.
- 29: a `35 * * * *` job first ran 4 min 17 s late.
