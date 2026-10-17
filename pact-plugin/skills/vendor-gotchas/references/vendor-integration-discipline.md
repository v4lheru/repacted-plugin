# Vendor integration discipline

Context: the cross-vendor method for integrating any third-party API or vendor MCP server so that a
confident, well-formed, WRONG answer does not reach production. Read this first, before the
vendor's own reference, whenever you integrate, migrate or debug a third-party API.

## Canonical rules
1. Third-party APIs return confident 200s that are wrong: ignored filters, field sets that depend on a version header, clamped pagination, errors inside a 200 body, size tokens that are only upper bounds. A 200 is not evidence.
2. Record the vendor, the endpoint, the API version or header, and the date with every measured fact. A fact without its version is a guess the next reader cannot re-check.
3. Probe the real response shape once and capture its bytes before coding against it. Never write a fixture from the docs or from the request (repacted:verification-discipline vd-c1b).
4. For writes, pass user text through the vendor's variables or parameter binding, never by string interpolation, and read the write back (quote-only escaping into GraphQL is injectable).
5. Before optimising a vendor integration, enumerate its FULL surface (for an MCP server, a raw JSON-RPC `tools/list`, which is free and read-only) and diff it against what you call. The gap is often the vendor's native workflow.
6. MCP tool SCHEMAS are free measurement: they carry limits, clamps, scope filtering and required parameter pairings. Read them before planning, but where a schema contradicts a live measurement, the measurement wins.
7. Migrating from a vendor's REST API to its MCP server is not a transport swap. Three properties invert, and all turn "could not read" into "there is nothing": delegated visibility, clamped pagination, errors inside a 200.
8. MCP access is often USER-DELEGATED: an account-level REST credential saw everything, the user token sees only that user's assignments, and an account-level read returns a SHORTER list with 200. Ask for a dedicated service user.
9. "Not found" in schema prose does not say whether a call errors or returns empty: measure it with a positive control. Some visibility switches are role privileges no scope grant restores; test with two real identities.
10. MCP pagination is offset/limit with low defaults that some tools clamp. A REST client that exhausted `nextPage` for every caller hands that duty back to every call site at once: page to exhaustion and count.
11. A JSON-RPC transport must throw on an error envelope, on `isError: true` inside a 200, and on a content block that is not the expected payload. Never let a tolerant parser turn prose into an empty array.
12. Land the error-shape conformance test BEFORE the adapter: the existing mock defines what an error is, so degrade tests stay green whichever way the real adapter behaves.
13. Before a transport swap, grep every predicate that BLOCKLISTS the old vendor shape (for example "not an http URI"): the new transport's unresolved value passes it. Invert to an allowlist of what a good value looks like, with one owner.
14. An MCP session id cached without invalidation is a process-lifetime outage after the first idle timeout. Clear it and retry with the retry decision carried ON the error, re-enter session setup INSIDE the retry, and assert the returned content.
15. A bulk expansion that removes one N+1 can require a parameter that creates another (one call per scope item). Check the cost before adopting it.
16. When a credential changes from an unscoped account secret to a scoped, delegated one, every containment argument premised on "the credential is god-mode" has expired: have security re-derive, not re-cite.
17. Building against an UNCONFIRMED vendor API: confine every shape assumption to ONE tolerant parser seam (multi-key lookups, passthrough schemas), keep raw vendor shapes out of business logic, and list the assumptions in the HANDOFF.
18. A client-level mock returning already-parsed objects cannot catch a parsing bug: mock the TRANSPORT and feed real raw response text through the real parser.
19. `Number.toLocaleString('en-US')` defaults to three fraction digits, so money renders as `$1,136.229`. Pass `{ maximumFractionDigits: 0 }` (or 2 and 2 for cents), and grep every money helper in every render layer in one pass.

## Where the vendor-specific traps live
- Silently ignored filters, unsigned webhooks, OAuth1 signing, version-headered field sets (course platforms, help desks, outreach CRMs, affiliate networks, Google Ads, HubSpot, Salesforce): `crm-ads-affiliate.md`.
- Discord 204 semantics, Monday, Jira, Slack, Telegram, Teamwork, Confluence: `work-management-and-chat.md` (plus `monday.md`, `jira.md`).
- CDN size tokens, YouTube counters, bot walls, Reddit, Apify: `scraping-and-content.md` (plus `apify.md`).
- Redirect credential stripping by header name: repacted:security-review-patterns. Drive restricted scopes and ACL diffs: repacted:google-workspace. Query scoping that fails both ways and image validation by header: repacted:verification-discipline.

## Examples
- 5: a vendor MCP server listed 26 tools; the client used 5 and skipped the vendor's plan, discover and refine workflow.
- 8, 10: a do-not-contact loader got 20 of 26 rules at the default page size and marked itself healthy, because it degraded only on a throw (verified 2026-09-14).
- 9: a bad project id returned an ERROR, not an empty list, while a real id returned 52 rows; the prose had suggested empty.
- 13: a "resolved label" check that rejected http URIs would have admitted a bare numeric option id into a client-facing CSV.
- 14: a mutated retry returned the session-initialise response text as the tool's answer, and a "promise resolves" test passed.
- 17, 18: a vendor MCP returned markdown text instead of JSON; the tolerant seam degraded with 0 credits spent, while a parsed-object mock had let the shape bug through.
- 19: one of three money helpers leaked `$1,136.229` into a report.
