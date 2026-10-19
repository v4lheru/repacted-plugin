# Init: pin context to CLAUDE.md

> Loaded by `/repacted:init pin [<content>]` ([init.md](../../commands/init.md)), and by `/repacted:pause` step 2. Run the steps as written.

## Mode

- **With arguments** (`/repacted:init pin <content>`): pin that content.
- **Without arguments**: review the session, apply "When to Pin", pin what matters (or report "No new context to pin.").

## When to Pin

Critical gotchas; key architectural decisions (the "why"); build/deploy commands needed every session; non-obvious patterns. NOT routine context (auto-memory and pact-memory hold it), things easy to find, or soon-stale facts.

## Process

**Target file**: `.claude/CLAUDE.md` if it exists, else `./CLAUDE.md`; if neither, create `.claude/CLAUDE.md`.

1. Read CLAUDE.md. Locate or create `## Pinned Context` (before `## Working Memory`).
2. **Cap check**: at most 12 pins (`### ` headings under `## Pinned Context`), each about 1500 characters or less. At 12, run the prune procedure first (`/repacted:init prune`, [prune.md](prune.md)); over the size, compress.
3. Add the entry under `<!-- pinned: YYYY-MM-DD -->` then `### Entry Title` and ~5-10 lines, **with the `Edit` tool, never `Write`** (a `Write` turns CRLF files into LF). Inside a pin body use `#### ` or bold, never `### ` (that reads as another pin).
4. **Commit, then confirm `CLAUDE.md` is in the commit**, not merely that the commit exited 0: a git-ignored CLAUDE.md is silently omitted by `git commit -a`. If it is missing, report the pin as **applied to the working file but not version-controlled**. NEVER `git add -f`.
