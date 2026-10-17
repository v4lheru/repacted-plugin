# Work management and chat APIs

Context: measured traps in Monday.com, Jira, Slack, Telegram, Discord, Teamwork and Confluence,
where a write silently no-ops or corrupts, or a read silently misses. Read the section for your
vendor before the first call. People-column lookups are in `monday.md`; Jira Forms and wiki-markup
comments are in `jira.md`.

## Canonical rules
### Monday.com GraphQL
1. Endpoint `https://api.monday.com/v2`, header `Authorization: <token>` with NO `Bearer`, plus an `API-Version` header. Errors arrive as `{errors:[...]}` on HTTP 200: check the body.
2. Pass `column_values` as a JSON-encoded string through a GraphQL VARIABLE, never by interpolating into the query with quote escaping (shown injectable).
3. `create_item` is ATOMIC on column errors: one unknown status or dropdown label (matching is case- and whitespace-sensitive) loses the whole submission. Normalisation tables must be total, with a valid fallback.
4. Intake-form constants rarely match the live board's labels: diff against `columns { id title type settings_str }` and map by the column's live TYPE. Keep `create_labels_if_missing` off (it silently adds junk labels) and fix the mapping.
5. Encodings: `status` `{"label":"X"}`; `dropdown` `{"labels":["X"]}`; `people` `{"personsAndTeams":[{"id":N,"kind":"person"}]}`; `date` `{"date":"YYYY-MM-DD"}`; `text` a bare string ONLY; `long_text` a bare string or `{"text":"a\nb"}`.
6. A two-value status write (`{"label":"High, Critical"}`) returns the SAME error as a renamed label. Detect multiplicity before writing; prefer a dropdown where multi-select is plausible. Branch on `extensions.code`, not the error prose.
7. `update_status_column` without each existing label's `id` silently REINDEXES labels, changing what every item means. Read `revision` + `settings_str`, keep every id, add new labels without ids and with unique colours; prefer the UI.
8. `create_column` `defaults` differ by type (status: an id-to-name map; dropdown: `{"settings":{"labels":[{id,name}]}}`), and a wrong shape is a silent 200 with no labels. Re-read the columns independently afterwards.
9. The `file` column cannot be written through `column_values`: the write "succeeds" with `assets: []`. Use multipart `add_file_to_column`, and check `assets`, never `value`.
10. `users(emails: null)` returns the whole account; look users up by exact email only (`users(name:)` matches bots and unrelated people). An organisation's email local-parts may not be derivable from display names.
11. Assigning a non-subscriber to a people column works and does not subscribe them. A people column needs a Monday user id, not an email string.
12. Pagination uses `items_page` first and `next_items_page(cursor)` after: different root fields. For incremental sync, order by `__last_updated__` and stop at the last sync time.
13. Rate limiting is account-wide (`API_TEMPORARILY_BLOCKED`): heavy testing can block production workflows on the same account. Probe on a throwaway private board and delete it by its recorded id.

### Jira REST
14. Comments: there is no cross-issue comment search. Use `GET /rest/api/3/search/jql?jql=... AND comment ~ "\"phrase\""&fields=summary,comment` (paged by `nextPageToken`) and filter dates client-side.
15. `/search/jql` `total` can be 0 while issues return: trust the array. `expand=renderedBody` gives HTML instead of ADF. `text ~` includes comments; `textfields ~` does not.
16. v3 bodies are ADF: walk the tree by node type and emit a newline on `hardBreak`. Bot-written descriptions are one paragraph split by hardBreaks, so a walker without it collapses every key-value line into one.
17. Field audits: `/rest/api/3/field/search` and `/rest/api/3/field` return DIFFERENT sets; union them, and carry a known-present field as a positive control before any absence claim.
18. A global field context reports ZERO project and issue-type mappings: that is universal coverage, not none. Cross-check with JQL `"Field" is not EMPTY`.
19. `editmeta` on real issues in several statuses is the check for whether an API PUT can set a field. A renamed field is found through `editmeta`, and its context name keeps the ORIGINAL field name.
20. Before mapping form answers to fields, check `schema.custom` types: same-named fields of the wrong type (a pick-list "Product Area") are traps, and renaming a global field hits every project. Prefer fresh prefixed text fields.
21. A tenant's real brief flag can be a global option custom field behind an opaque id (`customfield_<n>`); option ids change per tenant, so re-verify.
22. Jira automation endpoints answer 404 even on your own project: a capability gap, not a permission one. Prove Forms is on with `/rest/api/3/project/{KEY}/features` (`jsw.classic.forms`).
23. `/rest/plugins/1.0/` (with the installed-plugins Accept header) lists Marketplace apps under a plain basic-auth token. Form template internals need OAuth 3LO.
24. To learn which account an integration writes as, read `author` from `/issue/{key}/changelog`, not `/mypermissions`. Fields set at creation leave no changelog entry.

