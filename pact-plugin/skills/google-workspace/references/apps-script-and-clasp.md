# Apps Script and clasp

Field-measured traps of the Apps Script runtime, clasp deploys, web apps, quotas and testing. Read before pushing or deploying a script, before reading dates from a sheet, and before reviewing an Apps Script change.

## Canonical rules
1. A web-app `/exec` URL serves a PINNED version, not `@HEAD`. Saving or pushing changes nothing live: re-pin with `clasp deploy -i <deploymentId>` to keep the URL.
2. A plain `clasp deploy` (or "New deployment") mints a DIFFERENT URL; only `-i <existing id>` preserves a shared one.
3. Work in an isolated temp dir; never `clasp clone` into a project repo (it drops `.clasp.json`, `appsscript.json` and code files).
4. Apps Script evaluates the top level of EVERY pushed file before any function: one test file with `require` breaks every execution.
5. Use a deny-by-default `.claspignore` (`**/**` then `!file` lines), never an exclusion list, and run `clasp show-file-status` after every push.
6. The allowlist fails quietly too: an omitted source file pushes "N files" one short and fails at runtime as "<function> is not defined".
7. Falsify the ignore file once: move it aside, confirm the test file becomes tracked, restore. Do not also set `skipSubdirectories`, which would hide a broken ignore file.
8. The V8 runtime is older than `node --check`: numeric separators (`60_000`) are a ParseError; treat `?.`, `??`, `||=`, `&&=` and BigInt as suspect.
9. `getValues()` returns date-only cells as local midnight in the SPREADSHEET time zone: normalise with `Utilities.formatDate(v, spreadsheetTZ, …)`. UTC getters are wrong for these dates.
10. The spreadsheet time zone can differ from the script time zone; use `getSpreadsheetTimeZone()`, not `Session.getScriptTimeZone()`.
11. Parse date strings with a regex plus `Date.UTC`, never `new Date(str)` or "Month YYYY" through UTC getters.
12. Advanced services take the discovery document's `parameterOrder` POSITIONALLY; a positional param inside the options object gives "Invalid Value".
13. `doGet`/`doPost` cannot read custom request headers: put a shared secret in the body or a query param, and gate it first, fail-closed when the property is unset.
14. From n8n, send that secret through an `httpQueryAuth` credential, never `$vars` (it can resolve silently undefined) or a literal in the workflow.
15. A web-app POST answers with a 302 redirect: callers must follow it.
16. `getUi()` throws in trigger context: wrap every alert or toast in try/catch and fall back to logging.
17. Guard shared PropertiesService state with `LockService` (`tryLock` + release in every exit path, continuation returns included).
18. PropertiesService caps 9 KB per value and 500 KB total; module-level caches die between trigger continuations. Measure bytes with `Utilities.newBlob(s).getBytes().length`.
19. Batch reads and writes with `getValues`/`setValues`; cell-by-cell calls are 10–100x slower.
20. Separate evaluation from mutation: commit state only after the dependent external action succeeds.
21. A new script project needs a one-time browser consent before scope-bearing functions run; clasp cannot give it. Never add a probe to an authorised production script to borrow its consent.
22. Apps Script cannot run locally: syntax-check copies with `node --check`, run pure modules in Node with shimmed globals, and leave runtime I/O to a user runbook.
23. Fakes of an Apps Script API must enforce the call signature, or a call-shape bug passes every test.

## clasp (verified 2026-09-26: `/usr/local/bin/clasp` 3.3.0)

