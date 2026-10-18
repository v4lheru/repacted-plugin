# Build: HTTP, webhooks, Google, BigQuery and LLM nodes

Measured behaviour of the nodes that call other systems: HTTP Request pagination and large bodies, webhook and form triggers, Google Docs/Drive/Sheets, BigQuery, schedules, and LLM/agent nodes. Slack and email nodes are in build-slack-and-email.md. Read when wiring a node to an external system.

## Canonical rules
1. HTTP pagination nests twice: `options.pagination.pagination.*`. One level shallower is ignored silently (one request, 200, healthy node).
2. Pagination params apply to the FIRST request, where `$response` does not exist: write `{{ $response?.body?.nextPageToken || '' }}`.
3. Terminate pagination on an EMPTY page, not a short one, and cap `maxRequests`. Suspiciously round counts (25, 100) mean one page.
4. Above a size threshold the HTTP node hands on an unconsumed stream even with `responseFormat:text`; use `responseFormat:file` and read the binary.
5. A webhook with no `responseMode` answers "Workflow was started" before any node runs: a 2xx is delivery, not outcome.
6. Before switching a webhook to `lastNode`, make the side effect idempotent, or caller retries multiply duplicates.
7. The Form Trigger path is `parameters.options.path` and the URL is `/form/<path>`. Right after activate it answers 200 "isn't listening yet": wait and retry before touching config.
8. Pin `webhookId` in generated workflow JSON; n8n mints a new one on a node without it and a routine PUT moves a bookmarked URL.
9. Use a raw HTTP Request node for Monday mutations, never `mondayCom`: the node drops `errors[]` and crashes the host with `Cannot read properties of null (reading '$error')`.
10. Monday GraphQL built by string concatenation is injectable even with quote escaping; pass GraphQL `variables` with `specifyBody:json`.
11. Keep a datastore branch (BigQuery, Sheets) a SIBLING of the notification branch, so a missing table does not also kill the alert.
12. `googleDocs` create writes title and folder only. Body: create → move → markdownToHtml (`tables:true`) → convertToFile → force `mimeType:'text/html'` → Drive update.
13. Files in a Shared Drive or folder carry inherited permissions; a per-file permission delete returns 403. Drop those nodes or set node-level `onError`.
14. `spreadsheet:create` cannot place a file in a folder or Shared Drive; follow it with `googleDrive file:move`. It does return `spreadsheetUrl`.
15. Google Sheets v4.7 header-maps row 1 regardless of range settings; parse a matrix sheet from the union of `Object.keys` across rows.
16. The Sheets 10M-cell cap counts ALLOCATED cells across ALL tabs; a default new tab costs 26,000 cells. Size by column count.
17. `scheduleTrigger` uses the `cron` package: `L`, `W` and `#` throw. Use `28-31` plus an IF on `$now.day === $now.endOf('month').day`.
18. Moving a schedule changes semantics: trace the window computation, which usually derives the period from now.
19. The BigQuery node returns BOOL as strings; `'false'` is truthy. Coerce every boolean column explicitly.
20. The BigQuery node fires per-item queries concurrently (hard limit: 20 outstanding DML per table); batch DML as one MERGE over a JSON array param.
21. The LangChain agent and LM nodes run items strictly sequentially; a 429 there is external quota pressure, fixed with `retryOnFail`, not a limiter.
22. Compare every `SAFE_CAST` and upstream literal in a BigQuery MERGE against the DDL type; a cross-family mismatch fails only at runtime.
23. Pass arrays to BigQuery as a JSON string param and rebuild with `JSON_VALUE_ARRAY` in SQL, not the ARRAY parameterType.
24. Vertex 401/403 from n8n is usually OAuth scope, not IAM: check `cloud-platform` scope, then the predefined Vertex credential type, then broker through Apps Script.
25. The LangChain Supabase vector node in retrieve-as-tool mode queries the Query Name RPC (default `match_documents`), not the Table Name shown.
26. resourceLocator `mode` is load-bearing: "My Drive" is `mode:"list"`, never `mode:"id"`. Settle shapes by counting them across live workflows.
27. The markdown node (showdown) eats mid-word underscores in URLs: wrap URLs as `<https://…>` in the builder; a reimport can drop that fix.

## HTTP Request node

```json
"options": { "pagination": { "pagination": {
  "paginationMode": "updateAParameterInEachRequest",
  "parameters": { "parameters": [ { "type": "qs", "name": "page", "value": "={{ $pageCount + 1 }}" } ] },
  "paginationCompleteWhen": "other",
  "completeExpression": "={{ !$response?.body?.items || $response.body.items.length === 0 }}",
  "limitPagesFetched": true, "maxRequests": 120 } } }
```
`$pageCount` starts at 0 and exists on the first request. The shallow form is invisible in the UI too: find a working paginated node on the same instance and diff against it, and add a build assertion for the double nest. Measured: one fix took an index from 75 of about 3,400 records (2%) to all of them, and turned false "does not resolve" flags into real findings.

