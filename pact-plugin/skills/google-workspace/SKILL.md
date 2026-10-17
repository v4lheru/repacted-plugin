---
name: google-workspace
description: "Use for Google Apps Script and clasp (push, deploy, re-pinning web-app deployments, .claspignore, triggers, quotas, LockService, time zones) and the Google Drive, Docs, Sheets and Gmail APIs (shared drives, export, batch updates)."
---

# google-workspace

Context: field-measured traps of Apps Script, clasp and the Drive, Docs, Sheets and Gmail APIs, so that a deploy, a listing or a date is right the first time. Most of these fail with HTTP 200 or a green push, which is why each step below ends in a proof.

## Workflow
1. **Work in an isolated temp dir** (`/tmp/<name>`). NEVER `clasp clone` into a project repo. Project script and deployment IDs live in each project's docs/agent-notes/. → [apps-script-and-clasp](references/apps-script-and-clasp.md)
2. **Before pushing, make `.claspignore` deny-by-default.** Apps Script evaluates every pushed file's top level, so one test file breaks every run. After EVERY push run `clasp show-file-status` and count the tracked files. Avoid ES2020+ syntax (numeric separators fail in the V8 runtime even when `node --check` passes).
3. **To change a live web app, re-pin its deployment.** The `/exec` URL serves a pinned version, not `@HEAD`, so saving or pushing changes nothing live. Run `clasp deploy -i <deploymentId> -d "msg"` to keep the shared URL. A plain `clasp deploy` mints a new URL.
4. **Dates from `getValues()` are local midnight in the spreadsheet's time zone.** Normalise them with `Utilities.formatDate(v, ss.getSpreadsheetTimeZone(), …)`. NEVER use `getUTC*` getters on sheet dates.
5. **Drive listings inside a Shared Drive** pass `corpora='drive'` + `driveId` + `includeItemsFromAllDrives` + `supportsAllDrives` on EVERY list call, request `nextPageToken` in `fields`, and resolve shortcuts. Check the total against a known folder before trusting it. → [drive-docs-sheets-gmail](references/drive-docs-sheets-gmail.md)
6. **Prove the result the way the consumer sees it:** open the `/exec` URL and see the new behaviour, open the created Doc, compare the listing total. A push count, a 200 or an empty list is not proof. If access fails, test the capability with one read-only call before declaring it blocked.

## Map
- [apps-script-and-clasp](references/apps-script-and-clasp.md): clasp commands and the pinned-deployment trap, `.claspignore`, V8 syntax, sheet dates and time zones, advanced-service call shapes, web apps and n8n brokers, quotas, LockService, PropertiesService, review checklist, local testing.
- [drive-docs-sheets-gmail](references/drive-docs-sheets-gmail.md): Shared Drive parameters, pagination `fields`, OR'd-parent dropouts, shortcuts, `changes.list` cursors, ACL diffs, which credential can reach Drive, metadata forensics, Docs formatting, write quotas.

## Related skills
- Security review of an Apps Script: `repacted:security-review-patterns`.

## Refine
At the end of a session that used this skill, run [self-refine](protocols/self-refine.md) and record what it changed.