- The Apps Script API must be enabled for the logged-in account. Credentials live in `~/.clasprc.json`, outside any project; a `scriptId` is not a credential and is safe to commit.
- Commands: `clasp clone <scriptId>` · `clasp push -f` · `clasp show-file-status` · `clasp list-deployments` · `clasp deploy -i <deploymentId> -d "msg"`. The scriptId is the `/d/<id>/edit` URL segment; the deploymentId is the `AKfycb…` segment of the `/exec` URL.
- UI equivalent of a re-pin: Manage deployments → edit → Version → New version. "Save" is not "redeploy".
- clasp's default config (`rootDir:""`, `.js`/`.gs`/`.json`, subdirectories included) sweeps in tests: that is why the ignore file must exist. Only extensions in `scriptExtensions`/`htmlExtensions`/`jsonExtensions` are candidates, so a `.py` harness is never at risk.
- Project script and deployment IDs live in each project's docs/agent-notes/, not here.

## Web apps and brokers

- Manifest pins deploy settings: `webapp.executeAs:"USER_DEPLOYING"` (storage runs as one owner) and `webapp.access:"DOMAIN"` (internal only). Running as the deployer, a web app can create Docs from a backend POST without OAuth on the caller side.
- Broker pattern: when n8n cannot authenticate to a Google API, a web app calls it with `ScriptApp.getOAuthToken()` (scopes declared in `appsscript.json` `oauthScopes`), constant-time compares a shared secret from Script Properties, and returns JSON. n8n becomes one HTTP POST with no key material.
- A query-param secret can surface in proxy or referer logs; acceptable only for a domain-gated, rotatable secret.
- An org-restricted web app can take a Google OAuth bearer token, but Google validates it before `doPost` runs; `doPost` still reads no header.
- Server-to-client JSON in HtmlService: set `template.x = JSON.stringify(JSON.stringify(obj))` and print it with `<?!= x ?>`; the double encoding yields a safe string literal the client `JSON.parse`s.
- Properties-to-Drive size spill: store inline under about 8,000 bytes, above that one overwritten Drive file plus its id in Properties. When shrinking back inline, delete the Drive blob BEFORE deleting its id property.

## Runtime limits and review checklist

- Limits: 6-minute execution, 90 minutes a day on the free tier, 30 simultaneous executions, `MailApp.getRemainingDailyQuota()` for recipients, `Logger.log` retained 7 days.
- Continuation: time-check, save state (index, batch id) in PropertiesService, `ScriptApp.newTrigger(…).timeBased().after(5000).create()`, clean up old triggers first, and validate state (batch id match) on entry.
- `GmailApp.search()` passes a raw Gmail query: bare numbers match anywhere, so quote exact terms. Prefer `getPlainBody()` to stripping `getBody()`.
- HTML dialogs are sandboxed: pure-CSS spinners and a loading state for `google.script.run`, which can take minutes.
- Review checklist: idempotent re-runs (clear-then-write, not append), header-name lookup vs positional writes, fail-loud errors that name the entity, location, context and remediation, guards for negative or zero amounts, and a grep for old names after a rename.
- Replace a `{{PLACEHOLDER}}` with a table: `findText` → parent paragraph → `getChildIndex` → `removeChild` → `insertTable(index, cells)`. For the empty case use `replaceText(placeholder, '')` instead; do not mix the two.
- Drive walks from Apps Script: see drive-docs-sheets-gmail.md.

## Testing
- Static: copy `.gs` to `.js` and `node --check` each file and the concatenated bundle; grep `[0-9]_[0-9]`, `\?\.`, `\?\?` before deploy.
- Pure layer: `(0, eval)(bundle)` in Node with `globalThis.PropertiesService` and similar shims; renderer smoke test with a tiny DOM shim, then a real browser on a served preview.
- Keep render fixtures and storage-tier fixtures separate: a richer fixture can cross the inline size limit and silently flip the path the storage tests exercise.
- One-file share for a non-technical reviewer: inline CSS and renderer into one `doGet` `.gs` (split any literal `</script>`), generated by a build script.

## Examples
- A pushed node test file with `require('fs')` broke an hourly trigger, and the stack trace pointed at the test file.
- `Date.now() + 60_000` passed `node --check` and failed in the Apps Script editor.
- A monthly filter lost every row in a UTC+1 spreadsheet because the code used `getUTCMonth()`.
