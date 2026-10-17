---
name: memory-hygiene
description: 'Use when writing, correcting or migrating agent memory: MEMORY.md heads and the inbox, pact-memory records, correcting a claim in every carrier, the distill pass that moves inbox lessons into skills (distill-inbox), and index budgets (check-index).'
---

# Memory hygiene

Context: agent memory loads into every spawn through its head, so the head stays a small router
and lessons live elsewhere: the agent definition routes to skills, the head points, the inbox
holds new lessons until the distill pass moves each one into a skill reference, a project doc or
the vault. The size limits are the head budget in the index-upkeep rule of `teammate-memory`;
this skill never restates them, and `check-index` enforces them.

## Workflow
1. New lesson: append ONE line to your dir's inbox file (`MEMORY-inbox.md`) with Edit, never a
   rewrite: `YYYY-MM-DD | lesson | evidence path`. NEVER add a lesson to the head.
   → `references/head-and-inbox-policy.md`
2. Before any head edit, run `check-index <dir>`; after it, re-run and fix every violation.
   Parse links with a bracket-anchored regex, never split on " — ", and set-compare the
   (title, target) pairs before and after in both directions.
   → `references/head-and-inbox-policy.md`
3. Distill pass (the lead, at `/repacted:wrap-up` or when `check-index --all` fails):
   `distill-inbox list` → `distill-inbox plan --suggest` (a duplicate shortlist; you decide
   every row) → edit the chosen skill reference yourself, or `vault-note append`, or the project
   doc, or mark `duplicate` → `distill-inbox apply distill-plan.json` (it runs `check-index` and
   `scrub-check`) → commit in the plugin source repo as a patch bump, one sentence.
   → `references/distill-pass.md`
4. Correcting a claim: grep the plugin source repo, the heads, the inboxes and the vault by the claim's
   PHRASING, not its id; rewrite the rule in place in every carrier; put provenance in the commit
   sentence (repo) or a `Corrected YYYY-MM-DD:` suffix (vault, memory); pact-memory uses
   `--replace` with a provenance line and the complete list.
   → `references/correcting-and-carriers.md`
5. File a lesson by the PROPERTY it teaches, not the surface where it happened. A single instance
   is a fact; a generalisation is a candidate until a second, independent instance agrees.
   → `references/correcting-and-carriers.md`
6. A teammate NEVER shrinks its own index. Shrinking is a recorded migration that the lead runs
   (or a script the lead runs) while no session writes memory. → `references/head-and-inbox-policy.md`

## Map
- `references/head-and-inbox-policy.md` what a head may hold, the inbox, safe edits, scopes,
  the migration.
- `references/distill-pass.md` the pass step by step, choosing destinations, duplicates.
- `references/correcting-and-carriers.md` corrections in every carrier, pact-memory `--replace`,
  ids and state claims.
- `protocols/self-refine.md` and `refinement-log.md` keep the skill current.

## Commands
- `check-index [--all | DIR...] [--preamble-from FILE]`: exit 0 pass, 1 violation, 3 nothing
  checked (not a pass).
- `distill-inbox list | plan [--suggest] [--out F] | apply F`: exit 0 ok, 1 a check failed or a
  planned line could not be applied, 2 usage.
- `scrub-check PATH... [--denylist-only | --no-denylist]`: run on every skill file you touch.
  Exit 0 clean, 1 hits (printed as `path:line: kind`, never the match), 2 usage, 3 the scan
  proves nothing (the denylist outside git is missing or empty, or no file was scanned).

## Related skills
- repacted:pact-lead-playbook schedules the distill pass at wrap-up.
- repacted:pact-field-notes for pact-memory CLI bugs and the harvest ledger.
- repacted:verification-discipline for proving a correction landed (a count check is not a
  content check).
- `repacted:pact-memory` for the store itself.

## Refine
At the end of a session that used this skill, run `protocols/self-refine.md`.
