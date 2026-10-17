# Worktrees

A [worktree](../glossary.md#worktree) is a second checkout of your repository, on its own branch, in its own folder. Git supports this with `git worktree`. rePACTed uses one worktree for each feature, so two features (or two teammates on parts of one feature) can work at the same time without touching each other's files.

## Where they live

All worktrees live in `.worktrees/` at the root of your repository:

```
my-cli/
  .worktrees/
    feature-config/          <- branch feature-config
    feature-config--tests/   <- a sub-part of the same feature
  greet.py
```

The skill `repacted:worktree-setup` creates them. It also adds `.worktrees/` to your `.gitignore`, so the extra checkouts never get committed.

## How one is made

The lead runs the `repacted:worktree-setup` skill at the start of `/repacted:orchestrate` or `/repacted:comPACT`. It:

1. runs `git worktree list` and reuses a worktree that already exists for the branch
2. creates `.worktrees/` and adds it to `.gitignore` if needed
3. runs `git worktree add "<repo root>/.worktrees/<branch>" -b <branch>`
4. reports the path, which the lead passes to every teammate on that feature

If the branch already exists, the skill asks you whether to check it out or pick a new name.

## The worktree guard

When `PACT_WORKTREE_PATH` is set, the [worktree guard](../glossary.md#worktree-guard) refuses an `Edit`, `Write` or `NotebookEdit` on application code outside that worktree. When the variable is not set, the guard does nothing.

## Rules for teammates

Several agents can share one repository, so:

- use absolute paths, or `git -C <worktree path>`, in every command
- commit only on your own branch, never on `main`
- never run a bare `git stash` or `git stash pop`: every worktree shares one stash list

The skill `repacted:concurrent-agent-git` has the full list.

## Example

The lead starts a feature:

```
/repacted:orchestrate add a config file that sets the default greeting
```

The setup skill prints:

```
Worktree ready at /home/you/my-cli/.worktrees/feature-config
Branch: feature-config
```

The coder and the test engineer both work in that folder. Your main checkout stays clean. You can keep working there, or start a second feature in its own worktree.

## Cleaning up

When the pull request is merged, the skill `repacted:worktree-cleanup` saves any docs the worktree made, then removes the worktree and its branch.
