# Apify actors

Context: how to choose, test and call an Apify actor so it does not fail silently. The async
webhook dispatch pattern and per-platform scraping traps (Reddit, YouTube, bot walls) are in
`scraping-and-content.md`; read that too.

## Canonical rules
1. Curl-test every actor on a REAL failure URL from your workload before wiring it in. Input schemas advertise fields that do not work, and a test on another site proves nothing for this one.
2. HTTP 201 with `[]` from `run-sync-get-dataset-items` is a SILENT failure: the actor "succeeded" with no data. When items are 0, read the run log (`/v2/acts/{actor}/runs?limit=1&desc=1`, then `/v2/logs/{runId}`) for the internal error.
3. Pick candidates by success rate (above 95%), rating and a real review count and user base; skip high ratings from very few reviews. Compare 2-3 via `fetch-actor-details` for schema and pricing.
4. Check cost per URL times the expected failure-URL count against any daily cost cap before wiring.
5. Prefer an actor that needs no personal cookie for anything a non-technical user triggers: a required `cookie` field means a human re-harvests it on every expiry.
6. Some actors are ASYNC on the vendor's own backend: the Apify run finishes in seconds and a `ACTOR.RUN.SUCCEEDED` webhook fires with nothing behind it. Poll the actor's own status contract instead.
7. The two-step Sales Navigator contract: run `{sales_url, limit}` to get `request_id`, then run `{request_id, page}` and poll until `status` leaves `pending`/`processing` and `data` arrives.
8. Long boolean keyword queries (hundreds of characters of OR'd titles) degrade SILENTLY to a near-unfiltered result set. Split into short searches (4-6 titles) and sample the location and title distribution before trusting a set.
9. Sales Navigator URLs need literal outer parentheses with double-encoded content (`"` as `%2522`, space as `%2520`). Have the user build the search in the UI and paste it; surface `Invalid keywords` rather than writing an empty sheet.
10. Page 1 can be ONE dataset item wrapping an array (`{data:[...], message:"ok"}`): split it out, or you silently get a one-row sheet.
11. Sales Navigator rows carry an opaque member URN as `linkedin_url`, no email and no company domain. Enrichment is load-bearing, and it must match on name plus organisation, not on the URL.
12. Apollo `GET /api/v1/auth/health` reports the API's health, not your key's: it says healthy for a dead key. Validate a key against a real endpoint that returns 401 for a bad one.

## Examples
- 1, 2: three Reddit actors returned empty datasets or `HTTP 403` because they appended `.json` behind the scenes; a crawler validated only on a news site returned no results on every review-site URL in production while spending minutes per call.
- 6, 7: a Sales Navigator actor's run finished in about 4 s; 100 leads were ready after about 156-195 s of polling (verified 2026-08-29).
- 8: a 90-character, 4-title query returned about 80% on-profile leads; a 480-character, 22-title query returned 10% on-profile and 85% from the scraping pool's home country.
- 11: 100 of 100 rows carried the opaque URN, so URL-based enrichment would have matched nothing while looking healthy.
