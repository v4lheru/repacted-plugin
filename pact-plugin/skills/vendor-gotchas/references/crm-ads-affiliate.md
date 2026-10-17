# CRM, ads and affiliate APIs

Context: measured traps in Google Ads, an affiliate network, an outreach CRM (REST and MCP), HubSpot, Salesforce
write-back, help-desk API versioning, a course platform, a SOAP reporting service and OAuth1 signing. Read the
section for your vendor before the first call. The cross-vendor method is in
`vendor-integration-discipline.md`.

## Canonical rules
### Google Ads API (GAQL)
1. Policy and approval fields (`ad_group_ad.policy_summary.*`, `primary_status`) are CURRENT-STATE only. Adding `segments.date` does not error: it stamps TODAY's value onto every historical row, so a date-filtered backtest passes for any date.
2. Detect current-state stamping with an IMPOSSIBLE combination: a disapproved status beside non-zero impressions on the same dated row. If a dated query returns the same value for every date, suspect stamping.
3. Current state is perishable: when diagnosing through it, snapshot it first, bracket the capture with two timestamps, write a manifest naming the current-state fields, and keep it out of `/tmp`. Approval history exists only if you poll and store it.
4. Put a "no `segments.date` here" comment next to every policy query: someone will add a date filter in good faith.
5. Enums arrive as INTs, and one object can mix representations (`primary_status` int, `primary_status_reasons` strings). Decode through the library's `enums.*` at the acquisition boundary; never compare to a string or a literal.
6. `PolicyApprovalStatus` DISAPPROVED is 2 and APPROVED_LIMITED is 3, adjacent: the wrong constant gives a plausible count. When a count is 0 or round, recompute with the neighbouring constant. Persist raw ints in fixtures, never a derived boolean.
7. Serving eligibility: do NOT derive it from `campaign`, `ad_group` and `ad_group_ad` status all ENABLED (an ended campaign stays ENABLED). Use `ad_group_ad.primary_status_reasons`; `AD_GROUP_AD_DISAPPROVED` discriminates.
8. `primary_status == ELIGIBLE` alone is a trap: disapproval REMOVES an ad from the eligible set, so "disapproved among eligible" finds 0 during an outage. Define eligibility as "would serve but for the problem".
9. Filter statuses server-side in GAQL to cut rows, then fold `final_urls` across the disapproved set: many disapprovals often share one landing page.
10. `policy_topic_entries[].evidences` comes back as `[{}]`: `evidences?.length` is truthy and the detail is empty. Guard on a concrete inner field.
11. `login-customer-id` is per ACCOUNT: the manager id for a managed sub-account, and omitted for a direct account (sending the manager id there is PERMISSION_DENIED). Drive it from a nullable parent column. Customer ids are digits only.
12. The Opteo `google-ads-api` library version pins the Ads API version: pin the library. `customer.query(gaql)` returns an array; money fields are micros.
13. GAQL: `segments.date IN (...)` is rejected; use `BETWEEN 'a' AND 'b'`. Campaign labels come only through the `campaign_label` link resource (filterable by `label.id`, not name); fold them in memory.
14. `change_event` has a hard 30-day wall (an older start is an ERROR; the boundary is exclusive), needs a `LIMIT` of at most 10,000 (a result equal to the LIMIT is truncated), and never carries Google-side policy decisions.
15. `change_event` old and new resources contain only the changed fields: `changed_fields.paths.includes('status')` identifies a status change, and an absent old status means CREATE. `client_type` is an int enum.
16. Segment `change_event` by `client_type` (scripts vs web client), not by `user_email`: a script runs under a human's account. Print raw events before characterising a distribution; to-the-second clusters are a schedule.
17. `change_date_time` has NO timezone: it is in the account's zone (`customer.time_zone`). Append the offset computed for that instant (DST-safe, verified by round trip), and copy the six fractional digits verbatim; never go through JS `Date`.
18. Retention: `segments.hour` history goes back well over a year, so metric detectors are backtestable. Impression-share metrics are Search-only (Performance Max reports none). `conversion_lag_bucket` works `FROM campaign`, not `FROM conversion_action`.
19. Spend barely moves when ads die (surviving ads absorb the budget): alert on impressions or clicks. Fire on a channel SEGMENT; name the worst campaign behind a volume floor; report the worst control-day value beside the incident value.
20. Quota counts OPERATIONS: one Search or SearchStream is one op whatever the row count, and failed requests count. Batch one query per account.
21. A "paused unexpectedly" alert is a TRANSITION (store the prior status and diff), and a budget script's own pause label is a non-event: emit nothing and leave an open alert's state untouched.

### Affiliate network (advertiser side)
22. Amount and revenue fields can be a 0/1 conversion flag, not money. The real dollar fields are what you pay affiliates, not what you earn. Check what each field means before you sum it.
23. Some reports need an undocumented filter (`SUBAID=<campaignId>`) and return HTTP 200 with `@total: 0` without it. Run a positive control before believing an empty report. An account can run several campaigns: fetch each.
24. Actions API: ISO-8601 datetimes only, every numeric is a STRING, a 45-day maximum range (chunk at 30), a page cap (a round count is the tell), and `@nextpageuri` is `""` when done, not null.
25. Reports return a SUBSET of their declared attributes, and parameter conventions differ per report. A 401 can be per CAMPAIGN. Most actions are PENDING and a large share reverse: upsert approved only.

