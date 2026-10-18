# API and workflow lifecycle

How the n8n public REST API behaves on the instances observed: discovery, pagination, strict bodies, the PUT/publish/active axes, credentials, executions and authorship. Read before any write, and before trusting any count or absence.

## Canonical rules
1. Ask the instance first: `GET /api/v1/discover?include=schemas&resource=<x>` lists what THIS instance accepts; it outranks n8n.io docs and pasted specs, in both directions.
2. One page is never the dataset. Follow `nextCursor` until absent; a count equal to the limit (100, 250) is a cap, not a total.
3. Folders page with `take`/`skip`; `limit` on folders returns HTTP 400 "Unknown query parameter 'limit'" (observed 2026-09-26).
4. A field absent from a GET is not evidence the API lacks it: `writeOnly` fields (workflow `parentFolderId`, credential `data`) never come back.
5. `POST /workflows` and `PUT` are `additionalProperties:false`, recursively into `settings`. Send `name`, `nodes`, `connections`, `settings` (+ `projectId` on create) and filter `settings` against the discover schema.
6. The 400 "must NOT have additional properties" never names the field; it does name the object (`/settings`), which narrows the search.
7. Back up with a SEPARATE command before any write; never chain a backup and a write, and never chain two workflow ids in one command.
8. Before a PUT record `versionId`, `activeVersionId`, `active`; after it GET back and compare. Whether PUT keeps a workflow published is version-dependent on the same host.
9. Verify a write from a fresh GET (or the PUT response body for the node you changed), never from the status code; assert the target node's own params, not a substring of the blob.
10. Never reflexively re-activate after a write, and never automate re-activation: `active:false` is often the owner's choice. Confirm intent; off is the recoverable direction.
11. Once a node has a `credentials` block, PUTs that change its `parameters` return 200 and silently keep the old ones: PUT parameters first, attach credentials last, in separate PUTs.
12. One logical change per GET→PUT cycle: a second quick GET can serve pre-first-PUT state and the second PUT reverts the first.
13. Pass `projectId` on every create (workflows, credentials, data tables): without it the object lands in your personal project and team workflows fail at runtime.
14. Credentials must live in the workflow's project; a clone in your personal project hides a wrong one. Run the real workflow once.
15. `POST /credentials/{id}/test` on generic types (httpHeaderAuth) tests an arbitrary node and fails meaninglessly; a failed test there proves nothing.
16. Lock every API-created header/query credential to one host: `allowedHttpRequestDomains:"domains"` + `allowedDomains:"<host>"`. Secrets are write-only: overwrite (`PATCH`, `isPartialData:true`) or delete, never inspect.
17. Execution retry replays the workflow snapshot stored with that execution; check `runData` keys for your new nodes before believing a retry result.
18. There is no run-workflow endpoint: for a one-shot run add a temporary webhook trigger, tell the user, and re-read after removing it.
19. Read what a run executed from the execution (`?includeData=true` → `workflowData`, `workflowVersionId`), never from the live workflow.
20. `status=success` is not health: any node with `onError:continueRegularOutput` sends failures down the main branch. Accept only destination state as evidence.
21. Test-fire every error workflow and read ITS executions (`GET /executions?workflowId=<errorWorkflowId>`); `settings.errorWorkflow` is per workflow and defaults to unset.
22. Authorship exists: `GET /workflows/{id}/history` carries `authors` per version, inactive workflows included; `activeVersion.authors` only on active ones. Exact-match names.
23. Never use `$env` (throws) or `$vars` (silently undefined on some instances, unlicensed on others) in expressions. Secrets go in credentials; run-mode flags are literals.
24. An inactive workflow has never been node-validated: activate is the only check that names a bad node parameter. Activate what you build once it is structurally done.
25. Do n8n workflow surgery yourself with direct API calls; delegate only genuine development projects.

## Endpoint table (base `/api/v1`)

"live" = present in one production instance's discover output or read live on 2026-09-26; check your own instance's discover output before relying on a row.

