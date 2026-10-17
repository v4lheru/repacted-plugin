---
name: concurrent-agent-git
description: Use before git add, commit, stash, checkout, reset, rebase or a build in any worktree or scratch dir other agents may share, and whenever a result depends on which tree, branch, index or session root you are in.
---

# Concurrent agent git

Context: several agents in one worktree share one git index, one working tree, one stash stack, one
scratchpad and one build dir, and a session's root can move under it. This skill keeps commits to
your own paths, keeps your checks about the right tree, and keeps your cleanup off a peer's work.

## Workflow
1. Establish the frame in the SAME command as the write or the number you will report, and use
   absolute paths for every write:
   `pwd; /usr/bin/git rev-parse --show-toplevel --abbrev-ref HEAD; /usr/bin/git log -1 --format=%h`
   → `references/session-roots-and-shared-resources.md`
2. Before committing, run `/usr/bin/git diff --cached --stat` as its own command. Commit only your
   paths: `git commit -m "<one sentence>" -- <paths>`, then `git show --stat HEAD`. Hold work by
   NOT staging it; a hold written in prose is not a mechanism.
   → `references/shared-index-and-commits.md`
3. NEVER stash, reset, check out or switch branches in a shared tree without first confirming,
   with `git status --porcelain` and `git diff --stat <target>...HEAD`, that nobody else's work is
   in the way. Read setup scripts and error-suggested remediation commands before running them; use
   a private scratch copy if they touch a fixed-name shared resource.
   → `references/session-roots-and-shared-resources.md`
4. Verify the committed tree, not the working dir: `git archive <sha> | tar -x -C <scratch>` (or
   `git write-tree` for the staged set), symlink `node_modules` if needed, then build and test
   there with `tcount`. Attribute a red or green only after checking what else changed.
   → `references/verify-committed-tree.md`
5. Push early when a session root can move under you, and re-check `git log -1` after any pause.

## Map
- `references/shared-index-and-commits.md` the index, pathspec commits, stash, branch switches,
  holds, reporting tree state.
- `references/session-roots-and-shared-resources.md` moving roots, scratchpad, ports, build dirs,
  setup scripts, fresh-worktree dependencies.
- `references/verify-committed-tree.md` building what git has, co-commit sets, attribution.
- `protocols/self-refine.md` and `refinement-log.md` keep the skill current.

## Related skills
- repacted:pact-field-notes `worktrees.md`: worktree write-guard pins and refused writes.
- repacted:shell-tooling-traps: why `/usr/bin/git` and `tcount` (rtk rewrites git and test output).
- repacted:verification-discipline `vd-c1c-runners-and-toolchain.md`: what a green run proves.
- repacted:worktree-setup: creating an isolated worktree for a workflow.
Commit messages follow the core rule: one sentence, no attribution, no security-tooling names.
Merge and push approval is the core's rule, not this skill's.

## Refine
At the end of a session that used this skill, run `protocols/self-refine.md`.