Large bodies: measure first (`curl -o /dev/null -w '%{size_download}'`). Read `binary.data` base64 when present, else `this.helpers.getBinaryDataBuffer(0,'data')`, else `json.data`, and throw "UNCONSUMED STREAM" if the result is not a string. A fixture recorded with curl is always a string and cannot reproduce this.

Credentials in HTTP nodes: `authentication:"predefinedCredentialType"` + `nodeCredentialType` (for example `slackApi`) keeps the token out of the JSON. For basic auth use a native `httpBasicAuth` credential, never a computed header in a Set node.

Monday: `mutation($board:ID!,$item:String!,$cv:JSON!){…}` with `jsonBody = {{ JSON.stringify({query, variables}) }}`; `column_values` is a stringified JSON document; IDs as strings. Gate on `$json.data && $json.data.<field>`. A people-column write rejects non-subscribers (`invalidPersonAssignment`) even when the user exists; `add_subscribers_to_board` is idempotent.

## Webhooks and forms

- `responseMode:"responseNode"` + a `respondToWebhook` after the step that matters, once idempotent. An idempotency guard must route AROUND the side effect and rejoin, not return early, or the retry skips the downstream write.
- Cheaper fix for silent loss: an Error Trigger → Slack workflow set as `settings.errorWorkflow`, then fire a deliberately failing payload and confirm the notifier ran and Slack returned `ok:true`.
- Form states by BODY: 200 "Form Trigger isn't listening yet" (not registered or inactive), 404 "Problem loading form" (path wrong), the form's own title (working).
- A generic Webhook has no auth; verify signatures yourself when a vendor signs requests.

## Google Docs, Drive and Sheets nodes

- Docs chain: a pipe-table cell cannot hold a newline; escape `|`. `requireSpaceBeforeHeadingText:true` means `# Heading`. Read the builder with `$('Builder').first()` after Drive nodes; lineage does not survive them.
- `replaceAllText` keeps the placeholder run's formatting: style the template placeholder as the final text.
- Sheets `append` batches all items into one API call; per-minute write quotas do not apply to a bulk append.
- Shared Drive vs a shared My-Drive folder is an ownership question: the org owns Shared Drive files; a My-Drive file dies with its owner.
- Drive API parameters for Shared Drives: google-workspace skill.

## BigQuery node

Batch DML in one statement:
```sql
FROM UNNEST(JSON_QUERY_ARRAY(PARSE_JSON(@rows_json, wide_number_mode => 'round'))) r
-- JSON_VALUE(r,'$.col'), JSON_VALUE_ARRAY(r,'$.tags'), JSON_QUERY(r,'$.obj')
```
Dedup the batch on the merge key and add `QUALIFY ROW_NUMBER() … = 1` (a MERGE rejects a source matching a target row twice); use `WHEN MATCHED AND S.col IS NOT NULL` so a failed pass does not NULL-clobber. For a gated second LLM pass, give the gated column a dedicated single-writer UPDATE node and compute gate booleans in SQL. Test for overlap with `sum(executionTime)` vs wall span, never with the runs count. Verify a batched write on a `bq cp` clone: row count, fingerprint diff, 3x re-apply.

## LLM and agent nodes

- LM nodes attach with an `ai_languageModel` connection, tools with `ai_tool`; agent output is `$json.output` (a string unless a structured parser is attached).
- DB search tool: `n8n-nodes-base.postgresTool` with `$1` bound from `options.queryReplacement = {{ $fromAI('searchQuery','…') }}`; credentials by id, never inline. A private DB also needs an egress rule on the instance's network.
- Manual JWT mint (last resort): Web Crypto `RSASSA-PKCS1-v1_5` (Node `crypto` is often blocked), cache the token in workflow static data keyed by SA email with a 5-minute margin, `$helpers.httpRequest` to `oauth2.googleapis.com/token`. If Apps Script is available, broker through it instead.
- Gotenberg HTML→PNG: `POST /forms/chromium/screenshot/html`, the file part must be named `index.html`, `waitDelay` (not `waitForExpression`), `responseFormat:file`.
- Config for non-technical owners: read a Google Sheet at start, build one dynamic query plus a classification map from it.

## Examples
- An inventory audit indexed 2% of its records for a week because of the shallow pagination block.
- A 5.4 MB SOAP report arrived as a byte array after 291 green offline checks.
- A monthly "last day" trigger moved from day 1 would have reported the month before the one that just closed.

