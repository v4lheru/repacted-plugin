# Jira Forms and REST v2 comments

Context: two Jira Cloud behaviours that decide a design: Forms cannot update an existing ticket, and
v2 comment bodies render wiki markup. The wider Jira REST notes (comment search, field forensics,
ADF, form field mapping) are in `work-management-and-chat.md`; read that too.

## Canonical rules
1. Jira Cloud Forms (ProForma) on a company-managed software project are intake-only: submitting the form link creates a NEW work item. There is no way to fill a form on an existing ticket (verified 2026-07).
2. A Confluence Jira-issues macro filters and shows ticket FIELDS only, never comments: form answers that must be reported need real custom fields.
3. Native Jira cannot show a field only when another is "Yes" without a paid app. Keep the fields on screen but UNPINNED: empty ones collapse in the new issue view.
4. To write a brief onto an EXISTING ticket: Jira Automation (field changed) sends a web request to n8n, n8n DMs the owner a Slack button carrying the issue key, the Slack modal submits to n8n, and n8n PATCHes `/rest/api/2/issue/{key}`.
5. REST v2 accepts plain strings for textarea fields, which avoids ADF. Carry the issue key, channel and message ts in the modal's `private_metadata`, then `chat.update` the DM.
6. Slack interactivity needs an EMPTY 200 within 3 seconds, or the modal shows "We had some trouble connecting". In n8n use `responseMode: responseNode` with an empty Respond-to-Webhook node FIRST.
7. Slack sends interactivity as `application/x-www-form-urlencoded` with a `payload` field of URL-encoded JSON: parse `$json.body.payload`. One endpoint can handle both `block_actions` and `view_submission`; a `trigger_id` lives about 3 seconds.
8. The DM must come from the SAME Slack app that has interactivity enabled, and callback URLs given to external callers must use the instance's public webhook host.
9. `POST /rest/api/2/issue/{key}/comment` with a plain string renders Jira WIKI MARKUP, not Markdown: `*bold*`, `_italic_`, `* item`, `{{mono}}`, `{color:#hex}text{color}`.
10. A leading `[TAG ...]` prefix parses as a broken wiki link and renders as a red error span on every comment. A bare issue key renders as a heavy inline card; wrap self-references in `{{KEY}}`.
11. Verify rendering empirically: post to a ticket you own, read it back with `?expand=renderedBody`, then delete the probe.

## Examples
- 1: the issue view had no "add form" in either menu; the on-issue form behaviour belongs to JSM and older ProForma.
- 6: n8n's default webhook acknowledgement returned a JSON body and triggered the modal's error banner.
- 10: a bot's `[BOT <timestamp>]` prefix put a red error box on every comment it wrote, which read to stakeholders as a failing bot.
