# Scraping and content APIs

Context: traps in scraping services and content APIs (Apify, Reddit, YouTube, CDNs, client-rendered
pages, Cloudinary, GitHub, an LMS) where a success response carries missing or wrong content.
Read this before building or debugging a scraper or content ingester. Actor selection and testing
rules are in `apify.md`.

## Canonical rules
### Apify
1. Fire actors async with `POST /v2/acts/{id}/runs?webhooks=<base64 JSON array>`: separate success and failure entries (`ACTOR.RUN.SUCCEEDED`, `FAILED`, `TIMED_OUT`, `ABORTED`), an `idempotencyKey` each, and correlation fields in `payloadTemplate`.
2. Webhook receivers must answer 2xx within 30 s; deliveries retry with backoff for about a day and may duplicate, so make the receiver idempotent. Fetch results from `/v2/datasets/{defaultDatasetId}/items`.
3. Run metadata is wrapped in `data`; dataset items are a plain array. Poll `/v2/actor-runs/{runId}` to a terminal status when not using webhooks.
4. A generic crawler that passes many anti-bot sites can return 0 rows on a particular platform while billing minutes per URL: use a dedicated platform actor, and remove that host from the generic-fallback allowlist so a 403 cannot double-dip.
5. Actor output fields can be nested objects where a string is expected (a job title as `{__typename, text}`): read defensively. Route by URL path when one site has several content types (a post actor returns nothing for an article URL).
6. Instagram post URLs return `restricted_page`: scrape the profile and match by shortcode. Facebook field names vary between runs (`likes` vs `reactionLikeCount`), and single-tweet lookups by URL are unreliable (verified 2026-04).
7. YouTube transcript actors anchor their input regex right after the 11-character video id: normalise every URL shape to `https://www.youtube.com/watch?v=<id>` before dispatch.
8. A browser-based crawler with residential proxies gets past many generic 403 walls where the site's terms allow it. Gate it with an explicit host allowlist matched on the registrable-domain suffix, and book cost against the daily cap first.

### Bot walls and rendered pages
9. Bot mitigation can arrive as HTTP 200 with a thin body (a fraction of the healthy size, no text). A throttle keyed on status codes is blind to it: assert a minimum extracted-content length.
10. Measure before raising delays (a 2.5x delay can change nothing), and falsify the cheap hypotheses (rate, session state, URL variant). Path-scoped, pattern-triggered and self-healing is the signature.
11. Report a deliberate bot block with the healthy and degraded byte sizes; do not defeat it with rotated user agents or proxies. Every other host at 100% in the same run localises it to the vendor.
12. Sanity-check every 200 body before parsing: a Cloudflare challenge (`cf-browser-verification`, `cf_chl_`, "just a moment...") or an empty parse sends the item to the next tier, never to an LLM.
13. On a client-rendered page the DOM is chrome and the content sits in an embedded JSON payload. Selectors proven on a sibling page type extract almost nothing, and a `<title>` fallback can name a different subject.
14. Before writing an extractor, count `//article`, `//h1`, `//main` and JSON `<script>` blocks on several pages and compare visible text length with the payload length.
15. Per-request tokens and trace comments make raw HTML differ between fetches; a hash over a markup-stripping projection can still be stable. Test the real projection before designing change detection.

### Reddit
16. Each Reddit listing caps at about 1,000 items: union `new`, `hot`, `top` (all, year, month) and `controversial` (all), deduped by id. `controversial` finds posts the others miss.
17. Expand full comment trees with `submission.comments.replace_more(limit=None)` then `comments.list()`; fall back to a bounded `limit` on giant threads. Deleted authors are `None` or throw: wrap access.
18. Reddit OAuth returns 403 with an HTML body (not 401) for a stale bearer token, while a real content 403 has a JSON body. Refresh and retry once on a non-JSON 403. An empty `User-Agent` also 403s the token mint.
19. When Reddit JSON 403s from datacenter IPs, `https://old.reddit.com/comments/{id}` serves full server-rendered HTML. Extract the id with `/(?:\/comments\/|redd\.it\/)([a-z0-9]+)/i` from any URL shape.
20. old.reddit's sidebar `div.md` comes before the post: anchor parsing on the `sitetable linklisting` container. Link and image posts legitimately have empty selftext; require selftext AND comments empty before calling it "no content".
21. Some Reddit actors return 0 items for a bare `/comments/{id}` URL and need a subreddit-bearing URL or `redd.it/{id}`: curl each actor's URL-form sensitivity before wiring. When an actor returns no body, store `''`, never synthetic text.

