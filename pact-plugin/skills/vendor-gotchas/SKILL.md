---
name: vendor-gotchas
description: "Use when integrating third-party APIs not covered by the Postgres or Workspace skills: Monday.com GraphQL, Slack, Jira REST and Forms, Telegram bots, Apify actors, ad, affiliate, outreach and CRM APIs, scraping services, Google client auth off GCP, and deploys on Railway (beside use-railway)."
---

Railway operations: use the vendor-installed `use-railway` skill. Field notes it lacks: [references/railway.md](references/railway.md). This skill never edits `use-railway`.

# vendor-gotchas

Context: save a wrong turn on the third-party APIs you integrate.
Every rule records vendor behaviour the docs omit or contradict, measured
in the field. Postgres/Supabase and Google Workspace APIs have
their own skills (see Related skills).

## Workflow
1. **Name the vendor and read [vendor-integration-discipline](references/vendor-integration-discipline.md) first.** Decide whether the fact you need is instance-, account- or version-specific; if it is, you will record the version or header and the date with every measurement.
2. **Open that vendor's reference before writing the first call** (Map below). A fact older than a few months, or without a version stamp, is a hypothesis: re-probe it.
3. **Probe the real response shape once and capture its bytes before coding against it.** Never write a fixture from the docs or from your own request (repacted:verification-discipline vd-c1b). Treat a 200 as a claim: check the body for `errors`, `ok: false`, `isError`, empty data or a thin page.
4. **Prove every filter and scope with controls**: compare against the unfiltered baseline and a nonsense term, and read a self-identifying field in the payload. Page to exhaustion and count; a round count is a cap, not a total.
5. **For writes, pass user text through the vendor's variables or parameter binding, NEVER string interpolation** (Monday quote-escaping is injectable), and read the write back from an independent read. Probe destructive or label-creating writes on a throwaway board, project or record, and delete it by the id you recorded.
6. **For Railway, run the operation with `use-railway`, then check [railway](references/railway.md)** for the traps: `railway.json` is not applied, SUCCESS may predate your push, the old image keeps serving after a failed boot, and cron services prove nothing until a heartbeat lands.
7. **Report with provenance**: the vendor, endpoint, API version or header, the account or instance, and the as-of date.

## Map
- [vendor-integration-discipline](references/vendor-integration-discipline.md): the cross-vendor method, REST-to-MCP migration, tolerant parser seams, money formatting.
- [work-management-and-chat](references/work-management-and-chat.md): Monday writes and encodings, Jira REST, Slack, Telegram, Discord, Teamwork, Confluence.
- [monday](references/monday.md): people lookups that fan out to the whole account, typed email variables.
- [jira](references/jira.md): Forms are intake-only, the Slack-modal write-back pattern, v2 wiki-markup comments.
- [crm-ads-affiliate](references/crm-ads-affiliate.md): Google Ads query language, an affiliate network, an outreach CRM (REST and MCP), HubSpot, Salesforce write-back, help-desk API versions, a course platform, a SOAP reporting service, OAuth1.
- [scraping-and-content](references/scraping-and-content.md): Apify dispatch, bot walls, rendered pages, Reddit, YouTube, CDNs, Cloudinary, GitHub, an LMS.
- [apify](references/apify.md): choosing and curl-testing actors, async actors, Sales Navigator.
- [railway](references/railway.md): config, builds, deploy verification, cron services, tokens, Next.js and Astro on Railway.
- [google-auth](references/google-auth.md): the GCE metadata probe that hangs Google clients off GCP.

## Related skills
- Railway platform operation: `use-railway` (vendor-installed; not edited here).
- Postgres and Supabase: `repacted:postgres-supabase`. Drive, Docs, Sheets, Gmail, Apps Script: `repacted:google-workspace`.
- Credential handling and SSRF review: `repacted:security-review-patterns`. Before reporting a count, absence or "done": `repacted:verification-discipline`.
- A new project that needs a vendor MCP server registers it locally: `claude mcp add --scope local <name> ...` from the project directory.

## Refine
At the end of a session that used this skill, run [self-refine](protocols/self-refine.md) and record what it changed.
