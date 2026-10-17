---
name: safe-ops
description: "Use before touching secrets or doing anything destructive: .env and key files (env-get, never source), git init and the first add, Docker and Railway ignore files, Secret Manager values, deleting files, rows, Drive items or branches, and scratchpad cleanup."
---

# Safe ops

Context: secrets never reach the transcript, a git repo or a build context, and a destructive
operation only ever hits the exact object that was recorded. This skill is the mechanism behind
the core's one-line safety rules (`env-get`, "the user writes secret values", "delete by the ID you
recorded").

## Workflow
1. **Need a secret in a command?** Run `env-get FILE KEY[,KEY...] -- <cmd>` and reference the
   value through the environment in single quotes. NEVER `source`, `cat` or `grep` a `.env`
   (a hook blocks reads, not output) or put a value in command text. For a call a guard
   must see (n8n writes), keep `curl` as the program, send only the header:
   `curl -H @<(env-get FILE KEY -- sh -c 'printf "Header: %s\n" "$KEY"') ...`. NEVER wrap a
   guarded CLI as `env-get ... -- sh -c 'curl ...'`: guards match the segment's program.
   → `references/secrets-and-env.md`
2. **Need a secret created or changed?** Name the file and the key and ask the user to write the
   value. You only verify it: presence, length (`${#KEY}`), and a probe call that returns 200.
3. **Before `git init` or the first `git add` in a directory,** list the files that look like
   secrets (`*.env*`, `*key*`, `*.pem`, `credentials*`, `*.json` beside the code), add them to
   `.gitignore` first, then confirm with `git status --ignored`. Stage explicit paths.
   → `references/secrets-and-env.md`
4. **Before a build or upload** (Docker, `railway up`), check all three ignore layers: git, then
   the platform upload (`railway up` ships the working tree, `.railwayignore`), then the Docker
   context (`.dockerignore`: leading slashes are ignored, never use `**`). A file ignored by git
   can still ship; verify on the built artifact. → `references/destructive-ops-and-ignore-layers.md`
5. **Before deleting anything,** record the exact ID (file ID, row PK, branch SHA) in the step that
   found or created it, and delete by that ID. NEVER delete by a name or search query: Drive
   `contains` tokenises, and `files.delete` skips the trash. Prefer trash over permanent delete.
   → `references/destructive-ops-and-ignore-layers.md`
6. **Before ending a session that wrote scratch data,** sweep the scratchpad for PII and raw
   payloads you copied there, and delete them by path.

## Map
- `references/secrets-and-env.md` env-get, key files, who writes secrets, first commits, leaks, process listings.
- `references/destructive-ops-and-ignore-layers.md` delete by ID, Drive traps, the three ignore
  layers, image layers, artifact checks, scratch cleanup.
- Command (plugin `bin/`, on the Bash PATH): `env-get`. Run it; do not reimplement the parser.

## Related skills
- repacted:shell-tooling-traps: why an exit 0 means nothing (a sourced `.env` with a bad key still exits 0).
- repacted:security-review-patterns: reviewing someone's code for secret leakage.

## Refine
At the end of a session that used this skill, run `protocols/self-refine.md`.
