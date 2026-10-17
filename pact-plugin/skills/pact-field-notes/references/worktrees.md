# Worktree pins and refused writes

Context: the Claude Code worktree write guard and session pin, and the PACT worktree lifecycle, when
they misbehave. Read it when Write/Edit or git is refused with "This session is isolated in the
worktree X", when a command's result looks like it came from another tree, or when cleaning up
worktrees. Agent-side git discipline in a shared tree lives in repacted:concurrent-agent-git.

Stamps `(verified|observed DATE, VERSION; until MILESTONE)` as in [hooks-and-harness](hooks-and-harness.md); "re-probe" marks a Claude Code
platform behaviour with no fork fix.
Marks ("re-probe", "practice", "until Mn"): defined once, in [SKILL.md](../SKILL.md).

## Canonical rules
1. Two pins clear differently: a session pin clears with `ExitWorktree({action:"keep"})`; a per-agent write-guard pin clears with the re-key below. (observed 2026-08-25, Claude Code, version n/a; re-probe)
2. Pick the row by whose tree the refusal names: YOUR lead session refused `cd <tree> && git` → session pin; a teammate refused naming a PEER's tree → write-guard pin. (observed 2026-08-25, Claude Code, version n/a; re-probe)
3. Re-key: `EnterWorktree({path: "<the linked worktree you are already in>"})`. It errors "is the current working directory", and that failed call re-keys the guard. (observed 2026-08-25, Claude Code, version n/a; re-probe)
4. Against the MAIN working tree the re-key errors "not a linked worktree" and clears nothing; you are on the wrong row, use ExitWorktree. (observed 2026-08-25, Claude Code, version n/a; re-probe)
5. Prove every fix with a write probe: write a throwaway file, confirm it landed, delete it, check `git status --porcelain`. The return text is not evidence. (observed 2026-09-08, Claude Code, version n/a; practice)
6. The pin can move between two consecutive commands; print `pwd` and `git log -1` in the SAME command as any write or any number you report. (observed 2026-09-08, Claude Code, version n/a; re-probe)
7. A moved pin silently redirects READS: a relative-path test run reported another tree's suite as a real, wrong count. Use absolute paths. (observed 2026-09-08, Claude Code, version n/a; re-probe)
8. A refusal naming YOUR OWN tree under the rtk hook is rtk x guard: call `/usr/bin/git` by absolute path, one uncompounded command, no `cd`. (observed 2026-09-08, rtk 0.41.0; re-probe)
9. Absolute-path git is permitted only for the tree the guard currently attributes to you; `git -C <other tree>` is refused, and aiming it there is evasion. (observed 2026-09-08, Claude Code, version n/a; re-probe)
10. The spawn env's "Working directory" can be stale in the same way as the pin; when it disagrees with the dispatch, confirm with the lead and `ls` both trees. (observed 2026-08-25, Claude Code, version n/a; re-probe)
11. Never write into the tree the guard names while a peer works there, and never copy source between two checkouts to escape a pin. Report where the work is. (observed 2026-08-25, Claude Code, version n/a; practice)
12. A surprise auto-claim of a peer's task by `task_claim_gate` tends to precede the pin moving to that peer's tree; re-probe before any irreversible step. (observed 2026-09-08, PACT 4.7.x; until M12)
13. To rescue a blocked peer's work, copy untracked files OUT of every worktree plus `git diff > patch`; untracked files have no reflog. Do not commit in its lane. (observed 2026-09-08, PACT 4.7.x; practice)
14. Read-before-Edit state survives a teachback idle on a worktree path: the first Edit after the idle applied with no re-Read. After a compaction (lost there in 2026-08, not probed again) re-Read the target slice before the first Edit. (observed 2026-10-02 in a teammate spawn, Claude Code 2.1.288, repacted 1.3.7; re-probe)
15. Remove each feature worktree with `git worktree remove <path>` once its branch is merged; create worktrees under `.worktrees/`, not as sibling project folders. (observed 2026-07, PACT 4.6.x; practice)
16. A fresh worktree has no `node_modules`: symlink the main checkout's only if package files are unchanged, and remove the symlink (`rm`, not `rm -rf`) before staging. (verified 2026-10-02, git 2.54.0, Claude Code 2.1.288, repacted 1.3.7; re-probe)

## Detail

**Discriminator table.**

| Refusal or symptom | Fault | Fix |
|---|---|---|
| lead: git refused "must target its own worktree" | session pin | `ExitWorktree({action:"keep"})` |
| teammate: Write refused, names a peer's tree | write-guard pin | re-key (rule 3), probe |
| refusal names your own tree, rtk active | rtk rewrite opaque to the guard | `/usr/bin/git ...` |
| re-key says "main working tree" | wrong row | ExitWorktree |
| numbers differ from expectation, no error | pin moved, command measured another tree | re-run with absolute paths and `pwd` in the same call |

Fixing one fault can expose the next: after the pin re-key, refusals named the agent's own tree,
which was the rtk interaction. That is progress, not a failed fix. The pin can also flap within
one task, so re-issue the re-key immediately before a commit, with no intervening calls.

**rtk and the guard, by guard state.** With no worktree guard active, `rtk proxy git ...`
and `/usr/bin/git ...` both give raw output. With the guard active, `rtk proxy git` is refused
(an opaque launcher, like the rewrite) and only `/usr/bin/git` against your attributed tree passes.

**Verify with output.** `/usr/bin/git status --porcelain` returning exit 0 and nothing is equally
consistent with a clean tree and with "did nothing"; follow with `rev-parse --show-toplevel` and
`log --oneline -2`, which print. The guard also string-matches "git" inside heredoc prose, so
write files with the Write tool rather than a shell heredoc.

**Benign forms the guard refuses** while a pin is active: a runtime-computed interpreter name,
a space-containing absolute interpreter path, `cd <tree> && git`. Put the venv on PATH and call
`python3`; run git from the pinned cwd with no `cd`.

**The structural fix** is to need no local root: push early, verify the remote with `glab api`,
or author commits server-side. `glab`, `kubectl` and `helm` keep working when local git does not.

**node_modules details.** Compare package files with `git diff --stat HEAD -- package.json
package-lock.json` (a wrapped `diff` can mis-report). macOS sandboxing blocks native `.node`
loads for vitest; rerun that one command with the sandbox disabled (`tsc` does not need it).
Turbopack `next build` rejects a symlinked `node_modules`; clone it with `cp -Rc` for builds.
Never disable engine-strict. Note the symlink in the HANDOFF so TEST reinstalls.

**Cleanup.** `git worktree remove` deletes only the working dir and admin entry, not the branch,
so it is reversible and does not trip the merge guard. Remove only when `git status` is clean and
the branch is merged. See `repacted:worktree-cleanup` for the PACT lifecycle command.

## Examples
- Three of four overnight lanes accepted the refusal as a legitimate guard and wrote 22K of
  specifications instead of code; the re-key would have cost one call.
- An agent ran `EnterWorktree`, verified its tree, then `git branch -m` renamed a peer's branch:
  the pin moved between the two commands.
- A test run reported 519 passing instead of 553; the pin had flipped and the command ran the
  shared checkout's suite.