### Outreach CRM
26. The REST write contract is the public `swagger.json`; its write bodies are a generic wrapper, so write-probe against a throwaway TEST project (create, prove, delete in reverse order: state, website, project).
27. `?search=` is FUZZY on `/v1/websites` (exact-match `primaryDomain` client-side, never trust `numResults`) and exact-substring on `/v1/projects`. A structured `?project=` filter gives an exact `numResults` in one call.
28. Paging params are `offset` and `max_results` (not `pageSize`); read the vendor's own `nextPage` URL for real param names. Ignored params return the full set: compare with the unfiltered baseline AND a nonsense term.
29. REST `nextPage` DROPS the query filter: following it returns the unfiltered set reset to page 1 (contamination, not truncation). Carry the filter manually.
30. `relationshipStage` is an option URI, not a label: resolve at the client boundary through `custom_field_options` (built lazily, cached, empty map on failure), and rebuild a relative path from the digits rather than following the URI.
31. Websites are account-global and `project_states` scope them; a project's `websites` count can be 0 while it holds hundreds of people: emptiness is the AND over every collection. Project search is global across clients.
32. The MCP surface differs: filters hold across `offset`/`limit` pages, the history listing carries the project id inline but omits some row types (counts change on migration), and the type field is not a partition.
33. Its MCP project-context call returned the PEOPLE option list for the media stages (a vendor defect): resolve stages from the custom-field listing instead.

### Other vendors
34. Pinned-id comparisons must be three-valued (`yes | no | unevaluable`): verify the pin against the vendor's own vocabulary and fail closed when it cannot be loaded. Pin the literal the SOURCE emits in tests, built from live DISTINCT values.
35. OAuth1 two-legged signing: key `percentEncode(secret) + "&"`; base string `METHOD & enc(baseUri) & enc(sorted oauth_* + QUERY params)`. A JSON body is NOT signed (only form-encoded bodies are). Test with a vector; run the SSRF host check BEFORE attaching auth.
36. A static two-legged secret needs no token store, rotation or lock: do not mirror a rotating-refresh integration's machinery.
37. Help-desk APIs: a version header selects the FIELD SET. Pin it, re-measure under the pin, diff fields both ways, and record what the pin forgoes. A 401 on the identity endpoint can mean a valid token with the wrong scope, so the identity call is a bad health check.
38. HubSpot: GET the property and object schemas before any write. `hs_marketable_status` is read-only without Marketing Hub, required properties need non-null fallbacks, association type ids are numeric and DIRECTIONAL, custom objects need the `p<portal>_<name>` form. Each error fails the whole batch.
39. HubSpot's REST errors properly; its MCP returns success with blank fields for an unknown object. Derive roll-ups (read, compute, write) rather than incrementing.
40. Salesforce write-back (simple-salesforce, JWT bearer): a lazy thread-safe singleton under the session TTL, calls in `asyncio.to_thread`, target resolved by live SOQL, and manual SOQL literal escaping (raw `sf.query` is not parameterised).
41. Map Salesforce field-level-security errors (`INSUFFICIENT_ACCESS_OR_READONLY`, `INVALID_FIELD_FOR_INSERT_UPDATE`, `FIELD_INTEGRITY_EXCEPTION`) to a typed 403; boot without credentials; record the real user in your own audit log.
42. A course platform (an `apiKey` header): query filters are SILENTLY ignored (full list at 200), there is no enrollment id or coupon endpoint, webhooks are unsigned, and the webhook events list is empty. Page via `meta.number_of_pages` and match client-side.
43. A SOAP reporting service: prefer its WS-Security report endpoint with SOAP 1.2 and HTTP Basic (no credentials in the body). Parameters sit directly under the report's parameter list; the WSDL is public, so fetching it is no auth test.
44. The decoded `reportBytes` format is per REPORT (XML or BOM'd CSV): read the first bytes before choosing a parser, match `(?:\w+:)?reportBytes`, and parse CSV with RFC 4180 (quoted newlines). Route SOAP faults to an "inconclusive" branch.

## Examples
- 1, 2: 67 ads that served impressions on a past date reported DISAPPROVED on that row, 61 of them (verified 2026-08-18, Ads API v23).
- 7, 8: the three-status AND called 188 ads eligible, 92 of which had ended; `primary_status == ELIGIBLE` matched 6.
- 16: one email sat on 308 of 339 status transitions; split by `client_type`, medians were 12 h for scripts and 74 h for people.
- 19: during a disapproval outage, spend fell 21% while impressions fell 53%.
- 22: a month's "revenue" summed to a small fraction of both the action count and the payout cost, which only fits a 0/1 flag.
- 29: page 1 of `?search=2026` gave 180 results; its `nextPage` returned 183, reset to page 1.
- 37: `body_markdown` was on 250 of 250 articles unversioned and on 0 of 250 under a pinned version.
- 44: a line split produced 7,616 "rows" from 5,875 records.