| Purpose | Call | Status |
|---|---|---|
| Capability map | `GET /discover?include=schemas[&resource=x]` | live |
| List / read workflows | `GET /workflows?projectId=&name=&active=&excludePinnedData=true` · `GET /workflows/{id}` | live (list returns full nodes: whole-instance census in 4 pages) |
| Version and history | `GET /workflows/{id}/history` (`data[]`, `nextCursor`) · `GET /workflows/{id}/{versionId}` | live |
| Create | `POST /workflows` (required `name,nodes,connections,settings`; `projectId`) | live |
| Update | `PUT /workflows/{id}` | live |
| Publish / state | `POST /workflows/{id}/activate` \| `/deactivate` \| `/publish` \| `/unpublish` (`versionId` optional) | live |
| Archive (preferred) / delete | `POST /workflows/{id}/archive` \| `/unarchive` · `DELETE /workflows/{id}` | live |
| Move project | `PUT /workflows/{id}/transfer` (`destinationProjectId`) | live |
| Folders | `GET/POST /projects/{projectId}/folders` (`take`/`skip`, response `{data, count}`) | live |
| Credentials | `GET /credentials` (metadata only) · `GET /credentials/schema/{type}` · `POST /credentials` · `POST /credentials/{id}/test` | live |
| Executions | `GET /executions?workflowId=&status=&includeData=true` · `POST /executions/{id}/retry` \| `/stop` | live |
| Usage | `GET /insights/summary?startDate=&endDate=&projectId=` (cannot scope below a project) | live |
| Audit, projects, variables, tags, data tables | `POST /audit` · `GET /projects` · `/variables` · `/tags` · `/data-tables` | live |
| Bulk move between instances | `POST /n8n-packages/export` \| `/import` | live (import can overwrite many workflows) |

**`parentFolderId`:** in July one instance returned 400 for `parentFolderId` on POST and PUT, so workflows were created at the project root and dragged into folders in the UI. Observed 2026-09-26 (read-only): the same instance's /discover schema now lists `parentFolderId` on POST and PUT, and `/publish` and `/unpublish` exist. It has not been tested with a write: treat it as unverified, and try it once on a scratch workflow before relying on it. Discover has also been stale the other way (it omitted `projectId`, which the instance accepted).

## PUT, publish and active

Same host, both behaviours: an earlier PUT returned 200 with `active:false`, `activeVersionId:null`; on 2026-08-13 three PUTs advanced `versionId` and `activeVersionId` together and kept `active:true`. So:
1. Before: record `versionId`, `activeVersionId`, `versionCounter`, `active`, and the INTENDED state.
2. After: GET. `versionId` moved but `activeVersionId` did not = unpublished draft; the old version still runs.
3. Proof of what runs: `GET /workflows/{id}/{activeVersionId}` and count its nodes; top-level `nodes` says nothing about the active version.
4. `active` is readOnly on the PUT body. `/activate` and `/deactivate` change neither `versionId` nor `updatedAt`, so a flip leaves no trace and cannot be attributed.
5. A PUT on an active schedule-triggered workflow re-registers the timer: the next run can be skipped. Don't deploy in the minute before an awaited run. In-flight executions survive.
6. A poll trigger's new interval does not take effect on publish: deactivate then reactivate, then check execution spacing. Reactivation fetches the whole backlog in one poll.

## Credentials

- `GET /credentials/schema/{type}` before creating one. `httpHeaderAuth` data: `{name, value, allowedHttpRequestDomains:"domains", allowedDomains:"<host>"}`.
- A receiver that cannot read custom headers (Apps Script `doPost`) needs `httpQueryAuth` (appends `?name=value`).
- PUT-time variant: referencing a credential id your key cannot access fails at PUT with 400 "You don't have access to the credentials in the '<node>' node". Share it to the target project, or strip `credentials` from the nodes, PUT, and let the owner wire them in the UI.
- A harness using your own token cannot see a node with `credentials: {}`: check deployed bindings directly.
- Header auth rejects with 403, basic auth with 401; check the caller's retry rule before relying on either code.
- `$vars`: `GET /variables` → 403 "license does not allow feat:variables" on unlicensed instances; on others it resolves undefined and `JSON.stringify` drops the key, so a secret silently leaves the request.

## Executions and runs

- Execution lists lag 60 s or more: poll about 2 minutes before concluding a trigger failed (a second fire double-runs).
- `saveExecutionProgress` is off by default: a long run looks hung. Set it plus `saveDataSuccessExecution:"all"` while debugging.
- n8n runs a node once per input item: a select after a 268-item select re-runs 268 times. Use `executeOnce:true` on downstream selects.
- The execution-data table is pruned (about two weeks on a default install); longer history lives only in the insights tables of the instance's database.

## Authorship (verified live 2026-09-26)

- `GET /workflows/{id}/history` → `{data:[{versionId, authors, createdAt, …}], nextCursor}` on an inactive workflow (8 versions, all with authors). `activeVersion` is null on inactive workflows and a dict with `authors` (string) on active ones.
- Validate against the active set: newest history author vs `activeVersion.authors` agreed on 292 of 296 (published author vs last-saved author).
- `authors` is a string (not a list) and may be `system migration`. History starts later than the oldest workflows, so the earliest author is not the creator and "every version by X" can over-attribute; "≥1 version by X" is contribution.

## Examples
- A "pruned" execution history was page one of 223 runs; the report built on it had to be redone.
- A deploy read 60 s before the commit landed was reported as "not patched", with a confident cause attached.
- A green clone run hid a credential in the wrong project; the production workflow failed at runtime.

