# /repacted:wrap-up: why the cleanup steps are shaped as they are

> Loaded on demand from [wrap-up.md](../../commands/wrap-up.md) when a step 5-7 rule is in question: why the propagate request sits where it does in branches A and B, the remote-matching anchor, the minted-delete rules in full, recovery, and the task cleanup table.

## Step 5 recovery note

**Recovery note**: The journal lives in `{config_dir}/pact-sessions/{slug}/{session_id}/`, independent of the team directory — it survives both natural TTL cleanup and explicit team teardown. Old session directories are cleaned automatically after 30 days (with paused-session preservation). See [recovery.md](../../protocols/recovery.md) for the full State Recovery Protocol.

## Step 6, branch A: why the propagate request comes first

> This is the arc's terminal propagation, and its position is set by what it DEPENDS on rather than by where it reads tidily. It needs two things and only two: the step-5 drain, so the store it projects is the consolidated one — guaranteed for the whole of step 6 by the gate at the top of it, not by anything below — and a verified `MERGED`, which is what selecting this branch already means. Both hold before sub-step 1 runs. It depends on nothing the sub-steps produce: it carries no path, and the secretary resolves its own project by repository root, so removing the worktree cannot reach it. Placing it last gave a refused `--ff-only` pull in sub-step 4 the power to stop the sequence before the arc's only projection into the block — on a branch that writes no resumption claim, so nothing later retries it. It stays in the branch rather than moving back to step 1 because step 1 cannot know which branch this will be.

## Step 6, sub-step 3: the remote-matching anchor

The boundary anchor is load-bearing twice over: it prevents a bare-substring **prefix false-positive** (an un-anchored "contains `HEAD_OWNER/HEAD_REPO`" would also match a remote at `other-HEAD_OWNER/HEAD_REPO` or `HEAD_OWNER/HEAD_REPO-fork`), and it is **host-agnostic** — the same anchor matches SSH scp-form `<user>@github.com:O/R(.git)`, HTTPS `https://github.com/O/R(.git)`, `ssh://` scheme URLs, host-alias forms (`<user>@github.com-work:O/R`), and `insteadOf`-rewritten remotes alike, because it keys only on the owner/repo tail, not the host.

## Step 6, branch B: why the propagate request comes first

> It goes first here for the same reason it does in branch A, and the halt is a different one: `/repacted:worktree-cleanup` surfaces a loud warning when a worktree's `docs/` artifacts were never harvested and lets the user **abort**. An abort there would otherwise skip the arc's only projection into the block, on a branch that — like A — writes no resumption claim, so nothing later retries it.

## Step 7 cleanup rules

**Cleanup rules**:

| Task State | Cleanup Action |
|------------|----------------|
| `completed` Feature task | Archive summary, then delete with children |
| `in_progress` Feature task | Do NOT delete (workflow still active) |
| Orphaned `in_progress` | Document abandonment reason, then delete |
| `pending` blocked forever | Delete with note |

**Why conservative:** Tasks are session-scoped by default. Cleanup only matters for multi-session work via `CLAUDE_CODE_TASK_LIST_ID`.