### Slack
25. A Slack bot token with read scopes may already exist in a project: run `auth.test` before declaring "no access", and answer documented, available and deployed separately. It may also hold write scopes: mint a scoped bot when a boundary matters.
26. Find channels by listing every channel where `is_member` is true, not by regex (`product.?update` misses `production-updates`). `conversations.info` needs `include_num_members=true`.
27. Posts in a templated announcement channel followed the channel's posting template only rarely; a leading product emoji is the reliable scope key; threads hold corrections.
28. `conversations.history` re-serialises posted unicode emoji as `:short_name:`: classify by prose and structure, gate deletes on your own bot identity (`auth.test`), and dry-run first.
29. `chat.update` overwrites the prior state: a post-then-edit sequence destroys the message under review. Render every state to stdout before any network call.
30. `chat.postMessage` and `users.info` can return HTTP 200 with `ok: false`: branch on `ok`, never the status code, and store the response for audit.
31. Workflow Builder messages are `bot_message`s identified only by `workflow_id` (`Wf...`) and `trigger_id` (`Ft...`); they have text but no `user`. Fields appear as `*Label:* value` or `*Label:*\nvalue`: parse both.
32. The n8n `slackTrigger` `options.userIds` does not filter (verified 2026-08-13): restrict senders downstream. Edit and delete events have no top-level `text`.
33. `workflows.triggers.list` (`triggers:read`) lists every Workflow Builder trigger, so a `trigger_id` allowlist can be built before any submission; its `workflow.id` is an `Fn...` id, not the payload's `Wf...`.
34. Resolving roster names to Slack users needs three outcomes: active, deleted, unresolved. Only `deleted: true` on a MATCHED account proves a leaver; an unmatched name is a spelling problem.
35. Match order: accent-normalised name variants, then a derived work email, then diminutives (exact last name, 3+ shared leading letters, exactly one active candidate). Dedupe candidates by user id before judging ambiguity.
36. Split both name shapes (`Last, First`, `First Last`) in one function. Before trusting a fuzzy rule, replay it over every active member and count wrong-person matches.
37. `users.list` is rate limited, and a truncated roster fails silently: use `limit=1000`, space the pages, retry, and assert a minimum member count that THROWS.
38. Normalise Slack markup (`<url|label>`, `<#C...>`, `:emoji:`, `&gt;`) before text reaches retrieval or an LLM. A blank `user` means a bot post, but `bot_id` presence does not mean a bot.

### Telegram, Discord, Teamwork, Confluence
39. Telegram `callback_data` is capped at 64 bytes: use a short verb plus an 8-hex id prefix, validate `/^[0-9a-f]{8}$/`, look up with `.limit(2)` and fail on more than one match.
40. Telegram retries callbacks, so handlers must be idempotent: a conditional UPDATE on the prior value, checking affected rows via `.update(...).select('id')` and `data.length`. Collapse the keyboard in place to a ticked `noop` button.
41. One `reply_markup` per Telegram message: send one message per actionable item, capped at a handful, and put the rest in text.
42. Discord `PUT /guilds/{g}/members/{u}` returns 204 for an EXISTING member and discards `roles`. Always also `PUT .../roles/{r}`, re-fetch and assert. Bots need a real `User-Agent` and a role strictly above the granted one.
43. Stub a 204 with `new Response(null, {status: 204})`: 204, 205 and 304 are null-body statuses and a string body throws.
44. Teamwork v3: the base URL comes from the token's `installation.apiEndPoint`; OAuth2 tokens are permanent with no refresh; webhooks are managed only through V1 (HMAC in `X-Projects-Signature`).
45. Teamwork filters are inconsistent: tasks use `completedAfter` (ISO) with `includeCompletedTasks=true` and `responsiblePartyIds`; time entries use `assignedToUserIds`. `latestactivity` caps a page: narrow the date window.
46. Teamwork `people` returns active users only: get deleted users' names via `include=users` on time entries. The documented rate limit trips earlier in practice (re-probe before relying on a number).
47. Confluence: labels can be empty, so scope CQL by `ancestor=<id>` (transitive). `/content/search` returns `totalSize: null`: count results. Full URL = the response's top-level `_links.base` plus each result's `webui`.
48. Record a Confluence page's version and last-updated date beside any status you quote: statuses expire silently. Tool credentials often sit in that tool's skill directory: look there before claiming no access.

## Examples
- 7: an item reading Critical became Medium after a label update without ids (verified 2026-08-13, API 2026-01).
- 16: a key-value parser found `Legal Entity` on 0 of 100 tickets without `hardBreak` handling and on 94 of 100 with it.
- 17: `/field/search` gave 595 custom fields and `/field` 594; their union was 651.
- 29: one post plus one `chat.update` left a review channel showing only the resolved state.
- 32: a bot's `userIds` allowlist of 4 ids let 129 of 129 payloads through; 17 of 54 triggers on that instance set it.
- 35, 37: without diminutives, every residual failure was a real person; a roster paged at 200 hit 429s on pages 33, 41 and 42 of a large workspace.
