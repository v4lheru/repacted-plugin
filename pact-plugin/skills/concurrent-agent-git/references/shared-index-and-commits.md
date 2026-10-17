# Shared index and commits

Context: agents in one worktree share one git index, one working tree and one stash stack. Read
this before `git add`, `commit`, `stash`, `checkout`, `reset` or `branch` in a tree anyone else may
be writing, and before you report what is staged or committed.

## Canonical rules
1. Commit with an explicit pathspec: `git commit -m "<one sentence>" -- <paths>`. `git add <path>` scopes the add; only the pathspec scopes the commit.
2. There are two index windows: assert `git diff --cached --name-only` is empty (or only yours) BEFORE your `add`, and use the pathspec so a peer staging AFTER it is excluded.
3. Run the staged-set check as its own command, not chained to the commit; a check in the same `&&` chain reports after the action has run.
4. The pathspec form fails on an untracked file, so a new file needs `git add` first; chain `add && commit -- <path>` as two actions, nothing else between.
5. Read the "N files changed" line against what you expected every time, then `git show --stat HEAD`; it is the only signal a peer's files were swept in.
6. Name generated and lock files explicitly: a commit hook (e.g. `pnpm exec commitlint`) can rewrite the lockfile from peers' edits, and a pathspec silently leaves it out.
7. A pre-commit hook scans the whole staged index; if a peer's staged file blocks you, report it. Never weaken the hook and never unstage their file.
8. Never `git stash` in a shared tree: the stash stack is shared by every worktree of the repo, and a failed scoped push followed by pop applies a stranger's stash.
9. If you must stash in a private scratch worktree: `git stash push -u -m <unique-tag>`, capture the SHA, `git stash apply <sha>`, then drop that entry by SHA.
10. Red run (your tests against the pre-fix code): commit the fix, `git checkout <base> -- <file>`, run, `git checkout HEAD -- <file>`. Told not to commit: `git add` the fix, `git restore --source=<base> --worktree -- <file>`, run, `git restore --worktree -- <file>`. The fix stays in a commit or the index, never in the shared stash stack or a temp copy, and only named files move.
11. Before any branch switch run `git diff --stat <target>...HEAD` and `git status --porcelain`; non-empty output is the list of files you would change for everyone.
12. `checkout -b X`, commit, checkout back DELETES files that were untracked and got committed on X; in a shared tree with untracked work, do not switch branches.
13. To preserve work without touching index or checkout, use plumbing: a temporary `GIT_INDEX_FILE`, `read-tree`, `update-index --cacheinfo`, `write-tree`, `commit-tree`, `branch`.
14. Hold a change by NOT staging it (or a separate branch); "do not commit this yet" in a handoff is a request, not a control.
15. In a shared concurrent tree, do not self-commit unless the dispatch says to: list your exact files in the handoff and let the lead make one integrated commit.
16. A sole worker told to commit locally commits. "Specialists commit and open MRs" is a per-project ruling, not a default.
17. Report your own files by name with a timestamp ("I staged A, B at 12:29"); never characterise the shared index ("only X is staged").
18. Ship the command, not the claim: quote the output of the command beside any tree-state assertion, or do not make it.
19. Check a git command's exit code before a destructive follow-up; use `&&`, never `;`. Do not read `${PIPESTATUS[0]}` under zsh or rtk, it is empty.
20. A file missing from `git status` means reverted OR committed; resolve with `git show HEAD:<path>` and `git log -1 -- <path>`, never by inference.
21. A surviving `stash@{0}` after a reported pop means the pop did not complete; flag it.
22. After a rollback report, recover from the index (`git checkout -- <path>`, after checking `git diff --cached <path>`) or a commit, never by re-typing what you see.
23. If a sweep already happened, do not rewrite shared history to tidy it: disclose the sha and file list to the peer and the lead, and describe only files you opened.
24. A declared touched-files list is true at its task boundary only; when a commit lags that boundary, reconcile against `git status` and list deletions explicitly.
25. Keep every module importable after each edit in a shared checkout: add a definition before its reference, or make the multi-step change in one write.
26. A source change and the mock patch it needs are one atomic commit; say which staged files must land together.
27. Do not delete a shared type or block a peer's code imports; let their commit absorb the dedupe, then import theirs.
28. A test that restores a file with `git checkout --` reverts your unstaged edits to it; stage first, and grep for your edits after the suite.
29. Tools that act "on your behalf" in a shared namespace (index, task-status hook) can touch a peer's thing: verify whose, then report, do not revert.

## Detail

**The sequence that works in a shared tree.**
```bash
pwd; /usr/bin/git rev-parse --show-toplevel --abbrev-ref HEAD
/usr/bin/git diff --cached --name-only          # own command: empty, or only yours
/usr/bin/git add -- <your paths>                 # new files only need this
/usr/bin/git commit -m "<one sentence>" -- <your paths>
/usr/bin/git show --stat HEAD                    # exactly your paths
```
`/usr/bin/git` sidesteps the rtk rewrite (repacted:shell-tooling-traps); where a worktree guard is
active, see repacted:pact-field-notes `worktrees.md` for which forms it allows.

**Auditing a shared tree before a landing.** In order: `git diff --cached --name-only` (what a bare
commit would take), `git diff --name-only` (unstaged), `git ls-files --others --exclude-standard`
(untracked); flag any path in both of the first two. Attribute by diff hunk locations
(`git diff -U0 <file> | grep '^@@'`), not by paths grepped out of handoff prose: a path inside a
`not_mine` field or a verification note reads exactly like an edit claim. Sweep every handoff field
(cross-lane edits live under keys like `cross_agent_edits`, never in the editor's own manifest),
then subtract each lane's manifest; the remainder is the cross-lane surface. Ask who owns a file,
not who was working near it. Git cannot see a peer rewriting your untracked file: hash your
untracked files at the start (`md5 -q`) and re-hash before reporting. Clearing a stray index with
`git reset` changes no file content.

**Recovering a moved branch name.** A rename that hit a peer's branch is restored by renaming it
straight back (same commit); then tell the peer exactly what to ignore and retry.

**Holding with a `blockedBy` edge.** Ask whether the guarded condition has been met: not met and the
edge binds, the control works, so wait; met while the edge still binds, the edge is too coarse, so
report it. Inconvenience is not the test.

## Examples
- `git add docs/x.md; git commit` produced "5 files changed": four were a peer's staged tests, under
  a documentation subject. The first disclosure called them "just a label rename" after reading
  one file's tail; three carried real fixes.
- A lead's instruction `git checkout -b deploy/x origin/master` would have reverted a peer's
  committed CI guard out of the working tree; `git diff --stat origin/master...HEAD` showed it.
- A failed `git stash push -- <paths>` (untracked path) created no stash; the blind `pop` applied
  an old stash over a teammate's file and left `.gitignore` in conflict.
- A lead committed a staged set whole although the handoff said one file was held; the peer it
  waited on approved the change hours later, but the gate had been satisfied out of order.
