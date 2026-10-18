---
name: n8n-ops
description: "Use for any n8n instance over its REST API: reading, building, editing, activating or archiving workflows, executions, data tables, credentials, folders, authorship, pagination, per-instance keys. API-first; no n8n MCP. Node syntax lives in the pack's other n8n-* skills."
---

# n8n-ops

Context: work on any n8n instance through its public REST API (`/api/v1`), safely and without an MCP server. Node syntax and Code-node APIs live in the pack's node-syntax skills (`repacted-n8n:n8n-*`); this skill carries instance behaviour and field notes only. A shared production instance is a system other teams depend on: apply care on every write, and touch only what the user named or what you created.

## Workflow
1. **Pick the instance.** Name it on every call and NEVER conflate instances: each has its own base URL, key, data and users. Keep one key file per instance (for example `~/.config/n8n/<instance>.env`, mode 600, holding the base URL and the API key). If the file is missing, tell the user which file to create: you never write a secret value, and you never print, source or cat the file.
2. **Ask the instance what it accepts** before any call you have not made there this session: `GET /api/v1/discover?include=schemas&resource=<x>`. The live instance outranks n8n.io docs and pasted specs, in both directions.
3. **Read with GET only**, the key sent as the `X-N8N-API-KEY` header (not `Authorization: Bearer`). Follow `nextCursor` for any collection and state a count, an absence or "the oldest is" only once the cursor is exhausted. Folders page with `take`/`skip`. A field missing from a GET (`parentFolderId`, credential `data`) is not evidence the API lacks it. → [api-and-lifecycle](references/api-and-lifecycle.md)
4. **Before any write:** back up as a SEPARATE command (`GET /api/v1/workflows/<wid>` saved to a file), diff the exact nodes and params you will change, and record `versionId`, `activeVersionId` and `active`. NEVER touch another team's project: flag an adjacent broken workflow instead of fixing it. Read workflows through the API, never by asking the user for exports; the id in an export's file name is the live instance id.
5. **Write with explicit curl, keeping curl the program**, so the verb, the path and the key header stay visible in the command a reviewer or a guard reads. Feed the key header from a process substitution that reads the key file, so the key never enters argv, output or the transcript: `curl -sS -X PUT -H @<(<read the key and print "X-N8N-API-KEY: <key>">) -H "Content-Type: application/json" --data @wf-put.json "$BASE/api/v1/workflows/<wid>"`. Never hide a write inside a script, a helper, `sh -c` or `curl -K`. `POST /workflows` and `PUT` are strict: send only `name`, `nodes`, `connections`, `settings` (+ `projectId`), with `settings` filtered against discover.
6. **Prefer `/archive` over `DELETE`.** DELETE, transfer, credential deletion and package import need a real yes from the user every time, naming the workflow id and the exact command. If a guard or a reviewer stops you, STOP: do not rephrase, split or route around it.
7. **Prove it from the instance, then report.** GET back and compare against the INTENDED state: `activeVersionId` equals `versionId`, or the owner's chosen `active:false`. Never re-activate by reflex. Report the workflow id, its name and exactly what changed. An execution record or destination state is evidence; a 200 is not.

## Map
- [api-and-lifecycle](references/api-and-lifecycle.md): endpoint table, pagination, strict bodies, PUT/publish/active, credentials, retries, executions, authorship.
- [build-expressions-and-code](references/build-expressions-and-code.md): item lineage, `.item` vs `.first()`, clobbering nodes, Code-node modes and sandbox limits, dedup keys, loops, offline validation of workflow JSON.
- [build-nodes-and-integrations](references/build-nodes-and-integrations.md): HTTP pagination nesting, large responses, webhooks and forms, Google Docs/Drive/Sheets nodes, BigQuery, schedules, LLM nodes, Monday.
- [build-slack-and-email](references/build-slack-and-email.md): Slack threading, blocks, triggers, interactivity, scopes; Gmail and email parsing.
- [data-tables](references/data-tables.md): the node's OR filter, per-endpoint pagination, whole-table diffs.
- [api-first](references/api-first.md): why the node-syntax skills' MCP-era tool names do not apply.

## Related skills
- Code-node JavaScript/Python, expression syntax, node configuration, validation errors, workflow patterns: `repacted-n8n:n8n-code-javascript`, `repacted-n8n:n8n-code-python`, `repacted-n8n:n8n-expression-syntax`, `repacted-n8n:n8n-node-configuration`, `repacted-n8n:n8n-validation-expert`, `repacted-n8n:n8n-workflow-patterns`.
- Apps Script brokers that n8n calls: `repacted:google-workspace`. The third-party API a workflow calls (Monday, Jira, Slack Web API limits): `repacted:vendor-gotchas`.
- Before reporting any count, absence or "done": `repacted:verification-discipline`.
