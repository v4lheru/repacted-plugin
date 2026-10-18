# Build: Slack and email nodes

Measured behaviour of the n8n Slack node, Slack triggers and interactivity, and Gmail/email parsing. Split out of build-nodes-and-integrations.md to keep each file short. Read when a workflow sends to or listens on Slack, or parses inbound mail.

## Canonical rules
1. Thread with `$('Post Parent').first().json.ts || $('Post Parent').first().json.message.ts`; an empty `thread_ts` does not error, it posts to the CHANNEL.
2. `blocksUi` must be a Block Kit envelope `{"blocks":[…]}`; a bare array silently posts only the text fallback. Verify the posted `message.blocks`, not the send.
3. With `messageType:"block"`, a non-JSON `blocksUi` throws on every run; inside an error workflow that means the alert never fires.
4. Build blocks as JS objects in a Code node and `JSON.stringify({blocks})`; never interpolate user fields into hand-written block JSON (injectable).
5. `otherOptions.includeLinkToWorkflow` defaults to TRUE when absent and leaks the internal workflow URL. Set it false on every send node; assert absent-or-true as the failure.
6. The Slack node's `user` selector just sets Slack's `channel`: keep `select:'user'` and make the value a ternary for test-mode routing.
7. `sendAsUser` needs the `chat:write.customize` scope or is a silent no-op; read scopes from the `x-oauth-scopes` header on `auth.test`.
8. One Slack app per Slack Trigger (the Events API allows one Request URL per app); outbound calls can share one bot token.
9. Buttons and modals need a generic Webhook node (the Slack Trigger is Events API only): parse the urlencoded `payload`, ack within 3 s, verify the signing HMAC yourself.
10. Thread replies do not carry the parent text: fetch `conversations.replies` and classify `messages[0]` in a Code node (which can drop unknown types).
11. Correlate an async human reply by embedding the record key in the outbound message and regexing it back out; never auto-advance on an ambiguous match.
12. A private channel the bot cannot see returns `channel_not_found`, not `not_in_channel`; catch any error rather than enumerating codes.
13. Email links live only in HTML anchors: fetch the full message (Gmail Trigger `simple:false`) and hash dedup keys on extracted fields, not raw HTML.
14. Some providers send a stub `text/plain`; choose the body per provider, and use a newline-preserving HTML-to-text for line-based parsing.
15. The Gmail Trigger emits `from`/`to` as address objects: `String(j.from)` is the truthy `[object Object]`. Normalise the shape; use `json.date` for receipt time.
16. Gmail `.text` is often a markdown rendering; strip `* _ >` noise and extract addresses with a tight regex.
17. Add a within-run seen-set beside any persistent dedup ledger; two copies in one poll both read an empty ledger.

## Slack node details
- Parent-plus-thread digest: one item `{blocks, thread_replies:[…]}` → Post Parent → a Code node expanding replies (downstream of the parent, so `ts` exists) → Post Reply. Chunk at about 40 blocks / 2,900 characters (Slack caps 50 / 3,000) and label `part N of M`. No Always Output Data on the reply node, or an all-clear run posts a blank reply.
- Threading field: `otherOptions.thread_ts.replyValues.thread_ts`.
- Hide a whole block conditionally: a block with empty text is rejected, so emit the entire block plus a trailing comma from a `{{ (() => …)() }}` IIFE at array level, or `''`. Validate both branches with a JSON parse.
- Safe defaults on every send node: `includeLinkToWorkflow:false`, `unfurl_links:false`, `unfurl_media:false`, and `sendAsUser` where the scope exists. On one instance all 29 `sendAsUser` uses were typeVersion 2.3 (verified 2026-09-16); newer versions move toward `botProfile`.
- Zero-delivery target probe: `chat.scheduleMessage` with a future `post_at`, then `chat.deleteScheduledMessage`. A bogus id fails `channel_not_found`, so the probe can go red.
- Extra Web API methods (`users.lookupByEmail`) from an HTTP node with `nodeCredentialType:slackApi` and `onError:continueRegularOutput` (not-found arrives as 200 `ok:false`). Branch before the node when the input may be blank.

## Slack apps, triggers and scopes
- Get the Events Request URL by activating the workflow first; n8n answers the URL challenge only while active.
- Scopes: DM send `chat:write` + `im:write`; lookup by email `users:read` + `users:read.email`; a trigger reading DMs `im:history` + event `message.im`; reading a channel `channels:history` + `message.channels`.
- A trigger that lacks its history scope yields zero items whether broken or perfect: prove the input half separately.
- Modal flow: `block_actions` carries a `trigger_id` valid about 3 s → `views.open`; carry the record key in `private_metadata`; `view_submission` must be acked within 3 s before downstream writes. Modals have no file input.

## Email parsing
- Newline-preserving converter: turn `<br>` and the open/close tags of `p, div, tr, li, h1-6, table, ul, ol, td, th, blockquote, section` into `\n` before stripping tags; collapse only spaces and tabs; trim per line.
- `from` normaliser: accept string, `{text}`, `{value:[{address,name}]}` or an array; return `''` for an unknown shape so the fallback is reached; strip the `^(from|reply-to|to):\s*` prefix from `headers.*`.
- Drive at least one parser test from real trigger items pulled from execution data: hand-captured fixtures store `from` as a string and cannot reproduce the object trap.
- Backlog catch-up polls stamp a whole day with one poll time: never use `new Date()` as receipt time.

## Examples
- An error workflow's Slack node had a plain string in `blocksUi` and failed all four times it fired; "no alerts" had read as "no failures" for weeks.
- A provider classifier ran on its weakest signal for months because `from` was an object.

