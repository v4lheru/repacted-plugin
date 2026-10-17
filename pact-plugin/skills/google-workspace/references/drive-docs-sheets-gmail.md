# Drive, Docs, Sheets and Gmail APIs

Measured behaviour of the Google Drive v3, Docs and Sheets APIs, from Apps Script advanced services, REST and n8n HTTP nodes, plus what each common credential can and cannot reach. Read before listing, searching or writing Drive content, and before concluding that access is blocked.

## Canonical rules
1. Inside a Shared Drive, EVERY list call needs `corpora:'drive'` + `driveId` + `includeItemsFromAllDrives:true` + `supportsAllDrives:true`: root, descendants, folders, files and shortcuts.
2. `corpora:'drive'` and `driveId` require `includeItemsFromAllDrives:true`; they are mutually required, not alternatives.
3. Resolve `driveId` with `Files.get(id, {fields:'driveId', supportsAllDrives:true})`; never hardcode it. `corpora:'drive'` on a My Drive folder returns an empty list.
4. Create, move and update calls on Shared Drive items need `supportsAllDrives=true`, or they 404 on the parent.
5. `fields` must name `nextPageToken` AND `files(…)`, or pagination stops after page one with HTTP 200. Omitting `fields` drops every metadata field beyond kind, id, name and mimeType.
6. Declare the Drive advanced service at the version the code reads: v2 field names (`title`, `createdDate`) are undefined under v3 code.
7. Many `'X' in parents` clauses OR'd together silently drop some parents: query one parent per folder node, or partition batches by `driveId` under `corpora:'drive'`.
8. A mime-allowlisted listing drops shortcuts: resolve `shortcutDetails`, dedup on the TARGET id, and take the folder path from the SHORTCUT's parent.
9. Check a listing total against a known folder before trusting it; every failure above returns HTTP 200.
10. `changes.list` with an out-of-range page token returns 200 and zero changes forever: check `token > getStartPageToken()` and bound the page walk.
11. "Invalid Value" cannot tell a dead token from your own malformed call: re-issue the same call with a token that is good by construction (a positive control).
12. When resource A works and B is denied, diff both ACLs before hunting a principal to grant. On Drive, 404 means no visibility, 403 means visible but not permitted.
13. Test a credential's capability with one read-only call against the real resource; identity is not capability.
14. Where a Workspace admin blocks the gcloud OAuth client for the restricted Drive scope, ADC and gcloud tokens cannot reach Drive. Do not hunt workarounds; re-consent is the user's decision.
15. Under `drive.metadata.readonly`, `files/{id}/revisions` returns an empty list regardless of reality: use the file's `version` field.
16. Parallel writes through one impersonated service account share its per-user quota: low concurrency, full-jitter backoff, one shared token.
17. Docs `replaceAllText` keeps the placeholder run's formatting: style the template placeholder as the final text.
18. The Docs API `documents.create` always lands in My Drive root; relocate with `addParents=<folder>&removeParents=root&supportsAllDrives=true`.
19. Round-trip your own correlation key through `appProperties` on create (request it in `fields`); note `appProperties` are readable only by the OAuth client that wrote them, so use `properties` for tags other clients must read.

## Shared Drive listing, in code

```javascript
Drive.Files.list({
  q: "'" + folderId + "' in parents and trashed = false",
  corpora: 'drive', driveId: driveId,
  includeItemsFromAllDrives: true, supportsAllDrives: true,
  fields: 'nextPageToken, files(id, name, mimeType, modifiedTime, parents)',
  pageToken: pageToken
});
```
A shared-drive root id starts with `0A`. Propagate its `driveId` to every call in that root's walk (branch on a per-root flag, not on "is this the root call"), and carry a `folderId → driveId` map into file listing so each OR'd batch holds one drive. Measured damage from applying the hint to the root call only: 24 of 111 folders walked, then the missing files were marked deleted and removed from a production index.