### YouTube, CDNs and media
22. YouTube `videos.list?part=statistics` can OMIT `viewCount` on healthy public videos (other counters present). Parse every counter independently to `null` when absent; never `?? "0"`, and widen the type to `number | null` (verified 2026-08-20, Data API v3).
23. A nullability rule has two cases: key absent and CONTAINER absent. When a whole reading is lost, write no row, or "latest row" consumers let the null row outrank a good earlier one.
24. An image CDN size token in the URL (`shrink_800_800`, `=w800`) is the requested MAXIMUM; smaller originals come back unscaled. Classify by decoded pixel dimensions and store width and height.
25. Signed CDN URLs are path-bound: rewriting the size token returns 403, so "request a bigger one" cannot work. Derive the file extension from magic bytes, not the source's filename column.
26. Cloudinary `l_text` overlays render text PNGs for free: double-encode `,` as `%252C` and `/` as `%252F`, upload custom fonts as `raw` + `authenticated` with the extension and no underscores, and remember `letter_spacing` is pixels.
27. A wrong Cloudinary font id or type falls back to the default font silently; unsigned overlays need the overlay media-type restriction off (symptom: an `x-cld-error` header). The canvas height is fixed by the base image.

### GitHub and an LMS
28. GitHub `?author=<login>` counts only commits whose email is verified on the account. Census identities (name, email, linked login), count per email (`?author=<email>` with `Link rel="last"`), and check the buckets sum to the repo total.
29. Placeholder commit identities can attribute to a stranger's dormant account: ask the owner whether they wrote them from an unconfigured machine. `affiliation=owner,collaborator,organization_member` sets overlap: never sum them.
30. For exact commit counts use `per_page=1` and the `Link rel="last"` page number; `/stats/*` often answers 202 or null. Detect deploy files from the full tree listing, and parse dependency NAMES rather than substring-matching manifests.
31. LMS lesson text lives in a per-lesson content-items endpoint, not on the lesson object. Lessons have no URL field; courses do (per domain).
32. An invented LMS lesson URL returns 200 while an invented course slug returns 404: harvest real lesson links from the course page, never construct-and-status-check. Probe an invented sibling at each grain first.
33. The LMS course list is org-scoped (test catalogs included); map courses to domains through the per-domain published-course list. The lesson list needs a course id, and a wrong id returns 200 with a zero count.
34. LMS auth is HTTP Basic with the API key as username and an empty password; a mangled key returns 401 (the negative control). A 403 on a public site may only need a realistic user agent.
35. Raw HTML length and extracted text length have no fixed ratio across sources: state which one a threshold uses and never convert between them.

### Design resources
36. The awesome-design-md repo's per-site files are now stub READMEs: read the real DESIGN.md from a pre-migration commit via `gh api "repos/<owner>/<repo>/contents/<path>?ref=<sha>"`. Stitch-format DESIGN.md files have nine fixed sections (verified 2026-04).

## Examples
- 4: a generic crawler ran about 2 min on each of 8 review-site URLs and returned nothing; a dedicated actor returned 30 reviews per URL.
- 9, 10: a vendor's pages came back at about 70 KB instead of 530 KB; delays of 1.0 s and 2.5 s gave identical failure counts.
- 13: story pages had no `<article>` or `<h1>` on 80 of 80 pages, and `<title>` matched the real title on 4 of 80.
- 16: `new` gave 995 posts; the union of listings gave 1,394.
- 18: 386 Reddit fetches failed in one run because only 401 triggered a token refresh.
- 22: six public videos had likes and comments but no `viewCount`; `?? "0"` wrote 504 fabricated zero-view rows over three months.
- 24: 49 of 503 headshots with an 800 px token were under 512 px, some 96x96; a URL test missed 62% of low-resolution images.
- 28: a login-only count gave 584 commits against a true 1,776.
