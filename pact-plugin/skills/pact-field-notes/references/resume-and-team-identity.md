# Resume and team identity

Context: the one runbook for a resumed or compacted session whose dispatches fail because the
team name is wrong. Read it when `dispatch_gate` says "no Task assigned to owner=... in team X"
while TaskList shows the task, when a teammate reports "already a live member", or when a spawn
prompt, CLAUDE.md and the hook disagree about the team.

Stamp legend: `(verified|observed DATE, VERSION; until MILESTONE)`. "until M3" means the line is
a workaround for a fork bug that repacted milestone M3 fixes; delete the line when M3 lands.
Marks ("re-probe", "practice", "until Mn"): defined once, in [SKILL.md](../SKILL.md).

## Canonical rules
1. Neither CLAUDE.md `- Team:` nor the hook's announced team is authoritative; test the name with `ls -d <config>/teams/<name>/`. (verified 2026-09-26, PACT 4.8.1; until M3)
2. Run `pact-which-team` first: it names the context team, whether its dir exists, the freshest task store, and which branch below applies. (verified 2026-09-26, PACT 4.8.1; until M3)
3. The live team is the one whose `tasks/<team>/` was written seconds ago; create a task and see which dir it lands in to confirm. (observed 2026-09-04, PACT 4.7.x; until M3)
4. Passing the right `team_name` to `Agent()` does not help: `dispatch_gate` reads `pact-session-context.json`, not your argument. (observed 2026-09-04, PACT 4.7.x; until M3)
5. Branch 1 (no team config claims this session): back up the context file, set its `team_name` to the live team; leave `session_id` and the journal dir alone. (verified 2026-09-26, PACT 4.8.1; until M3)
6. Branch 2 (a stale team config's leadSessionId equals this session): the resolver's identity match overrides the edit; apply the branch 2 fix below. (verified 2026-09-26, PACT 4.8.1; until M3)
7. Root cause: the branch-3 resume census returns nothing unless exactly one candidate exists, so any second live session blocks realignment (the backlog: the upstream census fix, picked at M3). (verified 2026-09-26, PACT 4.8.1; until M3)
8. The copy of CLAUDE.md in your system prompt predates `session_init`'s rewrite; re-read the file from disk before quoting it. (observed 2026-08-26, PACT 4.6.44; re-probe)
9. A fresh block mtime does not make every field right: `session_init` can write a stale team name into a freshly written block. Test the name, not the timestamp. (observed 2026-09-02, PACT 4.6.54; until M3)
10. Before blaming a stale snapshot, `stat` the file: mtime later than the other reader's read means a write-after-read race, not a stale copy. (observed 2026-09-09, PACT 4.7.x; practice)
11. A git worktree carries its own copy of a TRACKED CLAUDE.md, frozen at its commit; `session_init` rewrites only the copy under the session's project dir, so read the file on the believer's cwd. An untracked CLAUDE.md is not in a worktree at all. (verified 2026-10-02, git 2.54.0, Claude Code 2.1.288, repacted 1.3.7, by probe and code read; re-probe)
12. The session dir is named after the RESUMED session id, so it never matches the team name; that mismatch is normal. (observed 2026-10-02 in a teammate spawn, Claude Code 2.1.288, repacted 1.3.7; re-probe)
13. Spawn results echo `name@team`; read the first one, it reveals the real team. A wrong team in a spawn prompt still routes, but the teammate mis-reports itself. (observed 2026-09-04, PACT 4.7.x; re-probe)
14. An empty task store after a resume is a routing symptom: list `tasks/` by mtime and grep your task id across every team dir before reporting loss. (observed 2026-07-21, PACT 4.6.x; until M3)

## The runbook

**Step 0, discriminate.** `pact-which-team` (read-only) prints the context file it used, the team
it names, whether that dir exists, the newest task stores, and a verdict line:
- `OK`: the context team exists and either identity-matches this session or holds the freshest
  store. The failure is elsewhere.
- `PHANTOM` / `STALE` plus `branch 1`: no team config claims this session, so the resolver falls
  back to the context file's `team_name`. Editing it holds.
- `PHANTOM` / `STALE` plus `branch 2`: another team's `config.json` has
  `leadSessionId == <this session>`. The resolver's branch-1 identity match returns that team on
  every hook call, so an edit of `team_name` appears to revert.

**Branch 1 fix.** Copy `pact-sessions/<project>/<session>/pact-session-context.json` aside, set
`team_name` to the live team, keep `session_id` and the journal directory unchanged (journal
continuity), then re-dispatch. The next hook process re-reads it (the resolver caches per process,
and every hook is a fresh process).

**Branch 2 fix (lead only).** Set the context
`team_name`, rewrite the project CLAUDE.md `- Team:` line, set the live team config's
`leadSessionId` to this session, and null the field in any other config that claims it. Status:
**consistent with the 4.8.1 resolver by static read** (branch 1 identity match then resolves to the
live team, and the census skips configs whose field is not a string), **not exercised live at
4.8.1**. It edits platform-owned team configs and a CLAUDE.md, so only the lead does it (teammates
never write CLAUDE.md). Retired at M3.
(verified 2026-09-26, PACT 4.8.1 static read; until M3)

**Why both fixes exist.** The resolver tries, in order: identity match on
`teams/*/config.json` `leadSessionId`; a config-less full-UUID team dir; the resume census; then
the persisted context `team_name`. Branch 1 is the census-fails-to-fallback case; branch 2 is the
identity-match-wins case. Pick by the verdict, not by habit.

**One resolved name feeds three surfaces** (the context file, the CLAUDE.md `- Team:` line and the
hook's announced value), so their agreement proves nothing. Only the filesystem settles it.

**Symptom if you guess wrong:** specialists spawn into a phantom team, and SendMessage and task
assignment cannot reach them. In-process ghost members of a dead team cannot be reaped by
TaskStop or SendMessage; the fixes above sidestep them.

## Examples
- Hook and CLAUDE.md both named a team that did not exist; the live team was the one whose task
  dir was seconds old. Following "trust CLAUDE.md" left dispatch dead.
- A teammate reported CLAUDE.md "still says" yesterday's team from its prompt snapshot; the file on
  disk had been current for a minute. The hook, not the file, was wrong.
- Three CODE lanes in one worktree kept citing a plan 19 days stale; the worktree's CLAUDE.md was
  frozen at its creation.