Better than a pre-pass: if the walk already visits every folder, query each node with a single-parent `q` and the dropout has no referent in the code. "I removed the failure mode" is checkable (grep for `' or '` in queries); "I mitigated it" is not.

Shortcuts: probe `Files.get(id, {fields:'shortcutDetails', supportsAllDrives:true})`, gate on `targetMimeType`, then fetch the target's metadata. Wrap both calls; skip broken shortcuts with a warning; log counters (seen, resolved, skipped for dedup, mime, broken). When a file id is contract-visible downstream, skip-and-record may beat resolving (a copy step would otherwise duplicate the shortcut).

## changes.list cursors (REST, measured 2026-08-18)

| token | response | behaviour |
|---|---|---|
| far-stale (below head) | `changes`, `nextPageToken`, no `newStartPageToken` | does not self-heal; walks empty pages forever |
| far-future (above head) | `changes`, `newStartPageToken` = head | self-heals, silently skipping the gap |

Far-future: compare with `getStartPageToken()` (options only, `parameterOrder` empty) before fetching. Far-stale: bound the walk; bound hit with ZERO records means the cursor is outside retention, while bound hit WITH records is a real backlog and must not touch the token. Rate limiting is its own identity (`userRateLimitExceeded`/`rateLimitExceeded`, 403 or 429); test for it before any auth branch. Whether the Apps Script wrapper exposes `e.details` is unobserved: keep the classifier in one pure function.

## Credentials and what they reached (measured on one Workspace tenant, 2026-08-12)

| Credential | Drive result |
|---|---|
| gcloud ADC | no Drive scope; re-consent refused by the Workspace admin ("This app is blocked") |
| `gcloud auth print-access-token` | a different token, also no Drive or Sheets scope |
| clasp's login (`~/.clasprc.json`, an authorized-user token) | `drive.metadata.readonly` + `drive.file`: metadata for anything the user sees, content NO (`export` and `alt=media` → 403 `appNotAuthorizedToFile`; Sheets API disabled in clasp's own OAuth project) |
| claude.ai Drive connector | a connection failure when not connected, not a permission verdict; fine for single files, not for bulk binaries |

- Confirm what a token holds with `https://oauth2.googleapis.com/tokeninfo?access_token=…` (granted scopes and email).
- The `drives` collection needs a higher scope than `files`: `GET /drives/{id}` is 403 while `GET /files/{driveId}?supportsAllDrives=true` is 200 (with the placeholder name "Drive": the real Shared Drive name is not retrievable on metadata scope).
- Free link-sharing probe: `…/spreadsheets/d/{id}/export?format=csv` or `/gviz/tq?tqx=out:csv` with no auth header. 200 means readable; 401 means genuinely restricted.
- A container-bound script's host sheet is not discoverable from Drive metadata; record it as unknown.
- Service-account impersonation in a project that has the Drive grant can mint a working Drive/Docs token where the user OAuth client is blocked.
- When content is truly blocked, ask the user to export by hand: `.xlsx` or `.docx`, never CSV (it destroys cell types and number formats), with the wall-clock time of the download.

## Metadata forensics
- `createdTime` vs `modifiedTime` answers "was it touched"; a uniform few-second create-to-modify gap across a batch is the generation signature, and outliers are human edits.
- Bucketing by `createdTime` around a suspect commit gives free before/after cohorts. Filenames encode intent; timestamps encode what happened.
- ACL diff: `permissions.list` works under metadata scope. Pair it with a liveness check of the working side; watch for two principals conflated (a runtime service account vs the executing user of an Apps Script web app).

## Examples
- A whole-drive crawl found 134 files, fewer than a narrower crawl, because descendant listings lacked `corpora:'drive'`.
- A doc at `version: 83` returned zero revisions; "nothing was edited" would have been fabricated reassurance.
- Four of five call sites were correct only because their methods have no positional parameter; the fifth took production down.
